package agentclient

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/http/httptest"
	"os"
	"reflect"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	ac "github.com/Jasonssssss9/musebridge/api/internal/agentcontract"
)

const testToken = "SYNTHETIC-TEST-TOKEN-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

func fixture(t *testing.T, name string, target any) {
	t.Helper()
	data, err := os.ReadFile("../../../docs/contracts/agent-v2.examples.json")
	if err != nil {
		t.Fatal(err)
	}
	var document struct {
		Cases []struct {
			Name    string          `json:"name"`
			Payload json.RawMessage `json:"payload"`
		} `json:"schema_cases"`
	}
	if err := json.Unmarshal(data, &document); err != nil {
		t.Fatal(err)
	}
	for _, entry := range document.Cases {
		if entry.Name == name {
			if err := json.Unmarshal(entry.Payload, target); err != nil {
				t.Fatal(err)
			}
			return
		}
	}
	t.Fatal("missing shared fixture", name)
}

func inputs(t *testing.T) (ac.RunRequest, ac.RunSnapshot) {
	t.Helper()
	var run ac.RunRequest
	var snapshot ac.RunSnapshot
	fixture(t, "run_request_initial", &run)
	fixture(t, "completed_run_snapshot", &snapshot)
	run.Execution.DeadlineAt = time.Now().UTC().Add(55 * time.Second).Format(time.RFC3339Nano)
	snapshot.RunID = run.RunID
	validator, err := ac.DefaultValidator()
	if err != nil || validator.CheckRunRequest(run) != nil || validator.CheckRunResult(run, *snapshot.Result) != nil {
		t.Fatal("shared test inputs do not satisfy the run relationships")
	}
	return run, snapshot
}

func newClient(t *testing.T, handler http.HandlerFunc) *Client {
	t.Helper()
	server := httptest.NewServer(handler)
	t.Cleanup(server.Close)
	client, err := New(server.URL, testToken)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(client.CloseIdleConnections)
	return client
}

func headers(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("X-Request-ID", r.Header.Get("X-Request-ID"))
	w.Header().Set("Cache-Control", "no-store")
}

func reply(t *testing.T, w http.ResponseWriter, r *http.Request, status int, body any) {
	t.Helper()
	headers(w, r)
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(body); err != nil {
		t.Error("writing synthetic HTTP response:", err)
	}
}

func assertFailure(t *testing.T, snapshot ac.RunSnapshot, err error, status int, code string, reconcile bool) *Error {
	t.Helper()
	var failure *Error
	if snapshot != (ac.RunSnapshot{}) || !errors.As(err, &failure) {
		t.Fatalf("expected a typed failure and no partial snapshot; got %+v, %v", snapshot, err)
	}
	if failure.Failure.Status != status || failure.Failure.Error.Code != code || failure.Failure.Reconcile != reconcile {
		t.Fatalf("unexpected safe failure: %+v", failure.Failure)
	}
	if strings.Contains(err.Error(), testToken) || strings.Contains(err.Error(), "PRIVATE") {
		t.Fatal("failure exposed private response/configuration text")
	}
	return failure
}

func TestRunSendsFrozenContractAndAcceptsBoundEvidence(t *testing.T) {
	run, expected := inputs(t)
	var count atomic.Int64
	client := newClient(t, func(w http.ResponseWriter, r *http.Request) {
		count.Add(1)
		if r.Method != http.MethodPost || r.URL.Path != "/internal/v2/agent/runs" || r.URL.RawQuery != "" {
			t.Error("unexpected private request route")
		}
		if len(r.Header.Values("Authorization")) != 1 || r.Header.Get("Authorization") != "Bearer "+testToken ||
			r.Header.Get("X-Request-ID") != "wire-test" || r.Header.Get("Content-Type") != "application/json" ||
			r.Header.Get("Accept-Encoding") != "identity" {
			t.Error("missing or invalid private request headers")
		}
		var received ac.RunRequest
		if json.NewDecoder(r.Body).Decode(&received) != nil || !reflect.DeepEqual(received, run) {
			t.Error("the wire request changed the frozen snapshot or deadline")
		}
		reply(t, w, r, 200, expected)
	})
	actual, err := client.Run(context.Background(), run, "wire-test")
	if err != nil || !reflect.DeepEqual(actual, expected) {
		t.Fatalf("completed run: %v", err)
	}
	if actual.Result.Answer.Citations[0].ChunkID != ac.Some("aic-28560-medium") || count.Load() != 1 {
		t.Fatal("the expected source or single-attempt behavior was lost")
	}
}

