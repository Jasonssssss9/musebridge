package httpapi

import (
	"net/http"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/Jasonssssss9/musebridge/api/internal/conversation"
)

func (h *handler) submitMessage(w http.ResponseWriter, r *http.Request, id, requestID string) (any, string, *requestFailure) {
	fields, failure := h.readPublicPOST(w, r, "client_message_id", "question", "answer_language")
	if failure != nil {
		return nil, "", failure
	}
	id, valid := conversation.CanonicalID(id)
	if !valid {
		return nil, "", reject(http.StatusBadRequest, "invalid_request", "conversation_id must be canonical UUID text.")
	}
	messageID, valid := conversation.CanonicalID(fields["client_message_id"])
	if !valid {
		return nil, "", reject(http.StatusBadRequest, "invalid_request", "client_message_id must be canonical UUID text.")
	}
	question := strings.TrimSpace(fields["question"])
	if count := utf8.RuneCountInString(question); count == 0 || count > 500 {
		return nil, "", reject(http.StatusBadRequest, "invalid_request", "The question must contain 1 to 500 Unicode code points.")
	}
	for _, r := range fields["question"] {
		if unicode.IsControl(r) && r != '\t' && r != '\n' && r != '\r' {
			return nil, "", reject(http.StatusBadRequest, "invalid_request", "The question contains invalid control characters.")
		}
	}
	language := fields["answer_language"]
	if language != "en" && language != "zh-CN" {
		return nil, "", reject(http.StatusBadRequest, "invalid_request", "answer_language must be en or zh-CN.")
	}
	turn, artworkID, failed := h.questions.Submit(r.Context(), visitorCookie(r), id,
		conversation.Submission{ClientMessageID: messageID, Question: question, AnswerLanguage: language}, requestID)
	if failed != nil {
		return nil, artworkID, &requestFailure{failed.Status, failed.Error}
	}
	return struct {
		Turn conversation.Turn `json:"turn"`
	}{turn}, artworkID, nil
}
