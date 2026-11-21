//go:build integration

package httpapi

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	ac "github.com/Jasonssssss9/musebridge/api/internal/agentcontract"
	m1test "github.com/Jasonssssss9/musebridge/api/internal/testsupport/m1services"
)

func dropSocket(t *testing.T, w http.ResponseWriter) {
	t.Helper()
	connection, _, err := w.(http.Hijacker).Hijack()
	if err != nil {
		t.Error("cannot drop controlled HTTP connection")
		return
	}
	_ = connection.Close()
}

func TestM2B4PrivateResponseLossWithRealWorkflow(t *testing.T) {
	services := m1test.Start(t, "none")
	target, err := url.Parse(services.Connection.AgentURL)
	if err != nil {
		t.Fatal(err)
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	t.Cleanup(transport.CloseIdleConnections)
	var mu sync.Mutex
	posts, gets := []privateCall{}, []privateCall{}
	proxy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(io.LimitReader(r.Body, ac.PrivateBodyLimit+1))
		if err != nil {
			t.Error(err)
			w.WriteHeader(500)
			return
		}
		out := r.Clone(r.Context())
		out.URL.Scheme = target.Scheme
		out.URL.Host = target.Host
		out.Host = target.Host
		out.RequestURI = ""
		out.Body = io.NopCloser(bytes.NewReader(body))
		response, err := transport.RoundTrip(out)
		if err != nil {
			t.Error("real Agent proxy call failed")
			w.WriteHeader(500)
			return
		}
		defer response.Body.Close()
		data, err := io.ReadAll(io.LimitReader(response.Body, ac.PrivateBodyLimit+1))
		if err != nil {
			t.Error(err)
			w.WriteHeader(500)
			return
		}
		mu.Lock()
		if r.Method == "POST" {
			var run ac.RunRequest
			_ = json.Unmarshal(body, &run)
			posts = append(posts, privateCall{Run: run, Trace: r.Header.Get("X-Request-ID"), At: time.Now()})
		} else {
			gets = append(gets, privateCall{Run: ac.RunRequest{RunID: ac.UUID(strings.TrimPrefix(r.URL.Path, "/internal/v2/agent/runs/"))}, Trace: r.Header.Get("X-Request-ID"), At: time.Now()})
		}
		mu.Unlock()
		if r.Method == "POST" {
			dropSocket(t, w)
			return
		}
		for name, values := range response.Header {
			w.Header()[name] = values
		}
		w.WriteHeader(response.StatusCode)
		_, _ = w.Write(data)
	}))
	t.Cleanup(proxy.Close)
	client := services.ClientAt(t, proxy.URL)
	h, err := NewAgent(testDataset(t), nil, Settings{PublicBaseURL: browserOrigin}, client)
	if err != nil {
		t.Fatal(err)
	}
	b := newPublicBrowser(t, h, nil)
	for i, artwork := range []string{"aic-28560", "aic-11723"} {
		current := b.open(t, artwork)
		input := agentMessage(i+1, 0)
		input.Question = "What material is this painting made of?"
		reply := b.post(current.ConversationID, input)
		var result ac.PublicSubmissionResponse
		decodePublic(t, reply, "PublicSubmissionResponse", &result)
		if result.Conversation.Revision != 1 || len(result.Turn.Answer.Citations) != 1 || result.Turn.Answer.Citations[0].ChunkID != ac.Some(artwork+"-medium") {
			t.Fatal("real workflow response was not recovered with scoped support")
		}
		var replay ac.PublicSubmissionResponse
		decodePublic(t, b.post(current.ConversationID, input), "PublicSubmissionResponse", &replay)
		if !reflectPublicTurn(result.Turn, replay.Turn) {
			t.Fatal("recovered turn replay changed the answer")
		}
		assertWorkflowCounts(t, services, reply.Trace, artwork, 2, 1)
		t.Logf("private_socket_loss trace=%s artwork=%s revision=1 POST=1 GET=1 model=2 retrieval=1", reply.Trace, artwork)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(posts) != 2 || len(gets) != 2 {
		t.Fatal("private response loss caused extra POSTs or status queries")
	}
	for i, post := range posts {
		if gets[i].Run.RunID != post.Run.RunID || gets[i].Trace != post.Trace || gets[i].At.Sub(post.At) < 900*time.Millisecond {
			t.Fatal("recovery changed identity or ignored the one-second query interval")
		}
	}
}