func TestRunBindsResponseToDetachedInput(t *testing.T) {
	run, expected := inputs(t)
	entered, release := make(chan struct{}), make(chan struct{})
	client := newClient(t, func(w http.ResponseWriter, r *http.Request) {
		close(entered)
		<-release
		reply(t, w, r, 200, expected)
	})
	finished := make(chan error, 1)
	go func() {
		_, err := client.Run(context.Background(), run, "detached-test")
		finished <- err
	}()
	<-entered
	run.Context.CatalogFacts[0].Value = "Caller changed the original title"
	close(release)
	if err := <-finished; err != nil {
		t.Fatal("later caller mutation affected response binding:", err)
	}
}

func TestRunAllowsRetainedReplayWithOriginalExpiredAgentDeadline(t *testing.T) {
	run, expected := inputs(t)
	run.Execution.DeadlineAt = "2026-01-01T00:00:00Z"
	client := newClient(t, func(w http.ResponseWriter, r *http.Request) {
		var received ac.RunRequest
		_ = json.NewDecoder(r.Body).Decode(&received)
		if received.Execution.DeadlineAt != run.Execution.DeadlineAt {
			t.Error("client refreshed the Agent admission deadline")
		}
		reply(t, w, r, 200, expected)
	})
	if _, err := client.Run(context.Background(), run, "retained-replay"); err != nil {
		t.Fatal("retained replay must reach the Agent's replay/admission decision:", err)
	}
}

func TestRunRejectsInvalidOutgoingInputBeforeNetwork(t *testing.T) {
	var calls atomic.Int64
	client := newClient(t, func(w http.ResponseWriter, r *http.Request) { calls.Add(1) })
	for _, trace := range []string{"", "two words", "x\n", "x\x7f", strings.Repeat("x", 129)} {
		run, _ := inputs(t)
		result, err := client.Run(context.Background(), run, trace)
		assertFailure(t, result, err, 502, "agent_invalid_response", false)
	}
	for _, mutate := range []func(*ac.RunRequest){
		func(r *ac.RunRequest) { r.Question = " padded " },
		func(r *ac.RunRequest) { r.Question = string([]byte{0xff}) },
		func(r *ac.RunRequest) { r.Question = strings.Repeat("x", ac.PrivateBodyLimit+1) },
		func(r *ac.RunRequest) { r.Scope.ArtworkID = "wrong" },
		func(r *ac.RunRequest) { r.Context.RecentTurns = nil },
		func(r *ac.RunRequest) { r.Execution.ProfileID = "unsupported" },
	} {
		run, _ := inputs(t)
		mutate(&run)
		result, err := client.Run(context.Background(), run, "invalid-input")
		assertFailure(t, result, err, 502, "agent_invalid_response", false)
	}
	run, _ := inputs(t)
	result, err := client.Run(nil, run, "nil-context")
	assertFailure(t, result, err, 502, "agent_invalid_response", false)
	if calls.Load() != 0 {
		t.Fatal("invalid input reached the network")
	}
}

