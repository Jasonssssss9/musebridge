package conversation

import (
	"encoding/base64"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func TestScopeOwnershipAndRestart(t *testing.T) {
	store := New(nil)
	token, first, created := store.Open("untrusted-cookie", "aic-28560", "aic-seed-v0.1")
	random, err := base64.RawURLEncoding.DecodeString(token)
	if !created || token == "untrusted-cookie" || err != nil || len(random) != 32 {
		t.Fatal("new identity must use a server-generated 256-bit token")
	}
	if id, valid := CanonicalID(first.ConversationID); !valid || id[14] != '4' || !strings.ContainsRune("89ab", rune(id[19])) {
		t.Fatal("conversation ID is not a canonical UUID v4")
	}
	if !first.ExpiresAt.Equal(first.CreatedAt.Add(Lifetime)) || first.CreatedAt.Location() != time.UTC {
		t.Fatal("conversation lifetime or UTC timestamp is incorrect")
	}
	_, restored, created := store.Open(token, first.ArtworkID, first.DatasetVersion)
	if created || restored != first {
		t.Fatal("same visitor and scope did not restore identical metadata")
	}
	_, otherArtwork, _ := store.Open(token, "aic-11723", first.DatasetVersion)
	_, otherRelease, _ := store.Open(token, first.ArtworkID, "aic-seed-v0.2")
	otherToken, otherVisitor, _ := store.Open("", first.ArtworkID, first.DatasetVersion)
	for _, current := range []Conversation{otherArtwork, otherRelease, otherVisitor} {
		if current.ConversationID == first.ConversationID {
			t.Fatal("different scopes or visitors shared a conversation")
		}
	}
	for _, identity := range []string{"", "unknown-cookie", otherToken} {
		if _, found := store.History(identity, first.ConversationID); found {
			t.Fatal("history was exposed to a caller without ownership")
		}
	}
	history, found := store.History(token, first.ConversationID)
	if !found || history.Conversation != first || history.Turns == nil || len(history.Turns) != 0 || history.PendingRequest != nil {
		t.Fatal("new history does not match the empty-history contract")
	}
	history.Conversation.ArtworkID = "changed-by-caller"
	history.Turns = append(history.Turns, Turn{Question: "changed-by-caller"})
	fresh, _ := store.History(token, first.ConversationID)
	if fresh.Conversation != first || len(fresh.Turns) != 0 {
		t.Fatal("changing a snapshot mutated stored state")
	}
	restarted := New(nil)
	if _, found := restarted.History(token, first.ConversationID); found {
		t.Fatal("a new process unexpectedly retained the old history")
	}
	newToken, newConversation, created := restarted.Open(token, first.ArtworkID, first.DatasetVersion)
	if !created || newToken == token || newConversation.ConversationID == first.ConversationID {
		t.Fatal("restart did not establish fresh anonymous state")
	}
}

func TestFixedExpiryAndIdentityRenewal(t *testing.T) {
	now := time.Date(2026, 11, 10, 12, 0, 0, 0, time.FixedZone("test", 8*60*60))
	store := New(func() time.Time { return now })
	token, first, _ := store.Open("", "aic-28560", "aic-seed-v0.1")
	now = now.Add(23 * time.Hour)
	_, restored, created := store.Open(token, first.ArtworkID, first.DatasetVersion)
	if created || restored != first {
		t.Fatal("restoring a conversation changed its fixed expiry")
	}
	now = first.ExpiresAt.Add(-time.Nanosecond)
	if _, found := store.History(token, first.ConversationID); !found {
		t.Fatal("conversation expired before its deadline")
	}
	now = first.ExpiresAt
	if _, found := store.History(token, first.ConversationID); found {
		t.Fatal("conversation remained available at its exact deadline")
	}
	renewedToken, next, created := store.Open(token, first.ArtworkID, first.DatasetVersion)
	if !created || renewedToken != token || next.ConversationID == first.ConversationID || !next.CreatedAt.Equal(now) {
		t.Fatal("an expired conversation was not replaced under the renewed identity")
	}
	if len(store.conversations) != 1 || len(store.visitors[token].conversations) != 1 {
		t.Fatal("expired conversation records were retained in an index")
	}
}

func TestHistoryDoesNotRenewIdentity(t *testing.T) {
	now := time.Date(2026, 11, 10, 0, 0, 0, 0, time.UTC)
	store := New(func() time.Time { return now })
	token, current, _ := store.Open("", "aic-28560", "aic-seed-v0.1")
	now = now.Add(23 * time.Hour)
	store.History(token, current.ConversationID)
	now = now.Add(time.Hour)
	if _, found := store.History(token, current.ConversationID); found || len(store.visitors) != 0 || len(store.conversations) != 0 {
		t.Fatal("history reads extended identity or retained expired state")
	}
	newToken, _, _ := store.Open(token, current.ArtworkID, current.DatasetVersion)
	if newToken == token {
		t.Fatal("expired identity was reused")
	}
}

func TestConcurrentOpenUsesOneConversation(t *testing.T) {
	store := New(nil)
	token, _, _ := store.Open("", "aic-28560", "aic-seed-v0.1")
	var createdCount atomic.Int32
	var wait sync.WaitGroup
	ids := make(chan string, 64)
	for range cap(ids) {
		wait.Add(1)
		go func() {
			defer wait.Done()
			_, current, created := store.Open(token, "aic-11723", "aic-seed-v0.1")
			if created {
				createdCount.Add(1)
			}
			ids <- current.ConversationID
			if _, found := store.History(token, current.ConversationID); !found {
				t.Error("concurrent history read lost an accepted conversation")
			}
		}()
	}
	wait.Wait()
	close(ids)
	first := <-ids
	for id := range ids {
		if id != first {
			t.Fatal("concurrent creation returned multiple conversations")
		}
	}
	if createdCount.Load() != 1 {
		t.Fatal("conversation creation was not atomic")
	}
}

func TestCanonicalID(t *testing.T) {
	id := "B15425C0-1F61-4ED2-9EC5-74AE9A9EA152"
	if canonical, ok := CanonicalID(id); !ok || canonical != strings.ToLower(id) {
		t.Fatal("canonical UUID text was not accepted case-insensitively")
	}
	for _, invalid := range []string{"", "not-a-uuid", "{" + id + "}", "urn:uuid:" + id, strings.ReplaceAll(id, "-", ""), " " + id, "g" + id[1:]} {
		if _, ok := CanonicalID(invalid); ok {
			t.Fatalf("accepted a malformed UUID: %q", invalid)
		}
	}
}
