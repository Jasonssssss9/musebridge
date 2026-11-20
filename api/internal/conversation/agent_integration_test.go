//go:build integration

package conversation

import (
	"context"
	"errors"
	"fmt"
	"reflect"
	"testing"
	"time"

	"github.com/Jasonssssss9/musebridge/api/internal/agentclient"
	ac "github.com/Jasonssssss9/musebridge/api/internal/agentcontract"
)

const m1Question = "What material is this painting made of?"

// TestM1GoOriginatedIntegration exercises the same store, wire contracts and
// Agent client intended for M2. Only the Python model provider/sizer is scripted.
// No public route, replacement Go client or locally fabricated result is used.
func TestM1GoOriginatedIntegration(t *testing.T) {
	validator, err := ac.DefaultValidator()
	if err != nil {
		t.Fatal(err)
	}
	dataset := agentDataset(t)
	store := New(nil)
	normal := startM1Services(t, "none")
	visitor := ""
	type row struct{ name, trace, expected, actual string }
	var rows []row
	t.Cleanup(func() {
		t.Log("| Case | Trace ID | Expected evidence | Actual evidence or error | Result |")
		t.Log("| --- | --- | --- | --- | --- |")
		for _, item := range rows {
			t.Logf("| %s | %s | %s | %s | PASS |", item.name, item.trace, item.expected, item.actual)
		}
	})
	committed := make(map[ac.UUID]ac.ConversationState)
	for _, item := range []struct{ artwork, name string }{
		{"aic-28560", "bedroom"}, {"aic-11723", "toilette"},
	} {
		t.Run(item.name, func(t *testing.T) {
			var conversation ac.PublicConversation
			var created bool
			var failure *Failure
			visitor, conversation, created, failure = store.OpenAgent(visitor, dataset, item.artwork)
			if failure != nil || !created || conversation.Revision != 0 {
				t.Fatal("cannot create the real initial Go conversation")
			}
			message := agentMessage(0, m1Question)
			attempt := beginAgent(t, store, visitor, conversation, message)
			original := cloneWire(attempt.Request)
			trace := m1Trace(attempt, item.name)
			deadline, err := time.Parse(time.RFC3339Nano, original.Execution.DeadlineAt)
			if err != nil || !deadline.Equal(attempt.Deadline.Add(-5*time.Second)) ||
				original.ConversationState.Revision != 0 || len(original.Context.RecentTurns) != 0 ||
				len(original.ConversationState.VerifiedEvidence) != 0 {
				t.Fatal("admission did not freeze the original deadline and initial snapshot")
			}
			ctx, cancel := context.WithDeadline(context.Background(), attempt.Deadline)
			defer cancel()
			snapshot, err := normal.client.Run(ctx, attempt.Request, trace)
			if err != nil {
				t.Fatalf("live Go-originated Agent request failed: %v", err)
			}
			if snapshot.RunID != original.RunID || snapshot.State != "completed" || snapshot.Result == nil ||
				snapshot.Error != nil || !reflect.DeepEqual(attempt.Request, original) {
				t.Fatal("the run identity, terminal state or original request changed")
			}
			queried, err := normal.client.Get(ctx, original, trace+"-status")
			if err != nil || !reflect.DeepEqual(queried, snapshot) {
				t.Fatalf("GET did not recover the real retained completed snapshot: %v", err)
			}
			result := *snapshot.Result
			if validator.CheckRunResult(original, result) != nil || result.Scope != original.Scope ||
				result.Answer.Status != "answered" || result.AnswerLanguage != "en" ||
				result.ConversationUpdate.BaseRevision != 0 || result.Execution.ModelName != normal.connection.ModelName ||
				result.Execution.ModelCalls != 2 || result.Execution.RetrievalCalls != 1 {
				t.Fatal("Go rejected the real workflow's scoped answer or execution relationships")
			}
			expected := item.artwork + "-medium"
			if len(result.EvidenceSnapshot) != 1 || result.EvidenceSnapshot[0].ChunkID != expected ||
				result.EvidenceSnapshot[0].ArtworkID != item.artwork || len(result.Answer.Citations) != 1 ||
				result.Answer.Citations[0].ChunkID != ac.Some(expected) || result.Answer.Citations[0].ArtworkID != item.artwork {
				t.Fatal("Go received the wrong evidence ID or citation for the artwork")
			}
			actual := assertM1Trace(t, normal, trace, item.artwork, false)
			response, failure := store.CompleteAgent(attempt, result)
			if failure != nil || response.Conversation.Revision != 1 || response.Turn.Sequence != 1 ||
				response.Turn.ClientMessageID != message.ClientMessageID || response.Turn.Answer.Mode != "llm" ||
				!reflect.DeepEqual(response.Turn.Answer.Citations, result.Answer.Citations) {
				t.Fatal("the real Go store did not commit the validated result atomically")
			}
			history, ok := store.AgentHistory(visitor, string(conversation.ConversationID))
			state := agentState(store, conversation.ConversationID)
			if !ok || len(history.Turns) != 1 || history.PendingRequest != nil || state.Revision != 1 ||
				len(state.VerifiedEvidence) != 1 || state.VerifiedEvidence[0].ChunkID != expected {
				t.Fatal("committed turn, revision, pending completion and evidence cache disagree")
			}
			_, replay, failure := store.BeginAgent(visitor, string(conversation.ConversationID), message)
			if failure != nil || replay == nil || !reflect.DeepEqual(*replay, response) ||
				!reflect.DeepEqual(agentState(store, conversation.ConversationID), state) {
				t.Fatal("completed submission replay changed state or lost the original turn")
			}
			assertM1Trace(t, normal, trace, item.artwork, false)
			for id, prior := range committed {
				if id == conversation.ConversationID || !reflect.DeepEqual(agentState(store, id), prior) {
					t.Fatal("the second artwork reused or modified the first artwork's conversation")
				}
			}
			committed[conversation.ConversationID] = state
			rows = append(rows, row{item.name, trace, expected, actual})
		})
	}

	t.Run("foreign-evidence", func(t *testing.T) {
		foreign := startM1Services(t, "foreign-evidence")
		failedStore := New(nil)
		owner, conversation, created, failure := failedStore.OpenAgent("", dataset, "aic-28560")
		if failure != nil || !created {
			t.Fatal("cannot create the rejection case's Go conversation")
		}
		before := agentState(failedStore, conversation.ConversationID)
		message := agentMessage(0, m1Question)
		attempt := beginAgent(t, failedStore, owner, conversation, message)
		trace := m1Trace(attempt, "foreign")
		ctx, cancel := context.WithDeadline(context.Background(), attempt.Deadline)
		defer cancel()
		snapshot, err := foreign.client.Run(ctx, attempt.Request, trace)
		var rejected *agentclient.Error
		if snapshot != (ac.RunSnapshot{}) || !errors.As(err, &rejected) || rejected.Failure.Status != 502 ||
			rejected.Failure.Error.Code != "retrieval_invalid_response" || rejected.Failure.Error.Retryable || rejected.Failure.Reconcile {
			t.Fatal("Go received a successful answer or wrong mapping for the foreign evidence")
		}
		queried, queryError := foreign.client.Get(ctx, attempt.Request, trace+"-status")
		var retained *agentclient.Error
		if queried != (ac.RunSnapshot{}) || !errors.As(queryError, &retained) || retained.RunState != "failed" ||
			retained.Failure != rejected.Failure {
			t.Fatal("GET did not map the real retained failed snapshot consistently with POST")
		}
		actual := assertM1Trace(t, foreign, trace, "aic-28560", true)
		mapped := rejected.Failure
		failure = failedStore.FailAgent(attempt, &Failure{Status: mapped.Status, Error: Error{
			Code: mapped.Error.Code, Message: mapped.Error.Message, Retryable: mapped.Error.Retryable,
		}})
		if failure == nil || failure.Status != 502 || failure.Code != "retrieval_invalid_response" {
			t.Fatal("the Go store lost the safe Agent error mapping")
		}
		after := agentState(failedStore, conversation.ConversationID)
		history, ok := failedStore.AgentHistory(owner, string(conversation.ConversationID))
		if !ok || !reflect.DeepEqual(before, after) || after.Revision != 0 || len(after.VerifiedEvidence) != 0 ||
			len(history.Turns) != 0 || history.Conversation.Revision != 0 || history.PendingRequest == nil ||
			history.PendingRequest.State != "failed" || history.PendingRequest.LastError == nil ||
			history.PendingRequest.LastError.Code != "retrieval_invalid_response" ||
			history.PendingRequest.ClientMessageID != message.ClientMessageID {
			t.Fatal("the failed attempt changed committed turns, revision, memory or verified evidence")
		}
		rows = append(rows, row{"foreign-evidence", trace, "aic-28560-medium",
			fmt.Sprintf("%s -> %s", actual, mapped.Error.Code)})
		t.Log("Injection: retrieval HTTP response serialization; rejection: RetrievalTool.search -> EvidenceManifest.validate_candidates; Go received no result.")
	})
	if !t.Failed() && len(rows) != 3 {
		t.Fatal("the M1 check did not execute all three acceptance cases")
	}
}