func TestRunRejectsWrongRunStateScopeRevisionAndCitations(t *testing.T) {
	changes := map[string]func(*ac.RunSnapshot){
		"other run":       func(s *ac.RunSnapshot) { s.RunID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" },
		"processing POST": func(s *ac.RunSnapshot) { s.State, s.Result = "processing", nil },
		"failed POST as 200": func(s *ac.RunSnapshot) {
			s.State, s.Result = "failed", nil
			s.Error = &ac.AgentError{Code: "retrieval_unavailable", Message: "Unavailable", Phase: "retrieval", Retryable: true}
		},
		"other artwork":         func(s *ac.RunSnapshot) { s.Result.Scope.ArtworkID = "aic-11723" },
		"other release":         func(s *ac.RunSnapshot) { s.Result.Scope.DatasetVersion = "unreleased" },
		"other revision":        func(s *ac.RunSnapshot) { s.Result.ConversationUpdate.BaseRevision = 1 },
		"changed catalog":       func(s *ac.RunSnapshot) { s.Result.CatalogSnapshot[0].Value = "Another title" },
		"foreign evidence":      func(s *ac.RunSnapshot) { s.Result.EvidenceSnapshot[0].ArtworkID = "aic-11723" },
		"foreign citation":      func(s *ac.RunSnapshot) { s.Result.Answer.Citations[0].ArtworkID = "aic-11723" },
		"unknown citation":      func(s *ac.RunSnapshot) { s.Result.Answer.Citations[0].ChunkID = ac.Some("missing-id") },
		"bad language":          func(s *ac.RunSnapshot) { s.Result.AnswerLanguage = "ko" },
		"wrong execution count": func(s *ac.RunSnapshot) { s.Result.Execution.RetrievalCalls = 0 },
	}
	for name, change := range changes {
		t.Run(name, func(t *testing.T) {
			run, snapshot := inputs(t)
			change(&snapshot)
			client := newClient(t, func(w http.ResponseWriter, r *http.Request) { reply(t, w, r, 200, snapshot) })
			result, err := client.Run(context.Background(), run, "bound-response")
			assertFailure(t, result, err, 502, "agent_invalid_response", false)
		})
	}
}

func TestRunRejectsMalformedJSONAndStatus(t *testing.T) {
	run, _ := inputs(t)
	for _, raw := range []string{`{"state":"completed","state":"processing"}`, `{"n":NaN}`, `{} {}`,
		`{"text":"\ud800"}`, string([]byte{0xff}), `[]`, strings.Repeat("[", 65) + "0" + strings.Repeat("]", 65)} {
		t.Run(fmt.Sprintf("invalid-%d", len(raw)), func(t *testing.T) {
			client := newClient(t, func(w http.ResponseWriter, r *http.Request) {
				headers(w, r)
				_, _ = io.WriteString(w, raw)
			})
			result, err := client.Run(context.Background(), run, "malformed-test")
			assertFailure(t, result, err, 502, "agent_invalid_response", false)
		})
	}
	for _, status := range []int{201, 202, 204, 418} {
		t.Run(fmt.Sprint(status), func(t *testing.T) {
			_, snapshot := inputs(t)
			client := newClient(t, func(w http.ResponseWriter, r *http.Request) {
				if status == http.StatusNoContent {
					headers(w, r)
					w.WriteHeader(status)
					return
				}
				reply(t, w, r, status, snapshot)
			})
			result, err := client.Run(context.Background(), run, "status-test")
			assertFailure(t, result, err, 502, "agent_invalid_response", false)
		})
	}
}

func TestRunValidatesResponseHeaders(t *testing.T) {
	changes := map[string]func(http.Header){
		"missing type":       func(h http.Header) { h.Del("Content-Type") },
		"wrong type":         func(h http.Header) { h.Set("Content-Type", "text/plain") },
		"wrong charset":      func(h http.Header) { h.Set("Content-Type", "application/json; charset=latin-1") },
		"extra parameter":    func(h http.Header) { h.Set("Content-Type", "application/json; version=2") },
		"duplicate type":     func(h http.Header) { h.Add("Content-Type", "application/json") },
		"missing trace":      func(h http.Header) { h.Del("X-Request-ID") },
		"wrong trace":        func(h http.Header) { h.Set("X-Request-ID", "different") },
		"duplicate trace":    func(h http.Header) { h.Add("X-Request-ID", "headers-test") },
		"missing cache":      func(h http.Header) { h.Del("Cache-Control") },
		"cacheable":          func(h http.Header) { h.Set("Cache-Control", "public") },
		"compressed":         func(h http.Header) { h.Set("Content-Encoding", "gzip") },
		"duplicate encoding": func(h http.Header) { h.Add("Content-Encoding", "identity"); h.Add("Content-Encoding", "identity") },
		"declared overflow":  func(h http.Header) { h.Set("Content-Length", fmt.Sprint(ac.PrivateBodyLimit+1)) },
	}
	for name, change := range changes {
		t.Run(name, func(t *testing.T) {
			run, snapshot := inputs(t)
			client := newClient(t, func(w http.ResponseWriter, r *http.Request) {
				headers(w, r)
				change(w.Header())
				_ = json.NewEncoder(w).Encode(snapshot)
			})
			result, err := client.Run(context.Background(), run, "headers-test")
			assertFailure(t, result, err, 502, "agent_invalid_response", false)
		})
	}
}

func TestRunBoundsActualChunkedBodyAndAcceptsCompatibleMetadata(t *testing.T) {
	run, snapshot := inputs(t)
	client := newClient(t, func(w http.ResponseWriter, r *http.Request) {
		headers(w, r)
		w.(http.Flusher).Flush()
		_, _ = io.WriteString(w, strings.Repeat(" ", ac.PrivateBodyLimit+1))
	})
	result, err := client.Run(context.Background(), run, "actual-bound")
	assertFailure(t, result, err, 502, "agent_invalid_response", false)
	client = newClient(t, func(w http.ResponseWriter, r *http.Request) {
		raw, _ := json.Marshal(snapshot)
		var extended map[string]any
		_ = json.Unmarshal(raw, &extended)
		extended["future_metadata"] = "inert"
		headers(w, r)
		w.Header().Set("Content-Type", `Application/JSON; charset="UTF-8"`)
		w.Header().Set("Content-Encoding", "identity")
		_ = json.NewEncoder(w).Encode(extended)
	})
	if got, err := client.Run(context.Background(), run, "compatible-metadata"); err != nil || !reflect.DeepEqual(got, snapshot) {
		t.Fatalf("compatible schema-open metadata: %v", err)
	}
}

func TestRunMapsEverySharedUpstreamFailureWithoutEchoingDiagnostics(t *testing.T) {
	data, err := os.ReadFile("../../../docs/contracts/agent-v2.errors.json")
	if err != nil {
		t.Fatal(err)
	}
	var document struct {
		Policies map[string][]json.RawMessage `json:"policies"`
		Messages map[string]string            `json:"messages"`
	}
	if err := json.Unmarshal(data, &document); err != nil {
		t.Fatal(err)
	}
	for code, fields := range document.Policies {
		t.Run(code, func(t *testing.T) {
			var privateStatus, publicStatus int
			var publicCode string
			var retryable bool
			for i, target := range []any{&privateStatus, &publicStatus, &publicCode, &retryable} {
				if err := json.Unmarshal(fields[i], target); err != nil {
					t.Fatal(err)
				}
			}
			run, _ := inputs(t)
			client := newClient(t, func(w http.ResponseWriter, r *http.Request) {
				reply(t, w, r, privateStatus, ac.ErrorEnvelope{RequestID: "mapped-error", RunID: &run.RunID,
					Error: ac.AgentError{Code: code, Message: "PRIVATE " + testToken, Retryable: retryable, Phase: "admission"}})
			})
			result, err := client.Run(context.Background(), run, "mapped-error")
			failure := assertFailure(t, result, err, publicStatus, publicCode, code == "agent_run_in_progress")
			if failure.Failure.Error.Retryable != retryable || failure.Failure.Error.Message != document.Messages[publicCode] {
				t.Fatal("upstream failure did not follow the shared error policy")
			}
		})
	}
}

func TestRunRejectsMismatchedErrorEnvelopes(t *testing.T) {
	for _, change := range []func(*ac.ErrorEnvelope){
		func(e *ac.ErrorEnvelope) { e.RequestID = "another-trace" },
		func(e *ac.ErrorEnvelope) { other := ac.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"); e.RunID = &other },
		func(e *ac.ErrorEnvelope) { e.Error.Retryable = false },
		func(e *ac.ErrorEnvelope) { e.Error.Code = "generation_timeout" },
	} {
		run, _ := inputs(t)
		envelope := ac.ErrorEnvelope{RequestID: "error-binding", RunID: &run.RunID,
			Error: ac.AgentError{Code: "service_not_ready", Message: "PRIVATE", Retryable: true, Phase: "capacity"}}
		change(&envelope)
		client := newClient(t, func(w http.ResponseWriter, r *http.Request) { reply(t, w, r, 503, envelope) })
		result, err := client.Run(context.Background(), run, "error-binding")
		assertFailure(t, result, err, 502, "agent_invalid_response", false)
	}
	// Admission can fail before a valid run identity has been established.
	run, _ := inputs(t)
	client := newClient(t, func(w http.ResponseWriter, r *http.Request) {
		reply(t, w, r, 401, ac.ErrorEnvelope{RequestID: "null-run", Error: ac.AgentError{
			Code: "agent_unauthorized", Message: "PRIVATE", Retryable: false, Phase: "admission"}})
	})
	result, err := client.Run(context.Background(), run, "null-run")
	assertFailure(t, result, err, 503, "agent_unavailable", false)
}

func TestRunDoesNotFollowRedirectsOrLeakCredentialsToAnotherOrigin(t *testing.T) {
	var calls atomic.Int64
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls.Add(1) }))
	defer target.Close()
	run, _ := inputs(t)
	client := newClient(t, func(w http.ResponseWriter, r *http.Request) {
		headers(w, r)
		http.Redirect(w, r, target.URL, http.StatusTemporaryRedirect)
	})
	result, err := client.Run(context.Background(), run, "redirect-test")
	assertFailure(t, result, err, 502, "agent_invalid_response", false)
	if calls.Load() != 0 {
		t.Fatal("redirect reached another origin")
	}
}

