package conversation

import (
	"fmt"
	"reflect"

	ac "github.com/Jasonssssss9/musebridge/api/internal/agentcontract"
	"github.com/Jasonssssss9/musebridge/api/internal/catalog"
)

type agentCatalog struct {
	artworkID, version, title, artist string
	facts                             []ac.CatalogFact
}

func projectAgentCatalog(dataset *catalog.Dataset, artworkID string, validator *ac.Validator) (agentCatalog, *Failure) {
	if dataset == nil {
		return agentCatalog{}, agentInternal()
	}
	artwork, ok := dataset.ArtworkBySlug(artworkID)
	if !ok {
		return agentCatalog{}, &Failure{404, Error{"artwork_not_found", "The requested artwork is not available.", false}}
	}
	origin, ok := dataset.ArtworkOrigin(artworkID)
	if !ok {
		return agentCatalog{}, agentInternal()
	}
	projection := agentCatalog{artworkID: artwork.ArtworkID, version: dataset.Version(), title: artwork.Title, artist: artwork.Artist, facts: []ac.CatalogFact{}}
	for i, field := range []string{"title", "artist", "date_display"} {
		fact := ac.CatalogFact{
			Kind: "catalog", FactID: artwork.ArtworkID + "-catalog-" + field,
			ArtworkID: artwork.ArtworkID, DatasetVersion: dataset.Version(), Field: field,
			Value:    []string{artwork.Title, artwork.Artist, artwork.DateDisplay}[i],
			SourceID: origin.SourceID, SourceTitle: origin.SourceTitle, SourceURL: origin.SourceURL,
			SourceLocator: fmt.Sprintf("artworks[artwork_id=%s].%s", artwork.ArtworkID, field),
			Attribution:   origin.Institution, Normalization: "Released catalog metadata; not a verbatim museum excerpt.",
		}
		if _, err := validator.Encode("CatalogFact", fact); err != nil {
			return agentCatalog{}, agentInternal()
		}
		projection.facts = append(projection.facts, fact)
	}
	return projection, nil
}

// cloneWire copies only wire values. Unlike a JSON round trip, it preserves
// exact string bytes, explicit nulls and Optional presence without coercion.
func cloneWire[T any](value T) T { return cloneWireValue(reflect.ValueOf(value)).Interface().(T) }

func cloneWireValue(value reflect.Value) reflect.Value {
	switch value.Kind() {
	case reflect.Pointer:
		if value.IsNil() {
			return reflect.Zero(value.Type())
		}
		copy := reflect.New(value.Type().Elem())
		copy.Elem().Set(cloneWireValue(value.Elem()))
		return copy
	case reflect.Slice:
		if value.IsNil() {
			return reflect.Zero(value.Type())
		}
		copy := reflect.MakeSlice(value.Type(), value.Len(), value.Len())
		for i := 0; i < value.Len(); i++ {
			copy.Index(i).Set(cloneWireValue(value.Index(i)))
		}
		return copy
	case reflect.Struct:
		copy := reflect.New(value.Type()).Elem()
		copy.Set(value)
		for i := 0; i < value.NumField(); i++ {
			if value.Type().Field(i).PkgPath == "" {
				copy.Field(i).Set(cloneWireValue(value.Field(i)))
			}
		}
		return copy
	default:
		return value
	}
}

// nextAgentState is pure. The store commits its output with the turn and replay
// record only after rechecking its own reservation and deadline.
func nextAgentState(validator *ac.Validator, run ac.RunRequest, result ac.RunResult) (ac.ConversationState, error) {
	if err := validator.CheckRunResult(run, result); err != nil {
		return ac.ConversationState{}, err
	}
	state := cloneWire(run.ConversationState)
	if state.Revision == 2147483647 {
		return ac.ConversationState{}, fmt.Errorf("revision limit reached")
	}
	language, err := validator.ResolveRunLanguage(run, result.LanguageDecision)
	if err != nil {
		return ac.ConversationState{}, err
	}
	state.Revision++
	state.LanguagePreference, state.AutoLanguage = language.NextLanguagePreference, language.NextAutoLanguage
	state.LastAnswerLanguage = &language.AnswerLanguage
	state.ActiveTopic = cloneWire(result.ConversationUpdate.Memory.ActiveTopic)
	state.ContinuitySummary = result.ConversationUpdate.Memory.ContinuitySummary
	update := result.ConversationUpdate.Memory.PendingUpdate
	switch update.Action {
	case "keep":
	case "clear":
		state.PendingClarification = nil
	case "set":
		pending := ac.PendingClarification{OriginalQuestion: run.Question, Question: result.Answer.Text, CreatedSequence: state.Revision}
		if update.Source.Value == "pending_clarification" {
			if state.PendingClarification == nil {
				return ac.ConversationState{}, fmt.Errorf("missing original clarification")
			}
			pending.OriginalQuestion = state.PendingClarification.OriginalQuestion
			pending.OriginalFragment = cloneWire(state.PendingClarification.OriginalFragment)
			pending.UnresolvedIntent = cloneWire(state.PendingClarification.UnresolvedIntent)
		}
		if update.RequestID.Value != nil {
			found := false
			for i, request := range result.Requests {
				if request.ID != *update.RequestID.Value {
					continue
				}
				if i >= len(result.RequestResults) || result.RequestResults[i].Support != "clarification_needed" {
					return ac.ConversationState{}, fmt.Errorf("invalid local clarification")
				}
				if update.Source.Value == "current_message" {
					fragment := request.OriginalFragment
					pending.OriginalFragment = &fragment
				}
				intent := request.Intent
				pending.UnresolvedIntent = &intent
				pending.Question = result.RequestResults[i].Text
				found = true
			}
			if !found {
				return ac.ConversationState{}, fmt.Errorf("missing clarification request")
			}
		}
		state.PendingClarification = &pending
	default:
		return ac.ConversationState{}, fmt.Errorf("invalid memory operation")
	}
	cache := []ac.Evidence{}
	seen := make(map[string]bool)
	for _, citation := range result.Answer.Citations {
		if citation.Kind.Present {
			continue
		}
		encoded, err := validator.Encode("Citation", citation)
		if err != nil {
			return ac.ConversationState{}, err
		}
		var evidence ac.Evidence
		if err := validator.Decode("Evidence", encoded, &evidence); err != nil {
			return ac.ConversationState{}, err
		}
		if !seen[evidence.ChunkID] {
			cache = append(cache, evidence)
			seen[evidence.ChunkID] = true
		}
	}
	for _, evidence := range state.VerifiedEvidence {
		if !seen[evidence.ChunkID] && len(cache) < 8 {
			cache = append(cache, evidence)
			seen[evidence.ChunkID] = true
		}
	}
	if len(cache) > 8 {
		return ac.ConversationState{}, fmt.Errorf("evidence cache exceeds limit")
	}
	state.VerifiedEvidence = cache
	if err := validator.CheckState(state); err != nil {
		return ac.ConversationState{}, err
	}
	return state, nil
}

func sameAgentSubmission(left, right ac.PublicMessageRequest) bool {
	return left.ContractVersion == right.ContractVersion && left.ClientMessageID == right.ClientMessageID &&
		left.Question == right.Question && left.ExpectedRevision == right.ExpectedRevision && reflect.DeepEqual(left.LanguageOverride, right.LanguageOverride)
}

func agentInternal() *Failure {
	return &Failure{500, Error{"internal_error", "The conversation service could not complete this operation.", true}}
}
func invalidAgentResult() *Failure {
	return &Failure{502, Error{"agent_invalid_response", "The Agent service returned an invalid response.", false}}
}
