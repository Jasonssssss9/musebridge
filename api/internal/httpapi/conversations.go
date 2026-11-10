package httpapi

import (
	"encoding/json"
	"errors"
	"io"
	"mime"
	"net/http"
	"strings"
	"time"

	"github.com/Jasonssssss9/musebridge/api/internal/conversation"
	"github.com/Jasonssssss9/musebridge/api/internal/jsonwire"
)

const (
	visitorCookieName = "musebridge_visitor"
	maxPostBytes      = 16 << 10
)

type requestFailure struct {
	status int
	apiError
}

func reject(status int, code, message string) *requestFailure {
	return &requestFailure{status, apiError{Code: code, Message: message}}
}

func (h *handler) createConversation(w http.ResponseWriter, r *http.Request) (any, string, *requestFailure) {
	origins := r.Header.Values("Origin")
	if h.settings.PublicBaseURL == "" || len(origins) != 1 || origins[0] != h.settings.PublicBaseURL {
		return nil, "", reject(http.StatusForbidden, "origin_not_allowed", "The request origin is not allowed.")
	}
	contentTypes := r.Header.Values("Content-Type")
	if len(contentTypes) != 1 {
		return nil, "", reject(http.StatusUnsupportedMediaType, "unsupported_media_type", "Send a UTF-8 JSON request.")
	}
	mediaType, parameters, err := mime.ParseMediaType(contentTypes[0])
	if err != nil || mediaType != "application/json" {
		return nil, "", reject(http.StatusUnsupportedMediaType, "unsupported_media_type", "Send a UTF-8 JSON request.")
	}
	for name, value := range parameters {
		if name != "charset" || !strings.EqualFold(value, "utf-8") {
			return nil, "", reject(http.StatusUnsupportedMediaType, "unsupported_media_type", "Send a UTF-8 JSON request.")
		}
	}
	if r.ContentLength > maxPostBytes {
		return nil, "", reject(http.StatusRequestEntityTooLarge, "payload_too_large", "The request body is too large.")
	}
	if r.Body == nil {
		return nil, "", reject(http.StatusBadRequest, "invalid_request", "Provide an artwork_id in a JSON object.")
	}
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, maxPostBytes))
	if err != nil {
		var tooLarge *http.MaxBytesError
		if errors.As(err, &tooLarge) {
			return nil, "", reject(http.StatusRequestEntityTooLarge, "payload_too_large", "The request body is too large.")
		}
		return nil, "", reject(http.StatusBadRequest, "invalid_request", "The request body could not be read.")
	}
	if err := jsonwire.Validate(body); err != nil {
		return nil, "", reject(http.StatusBadRequest, "invalid_request", "Provide one valid UTF-8 JSON object.")
	}
	var fields map[string]json.RawMessage
	var artworkID string
	if err := json.Unmarshal(body, &fields); err != nil || len(fields) != 1 {
		return nil, "", reject(http.StatusBadRequest, "invalid_request", "Provide only the required artwork_id field.")
	}
	value, exists := fields["artwork_id"]
	if !exists || json.Unmarshal(value, &artworkID) != nil || strings.TrimSpace(artworkID) == "" {
		return nil, "", reject(http.StatusBadRequest, "invalid_request", "artwork_id must be a nonempty string.")
	}
	if _, found := h.dataset.ArtworkBySlug(artworkID); !found {
		return nil, "", reject(http.StatusNotFound, "artwork_not_found", "The requested artwork is not in the catalog.")
	}
	token, current, created := h.sessions.Open(visitorCookie(r), artworkID, h.dataset.Version())
	http.SetCookie(w, &http.Cookie{
		Name: visitorCookieName, Value: token, Path: "/",
		HttpOnly: true, SameSite: http.SameSiteLaxMode, Secure: h.settings.CookieSecure,
		MaxAge: int(conversation.Lifetime / time.Second),
	})
	return struct {
		Conversation conversation.Conversation `json:"conversation"`
		Created      bool                      `json:"created"`
	}{current, created}, artworkID, nil
}

func (h *handler) conversationHistory(r *http.Request, id string) (any, string, *requestFailure) {
	id, valid := conversation.CanonicalID(id)
	if !valid {
		return nil, "", reject(http.StatusBadRequest, "invalid_request", "conversation_id must be canonical UUID text.")
	}
	history, found := h.sessions.History(visitorCookie(r), id)
	if !found {
		return nil, "", reject(http.StatusNotFound, "conversation_not_found", "The requested conversation is not available.")
	}
	return history, history.Conversation.ArtworkID, nil
}

// A duplicate identity cookie is ambiguous and is treated as absent.
func visitorCookie(r *http.Request) string {
	var token string
	count := 0
	for _, cookie := range r.Cookies() {
		if cookie.Name == visitorCookieName {
			count++
			token = cookie.Value
		}
	}
	if count != 1 {
		return ""
	}
	return token
}
