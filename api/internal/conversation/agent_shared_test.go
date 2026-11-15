package conversation

import (
	"encoding/json"
	"fmt"
	"os"
	"reflect"
	"sync"
	"testing"

	ac "github.com/Jasonssssss9/musebridge/api/internal/agentcontract"
)

type sharedAgentCase struct {
	Name     string          `json:"name"`
	Kind     string          `json:"kind"`
	Valid    bool            `json:"valid"`
	Payload  json.RawMessage `json:"payload"`
	Expected json.RawMessage `json:"expected"`
}

func sharedAgentCases(t *testing.T, kind string) []sharedAgentCase {
	t.Helper()
	data, err := os.ReadFile("../../../docs/contracts/agent-v2.examples.json")
	if err != nil {
		t.Fatal(err)
	}
	var document struct {
		Cases []sharedAgentCase `json:"runtime_cases"`
	}
	if err := json.Unmarshal(data, &document); err != nil {
		t.Fatal(err)
	}
	selected := []sharedAgentCase{}
	for _, entry := range document.Cases {
		if entry.Kind == kind {
			selected = append(selected, entry)
		}
	}
	return selected
}

func stateRun(t *testing.T, state ac.ConversationState, question string) ac.RunRequest {
	t.Helper()
	var run ac.RunRequest
	agentFixture(t, "run_request_initial", &run)
	run.ConversationID = state.ConversationID
	run.Scope = state.Scope
	run.ConversationState = cloneWire(state)
	run.Question = question
	run.Context.RecentTurns = []ac.HistoryTurn{}
	for sequence := max(int64(1), state.Revision-4); sequence <= state.Revision; sequence++ {
		run.Context.RecentTurns = append(run.Context.RecentTurns, ac.HistoryTurn{TurnID: ac.UUID(fmt.Sprintf("%08d-4444-4444-8444-444444444444", sequence)), Sequence: sequence, Question: "Earlier message", AnswerLanguage: *state.LastAnswerLanguage, AnswerStatus: "conversational", AnswerMode: "llm", AnswerText: "Earlier response"})
	}
	return run
}

func TestAgentSharedPendingMemoryTransitions(t *testing.T) {
	validator, _ := ac.DefaultValidator()
	cases := sharedAgentCases(t, "memory_transition")
	if len(cases) != 6 {
		t.Fatal("missing memory cases")
	}
	for _, entry := range cases {
		t.Run(entry.Name, func(t *testing.T) {
			var payload struct {
				State    ac.ConversationState `json:"prior_state"`
				Question string               `json:"question"`
				Answer   ac.AgentAnswer       `json:"answer"`
				Memory   ac.MemoryUpdate      `json:"memory_update"`
			}
			if err := json.Unmarshal(entry.Payload, &payload); err != nil {
				t.Fatal(err)
			}
			run := stateRun(t, payload.State, payload.Question)
			result := dialogueResult(AgentAttempt{Request: run})
			result.Answer = payload.Answer
			result.ConversationUpdate.Memory = payload.Memory
			if payload.Answer.Status == "clarification_needed" {
				result.Requests = []ac.InterpretedRequest{}
				result.RequestResults = []ac.RequestResult{}
			} else {
				result.RequestResults[0].Text = result.Answer.Text
			}
			next, err := nextAgentState(validator, run, result)
			if (err == nil) != entry.Valid {
				t.Fatalf("valid=%v: %v", entry.Valid, err)
			}
			if err == nil {
				var expected struct {
					Pending *ac.PendingClarification `json:"pending_clarification"`
				}
				if err := json.Unmarshal(entry.Expected, &expected); err != nil {
					t.Fatal(err)
				}
				if !reflect.DeepEqual(next.PendingClarification, expected.Pending) {
					t.Fatalf("got %+v expected %+v", next.PendingClarification, expected.Pending)
				}
			}
		})
	}
}

func TestAgentSharedFocusedPendingQuoteBinding(t *testing.T) {
	validator, _ := ac.DefaultValidator()
	for _, entry := range sharedAgentCases(t, "pending_focus_transition") {
		var payload struct {
			Prior    ac.PendingClarification `json:"prior_pending"`
			Revision int64                   `json:"current_revision"`
			Question string                  `json:"question"`
			Request  ac.InterpretedRequest   `json:"request"`
			Text     string                  `json:"clarifying_text"`
			Update   ac.PendingUpdate        `json:"pending_update"`
		}
		if err := json.Unmarshal(entry.Payload, &payload); err != nil {
			t.Fatal(err)
		}
		var state ac.ConversationState
		agentFixture(t, "initial_conversation_state", &state)
		language := ac.Language("en")
		state.Revision = payload.Revision
		state.LastAnswerLanguage = &language
		state.PendingClarification = &payload.Prior
		run := stateRun(t, state, payload.Question)
		result := dialogueResult(AgentAttempt{Request: run})
		result.Requests = []ac.InterpretedRequest{payload.Request}
		result.RequestResults = []ac.RequestResult{{RequestID: payload.Request.ID, Support: "clarification_needed", Text: payload.Text, CitationIDs: []string{}}}
		result.Answer = ac.AgentAnswer{Status: "clarification_needed", Text: payload.Text, Citations: []ac.Citation{}}
		result.ConversationUpdate.Memory.PendingUpdate = payload.Update
		next, err := nextAgentState(validator, run, result)
		if err != nil {
			t.Fatal(err)
		}
		var expected struct {
			Pending *ac.PendingClarification `json:"pending_clarification"`
		}
		_ = json.Unmarshal(entry.Expected, &expected)
		if !reflect.DeepEqual(next.PendingClarification, expected.Pending) {
			t.Fatal("focused pending differs from shared contract")
		}
	}
}

