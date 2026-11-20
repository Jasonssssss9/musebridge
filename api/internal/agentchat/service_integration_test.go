//go:build integration

package agentchat

import (
	"context"
	"fmt"
	"sync/atomic"
	"testing"

	ac "github.com/Jasonssssss9/musebridge/api/internal/agentcontract"
	"github.com/Jasonssssss9/musebridge/api/internal/catalog"
	"github.com/Jasonssssss9/musebridge/api/internal/conversation"
	m1test "github.com/Jasonssssss9/musebridge/api/internal/testsupport/m1services"
)

// discardPOST loses the completed POST outcome at the Go transport-return
// boundary. The delegate still executes real HTTP -> FastAPI -> workflow ->
// HTTP retrieval. Reconciliation uses the real GET client and retained run.
// Physical socket loss is exercised separately by the controlled HTTP test.
type discardPOST struct {
	delegate    Agent
	posts, gets atomic.Int64
}

func (a *discardPOST) Run(ctx context.Context, run ac.RunRequest, trace string) (ac.RunSnapshot, error) {
	a.posts.Add(1)
	_, _ = a.delegate.Run(ctx, run, trace)
	return ac.RunSnapshot{}, privateFailure("agent_unavailable", 503, true, true)
}
func (a *discardPOST) Get(ctx context.Context, run ac.RunRequest, trace string) (ac.RunSnapshot, error) {
	a.gets.Add(1)
	return a.delegate.Get(ctx, run, trace)
}

func TestM2B2RealAgentRecoveryAndFailedRun(t *testing.T) {
	dataset, err := catalog.Load("../../../knowledge/data/aic-seed-v0.1.json", "aic-seed-v0.1")
	if err != nil {
		t.Fatal(err)
	}
	normal := m1test.Start(t, "none")
	store := conversation.New(nil)
	owner := ""
	for _, artwork := range []string{"aic-28560", "aic-11723"} {
		t.Run(artwork, func(t *testing.T) {
			var current ac.PublicConversation
			var created bool
			var failure *conversation.Failure
			owner, current, created, failure = store.OpenAgent(owner, dataset, artwork)
			if failure != nil || !created {
				t.Fatal("cannot open real Go conversation")
			}
			port := &discardPOST{delegate: normal.Client}
			service, err := New(store, port, nil)
			if err != nil {
				t.Fatal(err)
			}
			input := message(0)
			input.Question = "What material is this painting made of?"
			trace := fmt.Sprintf("m2-b2-%s-%s", artwork, string(current.ConversationID)[:8])
			response, failure := service.Submit(context.Background(), owner, string(current.ConversationID), input, trace)
			if failure != nil || response.Conversation.Revision != 1 || response.Turn.Sequence != 1 ||
				len(response.Turn.Answer.Citations) != 1 || response.Turn.Answer.Citations[0].ChunkID != ac.Some(artwork+"-medium") {
				t.Fatal("Go did not recover and commit the actual Agent answer", failure)
			}
			_, failure = service.Submit(context.Background(), owner, string(current.ConversationID), input, trace+"-replay")
			if failure != nil || port.posts.Load() != 1 || port.gets.Load() != 1 {
				t.Fatal("recovered submission was inferred again on replay")
			}
			assertLiveCalls(t, normal, trace, artwork, 2)
			t.Logf("recovered %s: trace=%s evidence=%s POST=1 GET=1 model=2 retrieval=1 revision=1",
				artwork, trace, artwork+"-medium")
		})
	}
	t.Run("retained-failure", func(t *testing.T) {
		foreign := m1test.Start(t, "foreign-evidence")
		failedStore := conversation.New(nil)
		owner, current, _, failure := failedStore.OpenAgent("", dataset, "aic-28560")
		if failure != nil {
			t.Fatal("cannot create failed-run test conversation")
		}
		port := &discardPOST{delegate: foreign.Client}
		service, err := New(failedStore, port, nil)
		if err != nil {
			t.Fatal(err)
		}
		input := message(0)
		input.Question = "What material is this painting made of?"
		trace := "m2-b2-foreign-" + string(current.ConversationID)[:8]
		_, failure = service.Submit(context.Background(), owner, string(current.ConversationID), input, trace)
		assertFailed(t, failedStore, owner, current, failure, 502, "retrieval_invalid_response")
		if port.posts.Load() != 1 || port.gets.Load() != 1 {
			t.Fatal("retained failure caused another execution or repeated queries")
		}
		assertLiveCalls(t, foreign, trace, "aic-28560", 1)
		t.Logf("failed run recovered: trace=%s error=retrieval_invalid_response POST=1 GET=1 revision=0", trace)
	})
}

func assertLiveCalls(t *testing.T, services *m1test.Services, trace, artwork string, models int) {
	t.Helper()
	modelCalls, lookups := 0, 0
	for _, event := range services.Audit(t) {
		if event.RequestID != trace {
			continue
		}
		if event.ArtworkID != artwork {
			t.Fatal("recovery trace crossed artwork scopes")
		}
		if event.Event == "model_call" {
			modelCalls++
		}
		if event.Event == "retrieval_http_response" {
			lookups++
			if event.DatasetVersion != "aic-seed-v0.1" || len(event.EvidenceIDs) != 1 {
				t.Fatal("recovery did not execute the actual released retrieval")
			}
		}
	}
	if modelCalls != models || lookups != 1 {
		t.Fatalf("reconciliation repeated inference or retrieval: model=%d retrieval=%d", modelCalls, lookups)
	}
}
