package agentcontract

import (
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"
)

func fixture(t *testing.T, name string, destination any) {
	t.Helper()
	for _, c := range examples(t) {
		if c.Name == name {
			if err := json.Unmarshal(c.Payload, destination); err != nil {
				t.Fatal(err)
			}
			return
		}
	}
	t.Fatal("missing fixture", name)
}

func runtimeCases(t *testing.T, kind string) []json.RawMessage {
	t.Helper()
	data, err := os.ReadFile("../../../docs/contracts/agent-v2.examples.json")
	if err != nil {
		t.Fatal(err)
	}
	var document struct {
		Cases []json.RawMessage `json:"runtime_cases"`
	}
	if err := json.Unmarshal(data, &document); err != nil {
		t.Fatal(err)
	}
	var selected []json.RawMessage
	for _, data := range document.Cases {
		var c struct {
			Kind string `json:"kind"`
		}
		_ = json.Unmarshal(data, &c)
		if c.Kind == kind {
			selected = append(selected, data)
		}
	}
	return selected
}

func TestSharedLanguageResolution(t *testing.T) {
	v := validator(t)
	for _, data := range runtimeCases(t, "language_resolution") {
		var c struct {
			Name    string `json:"name"`
			Valid   bool   `json:"valid"`
			Payload struct {
				Question string              `json:"question"`
				Override *LanguagePreference `json:"language_override"`
				Context  ConversationContext `json:"conversation_context"`
				Decision LanguageDecision    `json:"language_decision"`
				Plan     struct {
					Decision LanguageDecision `json:"language_decision"`
				} `json:"plan"`
			} `json:"payload"`
			Expected LanguageResolution `json:"expected"`
		}
		if err := json.Unmarshal(data, &c); err != nil {
			t.Fatal(err)
		}
		t.Run(c.Name, func(t *testing.T) {
			p := c.Payload
			if p.Decision.AnswerLanguage == "" {
				p.Decision = p.Plan.Decision
			}
			actual, err := v.ResolveLanguage(p.Question, p.Override, p.Context.LanguagePreference, p.Context.AutoLanguage, p.Decision)
			if (err == nil) != c.Valid || (err == nil && actual != c.Expected) {
				t.Fatalf("got %+v %v, expected %+v valid=%v", actual, err, c.Expected, c.Valid)
			}
		})
	}
}

func TestSharedStatusDerivation(t *testing.T) {
	v := validator(t)
	for _, data := range runtimeCases(t, "status_derivation") {
		var c struct {
			Name    string `json:"name"`
			Valid   bool   `json:"valid"`
			Payload struct {
				Requests []InterpretedRequest `json:"requests"`
				Results  []RequestResult      `json:"results"`
			} `json:"payload"`
			Expected struct {
				Status string `json:"status"`
			} `json:"expected"`
		}
		if err := json.Unmarshal(data, &c); err != nil {
			t.Fatal(err)
		}
		t.Run(c.Name, func(t *testing.T) {
			actual, err := v.DeriveStatus(c.Payload.Requests, c.Payload.Results)
			if (err == nil) != c.Valid || (err == nil && actual != c.Expected.Status) {
				t.Fatalf("got %s %v, expected %s valid=%v", actual, err, c.Expected.Status, c.Valid)
			}
		})
	}
}

func TestSharedHistoryWindow(t *testing.T) {
	v := validator(t)
	for _, data := range runtimeCases(t, "history_window") {
		var c struct {
			Name    string `json:"name"`
			Valid   bool   `json:"valid"`
			Payload struct {
				Revision int64         `json:"conversation_revision"`
				Turns    []HistoryTurn `json:"recent_turns"`
				Last     *Language     `json:"state_last_answer_language"`
			} `json:"payload"`
		}
		if err := json.Unmarshal(data, &c); err != nil {
			t.Fatal(err)
		}
		t.Run(c.Name, func(t *testing.T) {
			var run RunRequest
			fixture(t, "run_request_initial", &run)
			run.ConversationState.Revision = c.Payload.Revision
			run.Context.RecentTurns = c.Payload.Turns
			if len(c.Payload.Turns) > 0 {
				language := c.Payload.Turns[len(c.Payload.Turns)-1].AnswerLanguage
				run.ConversationState.LastAnswerLanguage = &language
			}
			if c.Payload.Last != nil {
				run.ConversationState.LastAnswerLanguage = c.Payload.Last
			}
			if err := v.CheckRunRequest(run); (err == nil) != c.Valid {
				t.Fatalf("valid=%v: %v", c.Valid, err)
			}
		})
	}
}

