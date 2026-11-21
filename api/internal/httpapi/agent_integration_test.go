//go:build integration

package httpapi

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	ac "github.com/Jasonssssss9/musebridge/api/internal/agentcontract"
	m1test "github.com/Jasonssssss9/musebridge/api/internal/testsupport/m1services"
)

// The provider alone is scripted. Every public/private/retrieval hop uses real
// loopback HTTP, cookies, authentication and production workflow validators.
func TestM2B3PublicAgentIntegration(t *testing.T) {
	for _, fault := range []string{"none", "foreign-evidence"} {
		t.Run(fault, func(t *testing.T) {
			services := m1test.Start(t, fault)
			h, err := NewAgent(testDataset(t), nil, Settings{PublicBaseURL: browserOrigin}, services.Client)
			if err != nil {
				t.Fatal(err)
			}
			server := httptest.NewServer(h)
			defer server.Close()
			jar, _ := cookiejar.New(nil)
			browser := &http.Client{Jar: jar, Timeout: 65 * time.Second}
			call := func(method, path string, payload any) ([]byte, int, string) {
				t.Helper()
				var body io.Reader
				if payload != nil {
					data, err := json.Marshal(payload)
					if err != nil {
						t.Fatal(err)
					}
					body = bytes.NewReader(data)
				}
				r, err := http.NewRequest(method, server.URL+path, body)
				if err != nil {
					t.Fatal(err)
				}
				if method == "POST" {
					r.Header.Set("Origin", browserOrigin)
					r.Header.Set("Content-Type", "application/json")
				}
				r.Header.Set("X-Request-ID", "untrusted-browser-trace")
				response, err := browser.Do(r)
				if err != nil {
					t.Fatal("public HTTP transport failed", err)
				}
				defer response.Body.Close()
				data, err := io.ReadAll(io.LimitReader(response.Body, 1<<20))
				if err != nil {
					t.Fatal(err)
				}
				if response.Header.Get("Cache-Control") != "no-store" {
					t.Fatal("public response permits caching")
				}
				return data, response.StatusCode, response.Header.Get("X-Request-ID")
			}
			validator, _ := ac.DefaultValidator()
			conversations := map[string]ac.PublicConversation{}
			for n, artwork := range []string{"aic-28560", "aic-11723"} {
				data, status, _ := call("GET", "/api/artworks/"+artwork, nil)
				if status != 200 || !bytes.Contains(data, []byte(artwork)) {
					t.Fatal("artwork route unavailable")
				}
				data, status, _ = call("POST", "/api/conversations", ac.PublicCreateConversationRequest{ContractVersion: ac.PublicVersion, ArtworkID: artwork})
				var opened ac.PublicConversationResponse
				if status != 200 || validator.Decode("PublicConversationResponse", data, &opened) != nil || !opened.Created {
					t.Fatal("public creation failed", string(data))
				}
				conversations[artwork] = opened.Conversation
				path := "/api/conversations/" + string(opened.Conversation.ConversationID) + "/messages"
				input := agentMessage(n+1, 0)
				input.Question = "What material is this painting made of?"
				data, status, trace := call("POST", path, input)
				if trace == "" || trace == "untrusted-browser-trace" {
					t.Fatal("untrusted public trace reached private services")
				}
				models, lookups := 0, 0
				for _, event := range services.Audit(t) {
					if event.RequestID != trace {
						continue
					}
					if event.ArtworkID != artwork {
						t.Fatal("trace crossed artworks")
					}
					if event.Event == "model_call" {
						models++
					}
					if event.Event == "retrieval_http_response" {
						lookups++
					}
				}
				if lookups != 1 {
					t.Fatal("public run did not perform one actual HTTP lookup")
				}
				if fault == "none" {
					var result ac.PublicSubmissionResponse
					if status != 200 || validator.Decode("PublicSubmissionResponse", data, &result) != nil || result.Conversation.Revision != 1 ||
						result.Turn.Answer.Mode != "llm" || len(result.Turn.Answer.Citations) != 1 || result.Turn.Answer.Citations[0].ChunkID != ac.Some(artwork+"-medium") || models != 2 {
						t.Fatal("invalid public Agent answer", string(data))
					}
					replayed, replayStatus, _ := call("POST", path, input)
					if replayStatus != 200 || !bytes.Equal(data, replayed) {
						t.Fatal("public replay did not preserve original turn")
					}
					stale := agentMessage(n+11, 0)
					conflict, conflictStatus, _ := call("POST", path, stale)
					if conflictStatus != 409 || !bytes.Contains(conflict, []byte("conversation_state_conflict")) {
						t.Fatal("stale public submission admitted")
					}
				} else if status != 502 || !bytes.Contains(data, []byte("retrieval_invalid_response")) || models != 1 {
					t.Fatal("foreign evidence was not rejected", string(data))
				}
				data, status, _ = call("GET", path, nil)
				var history ac.PublicHistoryResponse
				if status != 200 || validator.Decode("PublicHistoryResponse", data, &history) != nil {
					t.Fatal("public history failed")
				}
				want := int64(1)
				if fault != "none" {
					want = 0
				}
				if history.Conversation.Revision != want || int64(len(history.Turns)) != want {
					t.Fatal("invalid public atomic save")
				}
				if fault != "none" && (history.PendingRequest == nil || history.PendingRequest.State != "failed") {
					t.Fatal("failed attempt missing from recovery history")
				}
				data, status, _ = call("POST", "/api/conversations", ac.PublicCreateConversationRequest{ContractVersion: ac.PublicVersion, ArtworkID: artwork})
				var restored ac.PublicConversationResponse
				if status != 200 || validator.Decode("PublicConversationResponse", data, &restored) != nil || restored.Created || restored.Conversation.ConversationID != opened.Conversation.ConversationID || restored.Conversation.Revision != want {
					t.Fatal("restore did not preserve session/revision")
				}
				t.Logf("fault=%s trace=%s artwork=%s expected=%s-medium revision=%d model=%d retrieval=1", fault, trace, artwork, artwork, want, models)
			}
			if conversations["aic-28560"].ConversationID == conversations["aic-11723"].ConversationID {
				t.Fatal("artwork conversations shared an ID")
			}
			for artwork, current := range conversations {
				data, status, _ := call("GET", "/api/conversations/"+string(current.ConversationID)+"/messages", nil)
				if status != 200 || !strings.Contains(string(data), artwork) {
					t.Fatal("switch-back lost the original history")
				}
			}
			totalModels := 0
			for _, event := range services.Audit(t) {
				if event.Event == "model_call" {
					totalModels++
				}
			}
			wantModels := 4
			if fault != "none" {
				wantModels = 2
			}
			if totalModels != wantModels {
				t.Fatal("replay/restore/stale rejection repeated inference")
			}
		})
	}
}
