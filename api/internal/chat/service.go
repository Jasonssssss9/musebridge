// Package chat runs bounded retrieval and fixture generation for one submission.
package chat

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"slices"
	"time"

	"github.com/Jasonssssss9/musebridge/api/internal/catalog"
	"github.com/Jasonssssss9/musebridge/api/internal/conversation"
	"github.com/Jasonssssss9/musebridge/api/internal/fixture"
	"github.com/Jasonssssss9/musebridge/api/internal/retrieval"
)

const (
	AttemptTimeout    = 8 * time.Second
	GenerationTimeout = 2 * time.Second
)

type Retriever interface {
	Retrieve(context.Context, retrieval.Request) (retrieval.Result, error)
}

type Generator interface {
	Generate(context.Context, fixture.Input) (fixture.Output, error)
}

type Service struct {
	store   *conversation.Store
	dataset *catalog.Dataset
	logger  *slog.Logger

	retriever Retriever
	generator Generator

	attemptTimeout, retrievalTimeout, generationTimeout time.Duration
}

func New(store *conversation.Store, dataset *catalog.Dataset, retriever Retriever, generator Generator, logger *slog.Logger) *Service {
	if logger == nil {
		logger = slog.New(slog.NewJSONHandler(io.Discard, nil))
	}
	return &Service{store: store, dataset: dataset, retriever: retriever, generator: generator, logger: logger,
		attemptTimeout: AttemptTimeout, retrievalTimeout: retrieval.RequestTimeout, generationTimeout: GenerationTimeout}
}

// Submit reserves once, then ignores browser cancellation while the accepted
// attempt runs under its own deadline. Replays perform no downstream work.
func (s *Service) Submit(ctx context.Context, token, id string, submission conversation.Submission, requestID string) (conversation.Turn, string, *conversation.Failure) {
	attempt, replay, failure := s.store.Begin(token, id, submission, s.attemptTimeout)
	if failure != nil {
		return conversation.Turn{}, "", failure
	}
	artworkID := attempt.Conversation.ArtworkID
	if replay != nil {
		s.logger.Info("question_replayed", "service", "api", "request_id", requestID,
			"artwork_id", artworkID, "dataset_version", attempt.Conversation.DatasetVersion, "code", "ok")
		return *replay, artworkID, nil
	}
	started := time.Now()
	var retrievalDuration, generationDuration time.Duration
	chunkIDs := make([]string, 0)
	code, status := "internal_error", 500
	defer func() {
		s.logger.Info("question_completed", "service", "api", "request_id", requestID,
			"artwork_id", artworkID, "dataset_version", attempt.Conversation.DatasetVersion,
			"code", code, "status", status, "chunk_ids", chunkIDs,
			"retrieval_ms", float64(retrievalDuration.Microseconds())/1000,
			"generation_ms", float64(generationDuration.Microseconds())/1000,
			"duration_ms", float64(time.Since(started).Microseconds())/1000)
	}()
	ctx, cancel := context.WithDeadline(context.WithoutCancel(ctx), attempt.Deadline)
	defer cancel()
	fail := func(failure *conversation.Failure) (conversation.Turn, string, *conversation.Failure) {
		failure = s.store.Fail(attempt, failure)
		code, status = failure.Code, failure.Status
		return conversation.Turn{}, artworkID, failure
	}
	query := fixture.Prepare(submission.Question, len(attempt.RecentTurns) > 0)
	stageStarted := time.Now()
	result, err := stage(ctx, s.retrievalTimeout, func(child context.Context) (retrieval.Result, error) {
		if s.retriever == nil {
			return retrieval.Result{}, errors.New("retrieval is unavailable")
		}
		return s.retriever.Retrieve(child, retrieval.Request{ArtworkID: artworkID, Query: query, RequestID: requestID})
	})
	retrievalDuration = time.Since(stageStarted)
	if ctx.Err() != nil {
		return fail(conversation.RequestTimeout())
	}
	if err != nil {
		if errors.Is(err, context.DeadlineExceeded) {
			return fail(problem(504, "retrieval_timeout", "The evidence service exceeded its deadline.", true))
		}
		var upstream *retrieval.Error
		if errors.As(err, &upstream) {
			switch upstream.Code {
			case "retrieval_invalid_response":
				return fail(problem(502, upstream.Code, "The evidence service returned an invalid response.", false))
			case "retrieval_timeout":
				return fail(problem(504, upstream.Code, "The evidence service exceeded its deadline.", true))
			}
		}
		return fail(problem(503, "retrieval_unavailable", "The evidence service is temporarily unavailable.", true))
	}
	if !s.validEvidence(attempt.Conversation, result) {
		return fail(problem(502, "retrieval_invalid_response", "The evidence service returned an invalid response.", false))
	}
	for _, evidence := range result.Evidence {
		chunkIDs = append(chunkIDs, evidence.ChunkID)
	}
	input := fixture.Input{
		ArtworkID: artworkID, DatasetVersion: attempt.Conversation.DatasetVersion,
		Question: submission.Question, RetrievalQuery: query, AnswerLanguage: submission.AnswerLanguage,
		RecentTurns: attempt.RecentTurns, Evidence: result.Evidence,
	}
	adapterInput := cloneInput(input)
	stageStarted = time.Now()
	output, err := stage(ctx, s.generationTimeout, func(child context.Context) (fixture.Output, error) {
		if s.generator == nil {
			return fixture.Output{}, errors.New("generation is unavailable")
		}
		return s.generator.Generate(child, adapterInput)
	})
	generationDuration = time.Since(stageStarted)
	if ctx.Err() != nil {
		return fail(conversation.RequestTimeout())
	}
	if err != nil {
		if errors.Is(err, context.DeadlineExceeded) {
			return fail(problem(504, "generation_timeout", "The answer adapter exceeded its deadline.", true))
		}
		return fail(problem(503, "generation_unavailable", "The answer adapter is temporarily unavailable.", true))
	}
	answer, err := fixture.Validate(input, output)
	if err != nil {
		return fail(problem(502, "generation_invalid_response", "The answer adapter returned an invalid response.", false))
	}
	turn, failure := s.store.Complete(attempt, answer)
	code, status = "ok", 200
	if failure != nil {
		code, status = failure.Code, failure.Status
	}
	return turn, artworkID, failure
}