func TestResultBindingRejectsTamperingWithoutMutation(t *testing.T) {
	v := validator(t)
	var run RunRequest
	fixture(t, "run_request_initial", &run)
	var original RunResult
	fixture(t, "run_result_mixed", &original)
	before, _ := json.Marshal(original)
	if err := v.CheckRunResult(run, original); err != nil {
		t.Fatal(err)
	}
	for name, change := range map[string]func(*RunResult){
		"scope":    func(r *RunResult) { r.Scope.ArtworkID = "aic-11723" },
		"revision": func(r *RunResult) { r.ConversationUpdate.BaseRevision++ },
		"language": func(r *RunResult) { r.AnswerLanguage = "ko" },
		"catalog":  func(r *RunResult) { r.CatalogSnapshot[0].Value = "Forged title" },
		"citation": func(r *RunResult) { r.RequestResults[1].CitationIDs[0] = "not-retrieved" },
		"text":     func(r *RunResult) { r.Answer.Text = "Altered text" },
		"status":   func(r *RunResult) { r.Answer.Status = "partially_answered" },
		"count":    func(r *RunResult) { r.Execution.RetrievalCalls++ },
		"query":    func(r *RunResult) { r.ExecutedQueries[0].RequestIDs[0] = "r1" },
		"snapshot": func(r *RunResult) { r.Answer.Citations[0].Excerpt.Value = "Forged excerpt" },
		"pending": func(r *RunResult) {
			r.ConversationUpdate.Memory.PendingUpdate = PendingUpdate{Action: "set", Source: Some("current_message"), RequestID: Some[*RequestID](nil)}
		},
	} {
		t.Run(name, func(t *testing.T) {
			var changed RunResult
			_ = json.Unmarshal(before, &changed)
			change(&changed)
			if err := v.CheckRunResult(run, changed); err == nil {
				t.Fatal("accepted inconsistent result")
			}
		})
	}
	after, _ := json.Marshal(original)
	if string(before) != string(after) {
		t.Fatal("mutated caller data")
	}
}

func TestCatalogAndLocalClarificationBindings(t *testing.T) {
	v := validator(t)
	for _, name := range []string{"catalog_only_run_result", "partial_answer_with_local_clarification_result"} {
		var run RunRequest
		fixture(t, "run_request_initial", &run)
		var result RunResult
		fixture(t, name, &result)
		fragments := []string{}
		for _, request := range result.Requests {
			fragments = append(fragments, request.OriginalFragment)
		}
		run.Question = strings.Join(fragments, " ")
		if err := v.CheckRunResult(run, result); err != nil {
			t.Fatalf("%s: %v", name, err)
		}
	}
}

func TestAllErrorMappingsAreSafeAndConsistent(t *testing.T) {
	for code, policy := range errorPolicies {
		upstream := AgentError{Code: code, Message: "private payload should never escape", Retryable: policy.Retryable, Phase: "admission"}
		mapped := MapAgentFailure(policy.PrivateStatus, upstream)
		if mapped.Status != policy.PublicStatus || mapped.Error.Code != policy.Code || mapped.Error.Retryable != policy.Retryable || mapped.Reconcile != (code == "agent_run_in_progress") {
			t.Fatalf("%s: %+v", code, mapped)
		}
		if strings.Contains(mapped.Error.Message, "private payload") {
			t.Fatal("leaked error text")
		}
		if MapAgentFailure(418, upstream).Error.Code != "agent_invalid_response" {
			t.Fatal("accepted invalid HTTP/code pair")
		}
	}
}

func TestUnicodeLimitsDepthAndNullableUnion(t *testing.T) {
	v := validator(t)
	for length, valid := range map[int]bool{500: true, 501: false} {
		raw := strings.Replace(message, "你好 한국어", strings.Repeat("韩", length), 1)
		if err := v.Validate("PublicMessageRequest", []byte(raw)); (err == nil) != valid {
			t.Fatalf("Unicode length %d: %v", length, err)
		}
	}
	for _, raw := range []string{`{"status":"ok","extra":` + strings.Repeat("[", 65) + `0` + strings.Repeat("]", 65) + `}`, `{"status":"ok","extra":1e309}`} {
		if v.Validate("HealthResponse", []byte(raw)) == nil {
			t.Fatal("accepted invalid JSON metadata")
		}
	}
	var pending PendingUpdate
	if err := v.Decode("PendingUpdate", []byte(`{"action":"set","source":"current_message","request_id":null}`), &pending); err != nil {
		t.Fatal(err)
	}
	if !pending.RequestID.Present || pending.RequestID.Value != nil {
		t.Fatal("lost required null")
	}
	encoded, err := v.Encode("PendingUpdate", pending)
	if err != nil || !strings.Contains(string(encoded), `"request_id":null`) {
		t.Fatal("lost explicit null", err)
	}
	if !reflect.DeepEqual(pending, PendingUpdate{Action: "set", Source: Some("current_message"), RequestID: Some[*RequestID](nil)}) {
		t.Fatal("changed optional union")
	}
}
