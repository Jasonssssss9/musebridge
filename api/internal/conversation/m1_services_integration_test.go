//go:build integration

package conversation

import (
	"fmt"
	"reflect"
	"strings"
	"testing"

	"github.com/Jasonssssss9/musebridge/api/internal/agentclient"
	m1test "github.com/Jasonssssss9/musebridge/api/internal/testsupport/m1services"
)

type m1Services struct {
	connection m1test.Connection
	client     *agentclient.Client
	services   *m1test.Services
}

func startM1Services(t *testing.T, fault string) *m1Services {
	services := m1test.Start(t, fault)
	return &m1Services{connection: services.Connection, client: services.Client, services: services}
}

func (h *m1Services) audit(t *testing.T) []m1test.Event {
	return h.services.Audit(t)
}

// assertM1Trace binds the actual retrieval HTTP response and tool acceptance to
// the same Go trace. Answer prose alone cannot distinguish these two artworks.
func assertM1Trace(t *testing.T, harness *m1Services, trace, artwork string, foreign bool) string {
	t.Helper()
	expected, actual := artwork+"-medium", artwork+"-medium"
	if foreign {
		actual = "aic-11723-medium"
	}
	counts := make(map[string]int)
	for _, event := range harness.audit(t) {
		if event.RequestID != trace {
			continue
		}
		if event.ArtworkID != artwork {
			t.Fatal("a trace crossed the active artwork boundary")
		}
		counts[event.Event]++
		switch event.Event {
		case "retrieval_http_response":
			if event.Status != 200 || event.DatasetVersion != "aic-seed-v0.1" ||
				!reflect.DeepEqual(event.OriginalEvidenceIDs, []string{expected}) ||
				!reflect.DeepEqual(event.EvidenceIDs, []string{actual}) || event.Fault != harness.connection.Fault {
				t.Fatal("the actual HTTP lookup did not return the expected scoped evidence")
			}
			if foreign && (event.InjectionBoundary == nil || *event.InjectionBoundary != "retrieval HTTP response serialization") {
				t.Fatal("the foreign record was not injected at the documented HTTP boundary")
			}
		case "retrieval_accepted":
			if !reflect.DeepEqual(event.EvidenceIDs, []string{expected}) {
				t.Fatal("the tool accepted unexpected evidence")
			}
		case "retrieval_rejected":
			if event.Code != "retrieval_invalid_response" || event.Phase != "retrieval" || event.Boundary != "RetrievalTool.search" {
				t.Fatal("foreign evidence was not rejected by the real retrieval tool")
			}
		case "model_call":
			counts["model_"+event.Operation]++
		default:
			t.Fatalf("unexpected trace event %q", event.Event)
		}
	}
	want := map[string]int{"retrieval_http_response": 1, "retrieval_accepted": 1,
		"model_call": 2, "model_plan": 1, "model_assess": 1}
	if foreign {
		want = map[string]int{"retrieval_http_response": 1, "retrieval_rejected": 1, "model_call": 1, "model_plan": 1}
	}
	if !reflect.DeepEqual(counts, want) {
		t.Fatalf("missing or repeated real-service operations: got %v, expected %v", counts, want)
	}
	return actual
}

func m1Trace(attempt AgentAttempt, name string) string {
	return fmt.Sprintf("m1-go-%s-%s", name, strings.ReplaceAll(string(attempt.Request.RunID), "-", "")[:8])
}
