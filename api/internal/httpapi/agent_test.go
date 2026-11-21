package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
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
)

type agentPort struct {
	run   func(context.Context, ac.RunRequest, string) (ac.RunSnapshot, error)
	posts atomic.Int64
}

func (a *agentPort) Run(ctx context.Context, run ac.RunRequest, trace string) (ac.RunSnapshot, error) {
	a.posts.Add(1)
	return a.run(ctx, run, trace)
}

func (*agentPort) Get(context.Context, ac.RunRequest, string) (ac.RunSnapshot, error) {
	return ac.RunSnapshot{}, &agentclient.Error{Failure: ac.Failure{Status: 503, Error: ac.PublicError{Code: "agent_unavailable", Retryable: true}}, RunMissing: true}
}

func newAgentHandler(t *testing.T, port *agentPort) http.Handler {
	t.Helper()
	h, err := NewAgent(testDataset(t), nil, Settings{PublicBaseURL: browserOrigin}, port)
	if err != nil {
		t.Fatal(err)
	}
	return h
}

func openAgentSession(t *testing.T, h http.Handler, artwork string, cookie *http.Cookie) (ac.PublicConversation, *http.Cookie, bool) {
	t.Helper()
	var cookies []*http.Cookie
	if cookie != nil {
		cookies = append(cookies, cookie)
	}
	body, _ := json.Marshal(ac.PublicCreateConversationRequest{ContractVersion: ac.PublicVersion, ArtworkID: artwork})
	w := sessionCall(h, "POST", "/api/conversations", string(body), cookies...)
	var result ac.PublicConversationResponse
	v, _ := ac.DefaultValidator()
	if w.Code != 200 || v.Decode("PublicConversationResponse", w.Body.Bytes(), &result) != nil || len(w.Result().Cookies()) != 1 {
		t.Fatalf("v2 creation failed: %d %s", w.Code, w.Body.String())
	}
	return result.Conversation, w.Result().Cookies()[0], result.Created
}

func agentMessage(n int, revision int64) ac.PublicMessageRequest {
	return ac.PublicMessageRequest{ContractVersion: ac.PublicVersion, ClientMessageID: ac.UUID(messageID(n)),
		Question: "This painting is beautiful. What material is it made of?", ExpectedRevision: revision}
}

func postAgent(h http.Handler, id ac.UUID, message ac.PublicMessageRequest, cookie *http.Cookie) *httptest.ResponseRecorder {
	body, _ := json.Marshal(message)
	var cookies []*http.Cookie
	if cookie != nil {
		cookies = append(cookies, cookie)
	}
	return sessionCall(h, "POST", "/api/conversations/"+string(id)+"/messages", string(body), cookies...)
}

func agentHistoryResponse(t *testing.T, h http.Handler, current ac.PublicConversation, cookie *http.Cookie) ac.PublicHistoryResponse {
	t.Helper()
	w := sessionCall(h, "GET", "/api/conversations/"+string(current.ConversationID)+"/messages", "", cookie)
	var history ac.PublicHistoryResponse
	v, _ := ac.DefaultValidator()
	if w.Code != 200 || v.Decode("PublicHistoryResponse", w.Body.Bytes(), &history) != nil || w.Header().Get("Set-Cookie") != "" {
		t.Fatalf("invalid v2 history: %d %s", w.Code, w.Body.String())
	}
	return history
}

func snapshotFactory(t *testing.T) func(ac.RunRequest) ac.RunSnapshot {
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
		t.Fatal("invalid shared examples")
	}
	for _, item := range examples.Cases {
		if item.Name == "completed_run_snapshot" {
			return func(run ac.RunRequest) ac.RunSnapshot {
				var result ac.RunSnapshot
				if json.Unmarshal(item.Payload, &result) != nil {
					panic("invalid shared snapshot")
				}
				result.RunID = run.RunID
				result.Result.Scope = run.Scope
				result.Result.CatalogSnapshot = run.Context.CatalogFacts
				result.Result.ConversationUpdate.BaseRevision = run.ConversationState.Revision
				return result
			}
		}
	}
	t.Fatal("missing shared completed snapshot")
	return nil
}

