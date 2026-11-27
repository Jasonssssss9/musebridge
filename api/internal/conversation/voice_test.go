package conversation

import (
	"testing"
	"time"
)

func TestVoiceConversationModeOwnershipAndExpiry(t *testing.T) {
	store, clock, token, current := newAgentTest(t, "aic-28560")
	id := string(current.ConversationID)
	mode, expires, found := store.VoiceConversation(token, id)
	if !found || mode != "guide" || !expires.Equal(clock.wall.UTC().Add(Lifetime)) {
		t.Fatalf("new Agent conversations are guide mode: %q %v %v", mode, expires, found)
	}
	store.mu.Lock()
	store.conversations[id].agent.mode = "vincent"
	store.mu.Unlock()
	if mode, _, _ := store.VoiceConversation(token, id); mode != "vincent" {
		t.Fatalf("mode %q", mode)
	}
	if _, _, found := store.VoiceConversation("someone-else", id); found {
		t.Fatal("a foreign visitor must not see the conversation")
	}
	if _, found := store.CommittedAnswer(token, id, "33333333-3333-4333-8333-333333333333"); found {
		t.Fatal("an unknown turn must be absent")
	}
	clock.wall = clock.wall.Add(Lifetime + time.Second)
	if _, _, found := store.VoiceConversation(token, id); found {
		t.Fatal("an expired conversation must be absent")
	}
}

func TestFixtureConversationsAreNotVoiceTargets(t *testing.T) {
	store := New(nil)
	token, current, _ := store.Open("", "aic-28560", "aic-seed-v0.1")
	if _, _, found := store.VoiceConversation(token, current.ConversationID); found {
		t.Fatal("fixture conversations have no voice mode")
	}
}
