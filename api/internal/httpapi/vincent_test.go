package httpapi

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"testing"

	ac "github.com/Jasonssssss9/musebridge/api/internal/agentcontract"
	"github.com/Jasonssssss9/musebridge/api/internal/catalog"
	"github.com/Jasonssssss9/musebridge/api/internal/voice"
)

const irises = "getty-103jnh"

func gettyDataset(t *testing.T) *catalog.Dataset {
	t.Helper()
	dataset, err := catalog.Load("../../../knowledge/data/getty-gallery-v2.json", "getty-gallery-v2")
	if err != nil {
		t.Fatal(err)
	}
	return dataset
}

func vincentExample(t *testing.T, name string) json.RawMessage {
	t.Helper()
	data, err := os.ReadFile("../../../docs/contracts/vincent-examples.json")
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
		t.Fatal("invalid Vincent examples")
	}
	for _, item := range examples.Cases {
		if item.Name == name {
			return item.Payload
		}
	}
	t.Fatalf("missing Vincent example %s", name)
	return nil
}

// v3Agent answers every run with the shared grounded Vincent result, bound to
// the actual run (IDs, scope, revision, cache and question). It records runs.
type v3Agent struct {
	mu       sync.Mutex
	runs     []ac.RunRequest
	template json.RawMessage
	tamper   func(*ac.RunResult)
}

func newV3Agent(t *testing.T) *v3Agent {
	return &v3Agent{template: vincentExample(t, "vincent_completed_run_snapshot")}
}

func (a *v3Agent) Run(_ context.Context, run ac.RunRequest, _ string) (ac.RunSnapshot, error) {
	a.mu.Lock()
	a.runs = append(a.runs, run)
	tamper := a.tamper
	a.mu.Unlock()
	var snapshot ac.RunSnapshot
	if err := json.Unmarshal(a.template, &snapshot); err != nil {
		panic(err)
	}
	snapshot.RunID = run.RunID
	result := snapshot.Result
	result.Scope = run.Scope
	result.CatalogSnapshot = run.Context.CatalogFacts
	result.ConversationUpdate.BaseRevision = run.ConversationState.Revision
	result.Requests[0].OriginalFragment = run.Question
	result.ReusedChunkIDs = []string{}
	for _, cached := range run.ConversationState.VerifiedEvidence {
		result.ReusedChunkIDs = append(result.ReusedChunkIDs, cached.ChunkID)
	}
	if tamper != nil {
		tamper(result)
	}
	return snapshot, nil
}

func (a *v3Agent) Get(context.Context, ac.RunRequest, string) (ac.RunSnapshot, error) {
	return ac.RunSnapshot{}, context.DeadlineExceeded
}

func (a *v3Agent) count() int {
	a.mu.Lock()
	defer a.mu.Unlock()
	return len(a.runs)
}

func (a *v3Agent) last() ac.RunRequest {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.runs[len(a.runs)-1]
}

func newVincentHandler(t *testing.T, agent *v3Agent, enabled bool) *handler {
	t.Helper()
	settings := Settings{PublicBaseURL: browserOrigin}
	if enabled {
		settings.Voice = voice.Config{Enabled: true, WhisperBaseURL: "http://127.0.0.1:1", OpenAIKey: "sk-test-0123456789abcdef",
			SpeechModel: voice.DefaultSpeechModel, SpeechVoice: voice.DefaultSpeechVoice, SpeechDelivery: voice.DefaultDelivery,
			SpeechEndpoint: "http://127.0.0.1:1", SpeechConcurrency: 1}
	}
	h, err := NewAgent(gettyDataset(t), nil, settings, agent)
	if err != nil {
		t.Fatal(err)
	}
	return h.(*handler)
}

func createV04(t *testing.T, h http.Handler, body string, cookie *http.Cookie) (*httptest.ResponseRecorder, ac.PublicConversationResponse, *http.Cookie) {
	t.Helper()
	var cookies []*http.Cookie
	if cookie != nil {
		cookies = append(cookies, cookie)
	}
	w := sessionCall(h, "POST", "/api/conversations", body, cookies...)
	var response ac.PublicConversationResponse
	if w.Code == 200 {
		v3, _ := ac.DefaultValidatorV3()
		if err := v3.Decode("PublicConversationResponse", w.Body.Bytes(), &response); err != nil {
			t.Fatalf("invalid v0.4 projection: %s", w.Body.String())
		}
		if cookie == nil && len(w.Result().Cookies()) == 1 {
			cookie = w.Result().Cookies()[0]
		}
	}
	return w, response, cookie
}