func TestAgentPublicFlowReplayAndStateConflict(t *testing.T) {
	completed := snapshotFactory(t)
	var trace string
	port := &agentPort{run: func(_ context.Context, run ac.RunRequest, requestID string) (ac.RunSnapshot, error) {
		trace = requestID
		return completed(run), nil
	}}
	var logs bytes.Buffer
	h, err := NewAgent(testDataset(t), slog.New(slog.NewJSONHandler(&logs, nil)), Settings{PublicBaseURL: browserOrigin}, port)
	if err != nil {
		t.Fatal(err)
	}
	current, cookie, created := openAgentSession(t, h, "aic-28560", nil)
	if !created || current.Revision != 0 || current.LanguagePreference != "auto" || current.AutoLanguage != "en" || current.LastAnswerLanguage != nil {
		t.Fatal("initial v2 projection is incorrect")
	}
	if cookie.Path != "/" || !cookie.HttpOnly || cookie.SameSite != http.SameSiteLaxMode || cookie.MaxAge != 86400 || cookie.Secure {
		t.Fatal("v2 cookie differs from the browser profile")
	}
	input := agentMessage(1, 0)
	input.Question = "\u2003\n" + input.Question + "\t "
	w := postAgent(h, current.ConversationID, input, cookie)
	var first ac.PublicSubmissionResponse
	v, _ := ac.DefaultValidator()
	if w.Code != 200 || v.Decode("PublicSubmissionResponse", w.Body.Bytes(), &first) != nil || first.Conversation.Revision != 1 ||
		first.Turn.Answer.Mode != "llm" || first.Turn.Question != strings.TrimSpace(input.Question) || trace != w.Header().Get("X-Request-ID") || trace == "" || w.Header().Get("Set-Cookie") != "" {
		t.Fatalf("public v2 submit failed: %d %s", w.Code, w.Body.String())
	}
	replay := postAgent(h, current.ConversationID, input, cookie)
	if replay.Code != 200 || !bytes.Equal(replay.Body.Bytes(), w.Body.Bytes()) || port.posts.Load() != 1 {
		t.Fatal("completed retry was not replayed exactly once")
	}
	changed := input
	changed.Question += "?"
	requireSessionError(t, postAgent(h, current.ConversationID, changed, cookie), 409, "idempotency_conflict")
	requireSessionError(t, postAgent(h, current.ConversationID, agentMessage(2, 0), cookie), 409, "conversation_state_conflict")
	restored, _, created := openAgentSession(t, h, "aic-28560", cookie)
	if created || !reflect.DeepEqual(restored, first.Conversation) {
		t.Fatal("refresh lost authoritative v2 state")
	}
	other, _, _ := openAgentSession(t, h, "aic-11723", cookie)
	if other.ConversationID == current.ConversationID || other.Revision != 0 || len(agentHistoryResponse(t, h, other, cookie).Turns) != 0 {
		t.Fatal("artwork switch mixed histories")
	}
	history := agentHistoryResponse(t, h, current, cookie)
	if history.Conversation.Revision != 1 || len(history.Turns) != 1 || history.PendingRequest != nil || port.posts.Load() != 1 {
		t.Fatal("history or rejected submissions changed the accepted turn")
	}
	for _, secret := range []string{cookie.Value, input.Question, string(current.ConversationID)} {
		if strings.Contains(logs.String(), secret) {
			t.Fatal("private text or identity leaked in routine logs")
		}
	}
	for _, secret := range []string{cookie.Value, "continuity_summary", "verified_evidence"} {
		if strings.Contains(w.Body.String(), secret) {
			t.Fatal("private state leaked in public response")
		}
	}
}

