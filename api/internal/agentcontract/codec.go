// Package agentcontract implements the versioned Agent boundary without I/O.
package agentcontract

import (
	"bytes"
	_ "embed"
	"encoding/json"
	"fmt"
	"math"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"unicode/utf8"

	"github.com/Jasonssssss9/musebridge/api/internal/jsonwire"
	"github.com/santhosh-tekuri/jsonschema/v6"
)

const (
	PublicVersion    = "agent-demo-v0.3"
	PrivateVersion   = "agent-run-v2"
	WorkflowID       = "museum_qa_v2"
	StateVersion     = "conversation-state-v1"
	ProfileID        = "local-ollama-v2"
	PublicBodyLimit  = 8 * 1024
	PrivateBodyLimit = 256 * 1024
)

// The mode-aware Vincent contract (docs/vincent-contract.md). Guide and
// Vincent conversations created through v0.4 both run on the private v3 Agent.
const (
	PublicVersionV3       = "agent-demo-v0.4"
	PrivateVersionV3      = "agent-run-v3"
	WorkflowIDV3          = "museum_qa_v3"
	StateVersionV3        = "conversation-state-v2"
	ModeGuide             = "guide"
	ModeVincent           = "vincent"
	VincentPackVersion    = "vincent-irises-v1"
	VincentArtworkID      = "getty-103jnh"
	VincentDatasetVersion = "getty-gallery-v2"
)

//go:generate python3 ../../../scripts/generate_agent_contracts.py

//go:embed schema_generated.json
var schemaJSON []byte

//go:embed schema_v3_generated.json
var schemaV3JSON []byte

// Optional preserves absent versus present-null union fields. Required nullable
// fields use ordinary pointers and are checked for presence by the schema.
type Optional[T any] struct {
	Value   T
	Present bool
}

func Some[T any](value T) Optional[T]              { return Optional[T]{Value: value, Present: true} }
func (o Optional[T]) IsZero() bool                 { return !o.Present }
func (o Optional[T]) MarshalJSON() ([]byte, error) { return json.Marshal(o.Value) }
func (o *Optional[T]) UnmarshalJSON(data []byte) error {
	o.Present = true
	return json.Unmarshal(data, &o.Value)
}
func (o Optional[T]) wireType() reflect.Type { return reflect.TypeFor[T]() }

// Violation intentionally contains no submitted values or schema diagnostics.
type Violation struct{ Definition string }

func (e *Violation) Error() string { return "invalid " + e.Definition + " contract" }
func invalid(name string) error    { return &Violation{Definition: name} }

type noLoader struct{}

func (noLoader) Load(string) (any, error) { return nil, fmt.Errorf("external schemas are disabled") }

type nonblankPattern struct{}

func (nonblankPattern) String() string                { return `\S` }
func (nonblankPattern) MatchString(value string) bool { return strings.TrimSpace(value) != "" }

// Go RE2 uses hex escapes instead of JSON Schema's Unicode escapes. Surrogates
// cannot occur in accepted UTF-8; jsonwire rejects escaped surrogates first.
func compilePattern(pattern string) (jsonschema.Regexp, error) {
	if pattern == `\S` {
		return nonblankPattern{}, nil
	}
	pattern = strings.ReplaceAll(pattern, `\ud800-\udfff`, "")
	unicodeEscape := regexp.MustCompile(`\\u([0-9a-fA-F]{4})`)
	pattern = unicodeEscape.ReplaceAllString(pattern, `\x{$1}`)
	return regexp.Compile(pattern)
}

// Validator compiles the embedded, generated copy of one schema version once.
// Compiled schemas are immutable and safe for concurrent validation.
type Validator struct {
	schemas map[string]*jsonschema.Schema
	// Version identifies the public, private, workflow and state contracts.
	public, private, workflow, state string
	// pack holds the pinned Vincent records by chunk ID (v3 only).
	pack map[string]Evidence
}

// PublicVersion, PrivateVersion, WorkflowID and StateVersion of this validator.
func (v *Validator) PublicVersion() string  { return v.public }
func (v *Validator) PrivateVersion() string { return v.private }
func (v *Validator) WorkflowID() string     { return v.workflow }
func (v *Validator) StateVersion() string   { return v.state }

// ModeAware reports whether the validator speaks the v0.4/v3 contracts.
func (v *Validator) ModeAware() bool { return v.public == PublicVersionV3 }

func NewValidator() (*Validator, error) {
	v, err := compileValidator(schemaJSON, "https://musebridge.local/contracts/agent-v2.schema.json", v2Definitions)
	if err != nil {
		return nil, err
	}
	v.public, v.private, v.workflow, v.state = PublicVersion, PrivateVersion, WorkflowID, StateVersion
	return v, nil
}

