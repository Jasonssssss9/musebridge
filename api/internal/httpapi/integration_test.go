//go:build integration

package httpapi

import (
	"encoding/json"
	"net/http"
	"os"
	"testing"

	"github.com/Jasonssssss9/musebridge/api/internal/conversation"
	"github.com/Jasonssssss9/musebridge/api/internal/retrieval"
)

// TestPythonAnswerIntegration exercises the public message contract with the
// actual Python service. It never starts or stops the configured service.
func TestPythonAnswerIntegration(t *testing.T) {
	baseURL := os.Getenv("RETRIEVAL_BASE_URL")
	if baseURL == "" {
		t.Skip("set RETRIEVAL_BASE_URL to a running Python retrieval service")
	}
	dataset := testDataset(t)
	client, err := retrieval.New(baseURL, dataset)
	if err != nil {
		t.Fatal(err)
	}
	defer client.CloseIdleConnections()
	h := New(dataset, nil, Settings{PublicBaseURL: browserOrigin}, client)
	data, err := os.ReadFile("../../../eval/aic-seed-v0.1.tests.json")
	if err != nil {
		t.Fatal(err)
	}
	var gold struct {
		Cases []struct {
			CaseID         string   `json:"case_id"`
			ArtworkID      string   `json:"artwork_id"`
			Question       string   `json:"question"`
			AnswerLanguage string   `json:"answer_language"`
			ExpectedStatus string   `json:"expected_status"`
			ExpectedChunks []string `json:"expected_chunk_ids"`
		} `json:"cases"`
	}
	if json.Unmarshal(data, &gold) != nil || len(gold.Cases) != 6 {
		t.Fatal("unexpected frozen case file")
	}
	var cookie *http.Cookie
	conversations := make(map[string]conversation.Conversation)
	for i, entry := range gold.Cases {
		t.Run(entry.CaseID, func(t *testing.T) {
			current, found := conversations[entry.ArtworkID]
			if !found {
				current, cookie, _ = openSession(t, h, entry.ArtworkID, cookie)
				conversations[entry.ArtworkID] = current
			}
			input := conversation.Submission{ClientMessageID: messageID(i + 1), Question: entry.Question, AnswerLanguage: entry.AnswerLanguage}
			turn := decodeTurn(t, postSubmission(h, current.ConversationID, input, cookie))
			if turn.Answer.Status != entry.ExpectedStatus || len(turn.Answer.Citations) != len(entry.ExpectedChunks) || turn.DatasetVersion != current.DatasetVersion {
				t.Fatalf("unexpected frozen answer: %+v", turn.Answer)
			}
			for j, citation := range turn.Answer.Citations {
				trusted, _ := dataset.EvidenceByChunkID(entry.ExpectedChunks[j])
				if citation != trusted || citation.ArtworkID != current.ArtworkID {
					t.Fatal("citation lost source provenance or crossed artworks")
				}
			}
			replay := decodeTurn(t, postSubmission(h, current.ConversationID, input, cookie))
			if replay.TurnID != turn.TurnID || replay.Sequence != turn.Sequence {
				t.Fatal("replay did not recover the frozen completed turn")
			}
		})
	}
	for i, entry := range []struct{ artwork, question, language, chunk string }{
		{"aic-28560", "馆方如何描述这件作品的笔触？", "zh-CN", "aic-28560-description-01"},
		{"aic-11723", "馆方如何描述这件作品的笔触？", "zh-CN", "aic-11723-description-01"},
		{"aic-28560", "What material is this artwork made of?", "en", "aic-28560-medium"},
		{"aic-11723", "How does the museum describe this artwork's brushwork?", "en", "aic-11723-description-01"},
		{"aic-28560", "Which elements might suggest nervous energy, according to the museum?", "en", "aic-28560-description-01"},
		{"aic-28560", "What about the brushwork?", "en", "aic-28560-description-01"},
		{"aic-11723", "那它是用什么做的？", "zh-CN", "aic-11723-medium"},
		{"aic-28560", "Ｗｈａｔ　ｍａｔｅｒｉａｌ　ｉｓ　ｔｈｉｓ　ａｒｔｗｏｒｋ　ｍａｄｅ　ｏｆ？", "en", "aic-28560-medium"},
	} {
		t.Run(entry.artwork+"/"+entry.question, func(t *testing.T) {
			current := conversations[entry.artwork]
			input := conversation.Submission{ClientMessageID: messageID(100 + i), Question: entry.question, AnswerLanguage: entry.language}
			turn := decodeTurn(t, postSubmission(h, current.ConversationID, input, cookie))
			if turn.Question != entry.question || turn.Answer.Status != "answered" || len(turn.Answer.Citations) != 1 || turn.Answer.Citations[0].ChunkID != entry.chunk {
				t.Fatalf("real-service language/scope/follow-up check failed: %+v", turn.Answer)
			}
		})
	}
	current, freshCookie, _ := openSession(t, h, "aic-28560", nil)
	input := conversation.Submission{ClientMessageID: messageID(200), Question: "What about the brushwork?", AnswerLanguage: "en"}
	turn := decodeTurn(t, postSubmission(h, current.ConversationID, input, freshCookie))
	if turn.Answer.Status == "answered" || len(turn.Answer.Citations) != 0 {
		t.Fatal("a fresh conversation inherited follow-up context")
	}
}
