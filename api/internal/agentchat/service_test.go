package agentchat

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/Jasonssssss9/musebridge/api/internal/agentclient"
	ac "github.com/Jasonssssss9/musebridge/api/internal/agentcontract"
	"github.com/Jasonssssss9/musebridge/api/internal/catalog"
	"github.com/Jasonssssss9/musebridge/api/internal/conversation"
)

const question = "This painting is beautiful. What material is it made of?"

var messageSequence atomic.Uint64

func message(revision int64) ac.PublicMessageRequest {
	return ac.PublicMessageRequest{ContractVersion: ac.PublicVersion,
		ClientMessageID: ac.UUID(fmt.Sprintf("11111111-1111-4111-8111-%012x", messageSequence.Add(1))),
		Question:        question, ExpectedRevision: revision}
}

type operation func(context.Context, ac.RunRequest, string) (ac.RunSnapshot, error)
type scriptedAgent struct {
	run, get    operation
	posts, gets atomic.Int64
}

func (a *scriptedAgent) Run(ctx context.Context, run ac.RunRequest, trace string) (ac.RunSnapshot, error) {
	a.posts.Add(1)
	return a.run(ctx, run, trace)
}
func (a *scriptedAgent) Get(ctx context.Context, run ac.RunRequest, trace string) (ac.RunSnapshot, error) {
	a.gets.Add(1)
	if a.get == nil {
		return ac.RunSnapshot{}, privateFailure("agent_invalid_response", 502, false, false)
	}
	return a.get(ctx, run, trace)
}

func privateFailure(code string, status int, retryable, reconcile bool) *agentclient.Error {
	return &agentclient.Error{Failure: ac.Failure{Status: status, Reconcile: reconcile,
		Error: ac.PublicError{Code: code, Message: "PRIVATE diagnostic", Retryable: retryable}}}
}

func completedFactory(t *testing.T) func(ac.RunRequest) ac.RunSnapshot {
	t.Helper()
	data, err := os.ReadFile("../../../docs/contracts/agent-v2.examples.json")
	if err != nil {
		t.Fatal(err)
	}
	var examples struct {
		Cases []struct {
			Name    string          `json:"name"`
			Payload json.RawMessage `json:"payload"`
		} `json:"schema_cases"`
	}
	if json.Unmarshal(data, &examples) != nil {
		t.Fatal("cannot read shared examples")
	}
	for _, item := range examples.Cases {
		if item.Name != "completed_run_snapshot" {
			continue
		}
		return func(run ac.RunRequest) ac.RunSnapshot {
			var snapshot ac.RunSnapshot
			if json.Unmarshal(item.Payload, &snapshot) != nil {
				panic("invalid shared fixture")
			}
			snapshot.RunID = run.RunID
			snapshot.Result.Scope = run.Scope
			snapshot.Result.CatalogSnapshot = run.Context.CatalogFacts
			snapshot.Result.ConversationUpdate.BaseRevision = run.ConversationState.Revision
			snapshot.Result.ReusedChunkIDs = []string{}
			for _, item := range run.ConversationState.VerifiedEvidence {
				snapshot.Result.ReusedChunkIDs = append(snapshot.Result.ReusedChunkIDs, item.ChunkID)
			}
			return snapshot
		}
	}
	t.Fatal("missing shared completed fixture")
	return nil
}

func setup(t *testing.T, agent Agent) (*Service, *conversation.Store, string, ac.PublicConversation) {
	t.Helper()
	dataset, err := catalog.Load("../../../knowledge/data/aic-seed-v0.1.json", "aic-seed-v0.1")
	if err != nil {
		t.Fatal(err)
	}
	store := conversation.New(nil)
	owner, current, created, failure := store.OpenAgent("", dataset, "aic-28560")
	if failure != nil || !created {
		t.Fatal("cannot create test conversation")
	}
	service, err := New(store, agent, nil)
	if err != nil {
		t.Fatal(err)
	}
	service.attemptLimit, service.postWait = time.Second, 100*time.Millisecond
	service.queryWait, service.pollInterval, service.commitReserve = 80*time.Millisecond, 5*time.Millisecond, 20*time.Millisecond
	return service, store, owner, current
}

