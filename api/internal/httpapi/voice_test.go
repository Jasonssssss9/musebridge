package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	ac "github.com/Jasonssssss9/musebridge/api/internal/agentcontract"
	"github.com/Jasonssssss9/musebridge/api/internal/conversation"
	"github.com/Jasonssssss9/musebridge/api/internal/voice"
)

// vincentStore marks chosen conversations as Vincent mode until the v0.4 create
// path exists; ownership, expiry and turns still come from the real store.
type vincentStore struct {
	store   *conversation.Store
	vincent map[string]bool
}

func (v vincentStore) VoiceConversation(token, id string) (string, time.Time, bool) {
	mode, expires, found := v.store.VoiceConversation(token, id)
	if found && v.vincent[id] {
		mode = "vincent"
	}
	return mode, expires, found
}

func (v vincentStore) CommittedAnswer(token, id, turn string) (string, bool) {
	return v.store.CommittedAnswer(token, id, turn)
}

type voiceFixture struct {
	h            *handler
	conversation ac.PublicConversation
	cookie       *http.Cookie
	turn         ac.PublicTurn
	whisperCalls atomic.Int32
	ttsCalls     atomic.Int32
	ttsBody      atomic.Value
	agentRuns    func() int64
	cfg          voice.Config
	revision     int64
}

func newVoiceFixture(t *testing.T) *voiceFixture {
	t.Helper()
	f := &voiceFixture{}
	whisper := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.whisperCalls.Add(1)
		if r.URL.Path != "/recognize" || r.Header.Get("Cookie") != "" {
			http.Error(w, `{"error":"transcription_unavailable"}`, 503)
			return
		}
		_, _ = io.WriteString(w, `{"text":"What do you mean by that?","language":"en"}`)
	}))
	t.Cleanup(whisper.Close)
	tts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.ttsCalls.Add(1)
		body, _ := io.ReadAll(r.Body)
		f.ttsBody.Store(string(body))
		w.Header().Set("Content-Type", "audio/mpeg")
		_, _ = w.Write([]byte("ID3-test-mp3"))
	}))
	t.Cleanup(tts.Close)

	completed := snapshotFactory(t)
	port := &agentPort{run: func(_ context.Context, run ac.RunRequest, _ string) (ac.RunSnapshot, error) {
		return completed(run), nil
	}}
	cfg := voice.Config{Enabled: true, WhisperBaseURL: whisper.URL, OpenAIKey: "sk-test-0123456789abcdef",
		SpeechModel: voice.DefaultSpeechModel, SpeechVoice: voice.DefaultSpeechVoice, SpeechDelivery: voice.DefaultDelivery,
		SpeechEndpoint: tts.URL, SpeechConcurrency: 2}
	handlerValue, err := NewAgent(testDataset(t), nil, Settings{PublicBaseURL: browserOrigin, Voice: cfg}, port)
	if err != nil {
		t.Fatal(err)
	}
	f.h = handlerValue.(*handler)
	f.cfg = cfg
	f.agentRuns = port.posts.Load
	f.conversation, f.cookie, _ = openAgentSession(t, f.h, "aic-28560", nil)
	w := postAgent(f.h, f.conversation.ConversationID, agentMessage(1, 0), f.cookie)
	var submitted ac.PublicSubmissionResponse
	if w.Code != 200 || json.Unmarshal(w.Body.Bytes(), &submitted) != nil {
		t.Fatalf("setup turn failed: %d %s", w.Code, w.Body.String())
	}
	f.turn, f.revision = submitted.Turn, submitted.Conversation.Revision
	return f
}

// useVincent rebuilds the voice service so the fixture conversation counts as Vincent.
func (f *voiceFixture) useVincent(t *testing.T, cfg voice.Config) {
	t.Helper()
	service, err := voice.New(cfg, browserOrigin, vincentStore{f.h.sessions, map[string]bool{string(f.conversation.ConversationID): true}})
	if err != nil {
		t.Fatal(err)
	}
	f.h.voice = service
}

