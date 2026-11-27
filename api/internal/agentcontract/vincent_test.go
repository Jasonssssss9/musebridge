package agentcontract

import (
	"encoding/json"
	"os"
	"testing"
)

type vincentCase struct {
	Name       string          `json:"name"`
	Schema     string          `json:"schema"`
	Definition string          `json:"definition"`
	Valid      bool            `json:"valid"`
	Payload    json.RawMessage `json:"payload"`
}

func vincentExamples(t *testing.T) map[string]vincentCase {
	t.Helper()
	data, err := os.ReadFile("../../../docs/contracts/vincent-examples.json")
	if err != nil {
		t.Fatal(err)
	}
	var examples struct {
		Cases []vincentCase `json:"schema_cases"`
	}
	if err := json.Unmarshal(data, &examples); err != nil {
		t.Fatal(err)
	}
	cases := make(map[string]vincentCase)
	for _, item := range examples.Cases {
		cases[item.Name] = item
	}
	return cases
}

func TestVincentSharedExamples(t *testing.T) {
	v2, err := NewValidator()
	if err != nil {
		t.Fatal(err)
	}
	v3, err := NewValidatorV3()
	if err != nil {
		t.Fatal(err)
	}
	cases := vincentExamples(t)
	if len(cases) < 20 {
		t.Fatalf("expected the shared Vincent examples, got %d", len(cases))
	}
	for _, item := range cases {
		t.Run(item.Name, func(t *testing.T) {
			validator := v3
			if item.Schema == "v2" {
				validator = v2
			}
			if err := validator.Validate(item.Definition, item.Payload); (err == nil) != item.Valid {
				t.Fatalf("%s valid=%v, got %v", item.Definition, item.Valid, err)
			}
		})
	}
}

func TestV2ValidatorDoesNotKnowV3Definitions(t *testing.T) {
	v2, _ := NewValidator()
	if v2.Validate("TranscriptionResult", []byte(`{"text":"Why?","language":"en"}`)) == nil {
		t.Fatal("v2 must not compile v3-only definitions")
	}
	if v2.ModeAware() || v2.PublicVersion() != PublicVersion || v2.PrivateVersion() != PrivateVersion {
		t.Fatal("v2 identity changed")
	}
	v3, _ := NewValidatorV3()
	if !v3.ModeAware() || v3.PublicVersion() != PublicVersionV3 || v3.PrivateVersion() != PrivateVersionV3 ||
		v3.WorkflowID() != WorkflowIDV3 || v3.StateVersion() != StateVersionV3 {
		t.Fatal("unexpected v3 identity")
	}
}

func TestReferencePackIsPinned(t *testing.T) {
	v3, err := NewValidatorV3()
	if err != nil {
		t.Fatal(err)
	}
	disk, err := os.ReadFile("../../../knowledge/data/reference-packs/vincent-irises-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	if string(disk) != string(referencePackJSON) {
		t.Fatal("the embedded pack differs from knowledge/; run scripts/generate_agent_contracts.py")
	}
	record, ok := v3.ReferenceRecord("vincent-772-garden-colors")
	if !ok || record.SourceKind.Value != "artist_letter" || record.License != "CC-BY-NC-SA-4.0" {
		t.Fatalf("missing letter 772: %+v", record)
	}
	if len(v3.pack) != 4 {
		t.Fatalf("expected the four reviewed records, got %d", len(v3.pack))
	}
}

func vincentRunAndResult(t *testing.T) (RunRequest, RunResult) {
	t.Helper()
	cases := vincentExamples(t)
	v3, _ := NewValidatorV3()
	var run RunRequest
	var result RunResult
	if err := v3.Decode("RunRequest", cases["run_initial_vincent"].Payload, &run); err != nil {
		t.Fatal(err)
	}
	if err := v3.Decode("RunResult", cases["vincent_grounded_run_result"].Payload, &result); err != nil {
		t.Fatal(err)
	}
	return run, result
}