func TestRunConnectionLossRequestsReconciliationWithoutRetryingPOST(t *testing.T) {
	run, _ := inputs(t)
	var calls atomic.Int64
	client := newClient(t, func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		connection, _, err := w.(http.Hijacker).Hijack()
		if err != nil {
			t.Error(err)
			return
		}
		_ = connection.Close()
	})
	result, err := client.Run(context.Background(), run, "lost-response")
	assertFailure(t, result, err, 503, "agent_unavailable", true)
	if calls.Load() != 1 {
		t.Fatal("POST was retried")
	}
}

func TestRunBrokenBodyRequestsReconciliation(t *testing.T) {
	run, _ := inputs(t)
	client := newClient(t, func(w http.ResponseWriter, r *http.Request) {
		headers(w, r)
		w.Header().Set("Content-Length", "1000")
		_, _ = io.WriteString(w, "{\"state\":")
	})
	result, err := client.Run(context.Background(), run, "broken-body")
	assertFailure(t, result, err, 503, "agent_unavailable", true)
}

func TestRunUnavailableServiceReturnsTypedFailure(t *testing.T) {
	run, _ := inputs(t)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	server.Close()
	client, err := New(server.URL, testToken)
	if err != nil {
		t.Fatal(err)
	}
	defer client.CloseIdleConnections()
	result, err := client.Run(context.Background(), run, "unavailable-service")
	assertFailure(t, result, err, 503, "agent_unavailable", true)
}