func assertFailed(t *testing.T, store *conversation.Store, owner string, current ac.PublicConversation, failure *conversation.Failure, status int, code string) {
	t.Helper()
	if failure == nil || failure.Status != status || failure.Code != code || strings.Contains(failure.Message, "PRIVATE") {
		t.Fatalf("unexpected safe failure: %+v", failure)
	}
	history, ok := store.AgentHistory(owner, string(current.ConversationID))
	if !ok || len(history.Turns) != 0 || history.Conversation.Revision != 0 || history.PendingRequest == nil ||
		history.PendingRequest.State != "failed" || history.PendingRequest.LastError == nil || history.PendingRequest.LastError.Code != code {
		t.Fatal("failure changed completed history or lost its pending error")
	}
	// Inspect a subsequent frozen input rather than exposing store internals.
	probe, replay, invalid := store.BeginAgent(owner, string(current.ConversationID), message(0))
	if invalid != nil || replay != nil || probe.Request.ConversationState.Revision != 0 ||
		len(probe.Request.ConversationState.VerifiedEvidence) != 0 || probe.Request.ConversationState.ActiveTopic != nil ||
		probe.Request.ConversationState.ContinuitySummary != "" || probe.Request.ConversationState.PendingClarification != nil {
		t.Fatal("failed work entered verified evidence or semantic state")
	}
}

func TestSubmitCommitsOnceAndReplaysWithoutDownstreamWork(t *testing.T) {
	completed := completedFactory(t)
	agent := &scriptedAgent{run: func(_ context.Context, run ac.RunRequest, _ string) (ac.RunSnapshot, error) {
		return completed(run), nil
	}}
	service, store, owner, current := setup(t, agent)
	input := message(0)
	response, failure := service.Submit(context.Background(), owner, string(current.ConversationID), input, "submit-once")
	if failure != nil || response.Conversation.Revision != 1 || response.Turn.Sequence != 1 || len(response.Turn.Answer.Citations) == 0 {
		t.Fatalf("completed submission did not commit: %v", failure)
	}
	replay, failure := service.Submit(context.Background(), owner, string(current.ConversationID), input, "submit-replay")
	if failure != nil || !reflect.DeepEqual(response, replay) || agent.posts.Load() != 1 || agent.gets.Load() != 0 {
		t.Fatal("completed replay reran the Agent or changed the saved response")
	}
	history, ok := store.AgentHistory(owner, string(current.ConversationID))
	if !ok || len(history.Turns) != 1 || history.PendingRequest != nil {
		t.Fatal("successful commit did not finish the pending request")
	}
	probe, _, failure := store.BeginAgent(owner, string(current.ConversationID), message(1))
	if failure != nil || len(probe.Request.Context.RecentTurns) != 1 ||
		len(probe.Request.ConversationState.VerifiedEvidence) != 1 || probe.Request.ConversationState.VerifiedEvidence[0].ChunkID != "aic-28560-medium" {
		t.Fatal("next input lost the committed turn or cited evidence")
	}
}

func TestSubmitReconcilesOriginalFrozenRunThroughProcessing(t *testing.T) {
	completed := completedFactory(t)
	var original ac.RunRequest
	agent := &scriptedAgent{}
	agent.run = func(_ context.Context, run ac.RunRequest, trace string) (ac.RunSnapshot, error) {
		encoded, _ := json.Marshal(run)
		_ = json.Unmarshal(encoded, &original)
		if trace != "recover-frozen" {
			t.Error("POST lost the request trace")
		}
		run.Context.CatalogFacts[0].Value = "PRIVATE caller mutation"
		return ac.RunSnapshot{}, privateFailure("agent_unavailable", 503, true, true)
	}
	agent.get = func(_ context.Context, run ac.RunRequest, trace string) (ac.RunSnapshot, error) {
		if !reflect.DeepEqual(run, original) || trace != "recover-frozen" {
			t.Error("reconciliation changed the original run, snapshot, deadline or trace")
		}
		if agent.gets.Load() == 1 {
			return ac.RunSnapshot{ContractVersion: ac.PrivateVersion, RunID: run.RunID, State: "processing"}, nil
		}
		return completed(run), nil
	}
	service, _, owner, current := setup(t, agent)
	response, failure := service.Submit(context.Background(), owner, string(current.ConversationID), message(0), "recover-frozen")
	if failure != nil || response.Turn.Sequence != 1 || agent.posts.Load() != 1 || agent.gets.Load() != 2 {
		t.Fatal("uncertain POST was not reconciled exactly once without another POST", failure)
	}
}

