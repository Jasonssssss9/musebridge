package voice

import (
	"bytes"
	"container/list"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"sync"
	"time"
)

const (
	cacheMaxBytes  = 32 << 20
	cacheTTL       = 10 * time.Minute
	maxSpeechBytes = 16 << 20
)

var (
	errProviderTimeout = errors.New("speech provider timed out")
	errProvider        = errors.New("speech provider failed")
)

// synthesizer turns committed answer text into MP3 bytes.
type synthesizer interface {
	Synthesize(ctx context.Context, text string, preset Preset) ([]byte, error)
	Close()
}

// openAISpeech calls POST /v1/audio/speech. The request carries only the
// committed answer and the server's speech settings: no citations, recordings,
// transcripts, history, memory, visitor cookie or project token.
type openAISpeech struct {
	endpoint, key string
	client        *http.Client
}

func newOpenAISpeech(endpoint, key string) *openAISpeech {
	return &openAISpeech{endpoint: endpoint, key: key, client: &http.Client{}}
}

func (o *openAISpeech) Close() { o.client.CloseIdleConnections() }

func (o *openAISpeech) Synthesize(ctx context.Context, text string, preset Preset) ([]byte, error) {
	payload, _ := json.Marshal(struct {
		Model          string `json:"model"`
		Voice          string `json:"voice"`
		Input          string `json:"input"`
		Instructions   string `json:"instructions,omitempty"`
		ResponseFormat string `json:"response_format"`
	}{preset.Model, preset.Voice, text, preset.Instructions, "mp3"})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, o.endpoint, bytes.NewReader(payload))
	if err != nil {
		return nil, errProvider
	}
	req.Header.Set("Authorization", "Bearer "+o.key)
	req.Header.Set("Content-Type", "application/json")
	resp, err := o.client.Do(req)
	if err != nil {
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			return nil, errProviderTimeout
		}
		return nil, errProvider
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		// Provider bodies may echo request details; never pass them on.
		_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 64<<10))
		return nil, errProvider
	}
	audio, err := io.ReadAll(io.LimitReader(resp.Body, maxSpeechBytes+1))
	if err != nil {
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			return nil, errProviderTimeout
		}
		return nil, errProvider
	}
	if len(audio) == 0 || len(audio) > maxSpeechBytes {
		return nil, errProvider
	}
	return audio, nil
}

// speaker coalesces identical requests, bounds paid syntheses in flight and
// keeps successful MP3s in a small LRU cache. Failures are never cached.
type speaker struct {
	tts     synthesizer
	preset  Preset
	now     func() time.Time
	timeout time.Duration
	slots   chan struct{}

	mu       sync.Mutex
	inflight map[string]*flight
	cache    *mp3Cache
}

type flight struct {
	done  chan struct{}
	audio []byte
	err   *Error
}

func newSpeaker(tts synthesizer, preset Preset, concurrency int, now func() time.Time, timeout time.Duration) *speaker {
	if concurrency < 1 {
		concurrency = 1
	}
	return &speaker{
		tts: tts, preset: preset, now: now, timeout: timeout, slots: make(chan struct{}, concurrency),
		inflight: make(map[string]*flight), cache: newMP3Cache(cacheMaxBytes),
	}
}

func (s *speaker) close() { s.tts.Close() }

// speak returns audio for one owned committed turn. ctx bounds this caller's wait;
// the shared synthesis has its own stage deadline so one disconnect cannot cancel it.
func (s *speaker) speak(ctx context.Context, turnKey, text string, conversationExpires time.Time) ([]byte, *Error) {
	key := turnKey + "#" + s.preset.Revision()
	s.mu.Lock()
	if audio, ok := s.cache.get(key, s.now()); ok {
		s.mu.Unlock()
		return audio, nil
	}
	f, joined := s.inflight[key]
	if !joined {
		select {
		case s.slots <- struct{}{}:
		default:
			s.mu.Unlock()
			return nil, errBusy
		}
		f = &flight{done: make(chan struct{})}
		s.inflight[key] = f
		go s.run(key, text, conversationExpires, f)
	}
	s.mu.Unlock()

	select {
	case <-f.done:
		return f.audio, f.err
	case <-ctx.Done():
		return nil, errTimeout
	}
}

func (s *speaker) run(key, text string, conversationExpires time.Time, f *flight) {
	defer func() { <-s.slots }()
	ctx, cancel := context.WithTimeout(context.Background(), s.timeout)
	audio, err := s.tts.Synthesize(ctx, text, s.preset)
	cancel()
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.inflight, key)
	switch {
	case err == nil:
		f.audio = audio
		now := s.now()
		expires := now.Add(cacheTTL)
		if conversationExpires.Before(expires) {
			expires = conversationExpires
		}
		if expires.After(now) {
			s.cache.put(key, audio, expires)
		}
	case errors.Is(err, errProviderTimeout):
		f.err = errTimeout
	default:
		f.err = errSpeech
	}
	close(f.done)
}

// mp3Cache is a size-bounded LRU with per-entry expiry. Callers hold speaker.mu.
type mp3Cache struct {
	max, size int
	order     *list.List
	entries   map[string]*list.Element
}

type cacheEntry struct {
	key     string
	audio   []byte
	expires time.Time
}

func newMP3Cache(max int) *mp3Cache {
	return &mp3Cache{max: max, order: list.New(), entries: make(map[string]*list.Element)}
}

func (c *mp3Cache) get(key string, now time.Time) ([]byte, bool) {
	el, ok := c.entries[key]
	if !ok {
		return nil, false
	}
	entry := el.Value.(*cacheEntry)
	if !now.Before(entry.expires) {
		c.remove(el)
		return nil, false
	}
	c.order.MoveToFront(el)
	return entry.audio, true
}

func (c *mp3Cache) put(key string, audio []byte, expires time.Time) {
	if len(audio) > c.max {
		return
	}
	if el, ok := c.entries[key]; ok {
		c.remove(el)
	}
	c.entries[key] = c.order.PushFront(&cacheEntry{key: key, audio: audio, expires: expires})
	c.size += len(audio)
	for c.size > c.max {
		c.remove(c.order.Back())
	}
}

func (c *mp3Cache) remove(el *list.Element) {
	entry := c.order.Remove(el).(*cacheEntry)
	delete(c.entries, entry.key)
	c.size -= len(entry.audio)
}
