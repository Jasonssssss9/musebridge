package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/Jasonssssss9/musebridge/api/internal/catalog"
	"github.com/Jasonssssss9/musebridge/api/internal/chat"
	"github.com/Jasonssssss9/musebridge/api/internal/conversation"
	"github.com/Jasonssssss9/musebridge/api/internal/fixture"
	"github.com/Jasonssssss9/musebridge/api/internal/retrieval"
)

type retrievalFunc func(context.Context, retrieval.Request) (retrieval.Result, error)

func (f retrievalFunc) Retrieve(ctx context.Context, r retrieval.Request) (retrieval.Result, error) {
	return f(ctx, r)
}

func messageID(n int) string { return fmt.Sprintf("00000000-0000-4000-8000-%012d", n) }

func postSubmission(h http.Handler, id string, submission conversation.Submission, cookie *http.Cookie) *httptest.ResponseRecorder {
	body, _ := json.Marshal(submission)
	r := httptest.NewRequest("POST", "/api/conversations/"+id+"/messages", bytes.NewReader(body))
	r.Header.Set("Origin", browserOrigin)
	r.Header.Set("Content-Type", "application/json")
	r.Header.Set("X-Request-ID", "untrusted-client-id")
	if cookie != nil {
		r.AddCookie(cookie)
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	return w
}

func decodeTurn(t *testing.T, w *httptest.ResponseRecorder) conversation.Turn {
	t.Helper()
	var body struct {
		Turn conversation.Turn `json:"turn"`
	}
	if w.Code != 200 || json.Unmarshal(w.Body.Bytes(), &body) != nil || body.Turn.TurnID == "" {
		t.Fatalf("message failed: %d %s", w.Code, w.Body.String())
	}
	if w.Header().Get("Set-Cookie") != "" {
		t.Fatal("message submission refreshed visitor identity")
	}
	return body.Turn
}

func TestMessageHTTPRetrievalAndRecovery(t *testing.T) {
	dataset := testDataset(t)
	var mode, calls atomic.Int32
	var lastRequestID atomic.Value
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		id := r.Header.Get("X-Request-ID")
		lastRequestID.Store(id)
		w.Header().Set("X-Request-ID", id)
		w.Header().Set("Content-Type", "application/json")
		var fields map[string]json.RawMessage
		if r.Method != "POST" || r.URL.Path != "/retrieve" || r.ContentLength <= 0 || id == "" || id == "untrusted-client-id" || json.NewDecoder(r.Body).Decode(&fields) != nil || len(fields) != 4 {
			t.Error("Go violated the private retrieval request contract")
			w.WriteHeader(500)
			return
		}
		var artwork, query, version string
		var topK int
		json.Unmarshal(fields["artwork_id"], &artwork)
		json.Unmarshal(fields["query"], &query)
		json.Unmarshal(fields["dataset_version"], &version)
		json.Unmarshal(fields["top_k"], &topK)
		if version != dataset.Version() || topK != 2 {
			t.Error("release or result limit was not pinned")
		}
		if mode.Load() == 2 {
			w.WriteHeader(503)
			json.NewEncoder(w).Encode(map[string]any{"request_id": id, "error": map[string]any{"code": "retrieval_unavailable", "message": "secret-upstream-diagnostic", "retryable": true}})
			return
		}
		chunk := artwork + "-medium"
		if strings.Contains(query, "brushwork") {
			chunk = artwork + "-description-01"
		}
		evidence, _ := dataset.EvidenceByChunkID(chunk)
		if mode.Load() == 1 {
			evidence.Excerpt = "altered under a valid chunk ID"
		}
		result := retrieval.Result{DatasetVersion: version, Evidence: []catalog.Evidence{evidence}}
		if mode.Load() == 3 {
			result.Evidence = []catalog.Evidence{}
		}
		json.NewEncoder(w).Encode(result)
	}))
	defer upstream.Close()
	client, err := retrieval.New(upstream.URL, dataset)
	if err != nil {
		t.Fatal(err)
	}
	defer client.CloseIdleConnections()
	var logs bytes.Buffer
	h := New(dataset, slog.New(slog.NewJSONHandler(&logs, nil)), Settings{PublicBaseURL: browserOrigin}, client)
	current, cookie, _ := openSession(t, h, "aic-28560", nil)
	first := conversation.Submission{ClientMessageID: messageID(1), Question: " \nWhat material is this artwork made of?\t", AnswerLanguage: "zh-CN"}
	w := postSubmission(h, strings.ToUpper(current.ConversationID), first, cookie)
	turn := decodeTurn(t, w)
	if lastRequestID.Load() != w.Header().Get("X-Request-ID") {
		t.Fatal("the trusted public request ID was not forwarded to retrieval")
	}
	trusted, _ := dataset.EvidenceByChunkID("aic-28560-medium")
	if turn.Question != strings.TrimSpace(first.Question) || turn.Sequence != 1 || turn.Answer.Mode != "fixture" || turn.Answer.Status != "answered" || len(turn.Answer.Citations) != 1 || turn.Answer.Citations[0] != trusted || turn.AnswerLanguage != "zh-CN" {
		t.Fatal("public turn lost original payload, ordering, language or provenance")
	}
	first.Question = strings.TrimSpace(first.Question)
	replay := postSubmission(h, current.ConversationID, first, cookie)
	decodeTurn(t, replay)
	if !bytes.Equal(replay.Body.Bytes(), w.Body.Bytes()) || calls.Load() != 1 {
		t.Fatal("lost-response replay changed or repeated retrieval")
	}
	changed := first
	changed.Question = "What  material is this artwork made of?"
	requireSessionError(t, postSubmission(h, current.ConversationID, changed, cookie), 409, "idempotency_conflict")
	requireSessionError(t, postSubmission(h, current.ConversationID, first, nil), 404, "conversation_not_found")
	_, foreign, _ := openSession(t, h, "aic-28560", nil)
	requireSessionError(t, postSubmission(h, current.ConversationID, first, foreign), 404, "conversation_not_found")
	if calls.Load() != 1 {
		t.Fatal("rejected requests contacted retrieval")
	}
	mode.Store(1)
	bad := first
	bad.ClientMessageID = messageID(2)
	requireSessionError(t, postSubmission(h, current.ConversationID, bad, cookie), 502, "retrieval_invalid_response")
	mode.Store(2)
	transient := first
	transient.ClientMessageID = messageID(3)
	failure := postSubmission(h, current.ConversationID, transient, cookie)
	if failure.Code != 503 || strings.Contains(failure.Body.String(), "secret-upstream") {
		t.Fatal("upstream failure was not mapped to a safe public error")
	}
	var history conversation.History
	historyReply := sessionCall(h, "GET", "/api/conversations/"+current.ConversationID+"/messages", "", cookie)
	json.Unmarshal(historyReply.Body.Bytes(), &history)
	if len(history.Turns) != 1 || history.PendingRequest.State != "failed" || history.PendingRequest.ClientMessageID != transient.ClientMessageID || !history.PendingRequest.LastError.Retryable {
		t.Fatal("failure did not preserve exactly one completed turn and the failed pending payload")
	}
	requireSessionError(t, postSubmission(h, current.ConversationID, bad, cookie), 409, "request_superseded")
	mode.Store(0)
	if retried := decodeTurn(t, postSubmission(h, current.ConversationID, transient, cookie)); retried.Sequence != 2 {
		t.Fatal("retry did not complete as the next contiguous turn")
	}
	for i, tc := range []struct {
		question, status string
		mode             int32
	}{
		{"unicorn", "insufficient", 3},
		{"Why did the artist choose this material?", "unsupported_question", 0},
		{"How many hours did Van Gogh actually spend painting the Chicago version?", "insufficient", 0},
	} {
		mode.Store(tc.mode)
		input := conversation.Submission{ClientMessageID: messageID(10 + i), Question: tc.question, AnswerLanguage: "en"}
		answer := decodeTurn(t, postSubmission(h, current.ConversationID, input, cookie)).Answer
		if answer.Status != tc.status || answer.Citations == nil || len(answer.Citations) != 0 {
			t.Fatal("answer fallback was confused with a technical failure or retained citations")
		}
	}
	if strings.Contains(logs.String(), cookie.Value) || strings.Contains(logs.String(), first.Question) || strings.Contains(logs.String(), first.ClientMessageID) {
		t.Fatal("visitor content leaked into routine logs")
	}
}

