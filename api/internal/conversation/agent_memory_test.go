package conversation

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	ac "github.com/Jasonssssss9/musebridge/api/internal/agentcontract"
)

func fixtureResult(t *testing.T, attempt AgentAttempt, name string) ac.RunResult {
	t.Helper()
	var result ac.RunResult
	agentFixture(t, name, &result)
	run := attempt.Request
	result.Scope = run.Scope
	result.CatalogSnapshot = cloneWire(run.Context.CatalogFacts)
	result.ConversationUpdate.BaseRevision = run.ConversationState.Revision
	result.ReusedChunkIDs = cacheIDs(run.ConversationState.VerifiedEvidence)
	evidence := cloneWire(run.ConversationState.VerifiedEvidence)
	seen := make(map[string]bool)
	for _, record := range evidence {
		seen[record.ChunkID] = true
	}
	for _, record := range result.EvidenceSnapshot {
		if !seen[record.ChunkID] {
			evidence = append(evidence, record)
			seen[record.ChunkID] = true
		}
	}
	result.EvidenceSnapshot = evidence
	return result
}

func releasedEvidence(t *testing.T, id string) ac.Evidence {
	t.Helper()
	record, ok := agentDataset(t).EvidenceByChunkID(id)
	if !ok {
		t.Fatal("missing evidence", id)
	}
	data, _ := json.Marshal(record)
	var evidence ac.Evidence
	if err := json.Unmarshal(data, &evidence); err != nil {
		t.Fatal(err)
	}
	return evidence
}

func citationFor(t *testing.T, evidence ac.Evidence) ac.Citation {
	t.Helper()
	validator, _ := ac.DefaultValidator()
	data, _ := json.Marshal(evidence)
	var citation ac.Citation
	if err := validator.Decode("Citation", data, &citation); err != nil {
		t.Fatal(err)
	}
	return citation
}

func TestAgentCachesOnlyCitedRetrievalAndPreservesSnapshots(t *testing.T) {
	store, _, token, conversation := newAgentTest(t, "aic-28560")
	attempt := beginAgent(t, store, token, conversation, agentMessage(0, "This painting is beautiful. What material is it made of?"))
	result := fixtureResult(t, attempt, "run_result_mixed")
	description := releasedEvidence(t, "aic-28560-description-01")
	result.EvidenceSnapshot = append(result.EvidenceSnapshot, description)
	response := completeAgent(t, store, attempt, result)
	state := agentState(store, conversation.ConversationID)
	if len(state.VerifiedEvidence) != 1 || state.VerifiedEvidence[0].ChunkID != "aic-28560-medium" {
		t.Fatal("cached uncited candidate", state.VerifiedEvidence)
	}
	result.Answer.Citations[0].Excerpt.Value = "caller mutation"
	response.Turn.Answer.Citations[0].Excerpt.Value = "caller mutation"
	history, _ := store.AgentHistory(token, string(conversation.ConversationID))
	if history.Turns[0].Answer.Citations[0].Excerpt.Value != "Oil on canvas" {
		t.Fatal("citation alias")
	}
	second := beginAgent(t, store, token, conversation, agentMessage(1, "How is the brushwork described?"))
	if len(second.Request.ConversationState.VerifiedEvidence) != 1 {
		t.Fatal("cache not included in frozen request")
	}
	next := dialogueResult(second)
	next.Requests[0].Kind = "artwork_fact"
	next.Requests[0].Intent = "Describe the released brushwork."
	next.RequestResults[0].Support = "supported"
	next.RequestResults[0].Text = "The museum describes varied brushwork."
	next.RequestResults[0].CitationIDs = []string{description.ChunkID}
	next.Answer = ac.AgentAnswer{Status: "answered", Text: next.RequestResults[0].Text, Citations: []ac.Citation{citationFor(t, description)}}
	next.EvidenceSnapshot = append(next.EvidenceSnapshot, description)
	next.ExecutedQueries = []ac.SearchQuery{{ID: "q1", RequestIDs: []ac.RequestID{"r1"}, Query: "How is the brushwork described?"}}
	next.Execution.ModelCalls = 2
	next.Execution.RetrievalCalls = 1
	completeAgent(t, store, second, next)
	state = agentState(store, conversation.ConversationID)
	if !reflect.DeepEqual(cacheIDs(state.VerifiedEvidence), []string{description.ChunkID, "aic-28560-medium"}) {
		t.Fatal("wrong cache priority", cacheIDs(state.VerifiedEvidence))
	}
	third := beginAgent(t, store, token, conversation, agentMessage(2, "Who painted this? When was it made?"))
	catalog := fixtureResult(t, third, "catalog_only_run_result")
	completeAgent(t, store, third, catalog)
	if !reflect.DeepEqual(cacheIDs(agentState(store, conversation.ConversationID).VerifiedEvidence), cacheIDs(state.VerifiedEvidence)) {
		t.Fatal("catalog entered cache")
	}
	fourth := beginAgent(t, store, token, conversation, agentMessage(3, "Thanks"))
	bad := dialogueResult(fourth)
	bad.EvidenceSnapshot[0].Excerpt = "altered cache"
	before := agentState(store, conversation.ConversationID)
	if _, failure := store.CompleteAgent(fourth, bad); failure == nil || failure.Code != "agent_invalid_response" {
		t.Fatal("altered reused cache committed", failure)
	}
	if !reflect.DeepEqual(before, agentState(store, conversation.ConversationID)) {
		t.Fatal("failed cache validation changed state")
	}
}

