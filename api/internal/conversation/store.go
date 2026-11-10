package conversation

import (
	"crypto/rand"
	"encoding/base64"
	"fmt"
	"strings"
	"sync"
	"time"
)

const Lifetime = 24 * time.Hour

type scope struct {
	artworkID, datasetVersion string
}

type visitor struct {
	expiresAt     time.Time
	conversations map[scope]string
}

type record struct {
	owner        string
	conversation Conversation
}

// Store is private to a handler/process. It is safe for concurrent requests.
// A supplied clock allows exact expiry checks without waiting in tests.
type Store struct {
	mu            sync.Mutex
	now           func() time.Time
	visitors      map[string]*visitor
	conversations map[string]record
}

func New(now func() time.Time) *Store {
	if now == nil {
		now = time.Now
	}
	return &Store{
		now: now, visitors: make(map[string]*visitor), conversations: make(map[string]record),
	}
}

// Open atomically resolves identity and creates or restores one live scope.
// The caller must validate the artwork and release against its loaded catalog.
// Only this operation renews visitor identity; conversation expiry stays fixed.
func (s *Store) Open(token, artworkID, datasetVersion string) (string, Conversation, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	now := s.now().UTC()
	s.expire(now)
	v := s.visitors[token]
	if v == nil {
		var random [32]byte
		_, _ = rand.Read(random[:])
		token = base64.RawURLEncoding.EncodeToString(random[:])
		v = &visitor{conversations: make(map[scope]string)}
		s.visitors[token] = v
	}
	v.expiresAt = now.Add(Lifetime)
	key := scope{artworkID, datasetVersion}
	if id, ok := v.conversations[key]; ok {
		return token, s.conversations[id].conversation, false
	}
	conversation := Conversation{
		ConversationID: newUUID(), ArtworkID: artworkID, DatasetVersion: datasetVersion,
		CreatedAt: now, ExpiresAt: now.Add(Lifetime),
	}
	s.conversations[conversation.ConversationID] = record{token, conversation}
	v.conversations[key] = conversation.ConversationID
	return token, conversation, true
}

// History never creates or renews identity. All unavailable ownership and
// expiry conditions have the same result, including after a process restart.
func (s *Store) History(token, id string) (History, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.expire(s.now().UTC())
	item, ok := s.conversations[id]
	if !ok || s.visitors[token] == nil || item.owner != token {
		return History{}, false
	}
	return History{Conversation: item.conversation, Turns: make([]Turn, 0)}, true
}

// expire runs under the lock and removes expired entries from both indexes.
func (s *Store) expire(now time.Time) {
	for id, item := range s.conversations {
		if !now.Before(item.conversation.ExpiresAt) {
			delete(s.conversations, id)
			if v := s.visitors[item.owner]; v != nil {
				delete(v.conversations, scope{item.conversation.ArtworkID, item.conversation.DatasetVersion})
			}
		}
	}
	for token, v := range s.visitors {
		if !now.Before(v.expiresAt) {
			delete(s.visitors, token)
		}
	}
}

// CanonicalID accepts canonical UUID text in either case, without accepting
// alternative representations or interpreting the ID as an authorization token.
func CanonicalID(id string) (string, bool) {
	if len(id) != 36 {
		return "", false
	}
	for i := range len(id) {
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

func newUUID() string {
	var bytes [16]byte
	_, _ = rand.Read(bytes[:])
	bytes[6] = bytes[6]&0x0f | 0x40
	bytes[8] = bytes[8]&0x3f | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", bytes[:4], bytes[4:6], bytes[6:8], bytes[8:10], bytes[10:])
}
