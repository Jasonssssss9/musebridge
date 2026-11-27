package agentcontract

import (
	"crypto/sha256"
	_ "embed"
	"encoding/hex"
	"fmt"
)

// reference_pack_generated.json is a byte-identical copy of
// knowledge/data/reference-packs/vincent-irises-v1.json. The generator checks
// it against the pinned .sha256 file, and loading checks it again.
//
//go:embed reference_pack_generated.json
var referencePackJSON []byte

func loadReferencePack(v *Validator) (map[string]Evidence, error) {
	digest := sha256.Sum256(referencePackJSON)
	if hex.EncodeToString(digest[:]) != ReferencePackSHA256 {
		return nil, fmt.Errorf("the Vincent reference pack does not match its pinned digest")
	}
	var pack VincentReferencePack
	if err := v.Decode("VincentReferencePack", referencePackJSON, &pack); err != nil {
		return nil, fmt.Errorf("the Vincent reference pack is invalid")
	}
	if pack.ReferencePackVersion != VincentPackVersion || pack.ArtworkID != VincentArtworkID || pack.DatasetVersion != VincentDatasetVersion {
		return nil, fmt.Errorf("the Vincent reference pack has an unexpected scope")
	}
	records := make(map[string]Evidence, len(pack.Records))
	for _, record := range pack.Records {
		if _, duplicate := records[record.ChunkID]; duplicate || record.ArtworkID != VincentArtworkID {
			return nil, fmt.Errorf("the Vincent reference pack has an invalid record")
		}
		records[record.ChunkID] = record
	}
	return records, nil
}

// ReferenceRecord returns a pinned Vincent pack record by chunk ID (v3 only).
func (v *Validator) ReferenceRecord(chunkID string) (Evidence, bool) {
	record, ok := v.pack[chunkID]
	return record, ok
}

// ScopeFor builds the server-owned scope of a conversation. Guide v0.4
// conversations carry an explicit guide mode and a null pack version.
func ScopeFor(publicVersion, mode, artworkID, datasetVersion string) Scope {
	scope := Scope{ArtworkID: artworkID, DatasetVersion: datasetVersion}
	if publicVersion != PublicVersionV3 {
		return scope
	}
	scope.ConversationMode = Some(ConversationMode(mode))
	var pack ReferencePackVersion
	if mode == ModeVincent {
		version := VincentPackVersion
		pack = &version
	}
	scope.ReferencePackVersion = Some(pack)
	return scope
}

// ScopeMode is the conversation mode of a scope; v2 scopes are guide.
func ScopeMode(scope Scope) string {
	if scope.ConversationMode.Present {
		return string(scope.ConversationMode.Value)
	}
	return ModeGuide
}

// SameScope compares scope values, including the nullable pack version.
// Go's == would compare the pack pointer rather than its text.
func SameScope(left, right Scope) bool {
	return sameJSON(left, right)
}

// evidenceAllowed applies the source policy of this contract version. A v3
// Vincent run may also cite the exact pinned pack records; guide runs never
// can. Records that are not in the pack keep the gallery policy.
func (v *Validator) evidenceAllowed(scope Scope, e Evidence) bool {
	if record, inPack := v.pack[e.ChunkID]; inPack {
		return ScopeMode(scope) == ModeVincent && sameJSON(record, e)
	}
	for _, record := range v.pack {
		if record.SourceID == e.SourceID {
			return false // a pack source ID cannot be reused by another record
		}
	}
	return validEvidence(e)
}

// ResolveRunLanguage resolves the language of a result for its run. Vincent
// always answers in English and never saves another preference.
func (v *Validator) ResolveRunLanguage(run RunRequest, decision LanguageDecision) (LanguageResolution, error) {
	state := run.ConversationState
	if ScopeMode(run.Scope) != ModeVincent {
		return v.ResolveLanguage(run.Question, run.LanguageOverride, state.LanguagePreference, state.AutoLanguage, decision)
	}
	if v.check("LanguageDecision", decision) != nil || decision.AnswerLanguage != "en" {
		return LanguageResolution{}, invalid("Vincent answer language")
	}
	return LanguageResolution{AnswerLanguage: "en", NextLanguagePreference: "en", NextAutoLanguage: "en"}, nil
}