func v04Message(id string, question string, revision int64, override string) string {
	body := map[string]any{"contract_version": ac.PublicVersionV3, "client_message_id": id, "question": question,
		"language_override": nil, "expected_revision": revision}
	if override != "" {
		body["language_override"] = override
	}
	data, _ := json.Marshal(body)
	return string(data)
}

func submitV04(t *testing.T, h http.Handler, conversation ac.UUID, body string, cookie *http.Cookie) (*httptest.ResponseRecorder, ac.PublicSubmissionResponse) {
	t.Helper()
	w := sessionCall(h, "POST", "/api/conversations/"+string(conversation)+"/messages", body, cookie)
	var response ac.PublicSubmissionResponse
	if w.Code == 200 {
		v3, _ := ac.DefaultValidatorV3()
		if err := v3.Decode("PublicSubmissionResponse", w.Body.Bytes(), &response); err != nil {
			t.Fatalf("invalid v0.4 submission: %s", w.Body.String())
		}
	}
	return w, response
}

func historyV04(t *testing.T, h http.Handler, conversation ac.UUID, cookie *http.Cookie) ac.PublicHistoryResponse {
	t.Helper()
	w := sessionCall(h, "GET", "/api/conversations/"+string(conversation)+"/messages", "", cookie)
	var history ac.PublicHistoryResponse
	v3, _ := ac.DefaultValidatorV3()
	if w.Code != 200 || v3.Decode("PublicHistoryResponse", w.Body.Bytes(), &history) != nil {
		t.Fatalf("invalid v0.4 history: %d %s", w.Code, w.Body.String())
	}
	return history
}

