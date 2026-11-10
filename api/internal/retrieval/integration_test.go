//go:build integration

package retrieval

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"testing"
)

// TestPythonServiceIntegration requires a separately running real C service.
// It exercises retrieval only; gold answer statuses belong to the answer adapter.
func TestPythonServiceIntegration(t *testing.T) {
	baseURL := os.Getenv("RETRIEVAL_BASE_URL")
	if baseURL == "" {
		t.Skip("set RETRIEVAL_BASE_URL to a running Python service")
	}
	client := testClient(t, baseURL)
	data, err := os.ReadFile("../../../eval/aic-seed-v0.1.tests.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixtures struct {
		DatasetVersion string `json:"dataset_version"`
		Cases          []struct {
			CaseID           string   `json:"case_id"`
			ArtworkID        string   `json:"artwork_id"`
			Question         string   `json:"question"`
			ExpectedChunkIDs []string `json:"expected_chunk_ids"`
		} `json:"cases"`
		CrossChecks []struct {
			CheckID  string `json:"check_id"`
			Query    string `json:"query"`
			Variants []struct {
				ArtworkID            string `json:"artwork_id"`
				ExpectedFirstChunkID string `json:"expected_first_chunk_id"`
			} `json:"variants"`
		} `json:"cross_artwork_checks"`
	}
	if err := json.Unmarshal(data, &fixtures); err != nil {
		t.Fatal(err)
	}
	if fixtures.DatasetVersion != client.dataset.Version() || len(fixtures.Cases) != 6 || len(fixtures.CrossChecks) != 1 {
		t.Fatal("unexpected frozen integration inputs")
	}
	for index, fixture := range fixtures.Cases {
		t.Run(fixture.CaseID, func(t *testing.T) {
			result, err := client.Retrieve(context.Background(), Request{
				ArtworkID: fixture.ArtworkID, Query: fixture.Question, RequestID: fmt.Sprintf("req-integration-frozen-%d", index),
			})
			if err != nil {
				t.Fatal(err)
			}
			found := make(map[string]bool)
			for _, evidence := range result.Evidence {
				found[evidence.ChunkID] = true
			}
			for _, id := range fixture.ExpectedChunkIDs {
				if !found[id] {
					t.Errorf("real C omitted required evidence %s", id)
				}
			}
		})
	}
	for _, check := range fixtures.CrossChecks {
		for _, variant := range check.Variants {
			t.Run(check.CheckID+"/"+variant.ArtworkID, func(t *testing.T) {
				result, err := client.Retrieve(context.Background(), Request{
					ArtworkID: variant.ArtworkID, Query: check.Query, RequestID: "req-integration-brushwork-" + variant.ArtworkID,
				})
				if err != nil || len(result.Evidence) == 0 || result.Evidence[0].ChunkID != variant.ExpectedFirstChunkID {
					t.Fatalf("real C brushwork scope/ranking mismatch: %+v %v", result, err)
				}
			})
		}
	}
	for _, fixture := range []struct{ name, query, expected string }{
		{"English material", "What material is this artwork made of?", "aic-28560-medium"},
		{"valid empty result", "unicorn", ""},
	} {
		t.Run(fixture.name, func(t *testing.T) {
			result, err := client.Retrieve(context.Background(), Request{
				ArtworkID: "aic-28560", Query: fixture.query, RequestID: "req-integration-extra",
			})
			if err != nil {
				t.Fatal(err)
			}
			if fixture.expected == "" {
				if result.Evidence == nil || len(result.Evidence) != 0 {
					t.Fatal("no-hit response did not remain a successful empty array")
				}
			} else if len(result.Evidence) == 0 || result.Evidence[0].ChunkID != fixture.expected {
				t.Fatal("English query did not return the expected evidence")
			}
		})
	}
}
