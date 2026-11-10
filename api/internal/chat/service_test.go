package chat

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/Jasonssssss9/musebridge/api/internal/catalog"
	"github.com/Jasonssssss9/musebridge/api/internal/conversation"
	"github.com/Jasonssssss9/musebridge/api/internal/fixture"
	"github.com/Jasonssssss9/musebridge/api/internal/retrieval"
)

type retrieverFunc func(context.Context, retrieval.Request) (retrieval.Result, error)

func (f retrieverFunc) Retrieve(ctx context.Context, r retrieval.Request) (retrieval.Result, error) {
	return f(ctx, r)
}

type generatorFunc func(context.Context, fixture.Input) (fixture.Output, error)

func (f generatorFunc) Generate(ctx context.Context, in fixture.Input) (fixture.Output, error) {
	return f(ctx, in)
}

func setup(t *testing.T) (*Service, string, conversation.Conversation) {
	t.Helper()
	dataset, err := catalog.Load("../../../knowledge/data/aic-seed-v0.1.json", "aic-seed-v0.1")
	if err != nil {
		t.Fatal(err)
	}
	store := conversation.New(nil)
	token, current, _ := store.Open("", "aic-28560", dataset.Version())
	service := New(store, dataset, nil, fixture.Adapter{}, nil)
	service.retriever = goodRetriever(dataset)
	return service, token, current
}

func goodRetriever(dataset *catalog.Dataset) Retriever {
	return retrieverFunc(func(_ context.Context, request retrieval.Request) (retrieval.Result, error) {
		id := request.ArtworkID + "-medium"
		if strings.Contains(request.Query, "brushwork") {
			id = request.ArtworkID + "-description-01"
		}
		evidence, _ := dataset.EvidenceByChunkID(id)
		return retrieval.Result{DatasetVersion: dataset.Version(), Evidence: []catalog.Evidence{evidence}}, nil
	})
}

func submission(id string) conversation.Submission {
	return conversation.Submission{ClientMessageID: id, Question: "What material is this artwork made of?", AnswerLanguage: "en"}
}

const firstID = "0dd2a1f3-8e81-4266-926a-cbe04584ee84"
const secondID = "0dd2a1f3-8e81-4266-926a-cbe04584ee85"

func TestPipelineContextAndReplay(t *testing.T) {
	service, token, current := setup(t)
	var calls atomic.Int32
	var lock sync.Mutex
	var requests []retrieval.Request
	var inputs []fixture.Input
	baseRetriever := service.retriever
	service.retriever = retrieverFunc(func(ctx context.Context, request retrieval.Request) (retrieval.Result, error) {
		calls.Add(1)
		lock.Lock()
		requests = append(requests, request)
		lock.Unlock()
		return baseRetriever.Retrieve(ctx, request)
	})
	service.generator = generatorFunc(func(ctx context.Context, input fixture.Input) (fixture.Output, error) {
		lock.Lock()
		inputs = append(inputs, input)
		lock.Unlock()
		return (fixture.Adapter{}).Generate(ctx, input)
	})
	first := submission(firstID)
	first.AnswerLanguage = "zh-CN"
	turn, _, failed := service.Submit(context.Background(), token, current.ConversationID, first, "req-first")
	if failed != nil || turn.Answer.Status != "answered" || turn.Answer.Text != "馆方记录的材质是布面油画。" {
		t.Fatalf("pipeline failed: %+v", failed)
	}
	replay, _, failed := service.Submit(context.Background(), token, current.ConversationID, first, "req-replay")
	if failed != nil || !reflect.DeepEqual(turn, replay) || calls.Load() != 1 || len(inputs) != 1 {
		t.Fatal("replay invoked dependencies or changed the turn")
	}
	followUp := submission(secondID)
	followUp.Question = "What about the brushwork?"
	next, _, failed := service.Submit(context.Background(), token, current.ConversationID, followUp, "req-followup")
	if failed != nil || next.Question != followUp.Question || next.Answer.Citations[0].ChunkID != "aic-28560-description-01" || next.Sequence != 2 {
		t.Fatal("follow-up did not use fresh in-scope evidence")
	}
	if requests[1].Query != "How does the museum describe this artwork's brushwork?" || requests[1].ArtworkID != current.ArtworkID || requests[1].RequestID != "req-followup" || len(inputs[1].RecentTurns) != 1 {
		t.Fatal("prepared query, scope, request ID or recent context was lost")
	}
	_, other, _ := service.store.Open(token, "aic-11723", current.DatasetVersion)
	otherTurn, _, failed := service.Submit(context.Background(), token, other.ConversationID, followUp, "req-other")
	if failed != nil || otherTurn.Answer.Status == "answered" || requests[2].Query != followUp.Question || len(inputs[2].RecentTurns) != 0 {
		t.Fatal("follow-up context crossed artwork conversations")
	}
}

