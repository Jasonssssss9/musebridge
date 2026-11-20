package agentclient

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	ac "github.com/Jasonssssss9/musebridge/api/internal/agentcontract"
)

func processing(run ac.RunRequest) ac.RunSnapshot {
	return ac.RunSnapshot{ContractVersion: ac.PrivateVersion, RunID: run.RunID, State: "processing"}
}

func TestGetAuthenticatesOriginalRunAndReturnsProcessingOrCompleted(t *testing.T) {
	for _, state := range []string{"processing", "completed"} {
		t.Run(state, func(t *testing.T) {
			run, expected := inputs(t)
			if state == "processing" {
				expected = processing(run)
			}
			var calls atomic.Int64
			client := newClient(t, func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				body, _ := io.ReadAll(r.Body)
				if r.Method != http.MethodGet || r.URL.Path != "/internal/v2/agent/runs/"+string(run.RunID) ||
					r.URL.RawQuery != "" || len(body) != 0 || r.ContentLength != 0 || r.Header.Get("Content-Type") != "" {
					t.Error("GET changed the run identity, carried a body or used the wrong route")
				}
				if len(r.Header.Values("Authorization")) != 1 || r.Header.Get("Authorization") != "Bearer "+testToken ||
					r.Header.Get("X-Request-ID") != "get-wire" || r.Header.Get("Accept") != "application/json" ||
					r.Header.Get("Accept-Encoding") != "identity" {
					t.Error("GET lost the private transport headers")
				}
				reply(t, w, r, 200, expected)
			})
			got, err := client.Get(context.Background(), run, "get-wire")
			if err != nil || !reflect.DeepEqual(got, expected) || calls.Load() != 1 {
				t.Fatalf("GET did not return the validated state without polling: %v", err)
			}
		})
	}
}

func TestGetRetainedFailureIsSafeTerminalErrorDespiteHTTP200(t *testing.T) {
	for _, input := range []struct {
		code      string
		retryable bool
		status    int
	}{
		{"retrieval_invalid_response", false, 502},
		{"generation_unavailable", true, 503},
		{"generation_timeout", true, 504},
	} {
		t.Run(input.code, func(t *testing.T) {
			run, _ := inputs(t)
			snapshot := processing(run)
			snapshot.State = "failed"
			snapshot.Error = &ac.AgentError{Code: input.code, Message: "PRIVATE " + testToken,
				Retryable: input.retryable, Phase: "validation"}
			client := newClient(t, func(w http.ResponseWriter, r *http.Request) { reply(t, w, r, 200, snapshot) })
			got, err := client.Get(context.Background(), run, "get-failed")
			failure := assertFailure(t, got, err, input.status, input.code, false)
			if failure.RunState != "failed" || failure.Failure.Error.Retryable != input.retryable || errors.Unwrap(err) != nil {
				t.Fatal("a retained failure lost its terminal state or retained private diagnostics")
			}
		})
	}
}

func TestGetBindsCompletedResultToDetachedOriginalRequest(t *testing.T) {
	run, expected := inputs(t)
	entered, release := make(chan struct{}), make(chan struct{})
	client := newClient(t, func(w http.ResponseWriter, r *http.Request) {
		close(entered)
		<-release
		reply(t, w, r, 200, expected)
	})
	finished := make(chan error, 1)
	go func() { _, err := client.Get(context.Background(), run, "get-detached"); finished <- err }()
	<-entered
	run.Context.CatalogFacts[0].Value = "Caller changed the title after GET started"
	close(release)
	if err := <-finished; err != nil {
		t.Fatal("GET rebound response validation to a later caller mutation:", err)
	}
}

func TestGetCanReadRetainedRunAfterAgentAdmissionDeadline(t *testing.T) {
	run, snapshot := inputs(t)
	run.Execution.DeadlineAt = "2026-01-01T00:00:00Z"
	client := newClient(t, func(w http.ResponseWriter, r *http.Request) { reply(t, w, r, 200, snapshot) })
	if _, err := client.Get(context.Background(), run, "get-retained"); err != nil {
		t.Fatal("an expired Agent admission deadline prevented a retained lookup:", err)
	}
}