func reflectPublicTurn(left, right ac.PublicTurn) bool {
	a, _ := json.Marshal(left)
	b, _ := json.Marshal(right)
	return bytes.Equal(a, b)
}

func assertWorkflowCounts(t *testing.T, services *m1test.Services, trace, artwork string, models, lookups int) {
	t.Helper()
	actualModels, actualLookups := 0, 0
	for _, event := range services.Audit(t) {
		if event.RequestID != trace {
			continue
		}
		if event.ArtworkID != artwork {
			t.Fatal("recovery crossed artwork scopes")
		}
		if event.Event == "model_call" {
			actualModels++
		}
		if event.Event == "retrieval_http_response" {
			actualLookups++
			if len(event.EvidenceIDs) != 1 || event.EvidenceIDs[0] != artwork+"-medium" {
				t.Fatal("actual retrieval returned the wrong medium record")
			}
		}
	}
	if actualModels != models || actualLookups != lookups {
		t.Fatalf("recovery repeated real workflow operations: models=%d lookups=%d", actualModels, actualLookups)
	}
}

func TestM2B4PublicResponseLossAfterRealCommit(t *testing.T) {
	services := m1test.Start(t, "none")
	h, err := NewAgent(testDataset(t), nil, Settings{PublicBaseURL: browserOrigin}, services.Client)
	if err != nil {
		t.Fatal(err)
	}
	var tracesMu sync.Mutex
	traces := []string{}
	var dropped sync.Map
	b := newPublicBrowser(t, h, func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.Method == "POST" && strings.HasSuffix(r.URL.Path, "/messages") {
				if _, already := dropped.LoadOrStore(r.URL.Path, true); already {
					next.ServeHTTP(w, r)
					return
				}
				recorder := httptest.NewRecorder()
				next.ServeHTTP(recorder, r)
				if recorder.Code != 200 {
					t.Error("expected a successful real commit before response loss")
				}
				tracesMu.Lock()
				traces = append(traces, recorder.Header().Get("X-Request-ID"))
				tracesMu.Unlock()
				dropSocket(t, w)
				return
			}
			next.ServeHTTP(w, r)
		})
	})
	for i, artwork := range []string{"aic-28560", "aic-11723"} {
		current := b.open(t, artwork)
		input := agentMessage(i+1, 0)
		input.Question = "What material is this painting made of?"
		reply := b.post(current.ConversationID, input)
		if reply.Err == nil {
			t.Fatal("test did not drop the actual public connection")
		}
		history := b.history(t, current.ConversationID)
		if history.Conversation.Revision != 1 || len(history.Turns) != 1 || history.PendingRequest != nil || len(history.Turns[0].Answer.Citations) != 1 || history.Turns[0].Answer.Citations[0].ChunkID != ac.Some(artwork+"-medium") {
			t.Fatal("public response loss did not leave one complete recoverable turn")
		}
		restored := b.open(t, artwork)
		if restored.ConversationID != current.ConversationID || restored.Revision != 1 {
			t.Fatal("refresh did not restore the committed turn")
		}
		var replay ac.PublicSubmissionResponse
		decodePublic(t, b.post(current.ConversationID, input), "PublicSubmissionResponse", &replay)
		if !reflectPublicTurn(history.Turns[0], replay.Turn) {
			t.Fatal("lost public reply did not replay the saved turn")
		}
		if second := b.history(t, current.ConversationID); second.Conversation.Revision != 1 || len(second.Turns) != 1 {
			t.Fatal("public retry duplicated the completed turn")
		}
		tracesMu.Lock()
		trace := traces[i]
		tracesMu.Unlock()
		assertWorkflowCounts(t, services, trace, artwork, 2, 1)
		t.Logf("public_socket_loss trace=%s artwork=%s history_turns=1 revision=1 model=2 retrieval=1", trace, artwork)
	}
	models := 0
	for _, event := range services.Audit(t) {
		if event.Event == "model_call" {
			models++
		}
	}
	if models != 4 {
		t.Fatal("lost public replies repeated actual inference")
	}
}

