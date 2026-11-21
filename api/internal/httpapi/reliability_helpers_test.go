package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/Jasonssssss9/musebridge/api/internal/agentclient"
	ac "github.com/Jasonssssss9/musebridge/api/internal/agentcontract"
)

const syntheticServiceToken = "SYNTHETIC-M2-HTTP-TOKEN-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

type privateCall struct {
	Run   ac.RunRequest
	Trace string
	At    time.Time
}

// privateHTTP scripts the Agent transport boundary, not language understanding.
// All callers still use the production Go HTTP client and public handlers.
type privateHTTP struct {
	server      *httptest.Server
	mu          sync.Mutex
	posts, gets []privateCall
	runs        map[ac.UUID]ac.RunRequest
	respond     func(http.ResponseWriter, *http.Request, ac.RunRequest)
}

func newPrivateHTTP(t *testing.T, respond func(http.ResponseWriter, *http.Request, ac.RunRequest)) *privateHTTP {
	t.Helper()
	a := &privateHTTP{runs: make(map[ac.UUID]ac.RunRequest), respond: respond}
	validator, err := ac.DefaultValidator()
	if err != nil {
		t.Fatal(err)
	}
	a.server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+syntheticServiceToken || r.Header.Get("X-Request-ID") == "" {
			t.Error("private request lost service authentication or trusted trace")
			w.WriteHeader(500)
			return
		}
		var run ac.RunRequest
		a.mu.Lock()
		if r.Method == "POST" {
			body, err := io.ReadAll(io.LimitReader(r.Body, ac.PrivateBodyLimit+1))
			if err != nil || r.URL.Path != "/internal/v2/agent/runs" || validator.Decode("RunRequest", body, &run) != nil || validator.CheckRunRequest(run) != nil {
				a.mu.Unlock()
				t.Error("invalid Go private request")
				w.WriteHeader(500)
				return
			}
			a.posts = append(a.posts, privateCall{run, r.Header.Get("X-Request-ID"), time.Now()})
			a.runs[run.RunID] = run
		} else {
			id := ac.UUID(strings.TrimPrefix(r.URL.Path, "/internal/v2/agent/runs/"))
			var found bool
			run, found = a.runs[id]
			if r.Method != "GET" || !found {
				a.mu.Unlock()
				t.Error("recovery did not query the original run")
				w.WriteHeader(500)
				return
			}
			a.gets = append(a.gets, privateCall{run, r.Header.Get("X-Request-ID"), time.Now()})
		}
		a.mu.Unlock()
		if a.respond != nil {
			a.respond(w, r, run)
		} else {
			writePrivate(w, r, 200, dialogueSnapshot(run))
		}
	}))
	t.Cleanup(a.server.Close)
	return a
}

func (a *privateHTTP) calls() ([]privateCall, []privateCall) {
	a.mu.Lock()
	defer a.mu.Unlock()
	return append([]privateCall{}, a.posts...), append([]privateCall{}, a.gets...)
}