func TestGeneratorCannotMutateTrustedEvidence(t *testing.T) {
	service, token, current := setup(t)
	service.generator = generatorFunc(func(ctx context.Context, input fixture.Input) (fixture.Output, error) {
		input.Evidence[0].Excerpt = "adapter mutation"
		input.Evidence[0].SourceURL = "https://invented.example"
		return (fixture.Adapter{}).Generate(ctx, input)
	})
	turn, _, failed := service.Submit(context.Background(), token, current.ConversationID, submission(firstID), "req-isolation")
	trusted, _ := service.dataset.EvidenceByChunkID("aic-28560-medium")
	if failed != nil || len(turn.Answer.Citations) != 1 || turn.Answer.Citations[0] != trusted {
		t.Fatal("adapter mutations escaped into citations")
	}
}

func TestFailuresRetainPendingWithoutAnswers(t *testing.T) {
	for _, tc := range []struct {
		name, code string
		status     int
		retryable  bool
	}{
		{"unavailable", "retrieval_unavailable", 503, true},
		{"bad response", "retrieval_invalid_response", 502, false},
		{"timeout", "retrieval_timeout", 504, true},
		{"altered excerpt", "retrieval_invalid_response", 502, false},
		{"foreign evidence", "retrieval_invalid_response", 502, false},
		{"wrong version", "retrieval_invalid_response", 502, false},
		{"generation failure", "generation_unavailable", 503, true},
		{"generation panic", "generation_unavailable", 503, true},
		{"invalid citation", "generation_invalid_response", 502, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			service, token, current := setup(t)
			base := service.retriever
			service.retriever = retrieverFunc(func(ctx context.Context, request retrieval.Request) (retrieval.Result, error) {
				switch tc.name {
				case "unavailable":
					return retrieval.Result{}, errors.New("secret transport diagnostic")
				case "bad response":
					return retrieval.Result{}, &retrieval.Error{Code: "retrieval_invalid_response", HTTPStatus: 502, Message: "secret upstream message"}
				case "timeout":
					return retrieval.Result{}, &retrieval.Error{Code: "retrieval_timeout", HTTPStatus: 504, Message: "secret upstream message"}
				}
				result, err := base.Retrieve(ctx, request)
				switch tc.name {
				case "altered excerpt":
					result.Evidence[0].Excerpt = "altered"
				case "foreign evidence":
					result.Evidence[0], _ = service.dataset.EvidenceByChunkID("aic-11723-medium")
				case "wrong version":
					result.DatasetVersion = "wrong-release"
				}
				return result, err
			})
			var generationCalls atomic.Int32
			service.generator = generatorFunc(func(ctx context.Context, input fixture.Input) (fixture.Output, error) {
				generationCalls.Add(1)
				if tc.name == "generation failure" {
					return fixture.Output{}, errors.New("secret model diagnostic")
				}
				if tc.name == "generation panic" {
					panic("secret model diagnostic")
				}
				output, err := (fixture.Adapter{}).Generate(ctx, input)
				if tc.name == "invalid citation" {
					output.CitationChunkIDs = []string{"aic-11723-medium"}
				}
				return output, err
			})
			turn, _, failed := service.Submit(context.Background(), token, current.ConversationID, submission(firstID), "req-failure")
			if failed == nil || failed.Code != tc.code || failed.Status != tc.status || failed.Retryable != tc.retryable || turn.TurnID != "" || strings.Contains(failed.Message, "secret") {
				t.Fatalf("incorrect safe failure: %+v", failed)
			}
			if strings.HasPrefix(tc.code, "retrieval_") && generationCalls.Load() != 0 {
				t.Fatal("generation ran after retrieval failed")
			}
			history, _ := service.store.History(token, current.ConversationID)
			if len(history.Turns) != 0 || history.PendingRequest == nil || history.PendingRequest.State != "failed" || history.PendingRequest.LastError.Code != tc.code {
				t.Fatal("technical failure became an answer or lost the pending request")
			}
		})
	}
}

