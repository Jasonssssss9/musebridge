package httpapi

import (
	"bytes"
	"encoding/json"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/Jasonssssss9/musebridge/api/internal/conversation"
)

const browserOrigin = "http://localhost:8080"

func sessionCall(h http.Handler, method, path, body string, cookies ...*http.Cookie) *httptest.ResponseRecorder {
	r := httptest.NewRequest(method, path, strings.NewReader(body))
	if method == http.MethodPost {
		r.Header.Set("Origin", browserOrigin)
		r.Header.Set("Content-Type", "application/json")
	}
	for _, cookie := range cookies {
		r.AddCookie(cookie)
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	return w
}

func openSession(t *testing.T, h http.Handler, artworkID string, cookie *http.Cookie) (conversation.Conversation, *http.Cookie, bool) {
	t.Helper()
	body, _ := json.Marshal(map[string]string{"artwork_id": artworkID})
	var cookies []*http.Cookie
	if cookie != nil {
		cookies = append(cookies, cookie)
	}
	w := sessionCall(h, "POST", "/api/conversations", string(body), cookies...)
	var result struct {
		Conversation conversation.Conversation `json:"conversation"`
		Created      bool                      `json:"created"`
	}
	if w.Code != 200 || json.Unmarshal(w.Body.Bytes(), &result) != nil {
		t.Fatalf("conversation creation failed: %d %s", w.Code, w.Body.String())
	}
	if len(w.Result().Cookies()) != 1 {
		t.Fatal("creation must establish or refresh exactly one visitor cookie")
	}
	value := w.Result().Cookies()[0]
	if value.Name != visitorCookieName || value.Value == "" || strings.Contains(w.Body.String(), value.Value) {
		t.Fatal("visitor identity is missing or leaked into public metadata")
	}
	return result.Conversation, value, result.Created
}

func requireSessionError(t *testing.T, w *httptest.ResponseRecorder, status int, code string) apiError {
	t.Helper()
	var body struct {
		RequestID string   `json:"request_id"`
		Error     apiError `json:"error"`
	}
	if w.Code != status || json.Unmarshal(w.Body.Bytes(), &body) != nil || body.Error.Code != code ||
		body.Error.Retryable || body.RequestID == "" || body.RequestID != w.Header().Get("X-Request-ID") {
		t.Fatalf("incorrect session error: %d %s", w.Code, w.Body.String())
	}
	if w.Header().Get("Set-Cookie") != "" || w.Header().Get("Cache-Control") != "no-store" {
		t.Fatal("an error renewed identity or allowed caching")
	}
	return body.Error
}

func TestConversationBrowserFlow(t *testing.T) {
	var logs bytes.Buffer
	h := New(testDataset(t), slog.New(slog.NewJSONHandler(&logs, nil)), Settings{PublicBaseURL: browserOrigin}, nil)
	first, cookie, created := openSession(t, h, "aic-28560", nil)
	if !created || first.ArtworkID != "aic-28560" || first.DatasetVersion != "aic-seed-v0.1" || first.ConversationID == "" {
		t.Fatal("new conversation has incorrect metadata")
	}
	if cookie.Path != "/" || !cookie.HttpOnly || cookie.SameSite != http.SameSiteLaxMode ||
		cookie.Secure || cookie.Domain != "" || cookie.MaxAge != 86400 {
		t.Fatal("local visitor cookie does not match the browser contract")
	}
	restored, renewed, created := openSession(t, h, first.ArtworkID, cookie)
	if created || restored != first || renewed.Value != cookie.Value {
		t.Fatal("refresh did not restore the original conversation and identity")
	}
	other, _, created := openSession(t, h, "aic-11723", cookie)
	if !created || other.ConversationID == first.ConversationID {
		t.Fatal("switching artwork did not create a separate conversation")
	}
	restored, _, created = openSession(t, h, first.ArtworkID, cookie)
	if created || restored != first {
		t.Fatal("returning to the first artwork lost its conversation")
	}
	w := sessionCall(h, "GET", "/api/conversations/"+strings.ToUpper(first.ConversationID)+"/messages", "", cookie)
	var history conversation.History
	if w.Code != 200 || json.Unmarshal(w.Body.Bytes(), &history) != nil || history.Conversation != first ||
		history.Turns == nil || len(history.Turns) != 0 || history.PendingRequest != nil {
		t.Fatalf("history does not match the browser contract: %s", w.Body.String())
	}
	if !strings.Contains(w.Body.String(), `"pending_request":null`) || w.Header().Get("Set-Cookie") != "" {
		t.Fatal("history omitted nullable state or renewed identity")
	}
	if strings.Contains(logs.String(), cookie.Value) || strings.Contains(logs.String(), first.ConversationID) {
		t.Fatal("visitor or conversation identifiers leaked into routine logs")
	}
}

func TestConversationOwnershipAndRestart(t *testing.T) {
	h := New(testDataset(t), nil, Settings{PublicBaseURL: browserOrigin}, nil)
	current, owner, _ := openSession(t, h, "aic-28560", nil)
	_, otherVisitor, _ := openSession(t, h, "aic-28560", nil)
	path := "/api/conversations/" + current.ConversationID + "/messages"
	var expected apiError
	for _, cookies := range [][]*http.Cookie{
		nil, {otherVisitor}, {{Name: visitorCookieName, Value: "malformed"}}, {owner, otherVisitor},
	} {
		err := requireSessionError(t, sessionCall(h, "GET", path, "", cookies...), 404, "conversation_not_found")
		if expected.Code != "" && err != expected {
			t.Fatal("unavailable conversation conditions disclosed different information")
		}
		expected = err
	}
	unknown := "/api/conversations/00000000-0000-0000-0000-000000000000/messages"
	if err := requireSessionError(t, sessionCall(h, "GET", unknown, "", owner), 404, "conversation_not_found"); err != expected {
		t.Fatal("unknown conversation has a distinguishable error")
	}
	restarted := New(testDataset(t), nil, Settings{PublicBaseURL: browserOrigin}, nil)
	requireSessionError(t, sessionCall(restarted, "GET", path, "", owner), 404, "conversation_not_found")
	next, nextCookie, created := openSession(t, restarted, current.ArtworkID, owner)
	if !created || next.ConversationID == current.ConversationID || nextCookie.Value == owner.Value {
		t.Fatal("server restart did not create new anonymous state")
	}
}

func TestConversationExpiry(t *testing.T) {
	now := time.Date(2026, 11, 10, 0, 0, 0, 0, time.UTC)
	h := New(testDataset(t), nil, Settings{PublicBaseURL: browserOrigin}, nil).(*handler)
	h.sessions = conversation.New(func() time.Time { return now })
	current, cookie, _ := openSession(t, h, "aic-28560", nil)
	now = now.Add(23 * time.Hour)
	restored, _, _ := openSession(t, h, current.ArtworkID, cookie)
	if restored != current {
		t.Fatal("cookie renewal extended the conversation lifetime")
	}
	now = current.ExpiresAt
	path := "/api/conversations/" + current.ConversationID + "/messages"
	requireSessionError(t, sessionCall(h, "GET", path, "", cookie), 404, "conversation_not_found")
	next, renewed, created := openSession(t, h, current.ArtworkID, cookie)
	if !created || renewed.Value != cookie.Value || next.ConversationID == current.ConversationID {
		t.Fatal("expired conversation was not replaced for the current visitor")
	}
}

func TestConversationOriginsAndSecureCookie(t *testing.T) {
	h := New(testDataset(t), nil, Settings{PublicBaseURL: browserOrigin}, nil)
	for _, origin := range []string{"", "null", "http://127.0.0.1:8080", browserOrigin + "/", "https://localhost:8080", "http://localhost:8081"} {
		r := httptest.NewRequest("POST", "/api/conversations", strings.NewReader(`{"artwork_id":"aic-28560"}`))
		if origin != "" {
			r.Header.Set("Origin", origin)
		}
		r.Header.Set("Content-Type", "application/json")
		r.Header.Set("X-Forwarded-Host", "localhost:8080")
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		requireSessionError(t, w, 403, "origin_not_allowed")
	}
	for _, emptySettings := range []bool{false, true} {
		r := httptest.NewRequest("POST", "/api/conversations", strings.NewReader(`{"artwork_id":"aic-28560"}`))
		if emptySettings {
			h = New(testDataset(t), nil, Settings{}, nil)
			r.Header.Set("Origin", "")
		} else {
			r.Header.Add("Origin", browserOrigin)
			r.Header.Add("Origin", browserOrigin)
		}
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		requireSessionError(t, w, 403, "origin_not_allowed")
	}
	secureOrigin := "https://museum.example:9443"
	h = New(testDataset(t), nil, Settings{PublicBaseURL: secureOrigin, CookieSecure: true}, nil)
	r := httptest.NewRequest("POST", "/api/conversations", strings.NewReader(`{"artwork_id":"aic-28560"}`))
	r.Header.Set("Origin", secureOrigin)
	r.Header.Set("Content-Type", "application/json; charset=UTF-8")
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Code != 200 || len(w.Result().Cookies()) != 1 || !w.Result().Cookies()[0].Secure {
		t.Fatal("the configured HTTPS profile did not set a Secure cookie")
	}
}

func TestConversationRequestValidation(t *testing.T) {
	h := New(testDataset(t), nil, Settings{PublicBaseURL: browserOrigin}, nil)
	for _, tc := range []struct {
		name, body, contentType, code string
		status                        int
	}{
		{"empty", "", "application/json", "invalid_request", 400},
		{"broken JSON", "{", "application/json", "invalid_request", 400},
		{"array", `[]`, "application/json", "invalid_request", 400},
		{"null", `null`, "application/json", "invalid_request", 400},
		{"missing field", `{}`, "application/json", "invalid_request", 400},
		{"null field", `{"artwork_id":null}`, "application/json", "invalid_request", 400},
		{"numeric field", `{"artwork_id":28560}`, "application/json", "invalid_request", 400},
		{"blank field", `{"artwork_id":"  "}`, "application/json", "invalid_request", 400},
		{"wrong case", `{"ARTWORK_ID":"aic-28560"}`, "application/json", "invalid_request", 400},
		{"unknown field", `{"artwork_id":"aic-28560","visitor_id":"untrusted"}`, "application/json", "invalid_request", 400},
		{"version override", `{"artwork_id":"aic-28560","dataset_version":"override"}`, "application/json", "invalid_request", 400},
		{"duplicate field", `{"artwork_id":"aic-28560","artwork_id":"aic-11723"}`, "application/json", "invalid_request", 400},
		{"trailing JSON", `{"artwork_id":"aic-28560"}{}`, "application/json", "invalid_request", 400},
		{"bad UTF-8", "{\"artwork_id\":\"\xff\"}", "application/json", "invalid_request", 400},
		{"unpaired surrogate", `{"artwork_id":"\ud800"}`, "application/json", "invalid_request", 400},
		{"unknown artwork", `{"artwork_id":"missing"}`, "application/json", "artwork_not_found", 404},
		{"missing content type", `{"artwork_id":"aic-28560"}`, "", "unsupported_media_type", 415},
		{"text body", `{"artwork_id":"aic-28560"}`, "text/plain", "unsupported_media_type", 415},
		{"wrong charset", `{"artwork_id":"aic-28560"}`, "application/json; charset=latin1", "unsupported_media_type", 415},
		{"oversize", strings.Repeat(" ", maxPostBytes+1), "application/json", "payload_too_large", 413},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := httptest.NewRequest("POST", "/api/conversations", strings.NewReader(tc.body))
			r.Header.Set("Origin", browserOrigin)
			if tc.contentType != "" {
				r.Header.Set("Content-Type", tc.contentType)
			}
			// Unknown-length requests must obey the same bound as fixed-length bodies.
			r.ContentLength = -1
			w := httptest.NewRecorder()
			h.ServeHTTP(w, r)
			requireSessionError(t, w, tc.status, tc.code)
		})
	}
	body := `{"artwork_id":"aic-28560"}`
	body += strings.Repeat(" ", maxPostBytes-len(body))
	if w := sessionCall(h, "POST", "/api/conversations", body); w.Code != 200 {
		t.Fatal("an exactly 16 KiB valid body was rejected")
	}
}

