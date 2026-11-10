// Package conversation holds anonymous, artwork-scoped state for one process.
package conversation

import (
	"time"

	"github.com/Jasonssssss9/musebridge/api/internal/catalog"
)

// Conversation is public metadata. Visitor identity is kept only in the store.
type Conversation struct {
	ConversationID string    `json:"conversation_id"`
	ArtworkID      string    `json:"artwork_id"`
	DatasetVersion string    `json:"dataset_version"`
	CreatedAt      time.Time `json:"created_at"`
	ExpiresAt      time.Time `json:"expires_at"`
}

type Error struct {
	Code      string `json:"code"`
	Message   string `json:"message"`
	Retryable bool   `json:"retryable"`
}

type Answer struct {
	Status    string             `json:"status"`
	Mode      string             `json:"mode"`
	Text      string             `json:"text"`
	Citations []catalog.Evidence `json:"citations"`
}

type Turn struct {
	TurnID          string    `json:"turn_id"`
	Sequence        int       `json:"sequence"`
	ClientMessageID string    `json:"client_message_id"`
	Question        string    `json:"question"`
	AnswerLanguage  string    `json:"answer_language"`
	DatasetVersion  string    `json:"dataset_version"`
	CreatedAt       time.Time `json:"created_at"`
	Answer          Answer    `json:"answer"`
}

type PendingRequest struct {
	ClientMessageID string `json:"client_message_id"`
	Question        string `json:"question"`
	AnswerLanguage  string `json:"answer_language"`
	State           string `json:"state"`
	LastError       *Error `json:"last_error"`
}

// History is an atomic snapshot. Message submission will populate turns and
// pending state in the next implementation slice; new conversations are empty.
type History struct {
	Conversation   Conversation    `json:"conversation"`
	Turns          []Turn          `json:"turns"`
	PendingRequest *PendingRequest `json:"pending_request"`
}