func TestStageDeadlinesAndLateResults(t *testing.T) {
	for _, target := range []string{"retrieval", "generation", "attempt"} {
		t.Run(target, func(t *testing.T) {
			service, token, current := setup(t)
			service.attemptTimeout, service.retrievalTimeout, service.generationTimeout = time.Second, time.Second, time.Second
			code := "retrieval_timeout"
			if target == "retrieval" {
				service.retrievalTimeout = 75 * time.Millisecond
			}
			if target == "generation" {
				service.generationTimeout = 75 * time.Millisecond
				code = "generation_timeout"
			}
			if target == "attempt" {
				service.attemptTimeout = 75 * time.Millisecond
				code = "request_timeout"
			}
			release, finished := make(chan struct{}), make(chan struct{})
			base := service.retriever
			if target == "generation" {
				service.generator = generatorFunc(func(_ context.Context, input fixture.Input) (fixture.Output, error) {
					defer close(finished)
					<-release
					return (fixture.Adapter{}).Generate(context.Background(), input)
				})
			} else {
				service.retriever = retrieverFunc(func(_ context.Context, request retrieval.Request) (retrieval.Result, error) {
					defer close(finished)
					<-release
					return base.Retrieve(context.Background(), request)
				})
			}
			turn, _, failed := service.Submit(context.Background(), token, current.ConversationID, submission(firstID), "req-deadline")
			close(release)
			<-finished
			if failed == nil || failed.Code != code || turn.TurnID != "" {
				t.Fatalf("incorrect deadline classification: %+v", failed)
			}
			history, _ := service.store.History(token, current.ConversationID)
			if len(history.Turns) != 0 || history.PendingRequest.State != "failed" {
				t.Fatal("late success committed after the deadline")
			}
			service.retriever, service.generator = goodRetriever(service.dataset), fixture.Adapter{}
			service.attemptTimeout, service.retrievalTimeout, service.generationTimeout = time.Second, time.Second, time.Second
			turn, _, failed = service.Submit(context.Background(), token, current.ConversationID, submission(firstID), "req-retry")
			if failed != nil || turn.Sequence != 1 {
				t.Fatal("original UUID could not retry after removing the injected failure")
			}
		})
	}
}

func TestAcceptedAttemptSurvivesBrowserCancellation(t *testing.T) {
	service, token, current := setup(t)
	entered, release := make(chan struct{}), make(chan struct{})
	base := service.retriever
	service.retriever = retrieverFunc(func(ctx context.Context, request retrieval.Request) (retrieval.Result, error) {
		close(entered)
		<-release
		return base.Retrieve(ctx, request)
	})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan *conversation.Failure, 1)
	go func() {
		_, _, failure := service.Submit(ctx, token, current.ConversationID, submission(firstID), "req-disconnect")
		done <- failure
	}()
	<-entered
	cancel()
	close(release)
	if failed := <-done; failed != nil {
		t.Fatal("browser cancellation interrupted an accepted attempt")
	}
	history, _ := service.store.History(token, current.ConversationID)
	if len(history.Turns) != 1 || history.PendingRequest != nil {
		t.Fatal("accepted attempt was not recoverable after disconnect")
	}
}