func TestVincentModeMatrix(t *testing.T) {
	agent := newV3Agent(t)
	h := newVincentHandler(t, agent, true)

	w, guide, cookie := createV04(t, h, `{"contract_version":"agent-demo-v0.4","artwork_id":"getty-103jnh"}`, nil)
	if w.Code != 200 || !guide.Created || guide.Conversation.ConversationMode.Value != ac.ModeGuide ||
		!guide.Conversation.ReferencePackVersion.Present || guide.Conversation.ReferencePackVersion.Value != nil ||
		guide.Conversation.LanguagePreference != "auto" {
		t.Fatalf("v0.4 default guide: %d %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), `"conversation_mode":"guide","reference_pack_version":null`) {
		t.Fatalf("guide must carry an explicit null pack: %s", w.Body.String())
	}
	w, vincent, _ := createV04(t, h, `{"contract_version":"agent-demo-v0.4","artwork_id":"getty-103jnh","conversation_mode":"vincent"}`, cookie)
	pack := vincent.Conversation.ReferencePackVersion
	if w.Code != 200 || !vincent.Created || vincent.Conversation.ConversationMode.Value != ac.ModeVincent ||
		pack.Value == nil || *pack.Value != ac.VincentPackVersion || vincent.Conversation.LanguagePreference != "en" ||
		vincent.Conversation.AutoLanguage != "en" || vincent.Conversation.LastAnswerLanguage != nil {
		t.Fatalf("Vincent creation: %d %s", w.Code, w.Body.String())
	}
	legacy, _, _ := openAgentSession(t, h, irises, cookie)
	if vincent.Conversation.ConversationID == guide.Conversation.ConversationID || legacy.ConversationID == guide.Conversation.ConversationID ||
		legacy.ConversationID == vincent.Conversation.ConversationID {
		t.Fatal("guide v0.4, Vincent and the v0.3 history must be separate conversations")
	}

	// Reopening restores each conversation by its own key.
	_, again, _ := createV04(t, h, `{"contract_version":"agent-demo-v0.4","artwork_id":"getty-103jnh","conversation_mode":"vincent"}`, cookie)
	_, guideAgain, _ := createV04(t, h, `{"contract_version":"agent-demo-v0.4","artwork_id":"getty-103jnh","conversation_mode":"guide"}`, cookie)
	if again.Created || again.Conversation.ConversationID != vincent.Conversation.ConversationID ||
		guideAgain.Created || guideAgain.Conversation.ConversationID != guide.Conversation.ConversationID {
		t.Fatal("reopening must restore the same conversation for the same mode")
	}

	// Two Vincent turns; the second sees the first in its history window.
	w, first := submitV04(t, h, vincent.Conversation.ConversationID, v04Message(messageID(1), "What color did you call the irises?", 0, ""), cookie)
	if w.Code != 200 || first.Turn.Sequence != 1 || first.Conversation.Revision != 1 || first.Conversation.LastAnswerLanguage == nil ||
		*first.Conversation.LastAnswerLanguage != "en" || first.Conversation.ConversationMode.Value != ac.ModeVincent {
		t.Fatalf("first Vincent turn: %d %s", w.Code, w.Body.String())
	}
	run := agent.last()
	if run.ContractVersion != ac.PrivateVersionV3 || run.Execution.WorkflowID != ac.WorkflowIDV3 ||
		run.ConversationState.StateVersion != ac.StateVersionV3 || ac.ScopeMode(run.Scope) != ac.ModeVincent ||
		!ac.SameScope(run.Scope, ac.ScopeFor(ac.PublicVersionV3, ac.ModeVincent, irises, ac.VincentDatasetVersion)) {
		t.Fatalf("Vincent must run on agent-run-v3 with its scope: %+v", run)
	}
	w, second := submitV04(t, h, vincent.Conversation.ConversationID, v04Message(messageID(2), "Then why do they look blue today?", 1, ""), cookie)
	if w.Code != 200 || second.Turn.Sequence != 2 || agent.count() != 2 {
		t.Fatalf("second Vincent turn: %d %s", w.Code, w.Body.String())
	}
	if run = agent.last(); len(run.Context.RecentTurns) != 1 || run.Context.RecentTurns[0].Question != "What color did you call the irises?" ||
		len(run.ConversationState.VerifiedEvidence) != 1 {
		t.Fatalf("the follow-up must carry Vincent's own history and cache: %+v", run.Context.RecentTurns)
	}

	// Repeating the same logical submission replays the same turn without a run.
	w, replay := submitV04(t, h, vincent.Conversation.ConversationID, v04Message(messageID(2), "Then why do they look blue today?", 1, ""), cookie)
	if w.Code != 200 || replay.Turn.TurnID != second.Turn.TurnID || agent.count() != 2 {
		t.Fatalf("replay: %d %s runs=%d", w.Code, w.Body.String(), agent.count())
	}

	// Each conversation restores only its own history.
	if history := historyV04(t, h, vincent.Conversation.ConversationID, cookie); len(history.Turns) != 2 || history.Conversation.Revision != 2 ||
		history.Turns[1].TurnID != second.Turn.TurnID || history.Conversation.ConversationMode.Value != ac.ModeVincent {
		t.Fatalf("Vincent history: %+v", history)
	}
	if history := historyV04(t, h, guide.Conversation.ConversationID, cookie); len(history.Turns) != 0 || history.Conversation.Revision != 0 {
		t.Fatal("the guide conversation must keep its own empty history")
	}
	if history := agentHistoryResponse(t, h, legacy, cookie); len(history.Turns) != 0 {
		t.Fatal("the v0.3 history must stay separate")
	}

	// A guide v0.4 turn also runs on v3, with the guide scope.
	w, _ = submitV04(t, h, guide.Conversation.ConversationID, v04Message(messageID(3), "What material is it made of?", 0, ""), cookie)
	if w.Code != 200 || ac.ScopeMode(agent.last().Scope) != ac.ModeGuide || agent.last().ContractVersion != ac.PrivateVersionV3 {
		t.Fatalf("guide v0.4 turn: %d %s", w.Code, w.Body.String())
	}

	// Another visitor cannot use either ID.
	_, _, stranger := createV04(t, h, `{"contract_version":"agent-demo-v0.4","artwork_id":"getty-103jnh"}`, nil)
	for _, id := range []ac.UUID{guide.Conversation.ConversationID, vincent.Conversation.ConversationID} {
		requireSessionError(t, sessionCall(h, "GET", "/api/conversations/"+string(id)+"/messages", "", stranger), 404, "conversation_not_found")
	}
}

func TestVincentCreateRules(t *testing.T) {
	h := newVincentHandler(t, newV3Agent(t), true)
	for _, item := range []struct {
		body   string
		status int
		code   string
	}{
		{`{"contract_version":"agent-demo-v0.3","artwork_id":"getty-103jnh","conversation_mode":"vincent"}`, 400, "invalid_request"},
		{`{"contract_version":"agent-demo-v0.4","artwork_id":"getty-103jnh","conversation_mode":null}`, 400, "invalid_request"},
		{`{"contract_version":"agent-demo-v0.4","artwork_id":"getty-103jnh","conversation_mode":"curator"}`, 400, "invalid_request"},
		{`{"contract_version":"agent-demo-v0.4","artwork_id":"getty-103jnh","conversation_mode":"vincent","reference_pack_version":"x"}`, 400, "invalid_request"},
		{`{"contract_version":"agent-demo-v0.4","artwork_id":"getty-missing","conversation_mode":"vincent"}`, 404, "artwork_not_found"},
		{`{"contract_version":"agent-demo-v0.4","artwork_id":"getty-103qt5","conversation_mode":"vincent"}`, 409, "conversation_mode_unavailable"},
		{`{"contract_version":"agent-demo-v0.9","artwork_id":"getty-103jnh","conversation_mode":"vincent"}`, 409, "agent_contract_mismatch"},
		{`{"contract_version":"agent-demo-v0.9","artwork_id":"getty-103jnh"}`, 409, "agent_contract_mismatch"},
	} {
		w, _, _ := createV04(t, h, item.body, nil)
		requireSessionError(t, w, item.status, item.code)
	}
	// v0.3 exact creation still works.
	if current, _, created := openAgentSession(t, h, irises, nil); !created || current.ContractVersion != ac.PublicVersion {
		t.Fatal("v0.3 creation must keep working")
	}

	disabled := newVincentHandler(t, newV3Agent(t), false)
	w, _, _ := createV04(t, disabled, `{"contract_version":"agent-demo-v0.4","artwork_id":"getty-103jnh","conversation_mode":"vincent"}`, nil)
	requireSessionError(t, w, 409, "conversation_mode_unavailable")
	if w, guide, _ := createV04(t, disabled, `{"contract_version":"agent-demo-v0.4","artwork_id":"getty-103jnh"}`, nil); w.Code != 200 || guide.Conversation.ConversationMode.Value != ac.ModeGuide {
		t.Fatal("guide v0.4 does not need the Vincent feature")
	}
}

func TestVincentCatalogAdvertisesModes(t *testing.T) {
	for _, enabled := range []bool{true, false} {
		h := newVincentHandler(t, newV3Agent(t), enabled)
		var list struct {
			Artworks []struct {
				ArtworkID         string   `json:"artwork_id"`
				ConversationModes []string `json:"conversation_modes"`
			} `json:"artworks"`
		}
		w := call(h, "GET", "/api/artworks")
		if w.Code != 200 || json.Unmarshal(w.Body.Bytes(), &list) != nil || len(list.Artworks) != 20 {
			t.Fatalf("catalog: %d %s", w.Code, w.Body.String())
		}
		for _, card := range list.Artworks {
			want := "guide"
			if enabled && card.ArtworkID == irises {
				want = "guide,vincent"
			}
			if strings.Join(card.ConversationModes, ",") != want {
				t.Fatalf("%s modes %v (enabled=%v)", card.ArtworkID, card.ConversationModes, enabled)
			}
		}
		var single struct {
			Artwork struct {
				ConversationModes []string `json:"conversation_modes"`
			} `json:"artwork"`
		}
		w = call(h, "GET", "/api/artworks/"+irises)
		if json.Unmarshal(w.Body.Bytes(), &single) != nil || (enabled && len(single.Artwork.ConversationModes) != 2) {
			t.Fatalf("card: %s", w.Body.String())
		}
	}
	fixture := New(gettyDataset(t), nil, Settings{}, nil)
	if strings.Contains(call(fixture, "GET", "/api/artworks").Body.String(), "conversation_modes") {
		t.Fatal("fixture deployments keep the v0.1 card shape")
	}
}

func TestVincentLanguageAndValidation(t *testing.T) {
	agent := newV3Agent(t)
	h := newVincentHandler(t, agent, true)
	_, vincent, cookie := createV04(t, h, `{"contract_version":"agent-demo-v0.4","artwork_id":"getty-103jnh","conversation_mode":"vincent"}`, nil)
	id := vincent.Conversation.ConversationID

	w, _ := submitV04(t, h, id, v04Message(messageID(1), "¿De qué color eran?", 0, "es"), cookie)
	requireSessionError(t, w, 400, "voice_language_unsupported")
	w, _ = submitV04(t, h, id, v04Message(messageID(2), "Tell me in Klingon", 0, "tlh"), cookie)
	requireSessionError(t, w, 400, "invalid_request")
	if agent.count() != 0 {
		t.Fatal("language errors must be rejected before admission")
	}
	// A v0.3 message cannot enter a v0.4 conversation.
	legacy, _ := json.Marshal(agentMessage(3, 0))
	requireSessionError(t, sessionCall(h, "POST", "/api/conversations/"+string(id)+"/messages", string(legacy), cookie), 409, "agent_contract_mismatch")

	// A foreign or altered source fails validation without a new revision.
	agent.tamper = func(result *ac.RunResult) {
		result.EvidenceSnapshot[0].Excerpt = "Oil on canvas, painted for Theo."
		result.Answer.Citations[0].Excerpt = ac.Some("Oil on canvas, painted for Theo.")
	}
	w, _ = submitV04(t, h, id, v04Message(messageID(4), "What is it painted on?", 0, "en"), cookie)
	requireSessionError(t, w, 502, "agent_invalid_response")
	if history := historyV04(t, h, id, cookie); history.Conversation.Revision != 0 || len(history.Turns) != 0 {
		t.Fatal("a rejected result must not commit")
	}
	agent.tamper = func(result *ac.RunResult) {
		result.Scope = ac.ScopeFor(ac.PublicVersionV3, ac.ModeGuide, irises, ac.VincentDatasetVersion)
	}
	w, _ = submitV04(t, h, id, v04Message(messageID(5), "What is it painted on?", 0, ""), cookie)
	requireSessionError(t, w, 502, "agent_invalid_response")
	agent.tamper = nil
	if w, ok := submitV04(t, h, id, v04Message(messageID(6), "What is it painted on?", 0, "en"), cookie); w.Code != 200 || ok.Turn.Sequence != 1 {
		t.Fatalf("explicit en is valid: %d %s", w.Code, w.Body.String())
	}
}

func TestVincentPackCitationsOnlyInVincentMode(t *testing.T) {
	agent := newV3Agent(t)
	h := newVincentHandler(t, agent, true)
	v3, _ := ac.DefaultValidatorV3()
	letter, _ := v3.ReferenceRecord("vincent-772-garden-colors")
	citeLetter := func(result *ac.RunResult) {
		data, _ := json.Marshal(letter)
		var citation ac.Citation
		_ = json.Unmarshal(data, &citation)
		result.EvidenceSnapshot = []ac.Evidence{letter}
		result.Answer.Citations = []ac.Citation{citation}
		result.RequestResults[0].CitationIDs = []string{letter.ChunkID}
	}
	agent.tamper = citeLetter
	_, vincent, cookie := createV04(t, h, `{"contract_version":"agent-demo-v0.4","artwork_id":"getty-103jnh","conversation_mode":"vincent"}`, nil)
	w, turn := submitV04(t, h, vincent.Conversation.ConversationID, v04Message(messageID(1), "What did you write about the irises?", 0, ""), cookie)
	if w.Code != 200 || len(turn.Turn.Answer.Citations) != 1 || turn.Turn.Answer.Citations[0].ChunkID.Value != letter.ChunkID ||
		turn.Turn.Answer.Citations[0].SourceKind.Value != "artist_letter" {
		t.Fatalf("Vincent may cite the pinned letter: %d %s", w.Code, w.Body.String())
	}
	_, guide, _ := createV04(t, h, `{"contract_version":"agent-demo-v0.4","artwork_id":"getty-103jnh"}`, cookie)
	w, _ = submitV04(t, h, guide.Conversation.ConversationID, v04Message(messageID(2), "What did he write about the irises?", 0, ""), cookie)
	requireSessionError(t, w, 502, "agent_invalid_response")
}
