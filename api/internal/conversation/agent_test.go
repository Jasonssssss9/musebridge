package conversation

import (
	"encoding/json"
	"os"
	"reflect"
	"sync"
	"testing"
	"time"

	ac "github.com/Jasonssssss9/musebridge/api/internal/agentcontract"
	"github.com/Jasonssssss9/musebridge/api/internal/catalog"
)

type agentClock struct{ wall, steady time.Time }

func newAgentTest(t *testing.T, artwork string) (*Store, *agentClock, string, ac.PublicConversation) {
	t.Helper()
	start := time.Date(2026, 11, 14, 12, 0, 0, 0, time.UTC)
	clock := &agentClock{wall: start, steady: start}
	store := New(func() time.Time { return clock.wall })
	store.steadyNow = func() time.Time { return clock.steady }
	token, conversation, created, failure := store.OpenAgent("", agentDataset(t), artwork)
	if failure != nil || !created {
		t.Fatalf("open: %v", failure)
	}
	return store, clock, token, conversation
}

func agentDataset(t *testing.T) *catalog.Dataset {
	t.Helper()
	dataset, err := catalog.Load("../../../knowledge/data/aic-seed-v0.1.json", "aic-seed-v0.1")
	if err != nil {
		t.Fatal(err)
	}
	return dataset
}

func agentMessage(revision int64, question string) ac.PublicMessageRequest {
	return ac.PublicMessageRequest{ContractVersion: ac.PublicVersion, ClientMessageID: ac.UUID(newUUID()), Question: question, ExpectedRevision: revision}
}

func beginAgent(t *testing.T, store *Store, token string, conversation ac.PublicConversation, message ac.PublicMessageRequest) AgentAttempt {
	t.Helper()
	attempt, replay, failure := store.BeginAgent(token, string(conversation.ConversationID), message)
	if failure != nil || replay != nil {
		t.Fatalf("begin: %+v %+v", failure, replay)
	}
	return attempt
}

func agentFixture(t *testing.T, name string, destination any) {
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
			if err := json.Unmarshal(entry.Payload, destination); err != nil {
				t.Fatal(err)
			}
			return
		}
	}
	t.Fatal("missing fixture", name)
}

func dialogueResult(attempt AgentAttempt) ac.RunResult {
	run := attempt.Request
	language := ac.Language(run.ConversationState.LanguagePreference)
	if run.LanguageOverride != nil {
		language = ac.Language(*run.LanguageOverride)
	}
	if language == "auto" {
		language = run.ConversationState.AutoLanguage
	}
	return ac.RunResult{
		Scope: run.Scope, AnswerLanguage: language,
		LanguageDecision: ac.LanguageDecision{DetectedLanguage: "und", AnswerLanguage: language},
		Answer:           ac.AgentAnswer{Status: "conversational", Text: "Thanks for sharing your reaction.", Citations: []ac.Citation{}},
		Requests:         []ac.InterpretedRequest{{ID: "r1", OriginalFragment: run.Question, Intent: "Respond to the visitor.", Kind: "conversation", Resolution: "clear"}},
		RequestResults:   []ac.RequestResult{{RequestID: "r1", Support: "not_applicable", Text: "Thanks for sharing your reaction.", CitationIDs: []string{}}},
		EvidenceSnapshot: cloneWire(run.ConversationState.VerifiedEvidence), CatalogSnapshot: cloneWire(run.Context.CatalogFacts),
		ReusedChunkIDs: cacheIDs(run.ConversationState.VerifiedEvidence), ExecutedQueries: []ac.SearchQuery{},
		ConversationUpdate: ac.ConversationUpdate{BaseRevision: run.ConversationState.Revision, Memory: ac.MemoryUpdate{ContinuitySummary: run.ConversationState.ContinuitySummary, ActiveTopic: cloneWire(run.ConversationState.ActiveTopic), PendingUpdate: ac.PendingUpdate{Action: "keep"}}},
		// Synthetic wire identity only; no inference provider is contacted.
		Execution: ac.ExecutionRecord{WorkflowID: ac.WorkflowID, ProfileID: ac.ProfileID, PromptVersion: "museum-prompts-v2", SchemaVersion: "agent-schema-v2", RetrievalStrategyVersion: "legacy-facet-v0.1", ModelProvider: "ollama", ModelName: "SYNTHETIC-TEST-ONLY", ModelDigest: "SYNTHETIC-NOT-A-MODEL-DIGEST", ModelCalls: 1},
	}
}

