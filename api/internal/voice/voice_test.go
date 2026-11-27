package voice

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

const (
	origin  = "https://museum.example"
	visitor = "visitor-token"
	convID  = "7b6cd85e-50fb-47df-a567-13cf87751e21"
	guideID = "11111111-1111-4111-8111-111111111111"
	turnID  = "d4fc3fee-17cb-44c5-b44e-b64910511600"
	answer  = "I wrote to Theo that the irises were violet. Look at the single white one."
)

type fakeStore struct{ expires time.Time }

func (f fakeStore) VoiceConversation(token, id string) (string, time.Time, bool) {
	if token != visitor {
		return "", time.Time{}, false
	}
	switch id {
	case convID:
		return "vincent", f.expires, true
	case guideID:
		return "guide", f.expires, true
	}
	return "", time.Time{}, false
}

func (f fakeStore) CommittedAnswer(token, id, turn string) (string, bool) {
	if token == visitor && id == convID && turn == turnID {
		return answer, true
	}
	return "", false
}

type fakeTTS struct {
	calls   atomic.Int32
	gate    chan struct{} // when set, Synthesize waits for it
	err     error
	text    string
	preset  Preset
	mu      sync.Mutex
	started chan struct{}
}

func (f *fakeTTS) Synthesize(ctx context.Context, text string, p Preset) ([]byte, error) {
	f.calls.Add(1)
	f.mu.Lock()
	f.text, f.preset = text, p
	f.mu.Unlock()
	if f.started != nil {
		f.started <- struct{}{}
	}
	if f.gate != nil {
		select {
		case <-f.gate:
		case <-ctx.Done():
			return nil, errProviderTimeout
		}
	}
	if f.err != nil {
		return nil, f.err
	}
	return []byte("ID3 mp3 for " + text[:10]), nil
}
func (f *fakeTTS) Close() {}

func mp4Audio() []byte {
	b := append([]byte{0, 0, 0, 24}, []byte("ftypM4A \x00\x00\x00\x00isomM4A ")...)
	b = append(b, []byte("....moov....trak\x00\x00\x00\x21hdlr\x00\x00\x00\x00\x00\x00\x00\x00soun")...)
	return append(b, bytes.Repeat([]byte{1}, 200)...)
}

func webmAudio() []byte {
	b := []byte{0x1A, 0x45, 0xDF, 0xA3, 0x9F, 0x42, 0x82, 0x84}
	b = append(b, []byte("webm....A_OPUS")...)
	return append(b, bytes.Repeat([]byte{2}, 200)...)
}

func newService(t *testing.T, worker http.HandlerFunc, tts *fakeTTS) *Service {
	return newServiceTimeout(t, worker, tts, 0)
}

func newServiceTimeout(t *testing.T, worker http.HandlerFunc, tts *fakeTTS, timeout time.Duration) *Service {
	t.Helper()
	cfg := Config{Enabled: true, SpeechModel: DefaultSpeechModel, SpeechVoice: DefaultSpeechVoice,
		SpeechDelivery: DefaultDelivery, SpeechConcurrency: 1, synthesizer: tts, stageTimeout: timeout}
	if worker != nil {
		srv := httptest.NewServer(worker)
		t.Cleanup(srv.Close)
		cfg.WhisperBaseURL = srv.URL
	}
	if tts == nil {
		cfg.synthesizer = &fakeTTS{}
	}
	s, err := New(cfg, origin, fakeStore{expires: time.Now().Add(time.Hour)})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(s.Close)
	return s
}

func uploadReq(body []byte, contentType string) (*httptest.ResponseRecorder, *http.Request) {
	r := httptest.NewRequest(http.MethodPost, "/api/conversations/"+convID+"/transcriptions", bytes.NewReader(body))
	r.Header.Set("Origin", origin)
	if contentType != "" {
		r.Header.Set("Content-Type", contentType)
	}
	return httptest.NewRecorder(), r
}

func okWorker(text string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]string{"text": text, "language": "en"})
	}
}

func wantCode(t *testing.T, err *Error, status int, code string) {
	t.Helper()
	if err == nil || err.Status != status || err.Code != code {
		t.Fatalf("want %d %s, got %+v", status, code, err)
	}
}