func TestMessagePendingConcurrencyAndCompletedReplay(t *testing.T) {
	dataset := testDataset(t)
	var calls atomic.Int32
	entered, release := make(chan struct{}), make(chan struct{})
	var released sync.Once
	t.Cleanup(func() { released.Do(func() { close(release) }) })
	h := New(dataset, nil, Settings{PublicBaseURL: browserOrigin}, retrievalFunc(func(_ context.Context, request retrieval.Request) (retrieval.Result, error) {
		if calls.Add(1) == 2 {
			close(entered)
			<-release
		}
		evidence, _ := dataset.EvidenceByChunkID(request.ArtworkID + "-medium")
		return retrieval.Result{DatasetVersion: dataset.Version(), Evidence: []catalog.Evidence{evidence}}, nil
	}))
	current, cookie, _ := openSession(t, h, "aic-28560", nil)
	first := conversation.Submission{ClientMessageID: messageID(1), Question: "What material is this artwork made of?", AnswerLanguage: "en"}
	original := postSubmission(h, current.ConversationID, first, cookie)
	decodeTurn(t, original)
	second := first
	second.ClientMessageID = messageID(2)
	done := make(chan *httptest.ResponseRecorder, 1)
	go func() { done <- postSubmission(h, current.ConversationID, second, cookie) }()
	<-entered
	path := "/api/conversations/" + current.ConversationID + "/messages"
	var history conversation.History
	w := sessionCall(h, "GET", path, "", cookie)
	if w.Code != 200 || json.Unmarshal(w.Body.Bytes(), &history) != nil || len(history.Turns) != 1 ||
		history.PendingRequest == nil || history.PendingRequest.State != "processing" || history.PendingRequest.LastError != nil || history.PendingRequest.ClientMessageID != second.ClientMessageID {
		t.Fatal("history did not expose an atomic processing snapshot")
	}
	for _, tc := range []struct {
		input conversation.Submission
		code  string
	}{
		{second, "request_in_progress"},
		{conversation.Submission{ClientMessageID: messageID(3), Question: first.Question, AnswerLanguage: "en"}, "conversation_busy"},
	} {
		w := postSubmission(h, current.ConversationID, tc.input, cookie)
		var body struct {
			Error conversation.Error `json:"error"`
		}
		if w.Code != 409 || json.Unmarshal(w.Body.Bytes(), &body) != nil || body.Error.Code != tc.code || !body.Error.Retryable {
			t.Fatal("active requests did not report retryable conflicts")
		}
	}
	replay := postSubmission(h, current.ConversationID, first, cookie)
	if replay.Code != 200 || !bytes.Equal(replay.Body.Bytes(), original.Body.Bytes()) || calls.Load() != 2 {
		t.Fatal("a completed replay was blocked by the newer active request")
	}
	released.Do(func() { close(release) })
	if turn := decodeTurn(t, <-done); turn.Sequence != 2 {
		t.Fatal("concurrent handling appended a duplicate or skipped sequence")
	}
	w = sessionCall(h, "GET", path, "", cookie)
	json.Unmarshal(w.Body.Bytes(), &history)
	if len(history.Turns) != 2 || history.PendingRequest != nil || calls.Load() != 2 {
		t.Fatal("final history retained processing state or duplicated downstream work")
	}
}