func TestM2B4ProductionRecoveryDeadlineAndLateResponse(t *testing.T) {
	if testing.Short() {
		t.Skip("production-budget check takes about 60 seconds; run without -short for M2-B4 acceptance")
	}
	started, release, lateFinished := make(chan struct{}), make(chan struct{}), make(chan struct{})
	var first atomic.Bool
	a := newPrivateHTTP(t, func(w http.ResponseWriter, r *http.Request, run ac.RunRequest) {
		if r.Method == "GET" {
			writePrivate(w, r, 200, ac.RunSnapshot{ContractVersion: ac.PrivateVersion, RunID: run.RunID, State: "processing"})
			return
		}
		if first.CompareAndSwap(false, true) {
			close(started)
			<-release
			late := dialogueSnapshot(run)
			late.Result.Answer.Text = "Late abandoned result."
			late.Result.RequestResults[0].Text = late.Result.Answer.Text
			writePrivate(w, r, 200, late)
			close(lateFinished)
			return
		}
		writePrivate(w, r, 200, dialogueSnapshot(run))
	})
	b := newReliabilityStack(t, a)
	current := b.open(t, "aic-28560")
	releaseOnCleanup(t, release)
	input := agentMessage(1, 0)
	startedAt := time.Now()
	reply := b.post(current.ConversationID, input)
	elapsed := time.Since(startedAt)
	waitSignal(t, started)
	publicFailure(t, reply, 503, "agent_unavailable", true)
	posts, gets := a.calls()
	if elapsed < 59*time.Second || elapsed > 65*time.Second || len(posts) != 1 || len(gets) == 0 {
		t.Fatalf("production recovery budget was changed or unbounded: elapsed=%s posts=%d gets=%d", elapsed, len(posts), len(gets))
	}
	if gets[0].At.Sub(posts[0].At) < 56*time.Second+900*time.Millisecond {
		t.Fatal("private POST child budget or first-query interval was shortened")
	}
	deadline, err := time.Parse(time.RFC3339Nano, posts[0].Run.Execution.DeadlineAt)
	if err != nil || deadline.Sub(posts[0].At) < 53*time.Second || deadline.Sub(posts[0].At) > 56*time.Second {
		t.Fatal("Agent deadline was not admission plus 55 seconds")
	}
	history := b.history(t, current.ConversationID)
	if history.Conversation.Revision != 0 || len(history.Turns) != 0 || history.PendingRequest == nil || history.PendingRequest.State != "failed" {
		t.Fatal("exhausted recovery committed a turn")
	}
	var accepted ac.PublicSubmissionResponse
	decodePublic(t, b.post(current.ConversationID, input), "PublicSubmissionResponse", &accepted)
	if accepted.Conversation.Revision != 1 {
		t.Fatal("deliberate retry did not complete once")
	}
	close(release)
	waitSignal(t, lateFinished)
	history = b.history(t, current.ConversationID)
	posts, _ = a.calls()
	if len(posts) != 2 || posts[0].Run.RunID == posts[1].Run.RunID || history.Conversation.Revision != 1 || len(history.Turns) != 1 || history.Turns[0].Answer.Text == "Late abandoned result." || history.PendingRequest != nil {
		t.Fatal("late private response replaced the accepted retry")
	}
	for _, get := range gets {
		if get.Run.RunID != posts[0].Run.RunID || get.Trace != posts[0].Trace {
			t.Fatal("recovery replaced the original run or trace")
		}
	}
	t.Logf("production_budget elapsed_ms=%d POST=1 GET=%d fallback=503/agent_unavailable revision_before_retry=0 revision_after_retry=1 late_result=discarded", elapsed.Milliseconds(), len(gets))
}
