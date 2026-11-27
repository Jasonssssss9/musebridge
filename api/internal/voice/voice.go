// Package voice implements the two public media routes for Talk with Vincent
// (docs/vincent-voice.md): recording upload and local transcription, and speech
// synthesis of an already committed answer. Neither route creates a message,
// advances a revision or touches conversation memory.
package voice

import (
	"context"
	"errors"
	"io"
	"mime"
	"net/http"
	"strings"
	"time"
)

const (
	// MaxRecordingBytes is the full upload limit, including bodies without a length.
	MaxRecordingBytes = 3 << 20
	// MaxRecordingSeconds is enforced by the worker on decoded audio.
	MaxRecordingSeconds = 20
	// StageTimeout is the separate Go deadline for each media operation.
	StageTimeout = 30 * time.Second
	// MaxTranscriptRunes matches the public message text limit.
	MaxTranscriptRunes = 500
)

// Error is a public failure. httpapi wraps it in the usual error envelope.
type Error struct {
	Status    int
	Code      string
	Message   string
	Retryable bool
}

func (e *Error) Error() string { return e.Code }

func fail(status int, code, message string, retryable bool) *Error {
	return &Error{Status: status, Code: code, Message: message, Retryable: retryable}
}

// Public failures from the voice contract's error table.
var (
	errOrigin       = fail(http.StatusForbidden, "origin_not_allowed", "Open the configured HTTPS page to use voice.", false)
	errInvalidID    = fail(http.StatusBadRequest, "invalid_request", "Conversation and turn IDs must be canonical UUID text.", false)
	errSpeechBody   = fail(http.StatusBadRequest, "invalid_request", "Send the speech request with an empty body.", false)
	errConversation = fail(http.StatusNotFound, "conversation_not_found", "The requested conversation is not available.", false)
	errTurn         = fail(http.StatusNotFound, "turn_not_found", "The requested answer is not available.", false)
	errMode         = fail(http.StatusConflict, "voice_mode_unavailable", "Voice is only available in Talk with Vincent. Enter it from the Irises page.", false)
	errTooLarge     = fail(http.StatusRequestEntityTooLarge, "payload_too_large", "The recording is too large. Record a shorter question.", false)
	errMediaType    = fail(http.StatusUnsupportedMediaType, "unsupported_media_type", "This recording format is not supported.", false)
	errAudio        = fail(http.StatusUnprocessableEntity, "audio_invalid", "The recording could not be read. Record your question again.", false)
	errNoSpeech     = fail(http.StatusUnprocessableEntity, "no_speech", "No clear speech was recognized. Record another question.", false)
	errTooLong      = fail(http.StatusUnprocessableEntity, "transcript_too_long", "That question is too long. Record a shorter one.", false)
	errBusy         = fail(http.StatusTooManyRequests, "voice_busy", "Voice is busy with another request. Try again in a moment.", true)
	errTranscribe   = fail(http.StatusServiceUnavailable, "transcription_unavailable", "Speech recognition is unavailable right now. Try again.", true)
	errSpeech       = fail(http.StatusServiceUnavailable, "speech_unavailable", "The spoken answer is unavailable right now. You can still read it.", true)
	errTimeout      = fail(http.StatusGatewayTimeout, "voice_timeout", "The voice service took too long. Try again.", true)
)

// Conversations is the part of the conversation store the media routes need.
// Lookups must apply the same ownership and expiry rules as the chat routes.
type Conversations interface {
	// VoiceConversation reports the mode ("vincent" or "guide") and expiry of a
	// live conversation owned by the visitor token.
	VoiceConversation(token, conversationID string) (mode string, expiresAt time.Time, found bool)
	// CommittedAnswer returns the stored answer.text of an owned, completed turn.
	CommittedAnswer(token, conversationID, turnID string) (text string, found bool)
}

// Transcript is the public success body of the transcription route.
type Transcript struct {
	Text     string `json:"text"`
	Language string `json:"language"`
}

// Service holds the media routes' dependencies and process-local state.
type Service struct {
	origin  string
	store   Conversations
	whisper *whisperClient
	speech  *speaker
	now     func() time.Time
	timeout time.Duration // StageTimeout; shortened only by tests
	// One recognition slot for the single local model.
	recognition chan struct{}
}

