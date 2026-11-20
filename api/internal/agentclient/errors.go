package agentclient

import (
	"context"
	"errors"
	"net/http"

	ac "github.com/Jasonssssss9/musebridge/api/internal/agentcontract"
)

// Error carries a safe application failure. Reconcile requests a bounded GET
// for the same run before applying the fallback; it never authorizes a POST retry.
// Only context sentinels are retained as causes, never URLs or upstream bodies.
type Error struct {
	Failure ac.Failure
	// RunState is "failed" only for a validated retained failed GET snapshot.
	// Empty means no terminal run state was established by this error.
	RunState string
	// RunMissing identifies a valid agent_run_not_found envelope. It never
	// authorizes recreating the run and is not a retained terminal state.
	RunMissing bool
	cause      error
}

func (e *Error) Error() string { return e.Failure.Error.Message }
func (e *Error) Unwrap() error { return e.cause }

func invalidResponse() *Error {
	return &Error{Failure: ac.Failure{Status: http.StatusBadGateway, Error: ac.PublicError{
		Code: "agent_invalid_response", Message: "The Agent service returned an invalid response.",
	}}}
}

func unavailable(reconcile bool, cause error) *Error {
	return &Error{Failure: ac.Failure{Status: http.StatusServiceUnavailable, Reconcile: reconcile,
		Error: ac.PublicError{Code: "agent_unavailable", Message: "The Agent service is unavailable.", Retryable: true}},
		cause: cause}
}

func timedOut() *Error {
	return &Error{Failure: ac.Failure{Status: http.StatusGatewayTimeout, Error: ac.PublicError{
		Code: "request_timeout", Message: "The request exceeded its deadline.", Retryable: true,
	}}, cause: context.DeadlineExceeded}
}

func contextFailure(parent, attempt context.Context) error {
	if errors.Is(parent.Err(), context.DeadlineExceeded) {
		return timedOut()
	}
	if errors.Is(parent.Err(), context.Canceled) {
		return unavailable(true, context.Canceled)
	}
	if attempt.Err() != nil {
		return unavailable(true, attempt.Err())
	}
	return nil
}