func TestSubmitRetriesTransientGETWithinOriginalBudget(t *testing.T) {
	completed := completedFactory(t)
	agent := &scriptedAgent{run: func(context.Context, ac.RunRequest, string) (ac.RunSnapshot, error) {
		return ac.RunSnapshot{}, privateFailure("agent_unavailable", 503, true, true)
	}}
	agent.get = func(_ context.Context, run ac.RunRequest, _ string) (ac.RunSnapshot, error) {
		if agent.gets.Load() == 1 {
			return ac.RunSnapshot{}, errors.New("PRIVATE connection detail")
		}
		return completed(run), nil
	}
	service, _, owner, current := setup(t, agent)
	_, failure := service.Submit(context.Background(), owner, string(current.ConversationID), message(0), "get-transient")
	if failure != nil || agent.posts.Load() != 1 || agent.gets.Load() != 2 {
		t.Fatal("transient GET failed to recover or resubmitted the model run", failure)
	}
}

func TestSubmitHandlesKnownPOSTFailureAndRetainedGETFailure(t *testing.T) {
	for _, query := range []bool{false, true} {
		t.Run(fmt.Sprint(query), func(t *testing.T) {
			known := privateFailure("generation_unavailable", 503, true, false)
			agent := &scriptedAgent{run: func(context.Context, ac.RunRequest, string) (ac.RunSnapshot, error) {
				if query {
					return ac.RunSnapshot{}, privateFailure("agent_unavailable", 503, true, true)
				}
				return ac.RunSnapshot{}, known
			}}
			agent.get = func(context.Context, ac.RunRequest, string) (ac.RunSnapshot, error) {
				known.RunState = "failed"
				return ac.RunSnapshot{}, known
			}
			service, store, owner, current := setup(t, agent)
			_, failure := service.Submit(context.Background(), owner, string(current.ConversationID), message(0), "known-failure")
			assertFailed(t, store, owner, current, failure, 503, "generation_unavailable")
			want := int64(0)
			if query {
				want = 1
			}
			if agent.posts.Load() != 1 || agent.gets.Load() != want {
				t.Fatal("known terminal/admission failure was retried")
			}
		})
	}
}

func TestSubmitMissingRunStopsWithoutRecreatingIt(t *testing.T) {
	agent := &scriptedAgent{run: func(context.Context, ac.RunRequest, string) (ac.RunSnapshot, error) {
		return ac.RunSnapshot{}, privateFailure("agent_unavailable", 503, true, true)
	}, get: func(context.Context, ac.RunRequest, string) (ac.RunSnapshot, error) {
		failure := privateFailure("agent_unavailable", 503, true, false)
		failure.RunMissing = true
		return ac.RunSnapshot{}, failure
	}}
	service, store, owner, current := setup(t, agent)
	_, failure := service.Submit(context.Background(), owner, string(current.ConversationID), message(0), "run-missing")
	assertFailed(t, store, owner, current, failure, 503, "agent_unavailable")
	if agent.posts.Load() != 1 || agent.gets.Load() != 1 {
		t.Fatal("a missing run was polled again or recreated")
	}
}

