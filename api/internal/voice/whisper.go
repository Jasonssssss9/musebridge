package voice

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
	"unicode"
	"unicode/utf8"
)

// whisperClient calls the private local recognition worker (POST /recognize).
type whisperClient struct {
	base   string
	client *http.Client
}

func newWhisperClient(base string) *whisperClient {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil // loopback only; never route clips through a proxy
	return &whisperClient{base: base, client: &http.Client{Transport: transport}}
}

// Private worker error codes and the public error each one maps to.
var workerErrors = map[int]map[string]*Error{
	http.StatusRequestEntityTooLarge: {"payload_too_large": errTooLarge},
	http.StatusUnsupportedMediaType:  {"unsupported_media_type": errMediaType},
	http.StatusUnprocessableEntity:   {"audio_invalid": errAudio, "no_speech": errNoSpeech, "transcript_too_long": errTooLong},
	http.StatusTooManyRequests:       {"voice_busy": errBusy},
	http.StatusServiceUnavailable:    {"transcription_unavailable": errTranscribe},
}

func (c *whisperClient) recognize(ctx context.Context, body []byte, mediaType, requestID string) (Transcript, *Error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.base+"/recognize", bytes.NewReader(body))
	if err != nil {
		return Transcript{}, errTranscribe
	}
	req.Header.Set("Content-Type", mediaType)
	req.Header.Set("X-Request-ID", requestID)
	resp, err := c.client.Do(req)
	if err != nil {
		if errors.Is(err, context.DeadlineExceeded) || errors.Is(ctx.Err(), context.DeadlineExceeded) {
			return Transcript{}, errTimeout
		}
		return Transcript{}, errTranscribe
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 16<<10))
	if err != nil {
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			return Transcript{}, errTimeout
		}
		return Transcript{}, errTranscribe
	}
	if resp.StatusCode != http.StatusOK {
		var body struct {
			Error string `json:"error"`
		}
		if json.Unmarshal(raw, &body) == nil {
			if mapped := workerErrors[resp.StatusCode][body.Error]; mapped != nil {
				return Transcript{}, mapped
			}
		}
		return Transcript{}, errTranscribe
	}
	// Strict success shape: exactly text and language. Anything else is a worker fault.
	var fields map[string]json.RawMessage
	if json.Unmarshal(raw, &fields) != nil || len(fields) != 2 {
		return Transcript{}, errTranscribe
	}
	var result Transcript
	if json.Unmarshal(fields["text"], &result.Text) != nil || json.Unmarshal(fields["language"], &result.Language) != nil {
		return Transcript{}, errTranscribe
	}
	if result.Language != "en" {
		return Transcript{}, errTranscribe
	}
	return checkTranscript(result.Text)
}

// checkTranscript applies the message text rules without truncating.
func checkTranscript(text string) (Transcript, *Error) {
	if !utf8.ValidString(text) {
		return Transcript{}, errTranscribe
	}
	text = strings.TrimFunc(text, unicode.IsSpace)
	if text == "" {
		return Transcript{}, errNoSpeech
	}
	if utf8.RuneCountInString(text) > MaxTranscriptRunes {
		return Transcript{}, errTooLong
	}
	for _, r := range text {
		if (r < 32 && r != '\t' && r != '\n' && r != '\r') || (r >= 127 && r <= 159) {
			return Transcript{}, errTranscribe
		}
	}
	return Transcript{Text: text, Language: "en"}, nil
}
