// Package agentchat owns Go submission lifecycle and reconciliation, not Agent
// reasoning, retrieval, model calls or public HTTP routing.
package agentchat

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"time"

	"github.com/Jasonssssss9/musebridge/api/internal/agentclient"
	ac "github.com/Jasonssssss9/musebridge/api/internal/agentcontract"
	"github.com/Jasonssssss9/musebridge/api/internal/conversation"
)

const (
	PostWait      = 56 * time.Second
	QueryWait     = 2 * time.Second
	PollInterval  = time.Second
	CommitReserve = 250 * time.Millisecond
)

// Agent is implemented by the existing private client. Run accepts only a
// completed result; Get may also return processing, a retained failed error or
// a lookup/transport failure. Neither operation creates replacement run IDs.
type Agent interface {
	Run(context.Context, ac.RunRequest, string) (ac.RunSnapshot, error)
	Get(context.Context, ac.RunRequest, string) (ac.RunSnapshot, error)
}

type Service struct {
	store     *conversation.Store
	agent     Agent
	validator *ac.Validator
	logger    *slog.Logger

	// Defaults are fixed contract budgets. Shorter values are used only by
	// package tests; no public configuration can extend the store's deadline.
	attemptLimit, postWait, queryWait, pollInterval, commitReserve time.Duration
}

func New(store *conversation.Store, agent Agent, logger *slog.Logger) (*Service, error) {
	if store == nil || agent == nil {
		return nil, fmt.Errorf("agentchat requires a conversation store and Agent client")
	}
	validator, err := ac.DefaultValidator()
	if err != nil {
		return nil, fmt.Errorf("Agent contract initialization failed")
	}
	if logger == nil {
		logger = slog.New(slog.NewJSONHandler(io.Discard, nil))
	}
	return &Service{store: store, agent: agent, validator: validator, logger: logger,
		attemptLimit: conversation.AgentAttemptBudget, postWait: PostWait,
		queryWait: QueryWait, pollInterval: PollInterval, commitReserve: CommitReserve}, nil
}

// Submit admits once and keeps accepted work alive after browser cancellation.
// Public handlers must validate Origin/cookies/body/trace before calling it.
// Completed replay and conflicts do not perform any downstream operation.
func (s *Service) Submit(parent context.Context, token, id string, message ac.PublicMessageRequest, requestID string) (ac.PublicSubmissionResponse, *conversation.Failure) {
	if parent == nil || !validTrace(requestID) {
		return ac.PublicSubmissionResponse{}, problem(400, "invalid_request", "The request is invalid.", false)
	}
	attempt, replay, failure := s.store.BeginAgent(token, id, message)
	if failure != nil {
		return ac.PublicSubmissionResponse{}, failure
	}
	if replay != nil {
		return *replay, nil
	}
	started := time.Now()
	deadline := minTime(attempt.Deadline, started.Add(s.attemptLimit))
	ctx, cancel := context.WithDeadline(context.WithoutCancel(parent), deadline)
	defer cancel()
	postCalls, queries, status, code := 0, 0, 500, "internal_error"
	defer func() {
		s.logger.Info("agent_submission_completed", "service", "api", "request_id", requestID,
			"run_id", attempt.Request.RunID, "artwork_id", attempt.Request.Scope.ArtworkID,
			"dataset_version", attempt.Request.Scope.DatasetVersion,
			"base_revision", attempt.Request.ConversationState.Revision,
			"post_calls", postCalls, "status_queries", queries, "status", status, "code", code,
			"duration_ms", float64(time.Since(started).Microseconds())/1000)
	}()
	fail := func(failure *conversation.Failure) (ac.PublicSubmissionResponse, *conversation.Failure) {
		if ctx.Err() != nil {
			failure = conversation.RequestTimeout()
		}
		failure = s.store.FailAgent(attempt, failure)
		status, code = failure.Status, failure.Code
		return ac.PublicSubmissionResponse{}, failure
	}
	// The run's private version selects its contract: agent-demo-v0.4
	// conversations (guide or Vincent) run on agent-run-v3.
	validator := s.validator
	if attempt.Request.ContractVersion == ac.PrivateVersionV3 {
		v3, err := ac.DefaultValidatorV3()
		if err != nil {
			return fail(invalidResponse())
		}
		validator = v3
	}
	// Keep independent encoded source bytes for every operation. A transport
	// cannot mutate the snapshot used for later GETs or result validation.
	frozen, err := validator.Encode("RunRequest", attempt.Request)
	if err != nil || validator.CheckRunRequest(attempt.Request) != nil {
		return fail(invalidResponse())
	}
	queryContext := ctx
	call := func(query bool) (ac.RunSnapshot, error) {
		var request ac.RunRequest
		if validator.Decode("RunRequest", frozen, &request) != nil {
			return ac.RunSnapshot{}, &agentclient.Error{Failure: ac.Failure{Status: 502,
				Error: ac.PublicError{Code: "agent_invalid_response", Message: "Invalid snapshot."}}}
		}
		limit, operation, operationContext := s.postWait, s.agent.Run, ctx
		if query {
			queries++
			limit, operation, operationContext = s.queryWait, s.agent.Get, queryContext
		} else {
			postCalls++
		}
		return invoke(operationContext, limit, func(child context.Context) (ac.RunSnapshot, error) {
			return operation(child, request, requestID)
		})
	}
	snapshot, err := call(false)
	if ctx.Err() != nil {
		return fail(conversation.RequestTimeout())
	}
	if err != nil {
		mapped, reconcile, missing, terminal := transportFailure(err)
		if missing || terminal || !reconcile {
			return fail(mapped)
		}
		var stopQueries context.CancelFunc
		queryContext, stopQueries = context.WithDeadline(ctx, deadline.Add(-s.commitReserve))
		defer stopQueries()
		for {
			// One second after the preceding call, including the uncertain POST.
			// This wait and each query consume the same accepted-attempt budget.
			timer := time.NewTimer(s.pollInterval)
			select {
			case <-queryContext.Done():
				timer.Stop()
				return fail(unavailable())
			case <-timer.C:
			}
			snapshot, err = call(true)
			if ctx.Err() != nil {
				return fail(conversation.RequestTimeout())
			}
			if err != nil {
				mapped, _, missing, terminal = transportFailure(err)
				if missing || terminal || !mapped.Retryable {
					return fail(mapped)
				}
				continue
			}
			if checkSnapshot(validator, attempt.Request, snapshot, true) != nil {
				return fail(invalidResponse())
			}
			if snapshot.State == "processing" {
				continue
			}
			break
		}
	}
	if checkSnapshot(validator, attempt.Request, snapshot, false) != nil {
		return fail(invalidResponse())
	}
	if ctx.Err() != nil {
		return fail(conversation.RequestTimeout())
	}
	response, failure := s.store.CompleteAgent(attempt, *snapshot.Result)
	if failure != nil {
		status, code = failure.Status, failure.Code
		return ac.PublicSubmissionResponse{}, failure
	}
	status, code = 200, "ok"
	return response, nil
}

