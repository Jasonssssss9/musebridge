package voice

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"net"
	"net/url"
	"strconv"
	"strings"
	"time"
)

// Defaults from docs/vincent-voice.md. A and C may revise the delivery preset
// together; changing any preset field changes the cache key.
const (
	DefaultSpeechModel = "gpt-4o-mini-tts"
	DefaultSpeechVoice = "cedar"
	DefaultDelivery    = "Speak as if you are standing beside one visitor in a quiet gallery. " +
		"Use a warm conversational voice and leave a brief pause between thoughts."
	DefaultSpeechEndpoint    = "https://api.openai.com/v1/audio/speech"
	DefaultSpeechConcurrency = 2
)

// Config is the voice part of the server configuration.
type Config struct {
	Enabled           bool
	WhisperBaseURL    string
	OpenAIKey         string
	SpeechModel       string
	SpeechVoice       string
	SpeechDelivery    string
	SpeechEndpoint    string
	SpeechConcurrency int

	synthesizer  synthesizer   // tests only
	stageTimeout time.Duration // tests only
}

// Formatting the config never prints the provider key.
func (c Config) String() string   { return "voice.Config (credentials redacted)" }
func (c Config) GoString() string { return c.String() }

// Preset is the server-owned speech delivery. The browser cannot change it.
type Preset struct {
	Model, Voice, Instructions string
}

// Revision identifies the preset in cache keys.
func (p Preset) Revision() string {
	sum := sha256.Sum256([]byte(p.Model + "\x00" + p.Voice + "\x00" + p.Instructions))
	return hex.EncodeToString(sum[:6])
}

func (c Config) Preset() Preset {
	return Preset{Model: c.SpeechModel, Voice: c.SpeechVoice, Instructions: c.SpeechDelivery}
}

// LoadConfig reads the voice variables. VINCENT_ENABLED defaults to false; when it
// is true, WHISPER_BASE_URL and OPENAI_API_KEY are required.
//
//	VINCENT_ENABLED            true | false (default false)
//	WHISPER_BASE_URL           private loopback worker origin, e.g. http://127.0.0.1:8003
//	OPENAI_API_KEY             server-only speech credential
//	VINCENT_TTS_VOICE          optional, default cedar
//	VINCENT_TTS_INSTRUCTIONS   optional delivery direction
//	VINCENT_TTS_CONCURRENCY    optional, paid syntheses in flight (default 2)
func LoadConfig(lookup func(string) (string, bool)) (Config, error) {
	cfg := Config{
		SpeechModel: DefaultSpeechModel, SpeechVoice: DefaultSpeechVoice, SpeechDelivery: DefaultDelivery,
		SpeechEndpoint: DefaultSpeechEndpoint, SpeechConcurrency: DefaultSpeechConcurrency,
	}
	if value, exists := lookup("VINCENT_ENABLED"); exists {
		switch value {
		case "true":
			cfg.Enabled = true
		case "false":
		default:
			return Config{}, fmt.Errorf("VINCENT_ENABLED must be true or false")
		}
	}
	if !cfg.Enabled {
		return cfg, nil
	}
	whisper, _ := lookup("WHISPER_BASE_URL")
	if err := privateOrigin(whisper); err != nil {
		return Config{}, fmt.Errorf("WHISPER_BASE_URL: %w", err)
	}
	cfg.WhisperBaseURL = strings.TrimRight(whisper, "/")
	cfg.OpenAIKey, _ = lookup("OPENAI_API_KEY")
	if strings.TrimSpace(cfg.OpenAIKey) != cfg.OpenAIKey || len(cfg.OpenAIKey) < 20 ||
		strings.ContainsFunc(cfg.OpenAIKey, func(r rune) bool { return r < 33 || r > 126 }) {
		return Config{}, fmt.Errorf("OPENAI_API_KEY must be set to a server-side API key when VINCENT_ENABLED=true")
	}
	if value, exists := lookup("VINCENT_TTS_VOICE"); exists {
		if value == "" || strings.ContainsFunc(value, func(r rune) bool { return !(r >= 'a' && r <= 'z' || r == '-' || r == '_') }) {
			return Config{}, fmt.Errorf("VINCENT_TTS_VOICE must be a lowercase voice name")
		}
		cfg.SpeechVoice = value
	}
	if value, exists := lookup("VINCENT_TTS_INSTRUCTIONS"); exists {
		if strings.TrimSpace(value) == "" || len(value) > 1000 || strings.ContainsAny(value, "\x00\r\n") {
			return Config{}, fmt.Errorf("VINCENT_TTS_INSTRUCTIONS must be one nonempty line of at most 1000 bytes")
		}
		cfg.SpeechDelivery = value
	}
	if value, exists := lookup("VINCENT_TTS_CONCURRENCY"); exists {
		n, err := strconv.Atoi(value)
		if err != nil || n < 1 || n > 8 {
			return Config{}, fmt.Errorf("VINCENT_TTS_CONCURRENCY must be an integer from 1 to 8")
		}
		cfg.SpeechConcurrency = n
	}
	return cfg, nil
}

// privateOrigin requires an http origin on a loopback address: the worker must
// stay off the public proxy (use a loopback port forward for another computer).
func privateOrigin(value string) error {
	u, err := url.Parse(value)
	if err != nil || u.Scheme != "http" || u.Host == "" || u.User != nil ||
		(u.Path != "" && u.Path != "/") || u.RawQuery != "" || u.Fragment != "" {
		return fmt.Errorf("must be an http origin such as http://127.0.0.1:8003")
	}
	host := u.Hostname()
	if host == "localhost" {
		return nil
	}
	if ip := net.ParseIP(host); ip == nil || !ip.IsLoopback() {
		return fmt.Errorf("must be a loopback address; forward the worker port if it runs on another computer")
	}
	return nil
}
