package httpapi

import (
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/Jasonssssss9/musebridge/api/internal/voice"
)

// TestVincentBrowserFixture optionally serves the real public API to a browser.
// All private providers are test doubles; no model, credentials or GPU are used.
// See docs/vincent-page-integration.md. Ordinary test runs skip this fixture.
func TestVincentBrowserFixture(t *testing.T) {
	dir := os.Getenv("VINCENT_BROWSER_TEST_DIR")
	if dir == "" {
		t.Skip("interactive browser fixture not requested")
	}
	origin := os.Getenv("VINCENT_BROWSER_TEST_ORIGIN")
	if origin == "" {
		t.Fatal("VINCENT_BROWSER_TEST_ORIGIN must match the local frontend origin")
	}
	audio, err := os.ReadFile(filepath.Join(dir, "answer.mp3"))
	if err != nil {
		t.Fatal("provide a local test MP3 as answer.mp3 before starting")
	}
	whisper := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"text":"Why is that?","language":"en"}`)
	}))
	defer whisper.Close()
	tts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		w.Header().Set("Content-Type", "audio/mpeg")
		_, _ = w.Write(audio)
	}))
	defer tts.Close()
	agent := newV3Agent(t)
	cfg := voice.Config{Enabled: true, WhisperBaseURL: whisper.URL, OpenAIKey: "browser-fixture-key-not-a-credential",
		SpeechModel: voice.DefaultSpeechModel, SpeechVoice: voice.DefaultSpeechVoice, SpeechDelivery: voice.DefaultDelivery,
		SpeechEndpoint: tts.URL, SpeechConcurrency: 2}
	h, err := NewAgent(gettyDataset(t), nil, Settings{PublicBaseURL: origin, Voice: cfg}, agent)
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(h)
	defer server.Close()
	urlFile := filepath.Join(dir, "api-url.txt")
	if err := os.WriteFile(urlFile, []byte(server.URL), 0600); err != nil {
		t.Fatal(err)
	}
	defer os.Remove(urlFile)
	t.Log("Local browser fixture ready. Private replies and audio are scripted test data.")
	deadline := time.NewTimer(10 * time.Minute)
	defer deadline.Stop()
	tick := time.NewTicker(250 * time.Millisecond)
	defer tick.Stop()
	for {
		select {
		case <-deadline.C:
			t.Fatal("browser fixture timed out; close it by creating the stop file")
		case <-tick.C:
			if _, err := os.Stat(filepath.Join(dir, "stop")); err == nil {
				t.Logf("Agent runs during browser check: %d", agent.count())
				return
			}
		}
	}
}