func TestSubmitBrowserCancellationPreservesAcceptedWorkAndConflicts(t *testing.T) {
	completed := completedFactory(t)
	entered, release := make(chan struct{}), make(chan struct{})
	agent := &scriptedAgent{run: func(ctx context.Context, run ac.RunRequest, _ string) (ac.RunSnapshot, error) {
		close(entered)
		<-release
		if ctx.Err() != nil {
			t.Error("browser cancellation reached accepted Agent work")
		}
		return completed(run), nil
	}}
	service, store, owner, current := setup(t, agent)
	service.postWait = time.Second
	input := message(0)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	finished := make(chan *conversation.Failure, 1)
	go func() {
		_, failure := service.Submit(ctx, owner, string(current.ConversationID), input, "browser-detached")
		finished <- failure
	}()
	<-entered
	cancel()
	for _, item := range []struct {
		message ac.PublicMessageRequest
		code    string
	}{{input, "request_in_progress"}, {message(0), "conversation_busy"}} {
		_, failure := service.Submit(context.Background(), owner, string(current.ConversationID), item.message, "conflict")
		if failure == nil || failure.Status != 409 || failure.Code != item.code {
			t.Error("an active submission did not retain its reservation")
		}
	}
	close(release)
	if failure := <-finished; failure != nil {
		t.Fatal("browser disconnect discarded accepted work", failure)
	}
	history, ok := store.AgentHistory(owner, string(current.ConversationID))
	if !ok || len(history.Turns) != 1 || history.Conversation.Revision != 1 || agent.posts.Load() != 1 {
		t.Fatal("accepted work did not produce exactly one turn")
	}
	_, failure := service.Submit(context.Background(), owner, string(current.ConversationID), message(0), "stale-revision")
	if failure == nil || failure.Code != "conversation_state_conflict" || agent.posts.Load() != 1 {
		t.Fatal("a stale submission reached the Agent")
	}
	input.Question += " Changed payload."
	_, failure = service.Submit(context.Background(), owner, string(current.ConversationID), input, "changed-payload")
	if failure == nil || failure.Code != "idempotency_conflict" || agent.posts.Load() != 1 {
		t.Fatal("a changed replay payload reached the Agent")
	}
}

