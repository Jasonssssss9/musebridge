package conversation

import (
	"reflect"
	"testing"
	"time"

	"github.com/Jasonssssss9/musebridge/api/internal/catalog"
)

func testSubmission() Submission {
	return Submission{newUUID(), "What material is this artwork made of?", "en"}
}

func testAnswer() Answer {
	return Answer{Status: "answered", Mode: "fixture", Text: "Reviewed answer.",
		Citations: []catalog.Evidence{{ChunkID: "aic-28560-medium", Excerpt: "Oil on canvas"}}}
}

func TestRequestReservationReplayAndSnapshots(t *testing.T) {
	store := New(nil)
	token, current, _ := store.Open("", "aic-28560", "aic-seed-v0.1")
	submission := testSubmission()
	attempt, replay, failure := store.Begin(token, current.ConversationID, submission, time.Minute)
	if failure != nil || replay != nil || len(attempt.RecentTurns) != 0 {
		t.Fatal("initial submission was not reserved")
	}
	history, _ := store.History(token, current.ConversationID)
	if len(history.Turns) != 0 || history.PendingRequest == nil || history.PendingRequest.State != "processing" || history.PendingRequest.LastError != nil {
		t.Fatal("processing state is not recoverable through history")
	}
	changed := submission
	changed.Question += "?"
	other := testSubmission()
	for _, tc := range []struct {
		input Submission
		code  string
	}{
		{submission, "request_in_progress"}, {changed, "idempotency_conflict"}, {other, "conversation_busy"},
	} {
		if _, _, failed := store.Begin(token, current.ConversationID, tc.input, time.Minute); failed == nil || failed.Status != 409 || failed.Code != tc.code {
			t.Fatalf("incorrect reservation conflict for %s", tc.code)
		}
	}
	if _, _, failed := store.Begin("foreign", current.ConversationID, submission, time.Minute); failed == nil || failed.Code != "conversation_not_found" {
		t.Fatal("reservation exposed a foreign conversation")
	}
	turn, failure := store.Complete(attempt, testAnswer())
	if failure != nil || turn.Sequence != 1 || turn.ClientMessageID != submission.ClientMessageID || turn.Question != submission.Question {
		t.Fatal("completed turn did not retain the accepted payload")
	}
	original := cloneTurn(turn)
	turn.Answer.Citations[0].Excerpt = "caller mutation"
	next, _, failure := store.Begin(token, current.ConversationID, other, time.Minute)
	if failure != nil || len(next.RecentTurns) != 1 {
		t.Fatal("next accepted submission did not receive completed context")
	}
	_, replay, failure = store.Begin(token, current.ConversationID, submission, time.Minute)
	if failure != nil || replay == nil || !reflect.DeepEqual(*replay, original) {
		t.Fatal("completed replay changed or was blocked by a newer processing request")
	}
	replay.Answer.Citations[0].Excerpt = "replay mutation"
	next.RecentTurns[0].Answer.Citations[0].Excerpt = "context mutation"
	history, _ = store.History(token, current.ConversationID)
	if len(history.Turns) != 1 || history.Turns[0].Answer.Citations[0].Excerpt != "Oil on canvas" || history.PendingRequest.ClientMessageID != other.ClientMessageID {
		t.Fatal("state leaked through a mutable snapshot or had an inconsistent pending key")
	}
	if _, failed := store.Complete(attempt, testAnswer()); failed == nil {
		t.Fatal("a completed attempt committed twice")
	}
	if _, failed := store.Complete(next, testAnswer()); failed != nil {
		t.Fatal(failed)
	}
	history, _ = store.History(token, current.ConversationID)
	if len(history.Turns) != 2 || history.Turns[1].Sequence != 2 || history.PendingRequest != nil {
		t.Fatal("commit did not atomically advance history and clear pending state")
	}
}

