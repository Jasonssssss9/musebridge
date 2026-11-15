package agentcontract

import (
	"encoding/json"
	"fmt"
	"net/url"
	"reflect"
	"slices"
	"strings"

	"golang.org/x/text/cases"
	"golang.org/x/text/unicode/norm"
)

func normalizedQuery(value string) string {
	return strings.Join(strings.Fields(cases.Fold().String(norm.NFKC.String(value))), " ")
}

func (v *Validator) check(name string, value any) error {
	_, err := v.Encode(name, value)
	return err
}

func sourceURL(raw string) bool {
	u, err := url.Parse(raw)
	return err == nil && u.Scheme == "https" && u.User == nil && u.Fragment == "" &&
		(u.Port() == "" || u.Port() == "443") && (u.Hostname() == "api.artic.edu" || u.Hostname() == "www.artic.edu")
}

// CheckState checks a detached snapshot, not ownership or a live store revision.
func (v *Validator) CheckState(state ConversationState) error {
	if v.check("ConversationState", state) != nil {
		return invalid("ConversationState")
	}
	if pending := state.PendingClarification; pending != nil {
		if pending.CreatedSequence > state.Revision || (pending.OriginalFragment != nil && !strings.Contains(pending.OriginalQuestion, *pending.OriginalFragment)) {
			return invalid("pending clarification")
		}
	}
	seen := make(map[string]bool)
	for _, evidence := range state.VerifiedEvidence {
		if evidence.ArtworkID != state.Scope.ArtworkID || seen[evidence.ChunkID] || !validEvidence(evidence) {
			return invalid("evidence cache")
		}
		seen[evidence.ChunkID] = true
	}
	return nil
}

func validEvidence(e Evidence) bool {
	licenses := map[string]string{"CC0-1.0": "https://creativecommons.org/publicdomain/zero/1.0/", "CC-BY-4.0": "https://creativecommons.org/licenses/by/4.0/"}
	return sourceURL(e.SourceURL) && licenses[e.License] == e.LicenseURL && e.Attribution == "Art Institute of Chicago"
}

// CheckRunRequest checks identities/history and declared source relationships.
// The Agent's manifest separately verifies canonical catalog/cache contents.
func (v *Validator) CheckRunRequest(run RunRequest) error {
	if v.check("RunRequest", run) != nil || v.CheckState(run.ConversationState) != nil {
		return invalid("RunRequest")
	}
	state := run.ConversationState
	if run.Question != strings.TrimSpace(run.Question) || state.Scope != run.Scope || state.ConversationID != run.ConversationID {
		return invalid("run identity")
	}
	count := min(int64(5), state.Revision)
	if int64(len(run.Context.RecentTurns)) != count {
		return invalid("history window")
	}
	seen := make(map[UUID]bool)
	for i, turn := range run.Context.RecentTurns {
		if turn.Sequence != state.Revision-count+1+int64(i) || seen[turn.TurnID] {
			return invalid("history window")
		}
		seen[turn.TurnID] = true
	}
	if count > 0 && (state.LastAnswerLanguage == nil || run.Context.RecentTurns[count-1].AnswerLanguage != *state.LastAnswerLanguage) {
		return invalid("history language")
	}
	for i, fact := range run.Context.CatalogFacts {
		field := []string{"title", "artist", "date_display"}[i]
		if fact.ArtworkID != run.Scope.ArtworkID || fact.DatasetVersion != run.Scope.DatasetVersion || fact.Field != field || fact.FactID != run.Scope.ArtworkID+"-catalog-"+field || !sourceURL(fact.SourceURL) {
			return invalid("catalog scope")
		}
	}
	return nil
}

type LanguageResolution struct {
	AnswerLanguage         Language           `json:"answer_language"`
	NextLanguagePreference LanguagePreference `json:"next_language_preference"`
	NextAutoLanguage       Language           `json:"next_auto_language"`
}