func checkSnapshot(validator *ac.Validator, run ac.RunRequest, snapshot ac.RunSnapshot, allowProcessing bool) error {
	if _, err := validator.Encode("RunSnapshot", snapshot); err != nil || snapshot.RunID != run.RunID {
		return fmt.Errorf("invalid Agent snapshot")
	}
	if snapshot.State == "processing" && allowProcessing && snapshot.Result == nil && snapshot.Error == nil {
		return nil
	}
	if snapshot.State != "completed" || snapshot.Result == nil || snapshot.Error != nil {
		return fmt.Errorf("invalid Agent terminal state")
	}
	return validator.CheckRunResult(run, *snapshot.Result)
}

// invoke bounds even an injected client which ignores cancellation. Results
// arriving after this operation returns cannot reach the store or block senders.
func invoke(parent context.Context, limit time.Duration, operation func(context.Context) (ac.RunSnapshot, error)) (ac.RunSnapshot, error) {
	if err := parent.Err(); err != nil {
		return ac.RunSnapshot{}, err
	}
	child, cancel := context.WithTimeout(parent, limit)
	defer cancel()
	if err := child.Err(); err != nil {
		return ac.RunSnapshot{}, err
	}
	type result struct {
		snapshot ac.RunSnapshot
		err      error
	}
	finished := make(chan result, 1)
	go func() {
		defer func() {
			if recover() != nil {
				finished <- result{err: fmt.Errorf("Agent operation failed")}
			}
		}()
		snapshot, err := operation(child)
		finished <- result{snapshot, err}
	}()
	select {
	case <-child.Done():
		return ac.RunSnapshot{}, child.Err()
	case value := <-finished:
		if child.Err() != nil {
			return ac.RunSnapshot{}, child.Err()
		}
		return value.snapshot, value.err
	}
}

func transportFailure(err error) (failure *conversation.Failure, reconcile, missing, terminal bool) {
	// A child operation may expire before the overall attempt. Its outcome is
	// uncertain even though the single-call client calls that context a parent.
	if errors.Is(err, context.DeadlineExceeded) || errors.Is(err, context.Canceled) {
		return unavailable(), true, false, false
	}
	var upstream *agentclient.Error
	if !errors.As(err, &upstream) || upstream == nil {
		return unavailable(), true, false, false
	}
	safe, invalid := ac.SafeAgentFailure(upstream.Failure)
	if invalid != nil {
		return invalidResponse(), false, false, false
	}
	if upstream.RunState != "" && (upstream.RunState != "failed" || safe.Reconcile) {
		return invalidResponse(), false, false, false
	}
	if upstream.RunMissing && (upstream.RunState != "" || safe.Status != 503 || safe.Error.Code != "agent_unavailable" ||
		!safe.Error.Retryable || safe.Reconcile) {
		return invalidResponse(), false, false, false
	}
	failure = &conversation.Failure{Status: safe.Status, Error: conversation.Error{
		Code: safe.Error.Code, Message: safe.Error.Message, Retryable: safe.Error.Retryable}}
	return failure, safe.Reconcile, upstream.RunMissing, upstream.RunState == "failed"
}

func validTrace(value string) bool {
	if len(value) == 0 || len(value) > 128 {
		return false
	}
	for i := range value {
		if value[i] < 33 || value[i] > 126 {
			return false
		}
	}
	return true
}

func minTime(a, b time.Time) time.Time {
	if a.Before(b) {
		return a
	}
	return b
}

func problem(status int, code, message string, retryable bool) *conversation.Failure {
	return &conversation.Failure{Status: status, Error: conversation.Error{Code: code, Message: message, Retryable: retryable}}
}
func unavailable() *conversation.Failure {
	return problem(503, "agent_unavailable", "The Agent service is unavailable.", true)
}
func invalidResponse() *conversation.Failure {
	return problem(502, "agent_invalid_response", "The Agent service returned an invalid response.", false)
}