func TestMessageValidationBeforeReservation(t *testing.T) {
	dataset := testDataset(t)
	var calls atomic.Int32
	h := New(dataset, nil, Settings{PublicBaseURL: browserOrigin}, retrievalFunc(func(context.Context, retrieval.Request) (retrieval.Result, error) {
		calls.Add(1)
		return retrieval.Result{DatasetVersion: dataset.Version(), Evidence: []catalog.Evidence{}}, nil
	}))
	current, cookie, _ := openSession(t, h, "aic-28560", nil)
	path := "/api/conversations/" + current.ConversationID + "/messages"
	for _, mutate := range []func(map[string]any){
		func(m map[string]any) { delete(m, "answer_language") },
		func(m map[string]any) { m["question"] = nil },
		func(m map[string]any) { m["client_message_id"] = 17 },
		func(m map[string]any) { m["client_message_id"] = "not-a-uuid" },
		func(m map[string]any) { m["question"] = " \t\r\n " },
		func(m map[string]any) { m["question"] = strings.Repeat("画", 501) },
		func(m map[string]any) { m["question"] = "text\x00control" },
		func(m map[string]any) { m["question"] = "text\u0085control" },
		func(m map[string]any) { m["answer_language"] = "zh" },
		func(m map[string]any) { m["artwork_id"] = "aic-11723" },
		func(m map[string]any) { m["dataset_version"] = "other" },
		func(m map[string]any) { m["visitor_id"] = "untrusted" },
	} {
		body := map[string]any{"client_message_id": messageID(1), "question": "valid question", "answer_language": "en"}
		mutate(body)
		encoded, _ := json.Marshal(body)
		requireSessionError(t, sessionCall(h, "POST", path, string(encoded), cookie), 400, "invalid_request")
	}
	request := conversation.Submission{ClientMessageID: messageID(1), Question: "valid question", AnswerLanguage: "en"}
	requireSessionError(t, postSubmission(h, "not-a-uuid", request, cookie), 400, "invalid_request")
	r := httptest.NewRequest("POST", path, strings.NewReader(`{}`))
	r.Header.Set("Content-Type", "application/json")
	r.AddCookie(cookie)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	requireSessionError(t, w, 403, "origin_not_allowed")
	if calls.Load() != 0 {
		t.Fatal("invalid submissions started downstream work")
	}
	request.Question = strings.Repeat("画", 500)
	turn := decodeTurn(t, postSubmission(h, current.ConversationID, request, cookie))
	if turn.Question != request.Question || turn.Sequence != 1 {
		t.Fatal("Unicode code point limit or rejected-key handling is incorrect")
	}
}