// ResolveLanguage checks the model's reported decision, not natural language.
func (v *Validator) ResolveLanguage(question string, override *LanguagePreference, preference LanguagePreference, automatic Language, decision LanguageDecision) (LanguageResolution, error) {
	var result LanguageResolution
	if v.check("LanguageDecision", decision) != nil || v.check("LanguagePreference", preference) != nil || v.check("Language", automatic) != nil {
		return result, invalid("language decision")
	}
	candidate := preference
	if override != nil {
		candidate = *override
	}
	if v.check("LanguagePreference", candidate) != nil {
		return result, invalid("language override")
	}
	effective := candidate
	instruction := decision.ExplicitInstruction
	if instruction != nil {
		if !strings.Contains(question, instruction.OriginalFragment) {
			return result, invalid("language fragment")
		}
		effective = instruction.Preference
	}
	if slices.Contains([]string{"en", "es", "zh-CN", "ko"}, decision.DetectedLanguage) {
		automatic = Language(decision.DetectedLanguage)
	}
	answer := Language(effective)
	if effective == "auto" {
		answer = automatic
	}
	if translation := decision.Translation; translation != nil {
		if !strings.Contains(question, translation.OriginalFragment) || (instruction != nil && instruction.Purpose == "turn_response" && answer != translation.TargetLanguage) {
			return result, invalid("translation language")
		}
		answer = translation.TargetLanguage
	}
	if answer != decision.AnswerLanguage {
		return result, invalid("answer language")
	}
	if instruction != nil && instruction.Purpose == "conversation_setting" {
		candidate = instruction.Preference
	}
	return LanguageResolution{answer, candidate, automatic}, nil
}

func sameJSON(left, right any) bool {
	a, errA := json.Marshal(left)
	b, errB := json.Marshal(right)
	return errA == nil && errB == nil && string(a) == string(b)
}

// CheckRunResult verifies relationships observable by Go. Fresh tool provenance,
// actual fitted translation context and semantic support remain Agent duties.
// No state is updated and no current store revision/deadline is consulted here.
func (v *Validator) CheckRunResult(run RunRequest, result RunResult) error {
	if v.CheckRunRequest(run) != nil || v.check("RunResult", result) != nil {
		return invalid("RunResult")
	}
	if result.Scope != run.Scope || result.ConversationUpdate.BaseRevision != run.ConversationState.Revision || !sameJSON(result.CatalogSnapshot, run.Context.CatalogFacts) {
		return invalid("result identity")
	}
	resolved, err := v.ResolveLanguage(run.Question, run.LanguageOverride, run.ConversationState.LanguagePreference, run.ConversationState.AutoLanguage, result.LanguageDecision)
	if err != nil || resolved.AnswerLanguage != result.AnswerLanguage {
		return invalid("result language")
	}
	if result.Execution.RetrievalCalls != int64(len(result.ExecutedQueries)) {
		return invalid("execution counts")
	}
	if (result.Execution.RefinementRounds == 1 && result.Execution.ModelCalls != 3) ||
		(result.Execution.ModelCalls == 3 && result.Execution.RefinementRounds != 1) ||
		(result.Execution.RetrievalCalls > 0 && result.Execution.ModelCalls < 2) {
		return invalid("execution counts")
	}
	queries := make(map[string]bool)
	for i, query := range result.ExecutedQueries {
		text := normalizedQuery(query.Query)
		if string(query.ID) != fmt.Sprintf("q%d", i+1) || queries[text] || strings.TrimSpace(query.Query) != query.Query {
			return invalid("executed queries")
		}
		queries[text] = true
	}
	requests := make(map[RequestID]InterpretedRequest)
	for i, request := range result.Requests {
		if string(request.ID) != fmt.Sprintf("r%d", i+1) || !strings.Contains(run.Question, request.OriginalFragment) {
			return invalid("result requests")
		}
		requests[request.ID] = request
	}
	for _, query := range result.ExecutedQueries {
		for _, id := range query.RequestIDs {
			r, exists := requests[id]
			if !exists || r.Kind != "artwork_fact" || r.Resolution != "clear" {
				return invalid("query links")
			}
		}
	}
	sources := make(map[string]Citation)
	for _, evidence := range result.EvidenceSnapshot {
		if evidence.ArtworkID != run.Scope.ArtworkID || !validEvidence(evidence) {
			return invalid("result evidence")
		}
		var citation Citation
		data, _ := json.Marshal(evidence)
		if v.Decode("Citation", data, &citation) != nil || sources[evidence.ChunkID].ArtworkID != "" {
			return invalid("result evidence")
		}
		sources[evidence.ChunkID] = citation
	}
	for _, fact := range result.CatalogSnapshot {
		data, _ := json.Marshal(fact)
		var citation Citation
		if v.Decode("Citation", data, &citation) != nil || sources[fact.FactID].ArtworkID != "" {
			return invalid("catalog identity")
		}
		sources[fact.FactID] = citation
	}
	if len(result.ReusedChunkIDs) != len(run.ConversationState.VerifiedEvidence) {
		return invalid("reused cache")
	}
	for i, cached := range run.ConversationState.VerifiedEvidence {
		if result.ReusedChunkIDs[i] != cached.ChunkID {
			return invalid("reused cache")
		}
		var expected Citation
		data, _ := json.Marshal(cached)
		_ = v.Decode("Citation", data, &expected)
		if !sameJSON(sources[cached.ChunkID], expected) {
			return invalid("cache consistency")
		}
	}
	if len(result.RequestResults) > 0 {
		status, err := v.DeriveStatus(result.Requests, result.RequestResults)
		if err != nil || status != result.Answer.Status {
			return invalid("answer status")
		}
		texts := make([]string, 0, len(result.RequestResults))
		ids := []string{}
		for i, part := range result.RequestResults {
			texts = append(texts, part.Text)
			for _, id := range part.CitationIDs {
				source, exists := sources[id]
				if !exists {
					return invalid("citation membership")
				}
				kind := result.Requests[i].Kind
				if kind == "catalog_fact" && !source.Kind.Present {
					return invalid("catalog citation")
				}
				if !slices.Contains(ids, id) {
					ids = append(ids, id)
				}
			}
			if result.Requests[i].Kind == "artwork_fact" && part.Support == "supported" {
				retrieval := false
				for _, id := range part.CitationIDs {
					if !sources[id].Kind.Present {
						retrieval = true
					}
				}
				if !retrieval {
					return invalid("artwork citation")
				}
			}
		}
		if strings.Join(texts, "\n\n") != result.Answer.Text || len(ids) != len(result.Answer.Citations) {
			return invalid("answer assembly")
		}
		for i, citation := range result.Answer.Citations {
			if !sameJSON(citation, sources[ids[i]]) {
				return invalid("citation snapshot")
			}
		}
	} else if (result.Answer.Status != "clarification_needed" && result.Answer.Status != "out_of_scope") || len(result.Answer.Citations) != 0 {
		return invalid("empty result parts")
	} else if result.Answer.Status == "out_of_scope" && len(result.Requests) != 0 {
		return invalid("scope result coverage")
	} else if result.Answer.Status == "clarification_needed" && result.LanguageDecision.Translation == nil {
		clear, local, independent := false, false, false
		for _, request := range result.Requests {
			if request.Resolution == "needs_clarification" {
				local = true
			}
			if request.Resolution == "clear" {
				clear = true
				independent = independent || request.Kind != "artwork_fact"
			}
		}
		if clear && (local || independent) {
			return invalid("discarded independent answer")
		}
	}
	return v.checkPending(run, result)
}