func TestSubmitRejectsInvalidAndForeignResultsWithoutStateChanges(t *testing.T) {
	completed := completedFactory(t)
	for name, change := range map[string]func(*ac.RunSnapshot){
		"wrong run":        func(s *ac.RunSnapshot) { s.RunID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" },
		"wrong artwork":    func(s *ac.RunSnapshot) { s.Result.Scope.ArtworkID = "aic-11723" },
		"wrong revision":   func(s *ac.RunSnapshot) { s.Result.ConversationUpdate.BaseRevision++ },
		"foreign evidence": func(s *ac.RunSnapshot) { s.Result.EvidenceSnapshot[0].ArtworkID = "aic-11723" },
		"unknown citation": func(s *ac.RunSnapshot) { s.Result.Answer.Citations[0].ChunkID = ac.Some("missing") },
		"processing POST":  func(s *ac.RunSnapshot) { s.State, s.Result = "processing", nil },
		"missing result":   func(s *ac.RunSnapshot) { s.Result = nil },
	} {
		t.Run(name, func(t *testing.T) {
			agent := &scriptedAgent{run: func(_ context.Context, run ac.RunRequest, _ string) (ac.RunSnapshot, error) {
				result := completed(run)
				change(&result)
				return result, nil
			}}
			service, store, owner, current := setup(t, agent)
			_, failure := service.Submit(context.Background(), owner, string(current.ConversationID), message(0), "invalid-result")
			assertFailed(t, store, owner, current, failure, 502, "agent_invalid_response")
			if agent.gets.Load() != 0 {
				t.Fatal("an invalid successful POST response triggered recovery")
			}
		})
	}
}

func TestSubmitRejectsInvalidReconciledResult(t *testing.T) {
	completed := completedFactory(t)
	agent := &scriptedAgent{run: func(context.Context, ac.RunRequest, string) (ac.RunSnapshot, error) {
		return ac.RunSnapshot{}, privateFailure("agent_unavailable", 503, true, true)
	}, get: func(_ context.Context, run ac.RunRequest, _ string) (ac.RunSnapshot, error) {
		snapshot := completed(run)
		snapshot.Result.Scope.ArtworkID = "aic-11723"
		return snapshot, nil
	}}
	service, store, owner, current := setup(t, agent)
	_, failure := service.Submit(context.Background(), owner, string(current.ConversationID), message(0), "invalid-get")
	assertFailed(t, store, owner, current, failure, 502, "agent_invalid_response")
	if agent.posts.Load() != 1 || agent.gets.Load() != 1 {
		t.Fatal("an invalid reconciled answer was retried")
	}
}

func TestSubmitPostChildTimeoutCanRecoverBeforeOriginalDeadline(t *testing.T) {
	completed := completedFactory(t)
	release, returned := make(chan struct{}), make(chan struct{})
	agent := &scriptedAgent{run: func(_ context.Context, run ac.RunRequest, _ string) (ac.RunSnapshot, error) {
		<-release
		defer close(returned)
		return completed(run), nil
	}, get: func(_ context.Context, run ac.RunRequest, _ string) (ac.RunSnapshot, error) {
		return completed(run), nil
	}}
	service, store, owner, current := setup(t, agent)
	service.postWait = 20 * time.Millisecond
	response, failure := service.Submit(context.Background(), owner, string(current.ConversationID), message(0), "post-child-timeout")
	close(release)
	<-returned
	if failure != nil || response.Turn.Sequence != 1 || agent.posts.Load() != 1 || agent.gets.Load() != 1 {
		t.Fatal("a child timeout incorrectly exhausted the whole attempt", failure)
	}
	history, _ := store.AgentHistory(owner, string(current.ConversationID))
	if len(history.Turns) != 1 {
		t.Fatal("late POST return added another turn")
	}
}

func TestSubmitParentTimeoutRejectsLateUncooperativeResult(t *testing.T) {
	completed := completedFactory(t)
	release, returned := make(chan struct{}), make(chan struct{})
	agent := &scriptedAgent{run: func(_ context.Context, run ac.RunRequest, _ string) (ac.RunSnapshot, error) {
		<-release
		defer close(returned)
		return completed(run), nil
	}}
	service, store, owner, current := setup(t, agent)
	service.attemptLimit, service.postWait = 80*time.Millisecond, time.Second
	_, failure := service.Submit(context.Background(), owner, string(current.ConversationID), message(0), "parent-timeout")
	close(release)
	<-returned
	assertFailed(t, store, owner, current, failure, 504, "request_timeout")
	if agent.posts.Load() != 1 || agent.gets.Load() != 0 {
		t.Fatal("queries started after the parent deadline")
	}
}

func TestSubmitQueryChildTimeoutDiscardsLateInvalidResult(t *testing.T) {
	completed := completedFactory(t)
	release, returned := make(chan struct{}), make(chan struct{})
	agent := &scriptedAgent{run: func(context.Context, ac.RunRequest, string) (ac.RunSnapshot, error) {
		return ac.RunSnapshot{}, privateFailure("agent_unavailable", 503, true, true)
	}}
	agent.get = func(_ context.Context, run ac.RunRequest, _ string) (ac.RunSnapshot, error) {
		if agent.gets.Load() == 1 {
			<-release
			defer close(returned)
			late := completed(run)
			late.Result.Scope.ArtworkID = "aic-11723"
			return late, nil
		}
		return completed(run), nil
	}
	service, store, owner, current := setup(t, agent)
	service.queryWait = 20 * time.Millisecond
	response, failure := service.Submit(context.Background(), owner, string(current.ConversationID), message(0), "query-child-timeout")
	close(release)
	<-returned
	if failure != nil || response.Turn.Sequence != 1 || agent.posts.Load() != 1 || agent.gets.Load() != 2 {
		t.Fatal("a query child timeout did not recover within the original budget", failure)
	}
	history, _ := store.AgentHistory(owner, string(current.ConversationID))
	if len(history.Turns) != 1 || history.Conversation.Revision != 1 {
		t.Fatal("late query results changed the committed conversation")
	}
}

func TestSubmitBoundsUnavailableReconciliationAndPreservesSafeLogs(t *testing.T) {
	agent := &scriptedAgent{run: func(context.Context, ac.RunRequest, string) (ac.RunSnapshot, error) {
		panic("PRIVATE panic details")
	}, get: func(context.Context, ac.RunRequest, string) (ac.RunSnapshot, error) {
		return ac.RunSnapshot{}, errors.New("PRIVATE network details")
	}}
	service, store, owner, current := setup(t, agent)
	service.attemptLimit, service.commitReserve = 150*time.Millisecond, 20*time.Millisecond
	var output bytes.Buffer
	service.logger = slog.New(slog.NewJSONHandler(&output, nil))
	started := time.Now()
	_, failure := service.Submit(context.Background(), owner, string(current.ConversationID), message(0), "unavailable-budget")
	assertFailed(t, store, owner, current, failure, 503, "agent_unavailable")
	if time.Since(started) > time.Second || agent.posts.Load() != 1 || agent.gets.Load() < 1 ||
		strings.Contains(output.String(), "PRIVATE") || strings.Contains(output.String(), owner) || strings.Contains(output.String(), question) {
		t.Fatal("unavailable recovery was unbounded, resubmitted or leaked private data")
	}
}

func TestSubmitRecoversPhysicalHTTPLossWithExistingClient(t *testing.T) {
	completed := completedFactory(t)
	const token = "SYNTHETIC-HTTP-TEST-" + "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
	var saved atomic.Pointer[ac.RunRequest]
	var posts, gets atomic.Int64
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+token {
			t.Error("missing service authentication")
		}
		if r.Method == http.MethodPost {
			posts.Add(1)
			var run ac.RunRequest
			if json.NewDecoder(r.Body).Decode(&run) != nil {
				t.Error("invalid original POST body")
			}
			saved.Store(&run)
			connection, _, err := w.(http.Hijacker).Hijack()
			if err != nil {
				t.Error(err)
				return
			}
			_ = connection.Close()
			return
		}
		gets.Add(1)
		run := saved.Load()
		if run == nil {
			t.Error("GET preceded the original POST")
			return
		}
		if r.URL.Path != "/internal/v2/agent/runs/"+string(run.RunID) {
			t.Error("GET did not target the original run")
		}
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("X-Request-ID", r.Header.Get("X-Request-ID"))
		_ = json.NewEncoder(w).Encode(completed(*run))
	}))
	defer server.Close()
	client, err := agentclient.New(server.URL, token)
	if err != nil {
		t.Fatal(err)
	}
	defer client.CloseIdleConnections()
	service, _, owner, current := setup(t, client)
	response, failure := service.Submit(context.Background(), owner, string(current.ConversationID), message(0), "http-response-lost")
	if failure != nil || response.Turn.Sequence != 1 || posts.Load() != 1 || gets.Load() != 1 {
		t.Fatal("actual HTTP response loss did not recover through the real Go client", failure)
	}
}

