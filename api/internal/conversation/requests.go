package conversation

import (
	"slices"
	"time"
)

type request struct {
	submission Submission
	state      string
	attemptID  string
	deadline   time.Time
	lastError  Error
	turn       Turn
}

// Attempt contains detached inputs and an unexported reservation identity.
// Deadlines use the process's monotonic clock, independently of the TTL clock.
type Attempt struct {
	Conversation                         Conversation
	Submission                           Submission
	RecentTurns                          []Turn
	Deadline                             time.Time
	conversationID, messageID, attemptID string
}

// Begin expects validated canonical IDs and a trimmed, validated payload.
// A completed replay is resolved before enforcing the current busy reservation.
func (s *Store) Begin(token, id string, submission Submission, budget time.Duration) (Attempt, *Turn, *Failure) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.expire(s.now().UTC())
	item := s.conversations[id]
	if item == nil || s.visitors[token] == nil || item.owner != token {
		return Attempt{}, nil, notFound()
	}
	previous := item.requests[submission.ClientMessageID]
	if previous != nil {
		if previous.submission != submission {
			return Attempt{}, nil, conflict("idempotency_conflict", "This message ID belongs to a different submission.", false)
		}
		switch previous.state {
		case "completed":
			turn := cloneTurn(previous.turn)
			return Attempt{Conversation: item.conversation}, &turn, nil
		case "processing":
			return Attempt{}, nil, conflict("request_in_progress", "This submission is still processing.", true)
		case "superseded":
			return Attempt{}, nil, conflict("request_superseded", "This submission was replaced by a newer question.", false)
		}
	}
	if pending := item.requests[item.pending]; pending != nil && pending.state == "processing" {
		return Attempt{}, nil, conflict("conversation_busy", "Another submission is processing in this conversation.", true)
	}
	if pending := item.requests[item.pending]; pending != nil && pending != previous {
		pending.state = "superseded"
	}
	current := &request{
		submission: submission, state: "processing", attemptID: newUUID(), deadline: time.Now().Add(budget),
	}
	item.requests[submission.ClientMessageID] = current
	item.pending = submission.ClientMessageID
	start := max(0, len(item.turns)-3)
	return Attempt{
		Conversation: item.conversation, Submission: submission, RecentTurns: cloneTurns(item.turns[start:]),
		Deadline: current.deadline, conversationID: id, messageID: submission.ClientMessageID, attemptID: current.attemptID,
	}, nil, nil
}

// Complete commits one validated answer and clears pending state atomically.
// Late results cannot commit after failure, retry, supersession or expiry.
func (s *Store) Complete(attempt Attempt, answer Answer) (Turn, *Failure) {
	s.mu.Lock()
	defer s.mu.Unlock()
	item, current, failure := s.active(attempt)
	if failure != nil {
		return Turn{}, failure
	}
	turn := Turn{
		TurnID: newUUID(), Sequence: len(item.turns) + 1,
		ClientMessageID: current.submission.ClientMessageID, Question: current.submission.Question,
		AnswerLanguage: current.submission.AnswerLanguage, DatasetVersion: item.conversation.DatasetVersion,
		CreatedAt: s.now().UTC(), Answer: answer,
	}
	turn = cloneTurn(turn)
	if !turn.CreatedAt.Before(item.conversation.ExpiresAt) {
		s.expire(turn.CreatedAt)
		return Turn{}, notFound()
	}
	if !time.Now().Before(current.deadline) {
		failure := RequestTimeout()
		current.state, current.lastError = "failed", failure.Error
		return Turn{}, failure
	}
	item.turns = append(item.turns, turn)
	current.state, current.turn = "completed", turn
	item.pending = ""
	return cloneTurn(turn), nil
}

// Fail records a recoverable pending request, without appending a turn.
func (s *Store) Fail(attempt Attempt, failure *Failure) *Failure {
	s.mu.Lock()
	defer s.mu.Unlock()
	_, current, inactive := s.active(attempt)
	if inactive != nil {
		return inactive
	}
	if !time.Now().Before(current.deadline) {
		failure = RequestTimeout()
	}
	current.state, current.lastError = "failed", failure.Error
	return failure
}

// active must be called under the lock, including the final expiry check.
func (s *Store) active(attempt Attempt) (*record, *request, *Failure) {
	s.expire(s.now().UTC())
	item := s.conversations[attempt.conversationID]
	if item == nil {
		return nil, nil, notFound()
	}
	current := item.requests[attempt.messageID]
	if current == nil || current.state != "processing" || current.attemptID != attempt.attemptID {
		return nil, nil, conflict("request_superseded", "This attempt is no longer active.", false)
	}
	return item, current, nil
}

func RequestTimeout() *Failure {
	return &Failure{504, Error{"request_timeout", "The question attempt exceeded its deadline.", true}}
}

func notFound() *Failure {
	return &Failure{404, Error{"conversation_not_found", "The requested conversation is not available.", false}}
}

func conflict(code, message string, retryable bool) *Failure {
	return &Failure{409, Error{code, message, retryable}}
}

func cloneTurn(turn Turn) Turn {
	turn.Answer.Citations = slices.Clone(turn.Answer.Citations)
	return turn
}

func cloneTurns(turns []Turn) []Turn {
	result := make([]Turn, len(turns))
	for i, turn := range turns {
		result[i] = cloneTurn(turn)
	}
	return result
}