func cacheIDs(evidence []ac.Evidence) []string {
	ids := []string{}
	for _, record := range evidence {
		ids = append(ids, record.ChunkID)
	}
	return ids
}

func completeAgent(t *testing.T, store *Store, attempt AgentAttempt, result ac.RunResult) ac.PublicSubmissionResponse {
	t.Helper()
	response, failure := store.CompleteAgent(attempt, result)
	if failure != nil {
		t.Fatalf("complete: %+v", failure)
	}
	return response
}

func agentState(store *Store, id ac.UUID) ac.ConversationState {
	store.mu.Lock()
	defer store.mu.Unlock()
	return cloneWire(store.conversations[string(id)].agent.state)
}

func TestAgentOpenIdentityVersionAndArtworkIsolation(t *testing.T) {
	store, clock, token, conversation := newAgentTest(t, "aic-28560")
	initial := agentState(store, conversation.ConversationID)
	if initial.Revision != 0 || initial.LanguagePreference != "auto" || initial.AutoLanguage != "en" || initial.LastAnswerLanguage != nil || initial.PendingClarification != nil || len(initial.VerifiedEvidence) != 0 {
		t.Fatal("wrong initial state", initial)
	}
	clock.wall = clock.wall.Add(time.Hour)
	sameToken, restored, created, failure := store.OpenAgent(token, agentDataset(t), "aic-28560")
	if failure != nil || created || token != sameToken || restored != conversation {
		t.Fatal("restore changed identity/expiry", failure)
	}
	_, legacy, _ := store.Open(token, "aic-28560", conversation.DatasetVersion)
	_, other, _, _ := store.OpenAgent(token, agentDataset(t), "aic-11723")
	if legacy.ConversationID == string(conversation.ConversationID) || other.ConversationID == conversation.ConversationID {
		t.Fatal("contract/artwork crossed conversations")
	}
	if _, ok := store.History(token, string(conversation.ConversationID)); ok {
		t.Fatal("fixture history exposed an Agent conversation")
	}
	if _, ok := store.AgentHistory(token, legacy.ConversationID); ok {
		t.Fatal("Agent history exposed fixture turns")
	}
	if _, _, failure := store.Begin(token, string(conversation.ConversationID), testSubmission(), AgentAttemptBudget); failure == nil || failure.Code != "conversation_not_found" {
		t.Fatal("fixture admission accepted Agent ID")
	}
	if _, ok := store.AgentHistory("foreign", string(conversation.ConversationID)); ok {
		t.Fatal("foreign history exposed")
	}
	if _, _, failure := store.BeginAgent("foreign", string(conversation.ConversationID), agentMessage(0, "Hello")); failure == nil || failure.Code != "conversation_not_found" {
		t.Fatal("foreign admission exposed")
	}
	_, stranger, _, _ := store.OpenAgent("", agentDataset(t), "aic-28560")
	if stranger.ConversationID == conversation.ConversationID {
		t.Fatal("shared visitor state")
	}
}