func TestAgentPublicStrictValidation(t *testing.T) {
	port := &agentPort{}
	h := newAgentHandler(t, port)
	current, cookie, _ := openAgentSession(t, h, "aic-28560", nil)
	base, _ := json.Marshal(agentMessage(1, 0))
	valid := string(base)
	for _, tc := range []struct {
		name, body, code string
		status           int
	}{
		{"missing nullable", strings.Replace(valid, `,"language_override":null`, "", 1), "invalid_request", 400},
		{"revision float", strings.Replace(valid, `"expected_revision":0`, `"expected_revision":0.0`, 1), "invalid_request", 400},
		{"revision exponent", strings.Replace(valid, `"expected_revision":0`, `"expected_revision":0e0`, 1), "invalid_request", 400},
		{"revision boolean", strings.Replace(valid, `"expected_revision":0`, `"expected_revision":false`, 1), "invalid_request", 400},
		{"revision negative", strings.Replace(valid, `"expected_revision":0`, `"expected_revision":-1`, 1), "invalid_request", 400},
		{"revision overflow", strings.Replace(valid, `"expected_revision":0`, `"expected_revision":2147483648`, 1), "invalid_request", 400},
		{"wrong language", strings.Replace(valid, `"language_override":null`, `"language_override":"fr"`, 1), "invalid_request", 400},
		{"null question", strings.Replace(valid, `"question":"`+agentMessage(1, 0).Question+`"`, `"question":null`, 1), "invalid_request", 400},
		{"wrong contract", strings.Replace(valid, ac.PublicVersion, "future-contract", 1), "agent_contract_mismatch", 409},
		{"wrong contract malformed", strings.Replace(strings.Replace(valid, ac.PublicVersion, "future-contract", 1), `"expected_revision":0`, `"expected_revision":null`, 1), "invalid_request", 400},
		{"null contract", strings.Replace(valid, `"`+ac.PublicVersion+`"`, `null`, 1), "invalid_request", 400},
		{"duplicate", strings.TrimSuffix(valid, "}") + `,"expected_revision":0}`, "invalid_request", 400},
		{"unknown field", strings.TrimSuffix(valid, "}") + `,"conversation_state":{}}`, "invalid_request", 400},
		{"wrong key case", strings.Replace(valid, "question", "Question", 1), "invalid_request", 400},
		{"control before trim", strings.Replace(valid, agentMessage(1, 0).Question, `\u000bhello`, 1), "invalid_request", 400},
		{"surrogate", strings.Replace(valid, agentMessage(1, 0).Question, `\ud800`, 1), "invalid_request", 400},
		{"blank", strings.Replace(valid, agentMessage(1, 0).Question, "   ", 1), "invalid_request", 400},
		{"too many code points", strings.Replace(valid, agentMessage(1, 0).Question, strings.Repeat("好", 501), 1), "invalid_request", 400},
		{"trailing", valid + "{}", "invalid_request", 400},
		{"oversize", valid + strings.Repeat(" ", ac.PublicBodyLimit), "payload_too_large", 413},
	} {
		t.Run(tc.name, func(t *testing.T) {
			requireSessionError(t, sessionCall(h, "POST", "/api/conversations/"+string(current.ConversationID)+"/messages", tc.body, cookie), tc.status, tc.code)
		})
	}
	for _, name := range []string{"Origin", "Content-Type", "Content-Encoding"} {
		r := httptest.NewRequest("POST", "/api/conversations/"+string(current.ConversationID)+"/messages", strings.NewReader(valid))
		r.Header.Set("Origin", browserOrigin)
		r.Header.Set("Content-Type", "application/json")
		r.AddCookie(cookie)
		if name == "Content-Encoding" {
			r.Header.Set(name, "gzip")
		} else {
			r.Header.Add(name, "invalid-duplicate")
		}
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		status, code := 415, "unsupported_media_type"
		if name == "Origin" {
			status, code = 403, "origin_not_allowed"
		}
		requireSessionError(t, w, status, code)
	}
	path := "/api/conversations/" + string(current.ConversationID) + "/messages"
	requireSessionError(t, sessionCall(h, "POST", path, valid), 404, "conversation_not_found")
	_, stranger, _ := openAgentSession(t, h, "aic-28560", nil)
	requireSessionError(t, sessionCall(h, "POST", path, valid, stranger), 404, "conversation_not_found")
	requireSessionError(t, sessionCall(h, "POST", path, valid, cookie, stranger), 404, "conversation_not_found")
	requireSessionError(t, sessionCall(h, "GET", strings.ToUpper(path), "", cookie), 404, "route_not_found")
	requireSessionError(t, sessionCall(h, "GET", "/api/conversations/"+strings.ToUpper(string(current.ConversationID))+"/messages", "", cookie), 400, "invalid_request")
	if port.posts.Load() != 0 {
		t.Fatal("invalid public request reached the Agent")
	}
}