func TestGetRejectsUnsafeIdentityAndInvalidOriginalSnapshotBeforeNetwork(t *testing.T) {
	var calls atomic.Int64
	client := newClient(t, func(w http.ResponseWriter, r *http.Request) { calls.Add(1) })
	for _, change := range []func(*ac.RunRequest){
		func(r *ac.RunRequest) { r.RunID = "../../private?token=PRIVATE" },
		func(r *ac.RunRequest) { r.RunID = "" },
		func(r *ac.RunRequest) { r.Scope.ArtworkID = "aic-11723" },
		func(r *ac.RunRequest) { r.Context.RecentTurns = nil },
		func(r *ac.RunRequest) { r.Question = string([]byte{0xff}) },
		func(r *ac.RunRequest) { r.Question = strings.Repeat("x", ac.PrivateBodyLimit+1) },
	} {
		run, _ := inputs(t)
		change(&run)
		got, err := client.Get(context.Background(), run, "get-invalid-input")
		assertFailure(t, got, err, 502, "agent_invalid_response", false)
	}
	run, _ := inputs(t)
	for _, trace := range []string{"", "two words", "line\n", strings.Repeat("x", 129)} {
		got, err := client.Get(context.Background(), run, trace)
		assertFailure(t, got, err, 502, "agent_invalid_response", false)
	}
	got, err := client.Get(nil, run, "get-nil-context")
	assertFailure(t, got, err, 502, "agent_invalid_response", false)
	if calls.Load() != 0 {
		t.Fatal("invalid GET input reached the network")
	}
}

func TestGetRejectsWrongIdentityStateScopeAndEvidence(t *testing.T) {
	for name, change := range map[string]func(*ac.RunSnapshot){
		"wrong run":             func(s *ac.RunSnapshot) { s.RunID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" },
		"unknown state":         func(s *ac.RunSnapshot) { s.State = "unknown" },
		"processing result":     func(s *ac.RunSnapshot) { s.State = "processing" },
		"missing result":        func(s *ac.RunSnapshot) { s.Result = nil },
		"failed without error":  func(s *ac.RunSnapshot) { s.State, s.Result = "failed", nil },
		"wrong artwork":         func(s *ac.RunSnapshot) { s.Result.Scope.ArtworkID = "aic-11723" },
		"wrong release":         func(s *ac.RunSnapshot) { s.Result.Scope.DatasetVersion = "other" },
		"wrong revision":        func(s *ac.RunSnapshot) { s.Result.ConversationUpdate.BaseRevision++ },
		"changed catalog":       func(s *ac.RunSnapshot) { s.Result.CatalogSnapshot[0].Value = "Other title" },
		"foreign evidence":      func(s *ac.RunSnapshot) { s.Result.EvidenceSnapshot[0].ArtworkID = "aic-11723" },
		"unresolved citation":   func(s *ac.RunSnapshot) { s.Result.Answer.Citations[0].ChunkID = ac.Some("missing") },
		"wrong answer language": func(s *ac.RunSnapshot) { s.Result.AnswerLanguage = "ko" },
		"invalid failed policy": func(s *ac.RunSnapshot) {
			s.State, s.Result = "failed", nil
			s.Error = &ac.AgentError{Code: "retrieval_unavailable", Message: "PRIVATE", Retryable: false, Phase: "retrieval"}
		},
		"failed claims processing": func(s *ac.RunSnapshot) {
			s.State, s.Result = "failed", nil
			s.Error = &ac.AgentError{Code: "agent_run_in_progress", Message: "PRIVATE", Retryable: true, Phase: "admission"}
		},
	} {
		t.Run(name, func(t *testing.T) {
			run, snapshot := inputs(t)
			change(&snapshot)
			client := newClient(t, func(w http.ResponseWriter, r *http.Request) { reply(t, w, r, 200, snapshot) })
			got, err := client.Get(context.Background(), run, "get-invalid-result")
			failure := assertFailure(t, got, err, 502, "agent_invalid_response", false)
			if failure.RunState != "" {
				t.Fatal("invalid metadata was trusted as a terminal failure")
			}
		})
	}
}

func TestGetLookupErrorsUseEnvelopeMappingRatherThanTerminalState(t *testing.T) {
	for _, input := range []struct {
		code      string
		status    int
		retryable bool
		reconcile bool
	}{
		{"agent_run_not_found", 404, true, false},
		{"agent_unauthorized", 401, false, false},
		{"agent_run_in_progress", 409, true, true},
	} {
		t.Run(input.code, func(t *testing.T) {
			run, _ := inputs(t)
			client := newClient(t, func(w http.ResponseWriter, r *http.Request) {
				reply(t, w, r, input.status, ac.ErrorEnvelope{RequestID: "get-lookup-error", RunID: &run.RunID,
					Error: ac.AgentError{Code: input.code, Message: "PRIVATE", Retryable: input.retryable, Phase: "admission"}})
			})
			got, err := client.Get(context.Background(), run, "get-lookup-error")
			failure := assertFailure(t, got, err, 503, "agent_unavailable", input.reconcile)
			if failure.RunState != "" || failure.Failure.Error.Retryable != input.retryable {
				t.Fatal("a lookup error was reported as a retained terminal run")
			}
		})
	}
}

func TestGetUsesSharedStrictHTTPBoundary(t *testing.T) {
	run, snapshot := inputs(t)
	for _, kind := range []string{"wrong trace", "gzip", "declared overflow", "actual overflow", "duplicate JSON", "wrong HTTP status"} {
		t.Run(kind, func(t *testing.T) {
			client := newClient(t, func(w http.ResponseWriter, r *http.Request) {
				headers(w, r)
				switch kind {
				case "wrong trace":
					w.Header().Set("X-Request-ID", "other")
				case "gzip":
					w.Header().Set("Content-Encoding", "gzip")
				case "declared overflow":
					w.Header().Set("Content-Length", fmt.Sprint(ac.PrivateBodyLimit+1))
				case "actual overflow":
					w.(http.Flusher).Flush()
					_, _ = io.WriteString(w, strings.Repeat(" ", ac.PrivateBodyLimit+1))
					return
				case "duplicate JSON":
					_, _ = io.WriteString(w, `{"state":"processing","state":"completed"}`)
					return
				case "wrong HTTP status":
					w.WriteHeader(http.StatusAccepted)
				}
				_ = json.NewEncoder(w).Encode(snapshot)
			})
			got, err := client.Get(context.Background(), run, "get-http-boundary")
			assertFailure(t, got, err, 502, "agent_invalid_response", false)
		})
	}
	var redirected atomic.Int64
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { redirected.Add(1) }))
	defer target.Close()
	client := newClient(t, func(w http.ResponseWriter, r *http.Request) {
		headers(w, r)
		http.Redirect(w, r, target.URL, http.StatusTemporaryRedirect)
	})
	got, err := client.Get(context.Background(), run, "get-redirect")
	assertFailure(t, got, err, 502, "agent_invalid_response", false)
	if redirected.Load() != 0 {
		t.Fatal("GET followed a redirect with service credentials")
	}
}