func TestTranscribeForwardsRecordingAndTrimsText(t *testing.T) {
	var got struct {
		path, contentType, requestID, cookie string
		body                                 []byte
	}
	s := newService(t, func(w http.ResponseWriter, r *http.Request) {
		got.path, got.contentType = r.URL.Path, r.Header.Get("Content-Type")
		got.requestID, got.cookie = r.Header.Get("X-Request-ID"), r.Header.Get("Cookie")
		got.body, _ = io.ReadAll(r.Body)
		okWorker("  Why do they look blue today?\n")(w, r)
	}, nil)
	clip := mp4Audio()
	w, r := uploadReq(clip, "audio/mp4; codecs=mp4a.40.2")
	r.AddCookie(&http.Cookie{Name: "musebridge_visitor", Value: visitor})
	result, err := s.Transcribe(w, r, visitor, convID, "req-1")
	if err != nil {
		t.Fatal(err)
	}
	if result != (Transcript{Text: "Why do they look blue today?", Language: "en"}) {
		t.Fatalf("result %+v", result)
	}
	if got.path != "/recognize" || got.contentType != "audio/mp4; codecs=mp4a.40.2" || got.requestID != "req-1" {
		t.Fatalf("worker request %+v", got)
	}
	if got.cookie != "" || !bytes.Equal(got.body, clip) {
		t.Fatal("worker must get the exact clip and no browser cookie")
	}
}

func TestTranscribeAcceptsSafariAndDesktopFormats(t *testing.T) {
	s := newService(t, okWorker("What color were they?"), nil)
	for _, c := range []struct {
		body []byte
		ct   string
	}{
		{mp4Audio(), "audio/mp4"},
		{mp4Audio(), "video/mp4;codecs=mp4a.40.2"},
		{mp4Audio(), "audio/mp4; codecs=opus"},
		{webmAudio(), "audio/webm;codecs=opus"},
		{webmAudio(), "audio/webm"},
	} {
		w, r := uploadReq(c.body, c.ct)
		if _, err := s.Transcribe(w, r, visitor, convID, "req"); err != nil {
			t.Errorf("%s: %+v", c.ct, err)
		}
	}
}

func TestTranscribeRejectsBeforeCallingWorker(t *testing.T) {
	var calls atomic.Int32
	s := newService(t, func(w http.ResponseWriter, r *http.Request) { calls.Add(1); okWorker("x")(w, r) }, nil)
	video := append(mp4Audio(), []byte("\x00\x00\x00\x21hdlr\x00\x00\x00\x00\x00\x00\x00\x00vide")...)
	matroska := append([]byte{0x1A, 0x45, 0xDF, 0xA3}, []byte("matroska")...)
	cases := []struct {
		name   string
		mutate func(*http.Request)
		token  string
		id     string
		body   []byte
		ct     string
		status int
		code   string
	}{
		{"missing origin", func(r *http.Request) { r.Header.Del("Origin") }, visitor, convID, mp4Audio(), "audio/mp4", 403, "origin_not_allowed"},
		{"other origin", func(r *http.Request) { r.Header.Set("Origin", "http://museum.example") }, visitor, convID, mp4Audio(), "audio/mp4", 403, "origin_not_allowed"},
		{"bad id", nil, visitor, "not-a-uuid", mp4Audio(), "audio/mp4", 400, "invalid_request"},
		{"foreign visitor", nil, "someone-else", convID, mp4Audio(), "audio/mp4", 404, "conversation_not_found"},
		{"unknown conversation", nil, visitor, "22222222-2222-4222-8222-222222222222", mp4Audio(), "audio/mp4", 404, "conversation_not_found"},
		{"guide conversation", nil, visitor, guideID, mp4Audio(), "audio/mp4", 409, "voice_mode_unavailable"},
		{"json body", nil, visitor, convID, []byte(`{"audio":"x"}`), "application/json", 415, "unsupported_media_type"},
		{"multipart", nil, visitor, convID, mp4Audio(), "multipart/form-data; boundary=x", 415, "unsupported_media_type"},
		{"wrong codec", nil, visitor, convID, webmAudio(), "audio/webm;codecs=vorbis", 415, "unsupported_media_type"},
		{"extra parameter", nil, visitor, convID, mp4Audio(), "audio/mp4;rate=44100", 415, "unsupported_media_type"},
		{"compressed", func(r *http.Request) { r.Header.Set("Content-Encoding", "gzip") }, visitor, convID, mp4Audio(), "audio/mp4", 415, "unsupported_media_type"},
		{"container disagrees", nil, visitor, convID, webmAudio(), "audio/mp4", 415, "unsupported_media_type"},
		{"matroska", nil, visitor, convID, matroska, "audio/webm", 415, "unsupported_media_type"},
		{"video track", nil, visitor, convID, video, "video/mp4", 415, "unsupported_media_type"},
		{"empty", nil, visitor, convID, nil, "audio/mp4", 422, "audio_invalid"},
		{"not audio", nil, visitor, convID, []byte("hello world, not a recording"), "audio/webm", 422, "audio_invalid"},
	}
	for _, c := range cases {
		w, r := uploadReq(c.body, c.ct)
		if c.mutate != nil {
			c.mutate(r)
		}
		_, err := s.Transcribe(w, r, c.token, c.id, "req")
		if err == nil || err.Status != c.status || err.Code != c.code {
			t.Errorf("%s: want %d %s, got %+v", c.name, c.status, c.code, err)
		}
	}
	if calls.Load() != 0 {
		t.Fatalf("worker called %d times for rejected uploads", calls.Load())
	}
}

