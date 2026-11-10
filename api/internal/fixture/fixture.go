// Package fixture supplies reviewed test answers and a narrow query resolver.
package fixture

import (
	"context"
	"errors"
	"slices"
	"strings"
	"unicode"
	"unicode/utf8"

	"golang.org/x/text/unicode/norm"

	"github.com/Jasonssssss9/musebridge/api/internal/catalog"
	"github.com/Jasonssssss9/musebridge/api/internal/conversation"
)

const (
	materialQuery  = "What material is this artwork made of?"
	brushworkQuery = "How does the museum describe this artwork's brushwork?"
)

type Input struct {
	ArtworkID, DatasetVersion, Question, RetrievalQuery, AnswerLanguage string
	RecentTurns                                                         []conversation.Turn
	Evidence                                                            []catalog.Evidence
}

// Output references current evidence by ID; an adapter cannot supply URLs.
type Output struct {
	Status           string
	Text             string
	CitationChunkIDs []string
}

type Adapter struct{}

type prepared struct {
	query, intent string
}

// Prepare changes only the two declared follow-ups when completed history exists.
// All standalone wording is sent to retrieval as its trimmed original text.
func Prepare(question string, hasHistory bool) string {
	return prepare(question, hasHistory).query
}

func prepare(question string, hasHistory bool) prepared {
	result := prepared{query: question}
	switch normalize(question) {
	case "what material is this artwork made of", "这件作品用了什么材料":
		result.intent = "material"
	case "how does the museum describe this artwork's brushwork", "馆方如何描述这件作品的笔触":
		result.intent = "brushwork"
	case "which elements might suggest nervous energy, according to the museum", "馆方认为,画面里的哪些元素可能让人感到紧张":
		result.intent = "energy"
	case "how many hours did van gogh actually spend painting the chicago version", "梵高画完芝加哥收藏的这一版,实际花了多少小时":
		result.intent = "duration"
	case "what is the woman's real name", "画中女子的真实姓名是什么":
		result.intent = "identity"
	case "what about the material", "那它是用什么做的":
		if hasHistory {
			result.query, result.intent = materialQuery, "material"
		}
	case "what about the brushwork", "那它的笔触呢":
		if hasHistory {
			result.query, result.intent = brushworkQuery, "brushwork"
		}
	}
	return result
}

func normalize(question string) string {
	question = norm.NFKC.String(question)
	question = strings.Map(func(r rune) rune {
		if unicode.Is(unicode.Latin, r) {
			return unicode.ToLower(r)
		}
		return r
	}, question)
	question = strings.Join(strings.Fields(question), " ")
	return strings.TrimSpace(strings.TrimRight(question, "?？.。"))
}

func (Adapter) Generate(ctx context.Context, input Input) (Output, error) {
	if err := ctx.Err(); err != nil {
		return Output{}, err
	}
	if !validInput(input) {
		return Output{}, errors.New("invalid fixture input")
	}
	return selectOutput(input), nil
}

// Validate enforces the reviewed fixture, requested language and exact evidence
// requirements before resolving IDs to the current trusted evidence snapshot.
func Validate(input Input, output Output) (conversation.Answer, error) {
	invalid := errors.New("invalid fixture output")
	if !validInput(input) || output.CitationChunkIDs == nil || !utf8.ValidString(output.Text) ||
		utf8.RuneCountInString(output.Text) > 2000 {
		return conversation.Answer{}, invalid
	}
	expected := selectOutput(input)
	if output.Status != expected.Status || output.Text != expected.Text || !slices.Equal(output.CitationChunkIDs, expected.CitationChunkIDs) {
		return conversation.Answer{}, invalid
	}
	available := make(map[string]catalog.Evidence)
	for _, evidence := range input.Evidence {
		if _, duplicate := available[evidence.ChunkID]; duplicate || evidence.ArtworkID != input.ArtworkID {
			return conversation.Answer{}, invalid
		}
		available[evidence.ChunkID] = evidence
	}
	answer := conversation.Answer{Status: output.Status, Mode: "fixture", Text: output.Text, Citations: make([]catalog.Evidence, 0)}
	for _, id := range output.CitationChunkIDs {
		evidence, found := available[id]
		if !found {
			return conversation.Answer{}, invalid
		}
		answer.Citations = append(answer.Citations, evidence)
	}
	return answer, nil
}

