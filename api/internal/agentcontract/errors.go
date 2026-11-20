package agentcontract

import (
	_ "embed"
	"encoding/json"
)

//go:embed errors_generated.json
var errorJSON []byte

type errorPolicy struct {
	PrivateStatus int
	PublicStatus  int
	Code          string
	Retryable     bool
}

var errorPolicies, messages = loadErrorPolicies()

func loadErrorPolicies() (map[string]errorPolicy, map[string]string) {
	var document struct {
		Policies map[string][]json.RawMessage `json:"policies"`
		Messages map[string]string            `json:"messages"`
	}
	if err := json.Unmarshal(errorJSON, &document); err != nil {
		panic(err)
	}
	policies := make(map[string]errorPolicy)
	for code, fields := range document.Policies {
		if len(fields) != 4 {
			panic("invalid embedded error policy")
		}
		var p errorPolicy
		for i, destination := range []any{&p.PrivateStatus, &p.PublicStatus, &p.Code, &p.Retryable} {
			if err := json.Unmarshal(fields[i], destination); err != nil {
				panic(err)
			}
		}
		policies[code] = p
	}
	return policies, document.Messages
}

// Failure is an application-facing decision, not a serialized error envelope.
// Reconcile means query the same run before using this fallback failure.
type Failure struct {
	Status    int
	Error     PublicError
	Reconcile bool
}

func (f *Failure) ErrorMessage() string { return f.Error.Message }

func publicFailure(status int, code string, retryable bool) *Failure {
	return &Failure{Status: status, Error: PublicError{
		Code: code, Message: messages[code], Retryable: retryable,
	}}
}

// MapAgentFailure validates the HTTP/code pairing and emits fixed safe prose.
// It never forwards an upstream phase, run ID or diagnostic message.
func MapAgentFailure(status int, upstream AgentError) *Failure {
	v, err := DefaultValidator()
	if err != nil {
		return publicFailure(502, "agent_invalid_response", false)
	}
	if _, err := v.Encode("AgentError", upstream); err != nil {
		return publicFailure(502, "agent_invalid_response", false)
	}
	p, exists := errorPolicies[upstream.Code]
	if !exists || status != p.PrivateStatus || upstream.Retryable != p.Retryable {
		return publicFailure(502, "agent_invalid_response", false)
	}
	result := publicFailure(p.PublicStatus, p.Code, p.Retryable)
	result.Reconcile = upstream.Code == "agent_run_in_progress"
	return result
}

// MapRunFailure maps a retained failed RunSnapshot, whose HTTP status is 200.
// The private failure status comes from the same embedded policy as POST errors;
// retryability and shape must still be valid. An in-progress error contradicts
// a terminal failed snapshot. No upstream diagnostic message is retained.
func MapRunFailure(upstream AgentError) (*Failure, error) {
	v, err := DefaultValidator()
	if err != nil {
		return nil, invalid("AgentError")
	}
	if _, err := v.Encode("AgentError", upstream); err != nil {
		return nil, invalid("AgentError")
	}
	p, exists := errorPolicies[upstream.Code]
	if !exists || upstream.Retryable != p.Retryable || upstream.Code == "agent_run_in_progress" {
		return nil, invalid("AgentError")
	}
	return MapAgentFailure(p.PrivateStatus, upstream), nil
}