func TestAgentDeadlineAndExpiryAreRecheckedAfterValidation(t *testing.T) {
	t.Run("attempt budget", func(t *testing.T) {
		store, clock, token, conversation := newAgentTest(t, "aic-28560")
		attempt := beginAgent(t, store, token, conversation, agentMessage(0, "Hello"))
		result := dialogueResult(attempt)
		before := agentState(store, conversation.ConversationID)
		calls := 0
		store.steadyNow = func() time.Time {
			calls++
			if calls == 1 {
				return clock.steady
			}
			return clock.steady.Add(AgentAttemptBudget)
		}
		if _, failure := store.CompleteAgent(attempt, result); failure == nil || failure.Code != "request_timeout" {
			t.Fatal("validation crossed deadline", failure)
		}
		if !reflect.DeepEqual(before, agentState(store, conversation.ConversationID)) {
			t.Fatal("late state committed")
		}
	})
	t.Run("conversation expiry", func(t *testing.T) {
		store, clock, token, conversation := newAgentTest(t, "aic-28560")
		attempt := beginAgent(t, store, token, conversation, agentMessage(0, "Hello"))
		result := dialogueResult(attempt)
		calls := 0
		store.now = func() time.Time {
			calls++
			if calls <= 2 {
				return clock.wall
			}
			return clock.wall.Add(Lifetime)
		}
		if _, failure := store.CompleteAgent(attempt, result); failure == nil || failure.Code != "conversation_not_found" {
			t.Fatal("validation crossed TTL", failure)
		}
		if len(store.conversations) != 0 {
			t.Fatal("expired state retained")
		}
	})
}

func TestAgentAdmissionOrderingReplayAndExactPayloadIdentity(t *testing.T) {
	store, _, token, conversation := newAgentTest(t, "aic-28560")
	message := agentMessage(0, "Hello")
	attempt := beginAgent(t, store, token, conversation, message)
	changed := cloneWire(message)
	changed.Question = "Hello!"
	for _, test := range []struct {
		message ac.PublicMessageRequest
		code    string
	}{
		{message, "request_in_progress"}, {changed, "idempotency_conflict"},
		{agentMessage(0, "Another"), "conversation_busy"}, {agentMessage(1, "Stale"), "conversation_state_conflict"},
	} {
		if _, _, failure := store.BeginAgent(token, string(conversation.ConversationID), test.message); failure == nil || failure.Code != test.code {
			t.Fatalf("expected %s: %+v", test.code, failure)
		}
	}
	first := completeAgent(t, store, attempt, dialogueResult(attempt))
	secondAttempt := beginAgent(t, store, token, conversation, agentMessage(1, "Another"))
	completeAgent(t, store, secondAttempt, dialogueResult(secondAttempt))
	third := beginAgent(t, store, token, conversation, agentMessage(2, "Still working"))
	_, replay, failure := store.BeginAgent(token, string(conversation.ConversationID), message)
	if failure != nil || replay == nil || !reflect.DeepEqual(replay.Turn, first.Turn) || replay.Conversation.Revision != 2 {
		t.Fatal("replay did not precede revision/busy check", failure)
	}
	for _, mutate := range []func(*ac.PublicMessageRequest){
		func(m *ac.PublicMessageRequest) { m.ExpectedRevision = 2 },
		func(m *ac.PublicMessageRequest) {
			preference := ac.LanguagePreference("en")
			m.LanguageOverride = &preference
		},
		func(m *ac.PublicMessageRequest) { m.Question = "HELLO" },
	} {
		changed = cloneWire(message)
		mutate(&changed)
		if _, _, failure := store.BeginAgent(token, string(conversation.ConversationID), changed); failure == nil || failure.Code != "idempotency_conflict" {
			t.Fatal("changed key accepted", failure)
		}
	}
	store.FailAgent(third, &Failure{503, Error{"generation_unavailable", "Unavailable.", true}})
}

func TestAgentFailureRetrySupersessionAndStaleKeys(t *testing.T) {
	store, _, token, conversation := newAgentTest(t, "aic-28560")
	message := agentMessage(0, "Hello")
	first := beginAgent(t, store, token, conversation, message)
	unavailable := &Failure{503, Error{"generation_unavailable", "Unavailable.", true}}
	store.FailAgent(first, unavailable)
	history, _ := store.AgentHistory(token, string(conversation.ConversationID))
	if len(history.Turns) != 0 || history.Conversation.Revision != 0 || history.PendingRequest == nil || history.PendingRequest.State != "failed" {
		t.Fatal("failure changed history or lost retry")
	}
	retry := beginAgent(t, store, token, conversation, cloneWire(message))
	if retry.runID == first.runID {
		t.Fatal("retry reused run ID")
	}
	if _, failure := store.CompleteAgent(first, dialogueResult(first)); failure == nil {
		t.Fatal("late old run committed")
	}
	if failure := store.FailAgent(first, unavailable); failure == nil || failure.Code != "request_superseded" {
		t.Fatal("old failure overwrote retry")
	}
	store.FailAgent(retry, unavailable)
	other := beginAgent(t, store, token, conversation, agentMessage(0, "Different"))
	if _, _, failure := store.BeginAgent(token, string(conversation.ConversationID), message); failure == nil || failure.Code != "request_superseded" {
		t.Fatal("failed key revived", failure)
	}
	completeAgent(t, store, other, dialogueResult(other))
	if _, _, failure := store.BeginAgent(token, string(conversation.ConversationID), message); failure == nil || failure.Code != "conversation_state_conflict" {
		t.Fatal("stale retry did not win over supersession", failure)
	}
}