func TestAgentCreationContractAndBodyLimits(t *testing.T) {
	if _, err := NewAgent(testDataset(t), nil, Settings{PublicBaseURL: browserOrigin}, nil); err == nil {
		t.Fatal("missing Agent client silently selected a usable public mode")
	}
	h := newAgentHandler(t, &agentPort{})
	valid := `{"contract_version":"agent-demo-v0.3","artwork_id":"aic-28560"}`
	for _, tc := range []struct {
		body   string
		status int
		code   string
	}{
		{`{"artwork_id":"aic-28560"}`, 400, "invalid_request"},
		{strings.Replace(valid, ac.PublicVersion, "future", 1), 409, "agent_contract_mismatch"},
		{strings.Replace(valid, "aic-28560", "unknown", 1), 404, "artwork_not_found"},
		{valid + strings.Repeat(" ", ac.PublicBodyLimit-len(valid)+1), 413, "payload_too_large"},
	} {
		requireSessionError(t, sessionCall(h, "POST", "/api/conversations", tc.body), tc.status, tc.code)
	}
	exact := valid + strings.Repeat(" ", ac.PublicBodyLimit-len(valid))
	if w := sessionCall(h, "POST", "/api/conversations", exact); w.Code != 200 {
		t.Fatal("exactly 8 KiB valid v2 body rejected")
	}
	r := httptest.NewRequest("POST", "/api/conversations", strings.NewReader(exact+" "))
	r.ContentLength = -1
	r.Header.Set("Origin", browserOrigin)
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	requireSessionError(t, w, 413, "payload_too_large")
	fixture := New(testDataset(t), nil, Settings{PublicBaseURL: browserOrigin}, nil)
	requireSessionError(t, sessionCall(fixture, "POST", "/api/conversations", valid), 400, "invalid_request")
	current, _, _ := openAgentSession(t, h, "aic-28560", nil)
	input := agentMessage(1, 0)
	for _, preference := range []string{"auto", "en", "es", "zh-CN", "ko"} {
		language := ac.LanguagePreference(preference)
		input.LanguageOverride = &language
		// A foreign cookie checks successful shape validation without executing a model.
		requireSessionError(t, postAgent(h, current.ConversationID, input, nil), 404, "conversation_not_found")
	}
	input.Question = strings.Repeat("好", 500)
	requireSessionError(t, postAgent(h, current.ConversationID, input, nil), 404, "conversation_not_found")
}

func TestAgentUnknownArtworkIsNotLoggedAsTrustedScope(t *testing.T) {
	var logs bytes.Buffer
	h, err := NewAgent(testDataset(t), slog.New(slog.NewJSONHandler(&logs, nil)), Settings{PublicBaseURL: browserOrigin}, &agentPort{})
	if err != nil {
		t.Fatal(err)
	}
	w := sessionCall(h, "POST", "/api/conversations", `{"contract_version":"agent-demo-v0.3","artwork_id":"PRIVATE untrusted catalog input"}`)
	requireSessionError(t, w, 404, "artwork_not_found")
	if strings.Contains(logs.String(), "PRIVATE") {
		t.Fatal("unknown artwork input leaked into trusted routine logs")
	}
}