func TestMessageExpiresDuringRetrieval(t *testing.T) {
	dataset := testDataset(t)
	var clock atomic.Int64
	clock.Store(time.Date(2026, 11, 10, 0, 0, 0, 0, time.UTC).UnixNano())
	store := conversation.New(func() time.Time { return time.Unix(0, clock.Load()).UTC() })
	entered, release := make(chan struct{}), make(chan struct{})
	var released sync.Once
	t.Cleanup(func() { released.Do(func() { close(release) }) })
	retriever := retrievalFunc(func(context.Context, retrieval.Request) (retrieval.Result, error) {
		close(entered)
		<-release
		evidence, _ := dataset.EvidenceByChunkID("aic-28560-medium")
		return retrieval.Result{DatasetVersion: dataset.Version(), Evidence: []catalog.Evidence{evidence}}, nil
	})
	h := New(dataset, nil, Settings{PublicBaseURL: browserOrigin}, retriever).(*handler)
	h.sessions = store
	h.questions = chat.New(store, dataset, retriever, fixture.Adapter{}, nil)
	current, cookie, _ := openSession(t, h, "aic-28560", nil)
	done := make(chan *httptest.ResponseRecorder, 1)
	go func() {
		done <- postSubmission(h, current.ConversationID, conversation.Submission{
			ClientMessageID: messageID(1), Question: "What material is this artwork made of?", AnswerLanguage: "en",
		}, cookie)
	}()
	<-entered
	clock.Store(current.ExpiresAt.UnixNano())
	released.Do(func() { close(release) })
	requireSessionError(t, <-done, 404, "conversation_not_found")
	path := "/api/conversations/" + current.ConversationID + "/messages"
	requireSessionError(t, sessionCall(h, "GET", path, "", cookie), 404, "conversation_not_found")
}