func TestAgentSnapshotsAreDetachedAndCommitUsesProtectedInput(t *testing.T) {
	store, _, token, conversation := newAgentTest(t, "aic-28560")
	preference := ac.LanguagePreference("es")
	message := agentMessage(0, "Hello")
	message.LanguageOverride = &preference
	attempt := beginAgent(t, store, token, conversation, message)
	result := dialogueResult(attempt)
	preference = "ko"
	attempt.Request.Question = "tampered"
	attempt.Request.Scope.ArtworkID = "aic-11723"
	attempt.Request.ConversationState.LanguagePreference = "ko"
	attempt.Request.Context.CatalogFacts[0].Value = "forged"
	attempt.Deadline = attempt.Deadline.Add(24 * time.Hour)
	response := completeAgent(t, store, attempt, result)
	if response.Turn.Question != "Hello" || response.Turn.AnswerLanguage != "es" || *response.Turn.LanguageOverride != "es" {
		t.Fatal("caller changed protected submission")
	}
	response.Turn.Answer.Text = "caller"
	*response.Conversation.LastAnswerLanguage = "ko"
	next := beginAgent(t, store, token, conversation, agentMessage(1, "Next"))
	next.Request.Context.RecentTurns[0].AnswerText = "caller"
	history, _ := store.AgentHistory(token, string(conversation.ConversationID))
	history.Turns[0].Answer.Text = "caller"
	history.PendingRequest.Question = "caller"
	fresh, _ := store.AgentHistory(token, string(conversation.ConversationID))
	if fresh.Turns[0].Answer.Text != "Thanks for sharing your reaction." || fresh.PendingRequest.Question != "Next" || fresh.PendingRequest.LanguageOverride != nil || *fresh.Conversation.LastAnswerLanguage != "es" {
		t.Fatal("snapshot mutation reached store")
	}
}

func TestAgentFivePairWindowAndBothArtworkCatalogs(t *testing.T) {
	for _, artwork := range []string{"aic-28560", "aic-11723"} {
		t.Run(artwork, func(t *testing.T) {
			store, _, token, conversation := newAgentTest(t, artwork)
			for revision := int64(0); revision < 7; revision++ {
				attempt := beginAgent(t, store, token, conversation, agentMessage(revision, "Hello"))
				if int64(len(attempt.Request.Context.RecentTurns)) != min(5, revision) {
					t.Fatal("wrong window")
				}
				if revision > 5 && attempt.Request.Context.RecentTurns[0].Sequence != revision-4 {
					t.Fatal("wrong retained sequence")
				}
				for _, fact := range attempt.Request.Context.CatalogFacts {
					if fact.ArtworkID != artwork {
						t.Fatal("foreign catalog")
					}
				}
				completeAgent(t, store, attempt, dialogueResult(attempt))
			}
			history, _ := store.AgentHistory(token, string(conversation.ConversationID))
			if len(history.Turns) != 7 || history.Conversation.Revision != 7 {
				t.Fatal("public history was truncated")
			}
		})
	}
}