func TestRunParentDeadlineAndCancellationWinWhileReadingBody(t *testing.T) {
	for _, expired := range []bool{false, true} {
		t.Run(fmt.Sprint(expired), func(t *testing.T) {
			run, _ := inputs(t)
			started := make(chan struct{})
			client := newClient(t, func(w http.ResponseWriter, r *http.Request) {
				headers(w, r)
				w.(http.Flusher).Flush()
				close(started)
				<-r.Context().Done()
			})
			ctx, cancel := context.WithCancel(context.Background())
			if expired {
				ctx, cancel = context.WithTimeout(context.Background(), 80*time.Millisecond)
			}
			defer cancel()
			finished := make(chan error, 1)
			go func() { _, err := client.Run(ctx, run, "parent-budget"); finished <- err }()
			<-started
			if !expired {
				cancel()
			}
			err := <-finished
			if expired {
				assertFailure(t, ac.RunSnapshot{}, err, 504, "request_timeout", false)
				if !errors.Is(err, context.DeadlineExceeded) {
					t.Fatal("parent deadline identity was lost")
				}
			} else {
				assertFailure(t, ac.RunSnapshot{}, err, 503, "agent_unavailable", true)
				if !errors.Is(err, context.Canceled) {
					t.Fatal("parent cancellation identity was lost")
				}
			}
		})
	}
}