func TestAgentLocalClarificationKeepsOnlyFocusedPending(t *testing.T) {
	store, _, token, conversation := newAgentTest(t, "aic-28560")
	original := "What material is it made of? What is that small object?"
	first := beginAgent(t, store, token, conversation, agentMessage(0, original))
	result := fixtureResult(t, first, "partial_answer_with_local_clarification_result")
	completeAgent(t, store, first, result)
	pending := agentState(store, conversation.ConversationID).PendingClarification
	if pending == nil || pending.OriginalQuestion != original || pending.OriginalFragment == nil || *pending.OriginalFragment != "What is that small object?" || pending.Question == result.Answer.Text || pending.Question != result.RequestResults[1].Text || pending.CreatedSequence != 1 {
		t.Fatal("saved whole mixed answer instead of local clarification", pending)
	}
	thanks := beginAgent(t, store, token, conversation, agentMessage(1, "Thanks"))
	completeAgent(t, store, thanks, dialogueResult(thanks))
	if !reflect.DeepEqual(pending, agentState(store, conversation.ConversationID).PendingClarification) {
		t.Fatal("acknowledgement lost pending")
	}
	followup := beginAgent(t, store, token, conversation, agentMessage(2, "The brushwork."))
	clarify := dialogueResult(followup)
	clarify.Requests[0].Kind = "artwork_fact"
	clarify.Requests[0].Resolution = "needs_clarification"
	clarify.Requests[0].Intent = "Clarify which aspect of the brushwork the visitor means."
	clarify.RequestResults[0].Support = "clarification_needed"
	clarify.RequestResults[0].Text = "Do you mean the marks or how they were made?"
	clarify.Answer.Status = "clarification_needed"
	clarify.Answer.Text = clarify.RequestResults[0].Text
	id := ac.RequestID("r1")
	clarify.ConversationUpdate.Memory.PendingUpdate = ac.PendingUpdate{Action: "set", Source: ac.Some("pending_clarification"), RequestID: ac.Some(&id)}
	completeAgent(t, store, followup, clarify)
	next := agentState(store, conversation.ConversationID).PendingClarification
	if next.OriginalQuestion != original || *next.OriginalFragment != *pending.OriginalFragment || *next.UnresolvedIntent != clarify.Requests[0].Intent || next.CreatedSequence != 3 || strings.Contains(*next.OriginalFragment, "The brushwork.") {
		t.Fatal("focused reply forged original quote", next)
	}
	abandon := beginAgent(t, store, token, conversation, agentMessage(3, "Never mind that question."))
	clear := dialogueResult(abandon)
	clear.ConversationUpdate.Memory.PendingUpdate = ac.PendingUpdate{Action: "clear"}
	completeAgent(t, store, abandon, clear)
	state := agentState(store, conversation.ConversationID)
	if state.PendingClarification != nil || len(state.VerifiedEvidence) != 1 {
		t.Fatal("clear discarded evidence or kept pending")
	}
}

func TestAgentLanguagePreferenceTemporaryReplyAndTranslation(t *testing.T) {
	store, _, token, conversation := newAgentTest(t, "aic-28560")
	preference := ac.LanguagePreference("es")
	firstMessage := agentMessage(0, "Hello")
	firstMessage.LanguageOverride = &preference
	first := beginAgent(t, store, token, conversation, firstMessage)
	completeAgent(t, store, first, dialogueResult(first))
	second := beginAgent(t, store, token, conversation, agentMessage(1, "English for this reply only."))
	one := dialogueResult(second)
	one.AnswerLanguage = "en"
	one.LanguageDecision.AnswerLanguage = "en"
	one.LanguageDecision.ExplicitInstruction = &ac.LanguageInstruction{Preference: "en", OriginalFragment: second.Request.Question, Purpose: "turn_response", Persistence: "turn"}
	completeAgent(t, store, second, one)
	state := agentState(store, conversation.ConversationID)
	if state.LanguagePreference != "es" || state.AutoLanguage != "en" || *state.LastAnswerLanguage != "en" {
		t.Fatal("temporary instruction persisted", state)
	}
	third := beginAgent(t, store, token, conversation, agentMessage(2, "Translate the previous response into Korean."))
	translation := dialogueResult(third)
	translation.AnswerLanguage = "ko"
	translation.LanguageDecision.AnswerLanguage = "ko"
	translation.LanguageDecision.Translation = &ac.TranslationTask{TargetLanguage: "ko", OriginalFragment: third.Request.Question, Source: &ac.TranslationSource{Kind: "history_answer", Sequence: ac.Some[int64](2)}}
	completeAgent(t, store, third, translation)
	state = agentState(store, conversation.ConversationID)
	if state.LanguagePreference != "es" || state.AutoLanguage != "en" || *state.LastAnswerLanguage != "ko" {
		t.Fatal("translation changed preference", state)
	}
	fourth := beginAgent(t, store, token, conversation, agentMessage(3, "OK"))
	neutral := dialogueResult(fourth)
	if neutral.AnswerLanguage != "es" {
		t.Fatal("neutral response followed temporary language")
	}
	completeAgent(t, store, fourth, neutral)
	automatic := ac.LanguagePreference("auto")
	message := agentMessage(4, "Hello again")
	message.LanguageOverride = &automatic
	fifth := beginAgent(t, store, token, conversation, message)
	korean := dialogueResult(fifth)
	korean.LanguageDecision.DetectedLanguage = "ko"
	korean.LanguageDecision.AnswerLanguage = "ko"
	korean.AnswerLanguage = "ko"
	completeAgent(t, store, fifth, korean)
	sixth := beginAgent(t, store, token, conversation, agentMessage(5, "OK"))
	ack := dialogueResult(sixth)
	if ack.AnswerLanguage != "ko" {
		t.Fatal("neutral acknowledgement changed Auto fallback")
	}
	completeAgent(t, store, sixth, ack)
}