// NewValidatorV3 compiles the effective v3 schema (v2 plus agent-v3.patch.json)
// and loads the pinned Vincent reference pack.
func NewValidatorV3() (*Validator, error) {
	names := make([]string, 0, len(wireTypes))
	for name := range wireTypes {
		names = append(names, name)
	}
	v, err := compileValidator(schemaV3JSON, "https://musebridge.local/contracts/agent-v3.schema.json", names)
	if err != nil {
		return nil, err
	}
	v.public, v.private, v.workflow, v.state = PublicVersionV3, PrivateVersionV3, WorkflowIDV3, StateVersionV3
	if v.pack, err = loadReferencePack(v); err != nil {
		return nil, err
	}
	return v, nil
}

func compileValidator(source []byte, origin string, names []string) (*Validator, error) {
	doc, err := jsonschema.UnmarshalJSON(bytes.NewReader(source))
	if err != nil {
		return nil, err
	}
	c := jsonschema.NewCompiler()
	c.UseLoader(noLoader{})
	c.UseRegexpEngine(compilePattern)
	c.AssertFormat()
	if err := c.AddResource(origin, doc); err != nil {
		return nil, err
	}
	v := &Validator{schemas: make(map[string]*jsonschema.Schema)}
	for _, name := range names {
		s, err := c.Compile(origin + "#/$defs/" + name)
		if err != nil {
			return nil, fmt.Errorf("compile %s: %w", name, err)
		}
		v.schemas[name] = s
	}
	return v, nil
}

var defaultValidator = sync.OnceValues(NewValidator)
var defaultValidatorV3 = sync.OnceValues(NewValidatorV3)

func DefaultValidator() (*Validator, error) { return defaultValidator() }

// DefaultValidatorV3 serves agent-demo-v0.4 and agent-run-v3.
func DefaultValidatorV3() (*Validator, error) { return defaultValidatorV3() }

// ValidatorFor returns the validator of a supported public contract version.
func ValidatorFor(publicVersion string) (*Validator, error) {
	switch publicVersion {
	case PublicVersion:
		return DefaultValidator()
	case PublicVersionV3:
		return DefaultValidatorV3()
	}
	return nil, fmt.Errorf("unsupported public contract")
}

func parse(data []byte, limit int) (any, error) {
	if len(data) > limit || jsonwire.Validate(data) != nil {
		return nil, invalid("JSON")
	}
	d := json.NewDecoder(bytes.NewReader(data))
	d.UseNumber()
	var value any
	if d.Decode(&value) != nil || checkValue(value, 0) != nil {
		return nil, invalid("JSON")
	}
	return value, nil
}

func checkValue(value any, depth int) error {
	if depth > 64 {
		return invalid("JSON")
	}
	checkText := func(s string) error {
		for _, r := range s {
			if (r < 32 && r != 9 && r != 10 && r != 13) || (r >= 127 && r <= 159) {
				return invalid("Unicode")
			}
		}
		return nil
	}
	switch x := value.(type) {
	case map[string]any:
		for key, child := range x {
			if checkText(key) != nil || checkValue(child, depth+1) != nil {
				return invalid("JSON")
			}
		}
	case []any:
		for _, child := range x {
			if checkValue(child, depth+1) != nil {
				return invalid("JSON")
			}
		}
	case string:
		return checkText(x)
	case json.Number:
		if strings.ContainsAny(string(x), ".eE") {
			number, err := strconv.ParseFloat(string(x), 64)
			if err != nil || math.IsInf(number, 0) || math.IsNaN(number) {
				return invalid("JSON number")
			}
		}
	}
	return nil
}

// project selects exact JSON field names before encoding/json can apply its
// case-insensitive aliases. Schema-open response metadata is safely discarded.
func project(value any, kind reflect.Type) any {
	if value == nil {
		return nil
	}
	for kind.Kind() == reflect.Pointer {
		kind = kind.Elem()
	}
	if optional, ok := reflect.Zero(kind).Interface().(interface{ wireType() reflect.Type }); ok {
		return project(value, optional.wireType())
	}
	switch kind.Kind() {
	case reflect.Struct:
		object, ok := value.(map[string]any)
		if !ok {
			return value
		}
		result := make(map[string]any)
		for i := 0; i < kind.NumField(); i++ {
			field := kind.Field(i)
			name := strings.Split(field.Tag.Get("json"), ",")[0]
			if child, exists := object[name]; exists {
				result[name] = project(child, field.Type)
			}
		}
		return result
	case reflect.Slice:
		items, ok := value.([]any)
		if !ok {
			return value
		}
		result := make([]any, len(items))
		for i, child := range items {
			result[i] = project(child, kind.Elem())
		}
		return result
	}
	return value
}