// New builds the service. publicOrigin is the exact browser origin allowed to POST.
func New(cfg Config, publicOrigin string, store Conversations) (*Service, error) {
	if !cfg.Enabled {
		return nil, errors.New("voice is not enabled")
	}
	if store == nil || publicOrigin == "" {
		return nil, errors.New("voice needs a conversation store and a public origin")
	}
	timeout := cfg.stageTimeout
	if timeout == 0 {
		timeout = StageTimeout
	}
	tts := cfg.synthesizer
	if tts == nil {
		tts = newOpenAISpeech(cfg.SpeechEndpoint, cfg.OpenAIKey)
	}
	return &Service{
		origin:      publicOrigin,
		store:       store,
		whisper:     newWhisperClient(cfg.WhisperBaseURL),
		speech:      newSpeaker(tts, cfg.Preset(), cfg.SpeechConcurrency, time.Now, timeout),
		now:         time.Now,
		timeout:     timeout,
		recognition: make(chan struct{}, 1),
	}, nil
}

// Close releases idle connections to the worker and the speech provider.
func (s *Service) Close() {
	s.whisper.client.CloseIdleConnections()
	s.speech.close()
}

func (s *Service) checkOrigin(r *http.Request) *Error {
	origins := r.Header.Values("Origin")
	if len(origins) != 1 || origins[0] != s.origin {
		return errOrigin
	}
	return nil
}

// owned resolves a live Vincent conversation before any private or paid work.
func (s *Service) owned(token, conversationID string) (time.Time, *Error) {
	mode, expires, found := s.store.VoiceConversation(token, conversationID)
	if !found {
		return time.Time{}, errConversation
	}
	if mode != "vincent" {
		return time.Time{}, errMode
	}
	return expires, nil
}

// Transcribe handles POST /api/conversations/{id}/transcriptions. The caller has
// already matched the route and method and rejected query strings.
func (s *Service) Transcribe(w http.ResponseWriter, r *http.Request, token, conversationID, requestID string) (Transcript, *Error) {
	if failure := s.checkOrigin(r); failure != nil {
		return Transcript{}, failure
	}
	id, ok := canonicalID(conversationID)
	if !ok {
		return Transcript{}, errInvalidID
	}
	if _, failure := s.owned(token, id); failure != nil {
		return Transcript{}, failure
	}
	declared, failure := declaredFormat(r.Header.Values("Content-Type"), r.Header.Values("Content-Encoding"))
	if failure != nil {
		return Transcript{}, failure
	}
	if r.ContentLength > MaxRecordingBytes {
		return Transcript{}, errTooLarge
	}
	if r.Body == nil {
		return Transcript{}, errAudio
	}
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, MaxRecordingBytes))
	if err != nil {
		var tooLarge *http.MaxBytesError
		if errors.As(err, &tooLarge) {
			return Transcript{}, errTooLarge
		}
		return Transcript{}, fail(http.StatusBadRequest, "invalid_request", "The recording upload could not be read.", false)
	}
	if failure := checkContainer(declared, body); failure != nil {
		return Transcript{}, failure
	}
	select {
	case s.recognition <- struct{}{}:
		defer func() { <-s.recognition }()
	default:
		return Transcript{}, errBusy
	}
	ctx, cancel := context.WithTimeout(r.Context(), s.timeout)
	defer cancel()
	return s.whisper.recognize(ctx, body, declared.mediaType, requestID)
}

// Speak handles POST /api/conversations/{id}/turns/{turn_id}/speech and returns MP3 bytes.
func (s *Service) Speak(r *http.Request, token, conversationID, turnID string) ([]byte, *Error) {
	if failure := s.checkOrigin(r); failure != nil {
		return nil, failure
	}
	id, ok := canonicalID(conversationID)
	if !ok {
		return nil, errInvalidID
	}
	turn, ok := canonicalID(turnID)
	if !ok {
		return nil, errInvalidID
	}
	if r.ContentLength > 0 {
		return nil, errSpeechBody
	}
	if r.Body != nil && r.ContentLength < 0 {
		var one [1]byte
		if n, _ := io.ReadFull(r.Body, one[:]); n > 0 {
			return nil, errSpeechBody
		}
	}
	expires, failure := s.owned(token, id)
	if failure != nil {
		return nil, failure
	}
	text, found := s.store.CommittedAnswer(token, id, turn)
	if !found {
		return nil, errTurn
	}
	ctx, cancel := context.WithTimeout(r.Context(), s.timeout)
	defer cancel()
	return s.speech.speak(ctx, id+"/"+turn, text, expires)
}