func TestVincentRunResultChecks(t *testing.T) {
	v3, _ := NewValidatorV3()
	run, result := vincentRunAndResult(t)
	if err := v3.CheckRunRequest(run); err != nil {
		t.Fatal(err)
	}
	if err := v3.CheckRunResult(run, result); err != nil {
		t.Fatalf("the shared grounded result must pass: %v", err)
	}
	resolved, err := v3.ResolveRunLanguage(run, result.LanguageDecision)
	if err != nil || resolved.AnswerLanguage != "en" || resolved.NextLanguagePreference != "en" || resolved.NextAutoLanguage != "en" {
		t.Fatalf("Vincent language: %+v %v", resolved, err)
	}

	t.Run("scope mismatch", func(t *testing.T) {
		changed := result
		changed.Scope = ScopeFor(PublicVersionV3, ModeGuide, run.Scope.ArtworkID, run.Scope.DatasetVersion)
		if v3.CheckRunResult(run, changed) == nil {
			t.Fatal("a guide-scoped result cannot complete a Vincent run")
		}
	})
	t.Run("non-English answer", func(t *testing.T) {
		data, _ := json.Marshal(result)
		var changed RunResult
		_ = json.Unmarshal(data, &changed)
		changed.AnswerLanguage = "es"
		changed.LanguageDecision.AnswerLanguage = "es"
		if v3.CheckRunResult(run, changed) == nil {
			t.Fatal("Vincent must answer in English")
		}
	})
	t.Run("altered pack record", func(t *testing.T) {
		letter, _ := v3.ReferenceRecord("vincent-772-garden-colors")
		if !v3.evidenceAllowed(run.Scope, letter) {
			t.Fatal("the exact pinned letter is allowed in Vincent mode")
		}
		altered := letter
		altered.Excerpt += " And I painted it for you."
		if v3.evidenceAllowed(run.Scope, altered) {
			t.Fatal("an altered letter must be rejected")
		}
		guide := ScopeFor(PublicVersionV3, ModeGuide, run.Scope.ArtworkID, run.Scope.DatasetVersion)
		if v3.evidenceAllowed(guide, letter) {
			t.Fatal("a guide conversation cannot cite the Vincent pack")
		}
		foreign := letter
		foreign.ChunkID = "invented-letter"
		if v3.evidenceAllowed(run.Scope, foreign) {
			t.Fatal("a record outside the pack cannot reuse a pack source")
		}
	})
}

func TestScopeHelpers(t *testing.T) {
	guide := ScopeFor(PublicVersionV3, ModeGuide, "getty-103jnh", "getty-gallery-v2")
	vincent := ScopeFor(PublicVersionV3, ModeVincent, "getty-103jnh", "getty-gallery-v2")
	legacy := ScopeFor(PublicVersion, "", "getty-103jnh", "getty-gallery-v2")
	if ScopeMode(guide) != ModeGuide || ScopeMode(vincent) != ModeVincent || ScopeMode(legacy) != ModeGuide {
		t.Fatal("unexpected scope modes")
	}
	if SameScope(guide, vincent) || SameScope(legacy, guide) || !SameScope(vincent, ScopeFor(PublicVersionV3, ModeVincent, "getty-103jnh", "getty-gallery-v2")) {
		t.Fatal("scope comparison must use values, not pointers")
	}
	data, _ := json.Marshal(guide)
	if string(data) != `{"artwork_id":"getty-103jnh","dataset_version":"getty-gallery-v2","conversation_mode":"guide","reference_pack_version":null}` {
		t.Fatalf("guide scope wire form: %s", data)
	}
	data, _ = json.Marshal(legacy)
	if string(data) != `{"artwork_id":"getty-103jnh","dataset_version":"getty-gallery-v2"}` {
		t.Fatalf("v2 scope wire form: %s", data)
	}
}