func voiceCall(h http.Handler, method, path string, body []byte, contentType string, cookie *http.Cookie) *httptest.ResponseRecorder {
	r := httptest.NewRequest(method, path, bytes.NewReader(body))
	r.Header.Set("Origin", browserOrigin)
	if contentType != "" {
		r.Header.Set("Content-Type", contentType)
	}
	if cookie != nil {
		r.AddCookie(cookie)
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	return w
}

func mp4Clip() []byte {
	b := append([]byte{0, 0, 0, 24}, []byte("ftypM4A \x00\x00\x00\x00isomM4A ")...)
	return append(b, bytes.Repeat([]byte{1}, 64)...)
}

func TestVoiceRoutesEndToEnd(t *testing.T) {
	f := newVoiceFixture(t)
	id := string(f.conversation.ConversationID)
	transcribe := "/api/conversations/" + id + "/transcriptions"
	speech := "/api/conversations/" + id + "/turns/" + string(f.turn.TurnID) + "/speech"

	// The setup conversation is a guide conversation: both routes refuse it before private work.
	requireSessionError(t, voiceCall(f.h, "POST", transcribe, mp4Clip(), "audio/mp4", f.cookie), 409, "voice_mode_unavailable")
	requireSessionError(t, voiceCall(f.h, "POST", speech, nil, "", f.cookie), 409, "voice_mode_unavailable")
	if f.whisperCalls.Load() != 0 || f.ttsCalls.Load() != 0 {
		t.Fatal("guide conversations must not reach the worker or the provider")
	}

	f.useVincent(t, f.cfg)
	revision := f.revision
	w := voiceCall(f.h, "POST", transcribe, mp4Clip(), "audio/mp4; codecs=mp4a.40.2", f.cookie)
	if w.Code != 200 || w.Header().Get("Cache-Control") != "no-store" || w.Header().Get("X-Request-ID") == "" ||
		strings.TrimSpace(w.Body.String()) != `{"text":"What do you mean by that?","language":"en"}` {
		t.Fatalf("transcription: %d %v %s", w.Code, w.Header(), w.Body.String())
	}

	for i := 0; i < 2; i++ {
		w = voiceCall(f.h, "POST", speech, nil, "", f.cookie)
		if w.Code != 200 || w.Header().Get("Content-Type") != "audio/mpeg" || w.Body.String() != "ID3-test-mp3" ||
			w.Header().Get("Cache-Control") != "no-store" || w.Header().Get("X-Request-ID") == "" {
			t.Fatalf("speech: %d %v %q", w.Code, w.Header(), w.Body.String())
		}
	}
	if f.ttsCalls.Load() != 1 {
		t.Fatalf("replay must use the cache; provider calls %d", f.ttsCalls.Load())
	}
	var sent map[string]any
	_ = json.Unmarshal([]byte(f.ttsBody.Load().(string)), &sent)
	if sent["input"] != f.turn.Answer.Text || sent["voice"] != "cedar" || sent["model"] != "gpt-4o-mini-tts" {
		t.Fatalf("speech must use the stored answer.text verbatim: %v", sent)
	}

	// Media routes never create a turn, advance the revision or rerun the Agent.
	history := agentHistoryResponse(t, f.h, f.conversation, f.cookie)
	if history.Conversation.Revision != revision || len(history.Turns) != 1 || f.agentRuns() != 1 {
		t.Fatal("voice routes changed conversation state")
	}

	requireSessionError(t, voiceCall(f.h, "GET", transcribe, nil, "", f.cookie), 405, "method_not_allowed")
	requireSessionError(t, voiceCall(f.h, "POST", transcribe+"?lang=fr", mp4Clip(), "audio/mp4", f.cookie), 400, "invalid_request")
	requireSessionError(t, voiceCall(f.h, "POST", transcribe, mp4Clip(), "audio/mp4", nil), 404, "conversation_not_found")
	requireSessionError(t, voiceCall(f.h, "POST", speech, []byte(`{"text":"say this"}`), "application/json", f.cookie), 400, "invalid_request")
	requireSessionError(t, voiceCall(f.h, "POST", "/api/conversations/"+id+"/turns/33333333-3333-4333-8333-333333333333/speech", nil, "", f.cookie), 404, "turn_not_found")
	_, otherCookie, _ := openAgentSession(t, f.h, "aic-28560", nil)
	requireSessionError(t, voiceCall(f.h, "POST", speech, nil, "", otherCookie), 404, "conversation_not_found")
}

func TestVoiceRoutesAbsentWhenDisabled(t *testing.T) {
	h := newAgentHandler(t, &agentPort{})
	current, cookie, _ := openAgentSession(t, h, "aic-28560", nil)
	path := "/api/conversations/" + string(current.ConversationID) + "/transcriptions"
	requireSessionError(t, voiceCall(h, "POST", path, mp4Clip(), "audio/mp4", cookie), 404, "route_not_found")
	fixtureHandler := New(testDataset(t), nil, Settings{PublicBaseURL: browserOrigin, Voice: voice.Config{Enabled: true}}, nil)
	requireSessionError(t, voiceCall(fixtureHandler, "POST", path, mp4Clip(), "audio/mp4", cookie), 404, "route_not_found")
}