func TestRunLocalTransportTimeoutHasUnknownOutcome(t *testing.T) {
	run, _ := inputs(t)
	release := make(chan struct{})
	client := newClient(t, func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		select {
		case <-r.Context().Done():
		case <-release:
		}
	})
	t.Cleanup(func() { close(release) })
	client.timeout = 30 * time.Millisecond
	result, err := client.Run(context.Background(), run, "transport-timeout")
	assertFailure(t, result, err, 503, "agent_unavailable", true)
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal("transport deadline identity was lost")
	}
}

func TestRunAlreadyExpiredParentNeverCallsAgent(t *testing.T) {
	run, _ := inputs(t)
	var calls atomic.Int64
	client := newClient(t, func(w http.ResponseWriter, r *http.Request) { calls.Add(1) })
	ctx, cancel := context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
	defer cancel()
	result, err := client.Run(ctx, run, "expired-parent")
	assertFailure(t, result, err, 504, "request_timeout", false)
	if calls.Load() != 0 {
		t.Fatal("expired parent reached the network")
	}
}

func TestClientRequiresVerifiedTLS(t *testing.T) {
	run, snapshot := inputs(t)
	var calls atomic.Int64
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		reply(t, w, r, 200, snapshot)
	}))
	server.Config.ErrorLog = log.New(io.Discard, "", 0)
	server.StartTLS()
	defer server.Close()
	client, err := New(server.URL, testToken)
	if err != nil {
		t.Fatal(err)
	}
	defer client.CloseIdleConnections()
	result, err := client.Run(context.Background(), run, "untrusted-certificate")
	assertFailure(t, result, err, 503, "agent_unavailable", true)
	if calls.Load() != 0 {
		t.Fatal("untrusted TLS received an authenticated HTTP request")
	}
	roots := x509.NewCertPool()
	roots.AddCert(server.Certificate())
	client.http.Transport.(*http.Transport).TLSClientConfig = &tls.Config{RootCAs: roots}
	if _, err := client.Run(context.Background(), run, "trusted-certificate"); err != nil {
		t.Fatal(err)
	}
}

func TestClientConfigurationAndFormattingKeepSecretsPrivate(t *testing.T) {
	for _, origin := range []string{"", "file:///tmp/socket", "http://user:PRIVATE@localhost", "http://localhost/path",
		"http://localhost?token=PRIVATE", "http://localhost#", "http://localhost:", "http://localhost:0", "http://localhost:65536", "http://localhost\n"} {
		if _, err := New(origin, testToken); err == nil || strings.Contains(err.Error(), "PRIVATE") {
			t.Fatal("invalid origin accepted or echoed")
		}
	}
	for _, token := range []string{"", "short", strings.Repeat("a", 42), strings.Repeat("a", 43) + " ", strings.Repeat("a", 43) + "\x7f"} {
		if _, err := New("http://localhost:8002", token); err == nil {
			t.Fatal("invalid token accepted")
		}
	}
	client, err := New("http://[::1]:8002/", testToken)
	if err != nil {
		t.Fatal(err)
	}
	defer client.CloseIdleConnections()
	for _, formatted := range []string{fmt.Sprint(client), fmt.Sprintf("%+v", client), fmt.Sprintf("%#v", client)} {
		if strings.Contains(formatted, testToken) {
			t.Fatal("formatted client exposed the token")
		}
	}
	transport := client.http.Transport.(*http.Transport)
	if transport.Proxy != nil || !transport.DisableCompression {
		t.Fatal("private transport inherited a proxy or decompression")
	}
}

func TestClientCanBeReusedConcurrently(t *testing.T) {
	run, snapshot := inputs(t)
	client := newClient(t, func(w http.ResponseWriter, r *http.Request) { reply(t, w, r, 200, snapshot) })
	var workers sync.WaitGroup
	for i := range 12 {
		workers.Go(func() {
			if _, err := client.Run(context.Background(), run, fmt.Sprintf("parallel-%d", i)); err != nil {
				t.Error(err)
			}
		})
	}
	workers.Wait()
}