func TestSubmitRejectsInvalidAdmissionWithoutAgentCalls(t *testing.T) {
	agent := &scriptedAgent{}
	service, store, owner, current := setup(t, agent)
	for _, trace := range []string{"", "two words", strings.Repeat("x", 129)} {
		_, failure := service.Submit(context.Background(), owner, string(current.ConversationID), message(0), trace)
		if failure == nil || failure.Status != 400 {
			t.Fatal("invalid trace was admitted")
		}
	}
	_, failure := service.Submit(nil, owner, string(current.ConversationID), message(0), "nil-context")
	if failure == nil || failure.Status != 400 {
		t.Fatal("nil context was admitted")
	}
	history, _ := store.AgentHistory(owner, string(current.ConversationID))
	if history.PendingRequest != nil || agent.posts.Load() != 0 || agent.gets.Load() != 0 {
		t.Fatal("invalid input reserved work or called the Agent")
	}
	if _, err := New(nil, agent, nil); err == nil {
		t.Fatal("missing store accepted")
	}
	if _, err := New(store, nil, nil); err == nil {
		t.Fatal("missing Agent accepted")
	}
}

func TestInvokeExhaustedBudgetDoesNotStartTransport(t *testing.T) {
	ctx, cancel := context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
	defer cancel()
	var calls atomic.Int64
	_, err := invoke(ctx, time.Second, func(context.Context) (ac.RunSnapshot, error) {
		calls.Add(1)
		return ac.RunSnapshot{}, nil
	})
	if !errors.Is(err, context.DeadlineExceeded) || calls.Load() != 0 {
		t.Fatal("an exhausted original budget started another transport call")
	}
}