func TestTranscribeSizeLimitWithAndWithoutLength(t *testing.T) {
	s := newService(t, okWorker("x"), nil)
	big := append(mp4Audio(), bytes.Repeat([]byte{0}, MaxRecordingBytes)...)
	w, r := uploadReq(big, "audio/mp4")
	_, err := s.Transcribe(w, r, visitor, convID, "req")
	wantCode(t, err, 413, "payload_too_large")

	w, r = uploadReq(nil, "audio/mp4")
	r.Body = io.NopCloser(bytes.NewReader(big))
	r.ContentLength = -1
	_, err = s.Transcribe(w, r, visitor, convID, "req")
	wantCode(t, err, 413, "payload_too_large")

	exact := append(mp4Audio(), bytes.Repeat([]byte{0}, MaxRecordingBytes-len(mp4Audio()))...)
	w, r = uploadReq(exact, "audio/mp4")
	if _, err := s.Transcribe(w, r, visitor, convID, "req"); err != nil {
		t.Fatalf("exactly 3 MiB must be accepted: %+v", err)
	}
}

func TestTranscribeMapsWorkerResults(t *testing.T) {
	cases := []struct {
		name   string
		status int
		body   string
		want   int
		code   string
	}{
		{"no speech", 422, `{"error":"no_speech"}`, 422, "no_speech"},
		{"broken audio", 422, `{"error":"audio_invalid"}`, 422, "audio_invalid"},
		{"too long", 422, `{"error":"transcript_too_long"}`, 422, "transcript_too_long"},
		{"too large", 413, `{"error":"payload_too_large"}`, 413, "payload_too_large"},
		{"format", 415, `{"error":"unsupported_media_type"}`, 415, "unsupported_media_type"},
		{"busy", 429, `{"error":"voice_busy"}`, 429, "voice_busy"},
		{"unavailable", 503, `{"error":"transcription_unavailable"}`, 503, "transcription_unavailable"},
		{"unknown code", 422, `{"error":"surprise"}`, 503, "transcription_unavailable"},
		{"code on wrong status", 500, `{"error":"no_speech"}`, 503, "transcription_unavailable"},
		{"html error page", 502, `<html>bad gateway</html>`, 503, "transcription_unavailable"},
		{"extra success field", 200, `{"text":"hi","language":"en","confidence":0.9}`, 503, "transcription_unavailable"},
		{"missing language", 200, `{"text":"hi"}`, 503, "transcription_unavailable"},
		{"other language", 200, `{"text":"hola","language":"es"}`, 503, "transcription_unavailable"},
		{"blank text", 200, `{"text":"   ","language":"en"}`, 422, "no_speech"},
		{"control character", 200, `{"text":"hi\u0007","language":"en"}`, 503, "transcription_unavailable"},
		{"501 characters", 200, `{"text":"` + strings.Repeat("a", 501) + `","language":"en"}`, 422, "transcript_too_long"},
	}
	for _, c := range cases {
		s := newService(t, func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(c.status)
			_, _ = io.WriteString(w, c.body)
		}, nil)
		w, r := uploadReq(mp4Audio(), "audio/mp4")
		_, err := s.Transcribe(w, r, visitor, convID, "req")
		if err == nil || err.Status != c.want || err.Code != c.code {
			t.Errorf("%s: want %d %s, got %+v", c.name, c.want, c.code, err)
		}
	}
	s := newService(t, okWorker(strings.Repeat("é", 500)), nil)
	w, r := uploadReq(mp4Audio(), "audio/mp4")
	if _, err := s.Transcribe(w, r, visitor, convID, "req"); err != nil {
		t.Fatalf("500 code points must pass without truncation: %+v", err)
	}
}