func TestConversationRouteBoundaries(t *testing.T) {
	h := New(testDataset(t), nil, Settings{PublicBaseURL: browserOrigin}, nil)
	current, cookie, _ := openSession(t, h, "aic-28560", nil)
	path := "/api/conversations/" + current.ConversationID + "/messages"
	for _, tc := range []struct {
		method, path, code, allow string
		status                    int
	}{
		{"GET", "/api/conversations", "method_not_allowed", "POST", 405},
		{"OPTIONS", "/api/conversations", "method_not_allowed", "POST", 405},
		{"DELETE", path, "method_not_allowed", "GET, POST", 405},
		{"GET", "/api/conversations/not-a-uuid/messages", "invalid_request", "", 400},
		{"GET", path + "?debug=true", "invalid_request", "", 400},
		{"POST", "/api/conversations?", "invalid_request", "", 400},
		{"GET", path + "/extra", "route_not_found", "", 404},
	} {
		w := sessionCall(h, tc.method, tc.path, "", cookie)
		requireSessionError(t, w, tc.status, tc.code)
		if w.Header().Get("Allow") != tc.allow {
			t.Fatal("method boundary has an incorrect Allow header")
		}
	}
	if w := sessionCall(h, "HEAD", path, "", cookie); w.Code != 405 || w.Body.Len() != 0 || w.Header().Get("Content-Length") == "" {
		t.Fatal("HEAD did not retain error headers while omitting the body")
	}
	unready := New(nil, nil, Settings{PublicBaseURL: browserOrigin}, nil)
	w := sessionCall(unready, "POST", "/api/conversations", `{"artwork_id":"aic-28560"}`)
	if w.Code != 503 || w.Header().Get("Set-Cookie") != "" {
		t.Fatal("uninitialized local state created a conversation")
	}
}