func writePrivate(w http.ResponseWriter, r *http.Request, status int, payload any) {
	w.Header().Set("X-Request-ID", r.Header.Get("X-Request-ID"))
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

func writePrivateError(w http.ResponseWriter, r *http.Request, run ac.RunRequest, status int, code, phase string, retry bool) {
	id := run.RunID
	writePrivate(w, r, status, ac.ErrorEnvelope{RequestID: r.Header.Get("X-Request-ID"), RunID: &id,
		Error: ac.AgentError{Code: code, Message: "PRIVATE test diagnostic", Retryable: retry, Phase: phase}})
}

func dialogueSnapshot(run ac.RunRequest) ac.RunSnapshot {
	language := ac.Language(run.ConversationState.LanguagePreference)
	if run.LanguageOverride != nil {
		language = ac.Language(*run.LanguageOverride)
	}
	if language == "auto" {
		language = run.ConversationState.AutoLanguage
	}
	reused := []string{}
	for _, e := range run.ConversationState.VerifiedEvidence {
		reused = append(reused, e.ChunkID)
	}
	return ac.RunSnapshot{ContractVersion: ac.PrivateVersion, RunID: run.RunID, State: "completed", Result: &ac.RunResult{
		Scope: run.Scope, AnswerLanguage: language, LanguageDecision: ac.LanguageDecision{DetectedLanguage: "und", AnswerLanguage: language},
		Answer:           ac.AgentAnswer{Status: "conversational", Text: "Synthetic conversation reply.", Citations: []ac.Citation{}},
		Requests:         []ac.InterpretedRequest{{ID: "r1", OriginalFragment: run.Question, Intent: "Respond to the visitor.", Kind: "conversation", Resolution: "clear"}},
		RequestResults:   []ac.RequestResult{{RequestID: "r1", Support: "not_applicable", Text: "Synthetic conversation reply.", CitationIDs: []string{}}},
		EvidenceSnapshot: append([]ac.Evidence{}, run.ConversationState.VerifiedEvidence...), ReusedChunkIDs: reused, ExecutedQueries: []ac.SearchQuery{},
		CatalogSnapshot: run.Context.CatalogFacts,
		ConversationUpdate: ac.ConversationUpdate{BaseRevision: run.ConversationState.Revision, Memory: ac.MemoryUpdate{
			ActiveTopic: run.ConversationState.ActiveTopic, ContinuitySummary: run.ConversationState.ContinuitySummary, PendingUpdate: ac.PendingUpdate{Action: "keep"}}},
		Execution: ac.ExecutionRecord{WorkflowID: ac.WorkflowID, ProfileID: ac.ProfileID, PromptVersion: "museum-prompts-v2", SchemaVersion: "agent-schema-v2",
			RetrievalStrategyVersion: "legacy-facet-v0.1", ModelProvider: "ollama", ModelName: "SYNTHETIC-HTTP-TEST-ONLY", ModelDigest: "SYNTHETIC-NOT-A-MODEL-DIGEST", ModelCalls: 1},
	}}
}

type publicReply struct {
	Body   []byte
	Status int
	Trace  string
	Err    error
}
type publicBrowser struct {
	url    string
	client *http.Client
}

func newPublicBrowser(t *testing.T, h http.Handler, wrap func(http.Handler) http.Handler) *publicBrowser {
	t.Helper()
	if wrap != nil {
		h = wrap(h)
	}
	server := httptest.NewServer(h)
	t.Cleanup(server.Close)
	jar, err := cookiejar.New(nil)
	if err != nil {
		t.Fatal(err)
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	b := &publicBrowser{url: server.URL, client: &http.Client{Jar: jar, Transport: transport, Timeout: 70 * time.Second}}
	t.Cleanup(b.client.CloseIdleConnections)
	return b
}

func newReliabilityStack(t *testing.T, a *privateHTTP) *publicBrowser {
	t.Helper()
	client, err := agentclient.New(a.server.URL, syntheticServiceToken)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(client.CloseIdleConnections)
	h, err := NewAgent(testDataset(t), nil, Settings{PublicBaseURL: browserOrigin}, client)
	if err != nil {
		t.Fatal(err)
	}
	return newPublicBrowser(t, h, nil)
}

func (b *publicBrowser) call(ctx context.Context, method, path string, payload any) publicReply {
	var body io.Reader
	if payload != nil {
		data, err := json.Marshal(payload)
		if err != nil {
			return publicReply{Err: err}
		}
		body = bytes.NewReader(data)
	}
	r, err := http.NewRequestWithContext(ctx, method, b.url+path, body)
	if err != nil {
		return publicReply{Err: err}
	}
	if method == "POST" {
		r.Header.Set("Origin", browserOrigin)
		r.Header.Set("Content-Type", "application/json")
	}
	r.Header.Set("X-Request-ID", "untrusted-browser-trace")
	response, err := b.client.Do(r)
	if err != nil {
		return publicReply{Err: err}
	}
	defer response.Body.Close()
	data, err := io.ReadAll(io.LimitReader(response.Body, 1<<20))
	if err == nil && (response.Header.Get("Cache-Control") != "no-store" || response.Header.Get("X-Request-ID") == "" || response.Header.Get("X-Request-ID") == "untrusted-browser-trace") {
		err = fmt.Errorf("invalid public response headers")
	}
	return publicReply{Body: data, Status: response.StatusCode, Trace: response.Header.Get("X-Request-ID"), Err: err}
}

func (b *publicBrowser) open(t *testing.T, artwork string) ac.PublicConversation {
	t.Helper()
	reply := b.call(context.Background(), "POST", "/api/conversations", ac.PublicCreateConversationRequest{ContractVersion: ac.PublicVersion, ArtworkID: artwork})
	var value ac.PublicConversationResponse
	decodePublic(t, reply, "PublicConversationResponse", &value)
	return value.Conversation
}
func messagesPath(id ac.UUID) string { return "/api/conversations/" + string(id) + "/messages" }
func (b *publicBrowser) post(id ac.UUID, input ac.PublicMessageRequest) publicReply {
	return b.call(context.Background(), "POST", messagesPath(id), input)
}
func (b *publicBrowser) history(t *testing.T, id ac.UUID) ac.PublicHistoryResponse {
	t.Helper()
	var value ac.PublicHistoryResponse
	decodePublic(t, b.call(context.Background(), "GET", messagesPath(id), nil), "PublicHistoryResponse", &value)
	return value
}
func decodePublic(t *testing.T, reply publicReply, definition string, target any) {
	t.Helper()
	validator, _ := ac.DefaultValidator()
	if reply.Err != nil || reply.Status != 200 || validator.Decode(definition, reply.Body, target) != nil {
		t.Fatalf("invalid public %s: status=%d err=%v body=%s", definition, reply.Status, reply.Err, reply.Body)
	}
}
func publicFailure(t *testing.T, reply publicReply, status int, code string, retry bool) {
	t.Helper()
	var value struct {
		RequestID string         `json:"request_id"`
		Error     ac.PublicError `json:"error"`
	}
	if reply.Err != nil || reply.Status != status || json.Unmarshal(reply.Body, &value) != nil || value.RequestID != reply.Trace || value.Error.Code != code || value.Error.Retryable != retry {
		t.Fatalf("incorrect public failure: status=%d err=%v body=%s", reply.Status, reply.Err, reply.Body)
	}
	for _, private := range []string{"PRIVATE", syntheticServiceToken, `"phase"`, `"run_id"`, `"conversation_update"`} {
		if bytes.Contains(reply.Body, []byte(private)) {
			t.Fatal("private diagnostics or state escaped to public error")
		}
	}
}

func waitSignal(t *testing.T, ch <-chan struct{}) {
	t.Helper()
	select {
	case <-ch:
	case <-time.After(5 * time.Second):
		t.Fatal("controlled operation did not reach its boundary")
	}
}
func releaseOnCleanup(t *testing.T, ch chan struct{}) {
	t.Helper()
	t.Cleanup(func() {
		select {
		case <-ch:
		default:
			close(ch)
		}
	})
}