func TestAgentDeadlineExpiryAndParentTimeout(t *testing.T) {
	store, clock, token, conversation := newAgentTest(t, "aic-28560")
	attempt := beginAgent(t, store, token, conversation, agentMessage(0, "Hello"))
	if deadline, _ := time.Parse(time.RFC3339Nano, attempt.Request.Execution.DeadlineAt); !deadline.Equal(clock.steady.Add(55 * time.Second)) {
		t.Fatal("wrong private deadline")
	}
	clock.steady = clock.steady.Add(AgentAttemptBudget)
	attempt.Deadline = clock.steady.Add(time.Hour)
	if _, failure := store.CompleteAgent(attempt, dialogueResult(attempt)); failure == nil || failure.Code != "request_timeout" {
		t.Fatal("late commit accepted", failure)
	}
	history, _ := store.AgentHistory(token, string(conversation.ConversationID))
	if len(history.Turns) != 0 || history.PendingRequest.LastError.Code != "request_timeout" {
		t.Fatal("timeout changed memory")
	}
	retry := beginAgent(t, store, token, conversation, historyToMessage(history.PendingRequest))
	clock.steady = clock.steady.Add(AgentAttemptBudget)
	if failure := store.FailAgent(retry, &Failure{504, Error{"generation_timeout", "Child timeout.", true}}); failure.Code != "request_timeout" {
		t.Fatal("child timeout hid parent")
	}
	third := beginAgent(t, store, token, conversation, agentMessage(0, "Third"))
	clock.wall = clock.wall.Add(Lifetime)
	if _, failure := store.CompleteAgent(third, dialogueResult(third)); failure == nil || failure.Code != "conversation_not_found" {
		t.Fatal("TTL-expired commit accepted", failure)
	}
	if _, ok := store.AgentHistory(token, string(conversation.ConversationID)); ok {
		t.Fatal("expired state visible")
	}
	_, fresh, created, failure := store.OpenAgent(token, agentDataset(t), "aic-28560")
	if failure != nil || !created || fresh.Revision != 0 || fresh.ConversationID == conversation.ConversationID {
		t.Fatal("expired conversation resumed")
	}
}

func historyToMessage(p *ac.PendingRequest) ac.PublicMessageRequest {
	return ac.PublicMessageRequest{ContractVersion: p.ContractVersion, ClientMessageID: p.ClientMessageID, Question: p.Question, LanguageOverride: cloneWire(p.LanguageOverride), ExpectedRevision: p.ExpectedRevision}
}

func TestAgentAtomicFailureAndConcurrentCompletion(t *testing.T) {
	store, _, token, conversation := newAgentTest(t, "aic-28560")
	attempt := beginAgent(t, store, token, conversation, agentMessage(0, "Hello"))
	before := agentState(store, conversation.ConversationID)
	bad := dialogueResult(attempt)
	topic := "Must not be committed"
	bad.ConversationUpdate.Memory.ActiveTopic = &topic
	bad.Answer.Text = "mismatched assembly"
	if _, failure := store.CompleteAgent(attempt, bad); failure == nil || failure.Code != "agent_invalid_response" {
		t.Fatal("invalid result accepted", failure)
	}
	if !reflect.DeepEqual(before, agentState(store, conversation.ConversationID)) {
		t.Fatal("part of invalid memory committed")
	}
	retry := beginAgent(t, store, token, conversation, attemptMessage(attempt))
	result := dialogueResult(retry)
	var wg sync.WaitGroup
	var mu sync.Mutex
	success := 0
	for range 16 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, failure := store.CompleteAgent(retry, result)
			if failure == nil {
				mu.Lock()
				success++
				mu.Unlock()
			}
		}()
	}
	wg.Wait()
	history, _ := store.AgentHistory(token, string(conversation.ConversationID))
	if success != 1 || len(history.Turns) != 1 || history.Conversation.Revision != 1 || history.PendingRequest != nil {
		t.Fatal("non-atomic duplicate completion", success)
	}
}

func attemptMessage(a AgentAttempt) ac.PublicMessageRequest {
	return ac.PublicMessageRequest{ContractVersion: ac.PublicVersion, ClientMessageID: a.Request.ClientMessageID, Question: a.Request.Question, LanguageOverride: cloneWire(a.Request.LanguageOverride), ExpectedRevision: a.Request.ConversationState.Revision}
}