func TestAgentSharedFirstCommitAndCompletedReplay(t *testing.T) {
	store, _, token, conversation := newAgentTest(t, "aic-28560")
	entry := sharedAgentCases(t, "conversation_commit")[0]
	var payload struct {
		Submission ac.PublicMessageRequest `json:"submission"`
		Result     ac.RunResult            `json:"run_result"`
	}
	if err := json.Unmarshal(entry.Payload, &payload); err != nil {
		t.Fatal(err)
	}
	attempt := beginAgent(t, store, token, conversation, payload.Submission)
	first := completeAgent(t, store, attempt, payload.Result)
	if first.Conversation.Revision != 1 {
		t.Fatal("first commit did not advance exactly once")
	}
	for revision := int64(1); revision < 5; revision++ {
		attempt := beginAgent(t, store, token, conversation, agentMessage(revision, "Thanks"))
		completeAgent(t, store, attempt, dialogueResult(attempt))
	}
	_, replay, failure := store.BeginAgent(token, string(conversation.ConversationID), payload.Submission)
	if failure != nil || replay == nil || replay.Conversation.Revision != 5 || !reflect.DeepEqual(replay.Turn, first.Turn) {
		t.Fatal("shared completed replay failed", failure)
	}
	history, _ := store.AgentHistory(token, string(conversation.ConversationID))
	if len(history.Turns) != 5 {
		t.Fatal("replay appended a turn")
	}
}

func TestAgentConcurrentReservationAndAtomicHistory(t *testing.T) {
	store, _, token, conversation := newAgentTest(t, "aic-28560")
	message := agentMessage(0, "Hello")
	var wg sync.WaitGroup
	var mu sync.Mutex
	attempts := []AgentAttempt{}
	for range 16 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			attempt, _, failure := store.BeginAgent(token, string(conversation.ConversationID), message)
			if failure == nil {
				mu.Lock()
				attempts = append(attempts, attempt)
				mu.Unlock()
			} else if failure.Code != "request_in_progress" {
				t.Errorf("unexpected conflict: %+v", failure)
			}
		}()
	}
	wg.Wait()
	if len(attempts) != 1 {
		t.Fatal("duplicate reservations", len(attempts))
	}
	for range 16 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for range 10 {
				history, ok := store.AgentHistory(token, string(conversation.ConversationID))
				if !ok || history.Conversation.Revision != int64(len(history.Turns)) {
					t.Error("partial history/state update")
				}
				if len(history.Turns) > 0 && history.PendingRequest != nil {
					t.Error("completed turn still pending")
				}
			}
		}()
	}
	completeAgent(t, store, attempts[0], dialogueResult(attempts[0]))
	wg.Wait()
}

func TestAgentLazyDeadlineRecoveryOverflowAndInvalidTypedText(t *testing.T) {
	store, clock, token, conversation := newAgentTest(t, "aic-28560")
	first := beginAgent(t, store, token, conversation, agentMessage(0, "Hello"))
	clock.steady = clock.steady.Add(AgentAttemptBudget)
	retry := beginAgent(t, store, token, conversation, attemptMessage(first))
	if retry.runID == first.runID {
		t.Fatal("expired run was restarted with its old identity")
	}
	completeAgent(t, store, retry, dialogueResult(retry))
	malformed := agentMessage(1, string([]byte{0xff}))
	if _, _, failure := store.BeginAgent(token, string(conversation.ConversationID), malformed); failure == nil || failure.Code != "invalid_request" {
		t.Fatal("silently repaired invalid typed text", failure)
	}
	store.mu.Lock()
	store.conversations[string(conversation.ConversationID)].agent.state.Revision = 2147483647
	store.mu.Unlock()
	if _, _, failure := store.BeginAgent(token, string(conversation.ConversationID), agentMessage(2147483647, "Overflow")); failure == nil || failure.Code != "conversation_state_conflict" {
		t.Fatal("revision overflow admitted", failure)
	}
}
