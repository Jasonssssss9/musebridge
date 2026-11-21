package httpapi

import (
	"encoding/json"
	"net/http"
	"strings"
	"unicode"

	ac "github.com/Jasonssssss9/musebridge/api/internal/agentcontract"
)

// readAgentPOST normalizes only surrounding message whitespace. Validate the
// remaining shape before distinguishing a well-typed unsupported contract.
func (h *handler) readAgentPOST(w http.ResponseWriter, r *http.Request, definition string, target any) *requestFailure {
	body, failure := h.readPublicBody(w, r, ac.PublicBodyLimit)
	if failure != nil {
		return failure
	}
	bad := func() *requestFailure {
		return reject(400, "invalid_request", "Provide the required v2 request fields and values.")
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(body, &fields) != nil || fields == nil {
		return bad()
	}
	var version string
	if raw := fields["contract_version"]; len(raw) == 0 || string(raw) == "null" || json.Unmarshal(raw, &version) != nil || invalidControls(version) {
		return bad()
	}
	fields["contract_version"], _ = json.Marshal(ac.PublicVersion)
	if definition == "PublicMessageRequest" {
		var question string
		if raw := fields["question"]; len(raw) == 0 || string(raw) == "null" || json.Unmarshal(raw, &question) != nil || invalidControls(question) {
			return bad()
		}
		fields["question"], _ = json.Marshal(strings.TrimSpace(question))
	}
	normalized, err := json.Marshal(fields)
	if err != nil {
		return bad()
	}
	validator, err := ac.DefaultValidator()
	if err != nil {
		return &requestFailure{500, apiError{Code: "internal_error", Message: "The service could not validate the request.", Retryable: true}}
	}
	if validator.Decode(definition, normalized, target) != nil {
		return bad()
	}
	if version != ac.PublicVersion {
		return reject(409, "agent_contract_mismatch", "The client and application contracts do not match.")
	}
	return nil
}

func invalidControls(text string) bool {
	return strings.ContainsFunc(text, func(r rune) bool {
		return unicode.IsControl(r) && r != '\t' && r != '\n' && r != '\r'
	})
}

func (h *handler) createAgentConversation(w http.ResponseWriter, r *http.Request) (any, string, *requestFailure) {
	var input ac.PublicCreateConversationRequest
	if failure := h.readAgentPOST(w, r, "PublicCreateConversationRequest", &input); failure != nil {
		return nil, "", failure
	}
	token, current, created, failure := h.sessions.OpenAgent(visitorCookie(r), h.dataset, input.ArtworkID)
	if failure != nil {
		return nil, "", &requestFailure{failure.Status, failure.Error}
	}
	h.setVisitorCookie(w, token)
	return ac.PublicConversationResponse{Conversation: current, Created: created}, current.ArtworkID, nil
}

func (h *handler) agentHistory(r *http.Request, id string) (any, string, *requestFailure) {
	// v2 UUIDs use the strict lowercase representation from the shared schema.
	validator, err := ac.DefaultValidator()
	if err != nil {
		return nil, "", &requestFailure{500, apiError{Code: "internal_error", Message: "The service could not validate the request.", Retryable: true}}
	}
	if _, err := validator.Encode("UUID", ac.UUID(id)); err != nil {
		return nil, "", reject(400, "invalid_request", "conversation_id must be a canonical lowercase UUID.")
	}
	history, found := h.sessions.AgentHistory(visitorCookie(r), id)
	if !found {
		return nil, "", reject(404, "conversation_not_found", "The requested conversation is not available.")
	}
	return history, history.Conversation.ArtworkID, nil
}

func (h *handler) submitAgentMessage(w http.ResponseWriter, r *http.Request, id, requestID string) (any, string, *requestFailure) {
	var input ac.PublicMessageRequest
	if failure := h.readAgentPOST(w, r, "PublicMessageRequest", &input); failure != nil {
		return nil, "", failure
	}
	_, artworkID, failure := h.agentHistory(r, id)
	if failure != nil {
		return nil, "", failure
	}
	response, failed := h.agent.Submit(r.Context(), visitorCookie(r), id, input, requestID)
	if failed != nil {
		return nil, artworkID, &requestFailure{failed.Status, failed.Error}
	}
	return response, artworkID, nil
}