func TestTranscribeUnavailableTimeoutAndBusy(t *testing.T) {
	s := newService(t, okWorker("x"), nil)
	s.whisper.base = "http://127.0.0.1:1" // nothing listening
	w, r := uploadReq(mp4Audio(), "audio/mp4")
	_, err := s.Transcribe(w, r, visitor, convID, "req")
	wantCode(t, err, 503, "transcription_unavailable")

	release := make(chan struct{})
	entered := make(chan struct{}, 2)
	blocking := func(w http.ResponseWriter, r *http.Request) {
		entered <- struct{}{}
		select {
		case <-release:
		case <-r.Context().Done():
		}
	}
	s = newServiceTimeout(t, blocking, nil, 100*time.Millisecond)
	w, r = uploadReq(mp4Audio(), "audio/mp4")
	_, err = s.Transcribe(w, r, visitor, convID, "req")
	wantCode(t, err, 504, "voice_timeout")

	s = newServiceTimeout(t, blocking, nil, 2*time.Second)
	done := make(chan *Error, 1)
	go func() {
		w, r := uploadReq(mp4Audio(), "audio/mp4")
		_, err := s.Transcribe(w, r, visitor, convID, "first")
		done <- err
	}()
	<-entered
	<-entered
	w, r = uploadReq(mp4Audio(), "audio/mp4")
	_, err = s.Transcribe(w, r, visitor, convID, "second")
	wantCode(t, err, 429, "voice_busy")
	close(release)
	<-done
}

func speechReq(id, turn string, body io.Reader) *http.Request {
	r := httptest.NewRequest(http.MethodPost, "/api/conversations/"+id+"/turns/"+turn+"/speech", body)
	r.Header.Set("Origin", origin)
	return r
}

func TestSpeakUsesCommittedTextAndServerPreset(t *testing.T) {
	tts := &fakeTTS{}
	s := newService(t, nil, tts)
	audio, err := s.Speak(speechReq(strings.ToUpper(convID), turnID, http.NoBody), visitor, strings.ToUpper(convID), turnID)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.HasPrefix(audio, []byte("ID3")) || tts.text != answer {
		t.Fatalf("audio %q text %q", audio, tts.text)
	}
	if tts.preset != (Preset{DefaultSpeechModel, DefaultSpeechVoice, DefaultDelivery}) {
		t.Fatalf("preset %+v", tts.preset)
	}
}

func TestSpeakRejections(t *testing.T) {
	tts := &fakeTTS{}
	s := newService(t, nil, tts)
	cases := []struct {
		name   string
		r      *http.Request
		token  string
		id     string
		turn   string
		status int
		code   string
	}{
		{"origin", func() *http.Request { r := speechReq(convID, turnID, nil); r.Header.Del("Origin"); return r }(), visitor, convID, turnID, 403, "origin_not_allowed"},
		{"bad turn id", speechReq(convID, "x", nil), visitor, convID, "x", 400, "invalid_request"},
		{"json body", speechReq(convID, turnID, strings.NewReader(`{"text":"say this"}`)), visitor, convID, turnID, 400, "invalid_request"},
		{"chunked body", func() *http.Request {
			r := speechReq(convID, turnID, nil)
			r.Body, r.ContentLength = io.NopCloser(strings.NewReader("x")), -1
			return r
		}(), visitor, convID, turnID, 400, "invalid_request"},
		{"foreign", speechReq(convID, turnID, nil), "someone-else", convID, turnID, 404, "conversation_not_found"},
		{"guide", speechReq(guideID, turnID, nil), visitor, guideID, turnID, 409, "voice_mode_unavailable"},
		{"unknown turn", speechReq(convID, "33333333-3333-4333-8333-333333333333", nil), visitor, convID, "33333333-3333-4333-8333-333333333333", 404, "turn_not_found"},
	}
	for _, c := range cases {
		_, err := s.Speak(c.r, c.token, c.id, c.turn)
		if err == nil || err.Status != c.status || err.Code != c.code {
			t.Errorf("%s: want %d %s, got %+v", c.name, c.status, c.code, err)
		}
	}
	if tts.calls.Load() != 0 {
		t.Fatal("no paid synthesis may run for a rejected request")
	}
}

