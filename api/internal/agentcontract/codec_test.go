package agentcontract

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func validator(t *testing.T) *Validator {
	t.Helper()
	v, err := DefaultValidator()
	if err != nil {
		t.Fatal(err)
	}
	return v
}

func examples(t *testing.T) []struct {
	Name       string          `json:"name"`
	Definition string          `json:"definition"`
	Valid      bool            `json:"valid"`
	Payload    json.RawMessage `json:"payload"`
} {
	t.Helper()
	data, err := os.ReadFile(filepath.Join("..", "..", "..", "docs", "contracts", "agent-v2.examples.json"))
	if err != nil {
		t.Fatal(err)
	}
	var document struct {
		Cases []struct {
			Name       string          `json:"name"`
			Definition string          `json:"definition"`
			Valid      bool            `json:"valid"`
			Payload    json.RawMessage `json:"payload"`
		} `json:"schema_cases"`
	}
	if err := json.Unmarshal(data, &document); err != nil {
		t.Fatal(err)
	}
	return document.Cases
}

func TestSharedSchemaExamplesAndTypedRoundTrips(t *testing.T) {
	v := validator(t)
	cases := examples(t)
	if len(cases) != 100 {
		t.Fatalf("expected 100 shared cases, got %d", len(cases))
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			if err := v.Validate(c.Definition, c.Payload); (err == nil) != c.Valid {
				t.Fatalf("valid=%v: %v", c.Valid, err)
			}
			if !c.Valid {
				return
			}
			destination := reflect.New(wireTypes[c.Definition]).Interface()
			if err := v.Decode(c.Definition, c.Payload, destination); err != nil {
				t.Fatal(err)
			}
			encoded, err := v.Encode(c.Definition, destination)
			if err != nil {
				t.Fatalf("typed round trip: %v\n%s", err, encoded)
			}
			if err := v.Validate(c.Definition, encoded); err != nil {
				t.Fatal(err)
			}
		})
	}
}

const message = `{"contract_version":"agent-demo-v0.3","client_message_id":"33333333-3333-4333-8333-333333333333","question":"你好 한국어","language_override":null,"expected_revision":0}`

func TestStrictPresenceIntegersAndJSON(t *testing.T) {
	v := validator(t)
	for name, raw := range map[string]string{
		"missing nullable": strings.Replace(message, `"language_override":null,`, "", 1),
		"missing zero":     strings.Replace(message, `,"expected_revision":0`, "", 1),
		"null zero":        strings.Replace(message, `"expected_revision":0`, `"expected_revision":null`, 1),
		"float zero":       strings.Replace(message, `"expected_revision":0`, `"expected_revision":0.0`, 1),
		"exponent zero":    strings.Replace(message, `"expected_revision":0`, `"expected_revision":0e0`, 1),
		"boolean zero":     strings.Replace(message, `"expected_revision":0`, `"expected_revision":false`, 1),
		"extra":            strings.Replace(message, `"question":`, `"extra":1,"question":`, 1),
		"duplicate":        strings.Replace(message, `"question":`, `"question":"first","question":`, 1),
		"trailing":         message + `{}`,
		"surrogate":        strings.Replace(message, "你好 한국어", `\ud800`, 1),
		"control":          strings.Replace(message, "你好 한국어", `\u0080`, 1),
	} {
		t.Run(name, func(t *testing.T) {
			if err := v.Validate("PublicMessageRequest", []byte(raw)); err == nil {
				t.Fatal("accepted malformed request")
			}
		})
	}
	var request PublicMessageRequest
	if err := v.Decode("PublicMessageRequest", []byte(message), &request); err != nil {
		t.Fatal(err)
	}
	if request.LanguageOverride != nil || request.Question != "你好 한국어" {
		t.Fatal("changed null or Unicode")
	}
}

func TestPublicVersionClassificationAndQuestionIdentity(t *testing.T) {
	v := validator(t)
	request, failure := v.DecodePublicMessage([]byte(strings.Replace(message, "你好 한국어", "  Why  this?　", 1)))
	if failure != nil || request.Question != "Why  this?" {
		t.Fatalf("trim: %+v %+v", request, failure)
	}
	_, failure = v.DecodePublicMessage([]byte(strings.Replace(message, PublicVersion, "future-version", 1)))
	if failure == nil || failure.Status != 409 {
		t.Fatalf("version: %+v", failure)
	}
	bad := strings.Replace(strings.Replace(message, PublicVersion, "future-version", 1), `"expected_revision":0`, `"expected_revision":"0"`, 1)
	_, failure = v.DecodePublicMessage([]byte(bad))
	if failure == nil || failure.Status != 400 {
		t.Fatalf("malformed versioned input: %+v", failure)
	}
	_, failure = v.DecodePublicMessage(bytes.Repeat([]byte(" "), PublicBodyLimit+1))
	if failure == nil || failure.Status != 413 {
		t.Fatal("size limit")
	}
}

func TestResponseMetadataCannotShadowTypedFields(t *testing.T) {
	v := validator(t)
	for _, c := range examples(t) {
		if !c.Valid || c.Definition != "PublicConversationResponse" {
			continue
		}
		var envelope map[string]json.RawMessage
		_ = json.Unmarshal(c.Payload, &envelope)
		var object map[string]json.RawMessage
		_ = json.Unmarshal(envelope["conversation"], &object)
		object["REVISION"] = json.RawMessage(`"shadow"`)
		object["metadata"] = json.RawMessage(`{"decimal":1.25}`)
		data, _ := json.Marshal(object)
		var result PublicConversation
		if err := v.Decode("PublicConversation", data, &result); err != nil {
			t.Fatal(err)
		}
		if result.Revision < 0 {
			t.Fatal("shadowed revision")
		}
		return
	}
	t.Fatal("missing conversation fixture")
}

func TestEmbeddedSchemaMatchesCanonicalFile(t *testing.T) {
	canonical, err := os.ReadFile("../../../docs/contracts/agent-v2.schema.json")
	if err != nil || !bytes.Equal(canonical, schemaJSON) {
		t.Fatal("regenerate schema from canonical source", err)
	}
	policies, err := os.ReadFile("../../../docs/contracts/agent-v2.errors.json")
	if err != nil || !bytes.Equal(policies, errorJSON) {
		t.Fatal("regenerate error policies", err)
	}
}
