// Package httpapi exposes the implemented subset of the public API contract.
package httpapi

import (
	"crypto/rand"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/Jasonssssss9/musebridge/api/internal/catalog"
	"github.com/Jasonssssss9/musebridge/api/internal/chat"
	"github.com/Jasonssssss9/musebridge/api/internal/conversation"
	"github.com/Jasonssssss9/musebridge/api/internal/fixture"
)

type handler struct {
	dataset   *catalog.Dataset
	logger    *slog.Logger
	settings  Settings
	sessions  *conversation.Store
	questions *chat.Service
}

// Settings carries the browser profile validated by the application config.
type Settings struct {
	PublicBaseURL string
	CookieSecure  bool
}

type apiError = conversation.Error

// New serves the public artwork/conversation API and private health checks.
// A nil dataset exposes liveness but reports that local state is not ready.
func New(dataset *catalog.Dataset, logger *slog.Logger, settings Settings, retriever chat.Retriever) http.Handler {
	if logger == nil {
		logger = slog.New(slog.NewJSONHandler(io.Discard, nil))
	}
	sessions := conversation.New(nil)
	return &handler{dataset: dataset, logger: logger, settings: settings, sessions: sessions,
		questions: chat.New(sessions, dataset, retriever, fixture.Adapter{}, logger)}
}

func (h *handler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	started := time.Now()
	requestID := "req-" + rand.Text()
	w.Header().Set("X-Request-ID", requestID)
	w.Header().Set("Cache-Control", "no-store")
	status, code, artworkID := http.StatusInternalServerError, "internal_error", ""
	var version *string
	if h.dataset != nil {
		value := h.dataset.Version()
		version = &value
	}
	defer func() {
		h.logger.Info("request_completed", "service", "api", "request_id", requestID,
			"artwork_id", artworkID, "dataset_version", version,
			"status", status, "code", code, "duration_ms", float64(time.Since(started).Microseconds())/1000)
	}()
	fail := func(httpStatus int, errorCode, message string, retryable bool) {
		status, code = httpStatus, errorCode
		h.writeJSON(w, r, status, struct {
			RequestID string   `json:"request_id"`
			Error     apiError `json:"error"`
		}{requestID, apiError{Code: code, Message: message, Retryable: retryable}})
	}
	path := r.URL.Path
	isHealth := path == "/healthz" || path == "/readyz"
	slug, isArtwork := strings.CutPrefix(path, "/api/artworks/")
	isArtwork = isArtwork && slug != "" && !strings.Contains(slug, "/")
	isCreate := path == "/api/conversations"
	conversationID, isHistory := strings.CutPrefix(path, "/api/conversations/")
	isHistory = isHistory && strings.HasSuffix(conversationID, "/messages")
	conversationID = strings.TrimSuffix(conversationID, "/messages")
	isHistory = isHistory && conversationID != "" && !strings.Contains(conversationID, "/")
	if !isHealth && !isArtwork && !isCreate && !isHistory {
		fail(http.StatusNotFound, "route_not_found", "The requested route does not exist.", false)
		return
	}
	if r.URL.RawQuery != "" || r.URL.ForceQuery || r.URL.Fragment != "" {
		fail(http.StatusBadRequest, "invalid_request", "Query parameters and fragments are not supported.", false)
		return
	}
	allowed := http.MethodGet
	if isCreate {
		allowed = http.MethodPost
	}
	if isHistory {
		allowed = "GET, POST"
	}
	methodAllowed := r.Method == http.MethodGet && !isCreate || r.Method == http.MethodPost && (isCreate || isHistory)
	if !methodAllowed {
		w.Header().Set("Allow", allowed)
		fail(http.StatusMethodNotAllowed, "method_not_allowed", "The HTTP method is not supported for this route.", false)
		return
	}
	if path == "/healthz" || (path == "/readyz" && h.dataset != nil) {
		status, code = http.StatusOK, "ok"
		h.writeJSON(w, r, status, struct {
			Status         string  `json:"status"`
			DatasetVersion *string `json:"dataset_version"`
		}{"ok", version})
		return
	}
	if h.dataset == nil {
		fail(http.StatusServiceUnavailable, "service_not_ready", "The API has not loaded its local dataset.", true)
		return
	}
	if isCreate || isHistory {
		var payload any
		var err *requestFailure
		if isCreate {
			payload, artworkID, err = h.createConversation(w, r)
		} else if r.Method == http.MethodPost {
			payload, artworkID, err = h.submitMessage(w, r, conversationID, requestID)
		} else {
			payload, artworkID, err = h.conversationHistory(r, conversationID)
		}
		if err != nil {
			fail(err.status, err.Code, err.Message, err.Retryable)
			return
		}
		status, code = http.StatusOK, "ok"
		h.writeJSON(w, r, status, payload)
		return
	}
	artwork, found := h.dataset.ArtworkBySlug(slug)
	if !found {
		fail(http.StatusNotFound, "artwork_not_found", "The requested artwork is not in the catalog.", false)
		return
	}
	artworkID, status, code = artwork.ArtworkID, http.StatusOK, "ok"
	h.writeJSON(w, r, status, struct {
		Artwork catalog.Artwork `json:"artwork"`
	}{artwork})
}

func (h *handler) writeJSON(w http.ResponseWriter, r *http.Request, status int, payload any) {
	// All payloads are concrete structs containing validated strings and numbers.
	encoded, err := json.Marshal(payload)
	if err != nil {
		panic("invalid internal JSON response")
	}
	encoded = append(encoded, '\n')
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Content-Length", strconv.Itoa(len(encoded)))
	w.WriteHeader(status)
	if r.Method != http.MethodHead {
		_, _ = w.Write(encoded)
	}
}
