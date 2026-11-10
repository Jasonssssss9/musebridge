package fixture

import (
	"context"
	"strings"
	"testing"

	"github.com/Jasonssssss9/musebridge/api/internal/catalog"
	"github.com/Jasonssssss9/musebridge/api/internal/conversation"
)

func fixtureInput(t *testing.T, artwork, question, language string, ids ...string) Input {
	t.Helper()
	dataset, err := catalog.Load("../../../knowledge/data/aic-seed-v0.1.json", "aic-seed-v0.1")
	if err != nil {
		t.Fatal(err)
	}
	input := Input{ArtworkID: artwork, DatasetVersion: dataset.Version(), Question: question,
		RetrievalQuery: Prepare(question, false), AnswerLanguage: language, Evidence: make([]catalog.Evidence, 0)}
	for _, id := range ids {
		evidence, found := dataset.EvidenceByChunkID(id)
		if !found {
			t.Fatal("test evidence is absent from the release")
		}
		input.Evidence = append(input.Evidence, evidence)
	}
	return input
}

func TestReviewedFixtureOutcomes(t *testing.T) {
	for _, tc := range []struct{ artwork, question, language, evidence, status, text string }{
		{"aic-28560", materialQuery, "en", "aic-28560-medium", "answered", "oil painting on canvas"},
		{"aic-11723", "这件作品用了什么材料？", "zh-CN", "aic-11723-medium", "answered", "布面油画"},
		{"aic-28560", brushworkQuery, "en", "aic-28560-description-01", "answered", "thick, broken brushwork"},
		{"aic-11723", "馆方如何描述这件作品的笔触？", "zh-CN", "aic-11723-description-01", "answered", "羽毛般的笔触"},
		{"aic-28560", "Which elements might suggest nervous energy, according to the museum?", "en", "aic-28560-description-01", "answered", "may create"},
		{"aic-28560", "馆方认为，画面里的哪些元素可能让人感到紧张？", "zh-CN", "aic-28560-description-01", "answered", "可能"},
		{"aic-28560", "How many hours did Van Gogh actually spend painting the Chicago version?", "en", "aic-28560-medium", "insufficient", "does not state how many hours"},
		{"aic-28560", "梵高画完芝加哥收藏的这一版，实际花了多少小时？", "zh-CN", "aic-28560-medium", "insufficient", "没有说明"},
		{"aic-11723", "What is the woman's real name?", "en", "aic-11723-description-01", "insufficient", "does not establish"},
		{"aic-11723", "画中女子的真实姓名是什么？", "zh-CN", "aic-11723-description-01", "insufficient", "无法确定"},
		{"aic-28560", "Why did the artist choose this material?", "en", "aic-28560-medium", "unsupported_question", "outside the supported examples"},
		{"aic-11723", "Which elements might suggest nervous energy, according to the museum?", "en", "aic-11723-description-01", "unsupported_question", "outside the supported examples"},
	} {
		t.Run(tc.artwork+"/"+tc.question, func(t *testing.T) {
			input := fixtureInput(t, tc.artwork, tc.question, tc.language, tc.evidence)
			output, err := (Adapter{}).Generate(context.Background(), input)
			if err != nil {
				t.Fatal(err)
			}
			answer, err := Validate(input, output)
			if err != nil || answer.Status != tc.status || answer.Mode != "fixture" || !strings.Contains(answer.Text, tc.text) || answer.Citations == nil {
				t.Fatalf("incorrect reviewed outcome: %+v %v", answer, err)
			}
			if tc.status == "answered" {
				if len(answer.Citations) != 1 || answer.Citations[0] != input.Evidence[0] {
					t.Fatal("citation did not preserve the current evidence record")
				}
			} else if len(answer.Citations) != 0 {
				t.Fatal("a fallback response exposed citations")
			}
		})
	}
}

func TestEmptyAndMissingRequiredEvidence(t *testing.T) {
	for _, question := range []string{materialQuery, "unrecognized question"} {
		for _, ids := range [][]string{nil, {"aic-28560-description-01"}} {
			if question != materialQuery && len(ids) != 0 {
				continue
			}
			input := fixtureInput(t, "aic-28560", question, "en", ids...)
			output, _ := (Adapter{}).Generate(context.Background(), input)
			answer, err := Validate(input, output)
			if err != nil || answer.Status != "insufficient" || len(answer.Citations) != 0 {
				t.Fatal("empty evidence or a missing required chunk did not produce insufficient")
			}
		}
	}
}

func TestNormalizationAndNarrowFollowUps(t *testing.T) {
	fullwidth := strings.Map(func(r rune) rune {
		if r == ' ' {
			return '\u3000'
		}
		if r >= '!' && r <= '~' {
			return r + 0xfee0
		}
		return r
	}, strings.ToUpper(materialQuery))
	for _, question := range []string{fullwidth, "  WHAT\t MATERIAL   is this artwork made of??.  "} {
		input := fixtureInput(t, "aic-28560", question, "en", "aic-28560-medium")
		output, _ := (Adapter{}).Generate(context.Background(), input)
		if output.Status != "answered" || input.RetrievalQuery != question {
			t.Fatal("fixture normalization changed the original retrieval query or missed compatibility text")
		}
	}
	for question, expected := range map[string]string{
		"What about the material?": materialQuery, "那它是用什么做的？": materialQuery,
		"What about the brushwork?": brushworkQuery, "那它的笔触呢？": brushworkQuery,
	} {
		if Prepare(question, false) != question || Prepare(question, true) != expected {
			t.Fatal("a follow-up rewrite ignored its completed-history precondition")
		}
		input := fixtureInput(t, "aic-28560", question, "zh-CN")
		input.RecentTurns = []conversation.Turn{{Answer: conversation.Answer{Citations: []catalog.Evidence{{ChunkID: "aic-28560-medium"}}}}}
		input.RetrievalQuery = Prepare(question, true)
		output, _ := (Adapter{}).Generate(context.Background(), input)
		if output.Status != "insufficient" || len(output.CitationChunkIDs) != 0 {
			t.Fatal("a follow-up reused old evidence instead of requiring fresh retrieval")
		}
	}
	if Prepare("Tell me more", true) != "Tell me more" {
		t.Fatal("undeclared follow-up wording was rewritten")
	}
}

func TestRejectInvalidAdapterOutput(t *testing.T) {
	input := fixtureInput(t, "aic-28560", materialQuery, "en", "aic-28560-medium", "aic-28560-description-01")
	for _, mutate := range []func(*Output){
		func(o *Output) { o.Status = "unknown" },
		func(o *Output) { o.Status = "insufficient" },
		func(o *Output) { o.Text = "" },
		func(o *Output) { o.Text = "馆方记录的材质是布面油画。" },
		func(o *Output) { o.Text = "<a href='https://invented.example'>answer</a>" },
		func(o *Output) { o.CitationChunkIDs = nil },
		func(o *Output) { o.CitationChunkIDs = []string{} },
		func(o *Output) { o.CitationChunkIDs = []string{"fabricated-id"} },
		func(o *Output) { o.CitationChunkIDs = []string{"aic-11723-medium"} },
		func(o *Output) { o.CitationChunkIDs = []string{"aic-28560-medium", "aic-28560-medium"} },
		func(o *Output) { o.CitationChunkIDs = []string{"aic-28560-medium", "aic-28560-description-01"} },
	} {
		output, _ := (Adapter{}).Generate(context.Background(), input)
		mutate(&output)
		if _, err := Validate(input, output); err == nil {
			t.Fatal("invalid text, status, language or citation structure was accepted")
		}
	}
}