func TestFailedRetrySupersessionAndLateAttempts(t *testing.T) {
	store := New(nil)
	token, current, _ := store.Open("", "aic-28560", "aic-seed-v0.1")
	first := testSubmission()
	attempt, _, _ := store.Begin(token, current.ConversationID, first, time.Minute)
	unavailable := &Failure{503, Error{"retrieval_unavailable", "Temporarily unavailable.", true}}
	store.Fail(attempt, unavailable)
	history, _ := store.History(token, current.ConversationID)
	if len(history.Turns) != 0 || history.PendingRequest.State != "failed" || history.PendingRequest.LastError.Code != unavailable.Code {
		t.Fatal("technical failure was saved as a completed turn or lost its pending payload")
	}
	history.PendingRequest.LastError.Code = "caller mutation"
	retry, _, failed := store.Begin(token, current.ConversationID, first, time.Minute)
	if failed != nil || retry.attemptID == attempt.attemptID {
		t.Fatal("retry did not receive a distinct attempt identity")
	}
	if _, failed := store.Complete(attempt, testAnswer()); failed == nil {
		t.Fatal("late success from a failed attempt was accepted")
	}
	if failed := store.Fail(attempt, unavailable); failed == nil {
		t.Fatal("late failure overwrote the active retry")
	}
	store.Fail(retry, unavailable)
	second := testSubmission()
	next, _, _ := store.Begin(token, current.ConversationID, second, time.Minute)
	if _, _, failed := store.Begin(token, current.ConversationID, first, time.Minute); failed == nil || failed.Code != "request_superseded" {
		t.Fatal("an old failed key was revived after accepting a new key")
	}
	third := testSubmission()
	store.Begin(token, current.ConversationID, third, time.Minute)
	if _, failed := store.Complete(next, testAnswer()); failed != nil {
		t.Fatal(failed)
	}
	if _, _, failed := store.Begin(token, current.ConversationID, third, time.Minute); failed != nil {
		t.Fatal("a rejected busy key was incorrectly retained as an accepted request")
	}
}

func TestCommitChecksExpiryAndAttemptBudget(t *testing.T) {
	now := time.Date(2026, 11, 10, 0, 0, 0, 0, time.UTC)
	store := New(func() time.Time { return now })
	token, current, _ := store.Open("", "aic-28560", "aic-seed-v0.1")
	attempt, _, _ := store.Begin(token, current.ConversationID, testSubmission(), time.Minute)
	now = current.ExpiresAt
	if _, failed := store.Complete(attempt, testAnswer()); failed == nil || failed.Code != "conversation_not_found" {
		t.Fatal("an answer committed after conversation expiry")
	}
	token, current, _ = store.Open(token, "aic-28560", "aic-seed-v0.1")
	attempt, _, _ = store.Begin(token, current.ConversationID, testSubmission(), -time.Second)
	if _, failed := store.Complete(attempt, testAnswer()); failed == nil || failed.Code != "request_timeout" {
		t.Fatal("an answer committed after its monotonic attempt deadline")
	}
	history, _ := store.History(token, current.ConversationID)
	if len(history.Turns) != 0 || history.PendingRequest.State != "failed" || history.PendingRequest.LastError.Code != "request_timeout" {
		t.Fatal("late completion did not leave the original submission recoverable")
	}
}

func TestRecentContextContainsOnlyLastThreeCompletedTurns(t *testing.T) {
	store := New(nil)
	token, current, _ := store.Open("", "aic-28560", "aic-seed-v0.1")
	for range 5 {
		attempt, _, _ := store.Begin(token, current.ConversationID, testSubmission(), time.Minute)
		store.Complete(attempt, testAnswer())
	}
	attempt, _, _ := store.Begin(token, current.ConversationID, testSubmission(), time.Minute)
	if len(attempt.RecentTurns) != 3 || attempt.RecentTurns[0].Sequence != 3 || attempt.RecentTurns[2].Sequence != 5 {
		t.Fatal("recent context did not retain the last three turns in order")
	}
	_, other, _ := store.Open(token, "aic-11723", current.DatasetVersion)
	attempt, _, _ = store.Begin(token, other.ConversationID, testSubmission(), time.Minute)
	if len(attempt.RecentTurns) != 0 {
		t.Fatal("context crossed artwork conversations")
	}
}