func TestAgentPublicFailureIsSafeAndDoesNotAdvanceState(t *testing.T) {
	port := &agentPort{run: func(context.Context, ac.RunRequest, string) (ac.RunSnapshot, error) {
		return ac.RunSnapshot{}, &agentclient.Error{Failure: ac.Failure{Status: 503, Error: ac.PublicError{Code: "generation_unavailable", Message: "PRIVATE provider diagnostic", Retryable: true}}}
	}}
	h := newAgentHandler(t, port)
	current, cookie, _ := openAgentSession(t, h, "aic-28560", nil)
	w := postAgent(h, current.ConversationID, agentMessage(1, 0), cookie)
	if w.Code != 503 || !strings.Contains(w.Body.String(), `"code":"generation_unavailable"`) || strings.Contains(w.Body.String(), "PRIVATE") || w.Header().Get("Set-Cookie") != "" {
		t.Fatal("unsafe or incorrect public failure", w.Body.String())
	}
	history := agentHistoryResponse(t, h, current, cookie)
	if history.Conversation.Revision != 0 || len(history.Turns) != 0 || history.PendingRequest == nil || history.PendingRequest.State != "failed" || !history.PendingRequest.LastError.Retryable {
		t.Fatal("technical failure advanced the conversation")
	}
}

func TestAgentPublicProcessingAndBrowserCancellation(t *testing.T) {
	completed := snapshotFactory(t)
	started, release := make(chan struct{}), make(chan struct{})
	port := &agentPort{run: func(ctx context.Context, run ac.RunRequest, _ string) (ac.RunSnapshot, error) {
		close(started)
		<-release
		if ctx.Err() != nil {
			return ac.RunSnapshot{}, ctx.Err()
		}
		return completed(run), nil
	}}
	h := newAgentHandler(t, port)
	current, cookie, _ := openAgentSession(t, h, "aic-28560", nil)
	input := agentMessage(1, 0)
	body, _ := json.Marshal(input)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	r := httptest.NewRequest("POST", "/api/conversations/"+string(current.ConversationID)+"/messages", bytes.NewReader(body)).WithContext(ctx)
	r.Header.Set("Origin", browserOrigin)
	r.Header.Set("Content-Type", "application/json")
	r.AddCookie(cookie)
	w := httptest.NewRecorder()
	done := make(chan struct{})
	go func() { defer close(done); h.ServeHTTP(w, r) }()
	t.Cleanup(func() {
		select {
		case <-release:
		default:
			close(release)
		}
		select {
		case <-done:
		case <-time.After(2 * time.Second):
			t.Error("public handler did not stop")
		}
	})
	select {
	case <-started:
	case <-time.After(2 * time.Second):
		t.Fatal("submission did not start")
	}
	history := agentHistoryResponse(t, h, current, cookie)
	if history.PendingRequest == nil || history.PendingRequest.State != "processing" || history.PendingRequest.ClientMessageID != input.ClientMessageID || history.Conversation.Revision != 0 || len(history.Turns) != 0 {
		t.Fatal("processing projection lost original retry payload")
	}
	duplicate := postAgent(h, current.ConversationID, input, cookie)
	busy := postAgent(h, current.ConversationID, agentMessage(2, 0), cookie)
	if duplicate.Code != 409 || !strings.Contains(duplicate.Body.String(), "request_in_progress") || busy.Code != 409 || !strings.Contains(busy.Body.String(), "conversation_busy") {
		t.Fatal("active public submission was duplicated")
	}
	cancel()
	close(release)
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("accepted request did not finish after browser cancellation")
	}
	history = agentHistoryResponse(t, h, current, cookie)
	if w.Code != 200 || history.Conversation.Revision != 1 || len(history.Turns) != 1 || history.PendingRequest != nil || port.posts.Load() != 1 {
		t.Fatal("browser cancellation lost the accepted turn or repeated inference")
	}
}