func (s *Service) validEvidence(current conversation.Conversation, result retrieval.Result) bool {
	if s.dataset == nil || result.DatasetVersion != current.DatasetVersion || s.dataset.Version() != current.DatasetVersion ||
		result.Evidence == nil || len(result.Evidence) > retrieval.TopK {
		return false
	}
	seen := make(map[string]bool)
	for _, evidence := range result.Evidence {
		trusted, found := s.dataset.EvidenceByChunkID(evidence.ChunkID)
		if !found || seen[evidence.ChunkID] || evidence.ArtworkID != current.ArtworkID || evidence != trusted {
			return false
		}
		seen[evidence.ChunkID] = true
	}
	return true
}

func problem(status int, code, message string, retryable bool) *conversation.Failure {
	return &conversation.Failure{Status: status, Error: conversation.Error{Code: code, Message: message, Retryable: retryable}}
}

func cloneInput(input fixture.Input) fixture.Input {
	input.Evidence = slices.Clone(input.Evidence)
	input.RecentTurns = slices.Clone(input.RecentTurns)
	for i := range input.RecentTurns {
		input.RecentTurns[i].Answer.Citations = slices.Clone(input.RecentTurns[i].Answer.Citations)
	}
	return input
}

type stageResult[T any] struct {
	value T
	err   error
}

// A buffered result channel lets a late dependency finish without committing
// state or blocking on an abandoned response. The parent budget always wins.
func stage[T any](parent context.Context, timeout time.Duration, call func(context.Context) (T, error)) (T, error) {
	ctx, cancel := context.WithTimeout(parent, timeout)
	defer cancel()
	results := make(chan stageResult[T], 1)
	go func() {
		defer func() {
			if recover() != nil {
				results <- stageResult[T]{err: errors.New("dependency failed unexpectedly")}
			}
		}()
		value, err := call(ctx)
		results <- stageResult[T]{value, err}
	}()
	var result stageResult[T]
	var zero T
	select {
	case result = <-results:
	case <-ctx.Done():
		result.err = ctx.Err()
	}
	if parent.Err() != nil {
		return zero, parent.Err()
	}
	if ctx.Err() != nil {
		return zero, ctx.Err()
	}
	return result.value, result.err
}