func TestSpeakCoalescesCachesAndRetriesFailures(t *testing.T) {
	tts := &fakeTTS{gate: make(chan struct{}), started: make(chan struct{}, 10)}
	s := newService(t, nil, tts)
	var wg sync.WaitGroup
	results := make(chan *Error, 5)
	for i := 0; i < 5; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, err := s.Speak(speechReq(convID, turnID, nil), visitor, convID, turnID)
			results <- err
		}()
	}
	<-tts.started
	time.Sleep(50 * time.Millisecond) // let the others join the same flight
	close(tts.gate)
	wg.Wait()
	close(results)
	for err := range results {
		if err != nil {
			t.Fatal(err)
		}
	}
	if tts.calls.Load() != 1 {
		t.Fatalf("simultaneous requests made %d provider calls, want 1", tts.calls.Load())
	}
	if _, err := s.Speak(speechReq(convID, turnID, nil), visitor, convID, turnID); err != nil || tts.calls.Load() != 1 {
		t.Fatalf("replay should come from cache: err=%v calls=%d", err, tts.calls.Load())
	}

	failing := &fakeTTS{err: errProvider}
	s = newService(t, nil, failing)
	_, err := s.Speak(speechReq(convID, turnID, nil), visitor, convID, turnID)
	wantCode(t, err, 503, "speech_unavailable")
	failing.err = nil
	if _, err := s.Speak(speechReq(convID, turnID, nil), visitor, convID, turnID); err != nil || failing.calls.Load() != 2 {
		t.Fatalf("a failed synthesis must not be cached: err=%v calls=%d", err, failing.calls.Load())
	}
}

func TestSpeakTimeoutAndBusy(t *testing.T) {
	slow := &fakeTTS{gate: make(chan struct{})}
	s := newServiceTimeout(t, nil, slow, 100*time.Millisecond)
	_, err := s.Speak(speechReq(convID, turnID, nil), visitor, convID, turnID)
	wantCode(t, err, 504, "voice_timeout")

	held := &fakeTTS{gate: make(chan struct{}), started: make(chan struct{}, 1)}
	s = newService(t, nil, held) // concurrency 1
	go func() { _, _ = s.speech.speak(context.Background(), "other/turn", answer, time.Now().Add(time.Hour)) }()
	<-held.started
	_, err = s.Speak(speechReq(convID, turnID, nil), visitor, convID, turnID)
	wantCode(t, err, 429, "voice_busy")
	close(held.gate)
}

func TestCacheExpiryAndEviction(t *testing.T) {
	now := time.Unix(1_800_000_000, 0)
	c := newMP3Cache(10)
	c.put("a", []byte("12345"), now.Add(time.Minute))
	c.put("b", []byte("12345"), now.Add(time.Minute))
	c.get("a", now) // a becomes most recent
	c.put("c", []byte("123"), now.Add(time.Minute))
	if _, ok := c.get("b", now); ok {
		t.Fatal("least recently used entry should be evicted")
	}
	if _, ok := c.get("a", now.Add(2*time.Minute)); ok {
		t.Fatal("expired entry must not be served")
	}
	if c.size != 3 {
		t.Fatalf("size %d", c.size)
	}

	tts := &fakeTTS{}
	sp := newSpeaker(tts, Preset{"m", "v", "i"}, 1, func() time.Time { return now }, StageTimeout)
	expires := now.Add(2 * time.Minute) // session ends before the 10-minute TTL
	if _, err := sp.speak(context.Background(), "c/t", answer, expires); err != nil {
		t.Fatal(err)
	}
	entry := sp.cache.entries["c/t#"+sp.preset.Revision()].Value.(*cacheEntry)
	if !entry.expires.Equal(expires) {
		t.Fatalf("cache TTL must be capped by session expiry: %v", entry.expires)
	}
	if (Preset{"m", "v", "i"}).Revision() == (Preset{"m", "v", "other"}).Revision() {
		t.Fatal("changing the delivery must change the cache key")
	}
}