func (v *Validator) decodeValue(name string, value any, destination any) error {
	kind, exists := wireTypes[name]
	if !exists || v.schemas[name] == nil {
		return invalid("unknown definition")
	}
	if v.schemas[name].Validate(value) != nil {
		return invalid(name)
	}
	// Reserved citation discriminators cannot hide in a schema-open cache record.
	if name == "RunRequest" || name == "ConversationState" {
		state := value.(map[string]any)
		if name == "RunRequest" {
			state = state["conversation_state"].(map[string]any)
		}
		for _, item := range state["verified_evidence"].([]any) {
			record := item.(map[string]any)
			if _, exists := record["kind"]; exists {
				return invalid("evidence cache")
			}
			if _, exists := record["fact_id"]; exists {
				return invalid("evidence cache")
			}
		}
	}
	if destination == nil {
		destination = reflect.New(kind).Interface()
	}
	if reflect.TypeOf(destination) != reflect.PointerTo(kind) || reflect.ValueOf(destination).IsNil() {
		return invalid("destination")
	}
	encoded, err := json.Marshal(project(value, kind))
	if err != nil || json.Unmarshal(encoded, destination) != nil {
		return invalid(name)
	}
	return nil
}

// Decode validates presence, nullability and schema constraints before typed
// decoding. Integer-valued decimals/exponents fail typed integer decoding.
func (v *Validator) Decode(name string, data []byte, destination any) error {
	limit := PrivateBodyLimit
	if name == "PublicMessageRequest" || name == "PublicCreateConversationRequest" {
		limit = PublicBodyLimit
	}
	value, err := parse(data, limit)
	if err != nil {
		return err
	}
	return v.decodeValue(name, value, destination)
}

func (v *Validator) Validate(name string, data []byte) error { return v.Decode(name, data, nil) }

// Encode checks outgoing messages too; callers cannot bypass union rules by
// constructing a Go struct directly. It returns a new byte slice.
func (v *Validator) Encode(name string, value any) ([]byte, error) {
	if !validTypedUTF8(reflect.ValueOf(value), 0) {
		return nil, invalid(name)
	}
	data, err := json.Marshal(value)
	if err != nil || v.Validate(name, data) != nil {
		return nil, invalid(name)
	}
	return data, nil
}

// encoding/json replaces invalid Go string bytes. Reject them before marshaling
// so typed snapshots cannot silently change original text during persistence.
func validTypedUTF8(value reflect.Value, depth int) bool {
	if depth > 64 {
		return false
	}
	if !value.IsValid() {
		return true
	}
	switch value.Kind() {
	case reflect.String:
		return utf8.ValidString(value.String())
	case reflect.Pointer, reflect.Interface:
		return value.IsNil() || validTypedUTF8(value.Elem(), depth+1)
	case reflect.Slice, reflect.Array:
		for i := 0; i < value.Len(); i++ {
			if !validTypedUTF8(value.Index(i), depth+1) {
				return false
			}
		}
	case reflect.Map:
		iter := value.MapRange()
		for iter.Next() {
			if !validTypedUTF8(iter.Key(), depth+1) || !validTypedUTF8(iter.Value(), depth+1) {
				return false
			}
		}
	case reflect.Struct:
		for i := 0; i < value.NumField(); i++ {
			if value.Type().Field(i).PkgPath == "" && !validTypedUTF8(value.Field(i), depth+1) {
				return false
			}
		}
	}
	return true
}

// DecodePublicMessage performs the sole public-question trim and classifies a
// well-typed unsupported version separately from malformed input. It does not
// consult a store, compare current revisions or reserve a request.
func (v *Validator) DecodePublicMessage(data []byte) (PublicMessageRequest, *Failure) {
	var request PublicMessageRequest
	failure := v.decodePublic("PublicMessageRequest", data, &request)
	return request, failure
}

func (v *Validator) DecodePublicCreate(data []byte) (PublicCreateConversationRequest, *Failure) {
	var request PublicCreateConversationRequest
	failure := v.decodePublic("PublicCreateConversationRequest", data, &request)
	return request, failure
}

func (v *Validator) decodePublic(name string, data []byte, destination any) *Failure {
	if len(data) > PublicBodyLimit {
		return publicFailure(413, "payload_too_large", false)
	}
	value, err := parse(data, PublicBodyLimit)
	if err != nil {
		return publicFailure(400, "invalid_request", false)
	}
	object, ok := value.(map[string]any)
	if !ok {
		return publicFailure(400, "invalid_request", false)
	}
	version, ok := object["contract_version"].(string)
	if !ok {
		return publicFailure(400, "invalid_request", false)
	}
	if question, ok := object["question"].(string); ok {
		object["question"] = strings.TrimSpace(question)
	}
	object["contract_version"] = v.public
	if v.decodeValue(name, object, destination) != nil {
		return publicFailure(400, "invalid_request", false)
	}
	if version != v.public {
		return publicFailure(409, "agent_contract_mismatch", false)
	}
	return nil
}