func (v *Validator) DeriveStatus(requests []InterpretedRequest, parts []RequestResult) (string, error) {
	if len(requests) == 0 || len(requests) > 4 || len(parts) != len(requests) {
		return "", invalid("result coverage")
	}
	supports := make(map[string]bool)
	seen := make(map[RequestID]bool)
	facts := false
	clarifications := 0
	for i, request := range requests {
		part := parts[i]
		if v.check("InterpretedRequest", request) != nil || v.check("RequestResult", part) != nil || seen[request.ID] || part.RequestID != request.ID {
			return "", invalid("result coverage")
		}
		seen[request.ID] = true
		allowed := []string{"not_applicable"}
		if request.Kind == "artwork_fact" || request.Kind == "catalog_fact" {
			facts = true
			allowed = []string{"supported", "insufficient"}
		}
		if request.Kind == "outside_scope" {
			allowed = []string{"out_of_scope"}
		}
		if request.Resolution == "needs_clarification" {
			allowed = []string{"clarification_needed"}
		}
		if !slices.Contains(allowed, part.Support) {
			return "", invalid("result support")
		}
		if part.Support == "clarification_needed" {
			clarifications++
		}
		supports[part.Support] = true
	}
	if clarifications > 1 {
		return "", invalid("clarification count")
	}
	if supports["supported"] {
		if supports["insufficient"] || supports["out_of_scope"] || supports["clarification_needed"] {
			return "partially_answered", nil
		}
		return "answered", nil
	}
	if supports["clarification_needed"] {
		return "clarification_needed", nil
	}
	if facts {
		return "insufficient", nil
	}
	if supports["out_of_scope"] {
		return "out_of_scope", nil
	}
	return "conversational", nil
}

func (v *Validator) checkPending(run RunRequest, result RunResult) error {
	p := result.ConversationUpdate.Memory.PendingUpdate
	local := (*RequestID)(nil)
	for _, part := range result.RequestResults {
		if part.Support == "clarification_needed" {
			id := part.RequestID
			local = &id
		}
	}
	whole := len(result.RequestResults) == 0 && result.Answer.Status == "clarification_needed"
	if whole || local != nil {
		if p.Action != "set" || !p.RequestID.Present || !reflect.DeepEqual(p.RequestID.Value, local) {
			return invalid("pending binding")
		}
		if p.Source.Value == "pending_clarification" && run.ConversationState.PendingClarification == nil {
			return invalid("pending source")
		}
	} else if p.Action == "set" {
		return invalid("unexpected pending update")
	}
	return nil
}