// canonicalID accepts canonical UUID text in either case, like the chat routes.
func canonicalID(id string) (string, bool) {
	if len(id) != 36 {
		return "", false
	}
	for i := 0; i < len(id); i++ {
		c := id[i]
		if i == 8 || i == 13 || i == 18 || i == 23 {
			if c != '-' {
				return "", false
			}
		} else if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f' || c >= 'A' && c <= 'F') {
			return "", false
		}
	}
	return strings.ToLower(id), true
}

// declared is the validated recording declaration from the browser.
type declared struct {
	mediaType string // the exact header value, forwarded to the worker
	container string // "mp4" or "webm"
}

func declaredFormat(contentTypes, encodings []string) (declared, *Error) {
	if len(encodings) > 1 || len(encodings) == 1 && !strings.EqualFold(encodings[0], "identity") {
		return declared{}, errMediaType
	}
	if len(contentTypes) != 1 {
		return declared{}, errMediaType
	}
	mediaType, params, err := mime.ParseMediaType(contentTypes[0])
	if err != nil {
		return declared{}, errMediaType
	}
	container := ""
	switch mediaType {
	case "audio/mp4", "video/mp4":
		container = "mp4"
	case "audio/webm":
		container = "webm"
	default:
		return declared{}, errMediaType
	}
	for name, value := range params {
		if name != "codecs" || !codecsAllowed(container, value) {
			return declared{}, errMediaType
		}
	}
	return declared{mediaType: contentTypes[0], container: container}, nil
}

func codecsAllowed(container, value string) bool {
	parts := strings.Split(value, ",")
	for _, part := range parts {
		codec := strings.ToLower(strings.TrimSpace(part))
		switch {
		case codec == "opus":
		case container == "mp4" && strings.HasPrefix(codec, "mp4a."):
		default:
			return false
		}
	}
	return len(parts) > 0
}

// checkContainer compares the bytes with the declaration. A recognizable
// container that disagrees, or one carrying video, is 415; bytes that match no
// supported container under a supported declaration are broken audio (422).
func checkContainer(d declared, body []byte) *Error {
	if len(body) == 0 {
		return errAudio
	}
	sniffed := sniffContainer(body)
	switch {
	case sniffed == "":
		return errAudio
	case sniffed == "matroska":
		return errMediaType
	case sniffed != d.container:
		return errMediaType
	case hasVideoTrack(sniffed, body):
		return errMediaType
	}
	return nil
}

func sniffContainer(body []byte) string {
	if len(body) >= 12 && string(body[4:8]) == "ftyp" {
		return "mp4"
	}
	if len(body) >= 4 && body[0] == 0x1A && body[1] == 0x45 && body[2] == 0xDF && body[3] == 0xA3 {
		head := body
		if len(head) > 64 {
			head = head[:64]
		}
		switch {
		case strings.Contains(string(head), "webm"):
			return "webm"
		case strings.Contains(string(head), "matroska"):
			return "matroska"
		}
	}
	return ""
}

// hasVideoTrack looks for an MP4 video handler or a WebM video codec ID.
func hasVideoTrack(container string, body []byte) bool {
	data := string(body)
	if container == "mp4" {
		for i := strings.Index(data, "hdlr"); i >= 0; {
			if i+16 <= len(data) && data[i+12:i+16] == "vide" {
				return true
			}
			next := strings.Index(data[i+4:], "hdlr")
			if next < 0 {
				break
			}
			i += 4 + next
		}
		return false
	}
	for _, id := range []string{"V_VP8", "V_VP9", "V_AV1", "V_MPEG4"} {
		if strings.Contains(data, id) {
			return true
		}
	}
	return false
}