func validInput(input Input) bool {
	return (input.ArtworkID == "aic-28560" || input.ArtworkID == "aic-11723") && input.DatasetVersion != "" &&
		(input.AnswerLanguage == "en" || input.AnswerLanguage == "zh-CN") && len(input.RecentTurns) <= 3 &&
		input.RetrievalQuery == Prepare(input.Question, len(input.RecentTurns) > 0)
}

type copyText struct{ en, zh string }

var (
	materialText         = copyText{"The museum records the work as an oil painting on canvas.", "馆方记录的材质是布面油画。"}
	bedroomBrushworkText = copyText{"The museum describes thick, broken brushwork.", "馆方描述了厚重、断续的笔触。"}
	morisotBrushworkText = copyText{"The museum describes light, featherlike strokes that suggest a patterned bed covering and wallpaper in the background.", "馆方描述了柔和、羽毛般的笔触，暗示背景中带花卉图案的床罩和墙纸。"}
	energyText           = copyText{"According to the museum, the vivid colors, thick and broken brushwork, and strongly receding lines may create a sense of nervous energy.", "馆方认为，鲜明的色彩、厚重而断续的笔触，以及明显向纵深延伸的线条，可能让画面显出紧张的活力。"}
	durationText         = copyText{"The material available in this demo does not state how many hours this version took to paint.", "本演示现有资料没有说明画完这一版本实际花了多少小时。"}
	identityText         = copyText{"The material available in this demo does not establish the woman's real name.", "本演示现有资料无法确定画中女子的真实姓名。"}
	insufficientText     = copyText{"The material available in this demo does not provide enough evidence to answer this question.", "本演示现有资料不足以回答这个问题。"}
	unsupportedText      = copyText{"This demo uses fixed test answers, and this question is outside the supported examples. Try asking about the material or brushwork.", "本演示使用固定测试回答，暂未覆盖这个问题。可以试着询问材质或笔触。"}
)

func selectOutput(input Input) Output {
	intent := prepare(input.Question, len(input.RecentTurns) > 0).intent
	reply := func(status string, text copyText, ids ...string) Output {
		value := text.en
		if input.AnswerLanguage == "zh-CN" {
			value = text.zh
		}
		return Output{Status: status, Text: value, CitationChunkIDs: append(make([]string, 0, len(ids)), ids...)}
	}
	if intent == "duration" && input.ArtworkID == "aic-28560" {
		return reply("insufficient", durationText)
	}
	if intent == "identity" && input.ArtworkID == "aic-11723" {
		return reply("insufficient", identityText)
	}
	if len(input.Evidence) == 0 {
		return reply("insufficient", insufficientText)
	}
	var required string
	var text copyText
	switch intent {
	case "material":
		required, text = input.ArtworkID+"-medium", materialText
	case "brushwork":
		required, text = input.ArtworkID+"-description-01", bedroomBrushworkText
		if input.ArtworkID == "aic-11723" {
			text = morisotBrushworkText
		}
	case "energy":
		if input.ArtworkID == "aic-28560" {
			required, text = "aic-28560-description-01", energyText
		}
	}
	if required == "" {
		return reply("unsupported_question", unsupportedText)
	}
	for _, evidence := range input.Evidence {
		if evidence.ChunkID == required && evidence.ArtworkID == input.ArtworkID {
			return reply("answered", text, required)
		}
	}
	return reply("insufficient", insufficientText)
}