func TestGetNetworkLossLeavesOutcomeUnknownWithoutPosting(t *testing.T) {
	run, _ := inputs(t)
	var gets, posts atomic.Int64
	client := newClient(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet {
			gets.Add(1)
		} else {
			posts.Add(1)
		}
		connection, _, err := w.(http.Hijacker).Hijack()
		if err != nil {
			t.Error(err)
			return
		}
		_ = connection.Close()
	})
	got, err := client.Get(context.Background(), run, "get-disconnected")
	failure := assertFailure(t, got, err, 503, "agent_unavailable", true)
	if failure.RunState != "" || gets.Load() != 1 || posts.Load() != 0 {
		t.Fatal("GET invented a terminal outcome, polled or started another run")
	}
}

func TestGetParentDeadlineAndCancellationWinWhileReading(t *testing.T) {
	for _, deadline := range []bool{false, true} {
		t.Run(fmt.Sprint(deadline), func(t *testing.T) {
			run, _ := inputs(t)
			started, release := make(chan struct{}), make(chan struct{})
			client := newClient(t, func(w http.ResponseWriter, r *http.Request) {
				headers(w, r)
				w.(http.Flusher).Flush()
				close(started)
				select {
				case <-r.Context().Done():
				case <-release:
				}
			})
			t.Cleanup(func() { close(release) })
			ctx, cancel := context.WithCancel(context.Background())
			if deadline {
				ctx, cancel = context.WithTimeout(context.Background(), 500*time.Millisecond)
			}
			defer cancel()
			finished := make(chan error, 1)
			go func() { _, err := client.Get(ctx, run, "get-parent-budget"); finished <- err }()
			select {
			case <-started:
			case <-ctx.Done():
				t.Fatal("GET did not reach the response-reading boundary")
			}
			status, cause, reconcile := 503, context.Canceled, true
			if deadline {
				status, cause, reconcile = 504, context.DeadlineExceeded, false
			} else {
				cancel()
			}
			err := <-finished
			code := "agent_unavailable"
			if deadline {
				code = "request_timeout"
			}
			failure := assertFailure(t, ac.RunSnapshot{}, err, status, code, reconcile)
			if !errors.Is(err, cause) || failure.RunState != "" {
				t.Fatal("GET lost context identity or invented a terminal state")
			}
		})
	}
}

func TestGetAlreadyExpiredParentDoesNotQuery(t *testing.T) {
	run, _ := inputs(t)
	var calls atomic.Int64
	client := newClient(t, func(w http.ResponseWriter, r *http.Request) { calls.Add(1) })
	ctx, cancel := context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
	defer cancel()
	got, err := client.Get(ctx, run, "get-expired")
	assertFailure(t, got, err, 504, "request_timeout", false)
	if calls.Load() != 0 {
		t.Fatal("an exhausted original budget reached the Agent")
	}
}

func TestGetConcurrentQueriesRetainTheirOwnTrace(t *testing.T) {
	run, snapshot := inputs(t)
	client := newClient(t, func(w http.ResponseWriter, r *http.Request) { reply(t, w, r, 200, snapshot) })
	var workers sync.WaitGroup
	for i := range 8 {
		workers.Go(func() {
			if _, err := client.Get(context.Background(), run, fmt.Sprintf("get-parallel-%d", i)); err != nil {
				t.Error(err)
			}
		})
	}
	workers.Wait()
}