func TestOpenAIRequestShape(t *testing.T) {
	var body map[string]any
	var auth, ct string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		auth, ct = r.Header.Get("Authorization"), r.Header.Get("Content-Type")
		_ = json.NewDecoder(r.Body).Decode(&body)
		w.Header().Set("Content-Type", "audio/mpeg")
		_, _ = w.Write([]byte("ID3audio"))
	}))
	defer srv.Close()
	o := newOpenAISpeech(srv.URL, "sk-test-key")
	audio, err := o.Synthesize(context.Background(), answer, Preset{DefaultSpeechModel, "cedar", DefaultDelivery})
	if err != nil || string(audio) != "ID3audio" {
		t.Fatalf("audio %q err %v", audio, err)
	}
	if auth != "Bearer sk-test-key" || ct != "application/json" {
		t.Fatalf("headers %q %q", auth, ct)
	}
	want := map[string]any{"model": DefaultSpeechModel, "voice": "cedar", "input": answer,
		"instructions": DefaultDelivery, "response_format": "mp3"}
	if len(body) != len(want) {
		t.Fatalf("request must carry only the answer and speech settings: %v", body)
	}
	for k, v := range want {
		if body[k] != v {
			t.Fatalf("%s = %v", k, body[k])
		}
	}

	for _, handler := range []http.HandlerFunc{
		func(w http.ResponseWriter, r *http.Request) { http.Error(w, `{"error":"quota"}`, 429) },
		func(w http.ResponseWriter, r *http.Request) {},
	} {
		srv := httptest.NewServer(handler)
		_, err := newOpenAISpeech(srv.URL, "sk-test-key").Synthesize(context.Background(), answer, Preset{})
		srv.Close()
		if !errors.Is(err, errProvider) {
			t.Fatalf("want provider failure, got %v", err)
		}
	}
}

func TestLoadConfig(t *testing.T) {
	env := func(m map[string]string) func(string) (string, bool) {
		return func(k string) (string, bool) { v, ok := m[k]; return v, ok }
	}
	cfg, err := LoadConfig(env(nil))
	if err != nil || cfg.Enabled {
		t.Fatalf("voice must be off by default: %+v %v", cfg, err)
	}
	good := map[string]string{"VINCENT_ENABLED": "true", "WHISPER_BASE_URL": "http://127.0.0.1:8003", "OPENAI_API_KEY": "sk-0123456789abcdefghij"}
	cfg, err = LoadConfig(env(good))
	if err != nil || !cfg.Enabled || cfg.SpeechVoice != "cedar" || cfg.SpeechConcurrency != 2 {
		t.Fatalf("%+v %v", cfg, err)
	}
	if strings.Contains(cfg.String()+cfg.GoString(), "sk-") {
		t.Fatal("config formatting leaked the key")
	}
	bad := []map[string]string{
		{"VINCENT_ENABLED": "yes"},
		{"VINCENT_ENABLED": "true", "OPENAI_API_KEY": good["OPENAI_API_KEY"]},
		{"VINCENT_ENABLED": "true", "WHISPER_BASE_URL": "http://192.168.1.20:8003", "OPENAI_API_KEY": good["OPENAI_API_KEY"]},
		{"VINCENT_ENABLED": "true", "WHISPER_BASE_URL": "https://127.0.0.1:8003", "OPENAI_API_KEY": good["OPENAI_API_KEY"]},
		{"VINCENT_ENABLED": "true", "WHISPER_BASE_URL": good["WHISPER_BASE_URL"]},
		{"VINCENT_ENABLED": "true", "WHISPER_BASE_URL": good["WHISPER_BASE_URL"], "OPENAI_API_KEY": good["OPENAI_API_KEY"], "VINCENT_TTS_CONCURRENCY": "0"},
		{"VINCENT_ENABLED": "true", "WHISPER_BASE_URL": good["WHISPER_BASE_URL"], "OPENAI_API_KEY": good["OPENAI_API_KEY"], "VINCENT_TTS_VOICE": "Cedar Voice"},
	}
	for i, m := range bad {
		if _, err := LoadConfig(env(m)); err == nil {
			t.Errorf("case %d should fail: %v", i, m)
		}
	}
}