func TestConcurrentConversationCreation(t *testing.T) {
	h := New(testDataset(t), nil, Settings{PublicBaseURL: browserOrigin}, nil)
	_, cookie, _ := openSession(t, h, "aic-28560", nil)
	var createdCount atomic.Int32
	var wait sync.WaitGroup
	ids := make(chan string, 32)
	for range cap(ids) {
		wait.Add(1)
		go func() {
			defer wait.Done()
			w := sessionCall(h, "POST", "/api/conversations", `{"artwork_id":"aic-11723"}`, cookie)
			var result struct {
				Conversation conversation.Conversation `json:"conversation"`
				Created      bool                      `json:"created"`
			}
			if w.Code != 200 || json.Unmarshal(w.Body.Bytes(), &result) != nil || len(w.Result().Cookies()) != 1 {
				t.Errorf("concurrent creation failed: %d %s", w.Code, w.Body.String())
				return
			}
			if result.Created {
				createdCount.Add(1)
			}
			if w.Result().Cookies()[0].Value != cookie.Value {
				t.Error("concurrent creation rotated an established identity")
			}
			ids <- result.Conversation.ConversationID
		}()
	}
	wait.Wait()
	close(ids)
	first := <-ids
	for id := range ids {
		if id != first {
			t.Fatal("concurrent requests created multiple conversations for one scope")
		}
	}
	if createdCount.Load() != 1 {
		t.Fatal("exactly one creation response must report created=true")
	}
}
