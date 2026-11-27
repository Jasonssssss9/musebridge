package httpapi

import (
	"encoding/json"
	"net/http"
	"slices"
	"strings"
	"unicode"

	ac "github.com/Jasonssssss9/musebridge/api/internal/agentcontract"
	"github.com/Jasonssssss9/musebridge/api/internal/catalog"
)

// readAgentPOST normalizes only surrounding message whitespace. The declared
// contract_version selects the decoder: agent-demo-v0.3 keeps its exact v2
// shapes and agent-demo-v0.4 uses the mode-aware v3 shapes. A well-typed
// request for another version is 409; anything malformed is 400. It returns
// the version whose decoder accepted the body.
func (h *handler) readAgentPOST(w http.ResponseWriter, r *http.Request, definition string, target any) (string, *requestFailure) {
	body, failure := h.readPublicBody(w, r, ac.PublicBodyLimit)
	if failure != nil {
		return "", failure
	}
	var probe struct {
		ContractVersion json.RawMessage `json:"contract_version"`
	}
	var declared string
	_ = json.Unmarshal(body, &probe)
	_ = json.Unmarshal(probe.ContractVersion, &declared)
	if declared == ac.PublicVersionV3 {
		return ac.PublicVersionV3, decodeAgentBody(body, definition, target, ac.PublicVersionV3)
	}
	failure = decodeAgentBody(body, definition, target, ac.PublicVersion)
	if failure != nil && failure.status == 400 && declared != ac.PublicVersion {
		// An unsupported version may be well-typed in the newer shape.
		var scratch ac.PublicCreateConversationRequest
		var message ac.PublicMessageRequest
		var other any = &scratch
		if definition == "PublicMessageRequest" {
			other = &message
		}
		if again := decodeAgentBody(body, definition, other, ac.PublicVersionV3); again != nil && again.status == 409 {
			return "", again
		}
	}
	return ac.PublicVersion, failure
}

func decodeAgentBody(body []byte, definition string, target any, publicVersion string) *requestFailure {
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
	fields["contract_version"], _ = json.Marshal(publicVersion)
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
	validator, err := ac.ValidatorFor(publicVersion)
	if err != nil {
		return &requestFailure{500, apiError{Code: "internal_error", Message: "The service could not validate the request.", Retryable: true}}
	}
	if validator.Decode(definition, normalized, target) != nil {
		return bad()
	}
	if version != publicVersion {
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
	version, failure := h.readAgentPOST(w, r, "PublicCreateConversationRequest", &input)
	if failure != nil {
		return nil, "", failure
	}
	mode := ""
	if version == ac.PublicVersionV3 {
		mode = ac.ModeGuide // omitted mode defaults to guide
		if input.ConversationMode.Present {
			mode = string(input.ConversationMode.Value)
		}
		if _, exists := h.dataset.ArtworkBySlug(input.ArtworkID); !exists {
			return nil, "", reject(404, "artwork_not_found", "The requested artwork is not in the catalog.")
		}
		if !slices.Contains(h.conversationModes(input.ArtworkID), mode) {
			return nil, input.ArtworkID, reject(409, "conversation_mode_unavailable", "This conversation mode is not available for this artwork.")
		}
	}
	token, current, created, opened := h.sessions.OpenAgentMode(visitorCookie(r), h.dataset, input.ArtworkID, version, mode)
	if opened != nil {
		failure := opened
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
	if _, failure := h.readAgentPOST(w, r, "PublicMessageRequest", &input); failure != nil {
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

// conversationModes lists the modes offered for an artwork in this Agent
// deployment: Vincent only for Getty's Irises in getty-gallery-v2 when
// VINCENT_ENABLED is set. It is configuration, not a provider health check.
func (h *handler) conversationModes(artworkID string) []string {
	if h.vincent && artworkID == ac.VincentArtworkID && h.dataset != nil && h.dataset.Version() == ac.VincentDatasetVersion {
		return []string{ac.ModeGuide, ac.ModeVincent}
	}
	return []string{ac.ModeGuide}
}

// modeCard is an artwork card with the ordered conversation_modes of a
// mode-aware deployment. Older clients ignore the extra property.
type modeCard struct {
	catalog.Artwork
	ConversationModes []string `json:"conversation_modes"`
}

func (h *handler) cards(artworks []catalog.Artwork) []modeCard {
	cards := make([]modeCard, 0, len(artworks))
	for _, artwork := range artworks {
		cards = append(cards, modeCard{artwork, h.conversationModes(artwork.ArtworkID)})
	}
	return cards
}
