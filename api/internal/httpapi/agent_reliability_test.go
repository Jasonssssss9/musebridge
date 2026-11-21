package httpapi

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/cookiejar"
	"reflect"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	ac "github.com/Jasonssssss9/musebridge/api/internal/agentcontract"
)

func TestM2B4PublicDependencyFailures(t *testing.T) {
	for _, tc := range []struct {
		code, phase, public string
		upstream, status    int
		retry               bool
	}{
		{"agent_busy", "capacity", "agent_unavailable", 503, 503, true},
		{"service_not_ready", "admission", "agent_unavailable", 503, 503, true},
		{"agent_unauthorized", "admission", "agent_unavailable", 401, 503, false},
		{"agent_contract_mismatch", "admission", "agent_invalid_response", 409, 502, false},
		{"agent_scope_mismatch", "context", "agent_invalid_response", 409, 502, false},
		{"agent_context_limit", "context", "agent_context_limit", 502, 502, false},
		{"retrieval_unavailable", "retrieval", "retrieval_unavailable", 503, 503, true},
		{"retrieval_timeout", "retrieval", "retrieval_timeout", 504, 504, true},
		{"retrieval_invalid_response", "retrieval", "retrieval_invalid_response", 502, 502, false},
		{"generation_unavailable", "planning", "generation_unavailable", 503, 503, true},
		{"generation_timeout", "assessment", "generation_timeout", 504, 504, true},
		{"generation_invalid_response", "validation", "generation_invalid_response", 502, 502, false},
		{"request_timeout", "planning", "request_timeout", 504, 504, true},
	} {
		t.Run(tc.code, func(t *testing.T) {
			var fail atomic.Bool
			fail.Store(true)
			a := newPrivateHTTP(t, func(w http.ResponseWriter, r *http.Request, run ac.RunRequest) {
				if fail.Load() {
					writePrivateError(w, r, run, tc.upstream, tc.code, tc.phase, tc.retry)
					return
				}
				writePrivate(w, r, 200, dialogueSnapshot(run))
			})
			b := newReliabilityStack(t, a)
			current := b.open(t, "aic-28560")
			message := agentMessage(1, 0)
			language := ac.LanguagePreference("es")
			message.LanguageOverride = &language
			publicFailure(t, b.post(current.ConversationID, message), tc.status, tc.public, tc.retry)
			history := b.history(t, current.ConversationID)
			if history.Conversation.Revision != 0 || history.Conversation.LanguagePreference != "auto" || history.Conversation.LastAnswerLanguage != nil || len(history.Turns) != 0 || history.PendingRequest == nil ||
				history.PendingRequest.State != "failed" || history.PendingRequest.ClientMessageID != message.ClientMessageID || history.PendingRequest.LanguageOverride == nil || *history.PendingRequest.LanguageOverride != "es" || history.PendingRequest.LastError.Code != tc.public {
				t.Fatal("technical failure changed state or lost the original retry payload")
			}
			posts, gets := a.calls()
			if len(posts) != 1 || len(gets) != 0 {
				t.Fatal("known dependency failure triggered extra execution/status queries")
			}
			fail.Store(false)
			if !tc.retry {
				message.ClientMessageID = ac.UUID(messageID(2))
			}
			var result ac.PublicSubmissionResponse
			decodePublic(t, b.post(current.ConversationID, message), "PublicSubmissionResponse", &result)
			posts, gets = a.calls()
			if len(posts) != 2 || len(gets) != 0 || posts[0].Run.RunID == posts[1].Run.RunID || posts[1].Run.ConversationState.Revision != 0 || len(posts[1].Run.ConversationState.VerifiedEvidence) != 0 || result.Conversation.Revision != 1 || result.Conversation.LanguagePreference != "es" {
				t.Fatal("deliberate recovery reused a failed run or persisted failed language/evidence")
			}
		})
	}
}

func TestM2B4PublicConcurrentAdmissionAndRefresh(t *testing.T) {
	started, release := make(chan struct{}), make(chan struct{})
	a := newPrivateHTTP(t, func(w http.ResponseWriter, r *http.Request, run ac.RunRequest) {
		if run.Scope.ArtworkID == "aic-28560" {
			close(started)
			select {
			case <-release:
			case <-r.Context().Done():
				return
			}
		}
		writePrivate(w, r, 200, dialogueSnapshot(run))
	})
	b := newReliabilityStack(t, a)
	first := b.open(t, "aic-28560")
	second := b.open(t, "aic-11723")
	releaseOnCleanup(t, release)
	original := agentMessage(1, 0)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	finished := make(chan publicReply, 1)
	go func() { finished <- b.call(ctx, "POST", messagesPath(first.ConversationID), original) }()
	waitSignal(t, started)
	const count = 12
	var wait sync.WaitGroup
	replies := make(chan publicReply, count)
	for i := range count {
		wait.Add(1)
		go func() {
			defer wait.Done()
			message := original
			if i%2 == 1 {
				message.ClientMessageID = ac.UUID(messageID(i + 10))
			}
			replies <- b.post(first.ConversationID, message)
		}()
	}
	wait.Wait()
	close(replies)
	duplicate, busy := 0, 0
	for reply := range replies {
		if strings.Contains(string(reply.Body), "request_in_progress") {
			publicFailure(t, reply, 409, "request_in_progress", true)
			duplicate++
		} else {
			publicFailure(t, reply, 409, "conversation_busy", true)
			busy++
		}
	}
	if duplicate != count/2 || busy != count/2 {
		t.Fatal("concurrent admission did not distinguish duplicate and competing messages")
	}
	changed := original
	changed.Question += "?"
	publicFailure(t, b.post(first.ConversationID, changed), 409, "idempotency_conflict", false)
	cancel()
	if reply := <-finished; reply.Err == nil {
		t.Fatal("browser connection unexpectedly survived deliberate cancellation")
	}
	restored := b.open(t, "aic-28560")
	history := b.history(t, first.ConversationID)
	if restored.ConversationID != first.ConversationID || history.Conversation.Revision != 0 || history.PendingRequest == nil || history.PendingRequest.State != "processing" || history.PendingRequest.ClientMessageID != original.ClientMessageID {
		t.Fatal("refresh did not recover the processing submission")
	}
	var other ac.PublicSubmissionResponse
	decodePublic(t, b.post(second.ConversationID, agentMessage(2, 0)), "PublicSubmissionResponse", &other)
	if other.Conversation.Revision != 1 {
		t.Fatal("blocked artwork prevented a different artwork conversation")
	}
	close(release)
	deadline := time.Now().Add(5 * time.Second)
	for {
		history = b.history(t, first.ConversationID)
		if history.Conversation.Revision == 1 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("cancelled browser's accepted turn was not completed")
		}
		time.Sleep(10 * time.Millisecond)
	}
	var replay ac.PublicSubmissionResponse
	decodePublic(t, b.post(first.ConversationID, original), "PublicSubmissionResponse", &replay)
	posts, gets := a.calls()
	if len(posts) != 2 || len(gets) != 0 || history.PendingRequest != nil || len(history.Turns) != 1 || replay.Turn.TurnID != history.Turns[0].TurnID || b.history(t, second.ConversationID).Conversation.Revision != 1 {
		t.Fatal("cancel/refresh/retry duplicated a turn or mixed artwork histories")
	}
}

func materialResultFactory(t *testing.T) func(ac.RunRequest) ac.RunSnapshot {
	t.Helper()
	dataset := testDataset(t)
	records := map[string]ac.Evidence{}
	citations := map[string]ac.Citation{}
	for _, art := range []string{"aic-28560", "aic-11723"} {
		e, found := dataset.EvidenceByChunkID(art + "-medium")
		if !found {
			t.Fatal("missing released test evidence")
		}
		data, _ := json.Marshal(e)
		var record ac.Evidence
		var citation ac.Citation
		if json.Unmarshal(data, &record) != nil || json.Unmarshal(data, &citation) != nil {
			t.Fatal("invalid released test evidence")
		}
		records[art], citations[art] = record, citation
	}
	return func(run ac.RunRequest) ac.RunSnapshot {
		snapshot := dialogueSnapshot(run)
		result := snapshot.Result
		evidence := records[run.Scope.ArtworkID]
		found := false
		for _, e := range result.EvidenceSnapshot {
			found = found || e.ChunkID == evidence.ChunkID
		}
		if !found {
			result.EvidenceSnapshot = append(result.EvidenceSnapshot, evidence)
		}
		result.Requests[0].Kind = "artwork_fact"
		result.Requests[0].Intent = "Identify the released painting medium."
		result.RequestResults[0].Support = "supported"
		result.RequestResults[0].Text = "The medium is oil on canvas."
		result.RequestResults[0].CitationIDs = []string{evidence.ChunkID}
		result.Answer = ac.AgentAnswer{Status: "answered", Text: result.RequestResults[0].Text, Citations: []ac.Citation{citations[run.Scope.ArtworkID]}}
		result.Execution.ModelCalls = 2
		if !found {
			result.ExecutedQueries = []ac.SearchQuery{{ID: "q1", Query: "What material is this painting made of?", RequestIDs: []ac.RequestID{"r1"}}}
			result.Execution.RetrievalCalls = 1
		}
		return snapshot
	}
}

func TestM2B4PublicInvalidResultCannotOverwriteState(t *testing.T) {
	material := materialResultFactory(t)
	for _, tc := range []struct {
		name   string
		mutate func(*ac.RunSnapshot)
	}{
		{"run-id", func(s *ac.RunSnapshot) { s.RunID = ac.UUID(messageID(999)) }},
		{"artwork", func(s *ac.RunSnapshot) { s.Result.Scope.ArtworkID = "aic-11723" }},
		{"dataset", func(s *ac.RunSnapshot) { s.Result.Scope.DatasetVersion = "other-release" }},
		{"base-revision", func(s *ac.RunSnapshot) { s.Result.ConversationUpdate.BaseRevision++ }},
		{"language", func(s *ac.RunSnapshot) { s.Result.AnswerLanguage = "ko" }},
		{"catalog", func(s *ac.RunSnapshot) { s.Result.CatalogSnapshot[0].Value = "PRIVATE forged catalog" }},
		{"cache", func(s *ac.RunSnapshot) { s.Result.EvidenceSnapshot[0].Excerpt = "PRIVATE forged cache" }},
		{"foreign-citation", func(s *ac.RunSnapshot) { s.Result.Answer.Citations[0].ArtworkID = "aic-11723" }},
		{"citation-id", func(s *ac.RunSnapshot) { s.Result.Answer.Citations[0].ChunkID = ac.Some("aic-11723-medium") }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var corrupt atomic.Bool
			a := newPrivateHTTP(t, func(w http.ResponseWriter, r *http.Request, run ac.RunRequest) {
				result := material(run)
				if corrupt.Load() {
					tc.mutate(&result)
				}
				writePrivate(w, r, 200, result)
			})
			b := newReliabilityStack(t, a)
			current := b.open(t, "aic-28560")
			var first ac.PublicSubmissionResponse
			decodePublic(t, b.post(current.ConversationID, agentMessage(1, 0)), "PublicSubmissionResponse", &first)
			before := b.history(t, current.ConversationID)
			corrupt.Store(true)
			publicFailure(t, b.post(current.ConversationID, agentMessage(2, 1)), 502, "agent_invalid_response", false)
			after := b.history(t, current.ConversationID)
			if !reflect.DeepEqual(before.Conversation, after.Conversation) || !reflect.DeepEqual(before.Turns, after.Turns) || after.PendingRequest == nil || after.PendingRequest.State != "failed" {
				t.Fatal("invalid result changed a committed public turn or revision")
			}
			corrupt.Store(false)
			var last ac.PublicSubmissionResponse
			decodePublic(t, b.post(current.ConversationID, agentMessage(3, 1)), "PublicSubmissionResponse", &last)
			posts, gets := a.calls()
			if len(posts) != 3 || len(gets) != 0 || !reflect.DeepEqual(posts[1].Run.ConversationState, posts[2].Run.ConversationState) || last.Conversation.Revision != 2 {
				t.Fatal("invalid result changed hidden memory/cache or triggered another inference")
			}
		})
	}
}

func TestM2B4PublicFourLanguageStateAndReplay(t *testing.T) {
	type step struct {
		question   string
		override   *ac.LanguagePreference
		decision   ac.LanguageDecision
		answer     ac.Language
		preference ac.LanguagePreference
		automatic  ac.Language
	}
	pref := func(s ac.LanguagePreference) *ac.LanguagePreference { return &s }
	instruction := func(language ac.LanguagePreference, fragment, purpose, persistence string) *ac.LanguageInstruction {
		return &ac.LanguageInstruction{Preference: language, OriginalFragment: fragment, Purpose: purpose, Persistence: persistence}
	}
	steps := []step{
		{question: "Hello, I enjoy art.", decision: ac.LanguageDecision{DetectedLanguage: "en", AnswerLanguage: "en"}, answer: "en", preference: "auto", automatic: "en"},
		{question: "Me gusta visitar museos.", decision: ac.LanguageDecision{DetectedLanguage: "es", AnswerLanguage: "es"}, answer: "es", preference: "auto", automatic: "es"},
		{question: "我喜欢参观博物馆。", decision: ac.LanguageDecision{DetectedLanguage: "zh-CN", AnswerLanguage: "zh-CN"}, answer: "zh-CN", preference: "auto", automatic: "zh-CN"},
		{question: "미술관 방문을 좋아해요.", decision: ac.LanguageDecision{DetectedLanguage: "ko", AnswerLanguage: "ko"}, answer: "ko", preference: "auto", automatic: "ko"},
		{question: "Thanks", override: pref("en"), decision: ac.LanguageDecision{DetectedLanguage: "und", AnswerLanguage: "en"}, answer: "en", preference: "en", automatic: "ko"},
		{question: "For this reply only, use Spanish.", decision: ac.LanguageDecision{DetectedLanguage: "und", ExplicitInstruction: instruction("es", "For this reply only, use Spanish.", "turn_response", "turn"), AnswerLanguage: "es"}, answer: "es", preference: "en", automatic: "ko"},
		{question: "Thanks again", decision: ac.LanguageDecision{DetectedLanguage: "und", AnswerLanguage: "en"}, answer: "en", preference: "en", automatic: "ko"},
		{question: "From now on, use Korean.", decision: ac.LanguageDecision{DetectedLanguage: "und", ExplicitInstruction: instruction("ko", "From now on, use Korean.", "conversation_setting", "conversation"), AnswerLanguage: "ko"}, answer: "ko", preference: "ko", automatic: "ko"},
		{question: "Me gusta aprender.", override: pref("zh-CN"), decision: ac.LanguageDecision{DetectedLanguage: "es", AnswerLanguage: "zh-CN"}, answer: "zh-CN", preference: "zh-CN", automatic: "es"},
		{question: "Translate your last reply into English.", decision: ac.LanguageDecision{DetectedLanguage: "und", AnswerLanguage: "en", Translation: &ac.TranslationTask{TargetLanguage: "en", OriginalFragment: "Translate your last reply into English.", Source: &ac.TranslationSource{Kind: "history_answer", Sequence: ac.Some(int64(9))}}}, answer: "en", preference: "zh-CN", automatic: "es"},
		{question: "Thanks", decision: ac.LanguageDecision{DetectedLanguage: "und", AnswerLanguage: "zh-CN"}, answer: "zh-CN", preference: "zh-CN", automatic: "es"},
		{question: "Thanks", override: pref("auto"), decision: ac.LanguageDecision{DetectedLanguage: "und", AnswerLanguage: "es"}, answer: "es", preference: "auto", automatic: "es"},
	}
	for _, artwork := range []string{"aic-28560", "aic-11723"} {
		t.Run(artwork, func(t *testing.T) {
			a := newPrivateHTTP(t, func(w http.ResponseWriter, r *http.Request, run ac.RunRequest) {
				item := steps[run.ConversationState.Revision]
				snapshot := dialogueSnapshot(run)
				snapshot.Result.LanguageDecision = item.decision
				snapshot.Result.AnswerLanguage = item.answer
				writePrivate(w, r, 200, snapshot)
			})
			b := newReliabilityStack(t, a)
			current := b.open(t, artwork)
			var first ac.PublicSubmissionResponse
			for i, item := range steps {
				message := agentMessage(i+1, int64(i))
				message.Question = item.question
				message.LanguageOverride = item.override
				var result ac.PublicSubmissionResponse
				decodePublic(t, b.post(current.ConversationID, message), "PublicSubmissionResponse", &result)
				if i == 0 {
					first = result
				}
				if result.Conversation.Revision != int64(i+1) || result.Conversation.LanguagePreference != item.preference || result.Conversation.AutoLanguage != item.automatic || result.Conversation.LastAnswerLanguage == nil || *result.Conversation.LastAnswerLanguage != item.answer || result.Turn.AnswerLanguage != item.answer || !reflect.DeepEqual(result.Turn.LanguageOverride, item.override) {
					t.Fatalf("incorrect saved language state at turn %d", i+1)
				}
				history := b.history(t, current.ConversationID)
				restored := b.open(t, artwork)
				if history.Conversation.Revision != int64(i+1) || restored.ConversationID != current.ConversationID || !reflect.DeepEqual(history.Conversation, result.Conversation) {
					t.Fatal("refresh lost language state")
				}
			}
			posts, gets := a.calls()
			if len(posts) != len(steps) || len(gets) != 0 {
				t.Fatal("language changes performed unexpected transport")
			}
			for i, call := range posts {
				if call.Run.Scope.ArtworkID != artwork || len(call.Run.Context.RecentTurns) != min(i, 5) {
					t.Fatal("language journey lost artwork or five-pair context")
				}
				for j, turn := range call.Run.Context.RecentTurns {
					if turn.Sequence != int64(max(0, i-5)+j+1) {
						t.Fatal("context does not contain latest five committed pairs")
					}
				}
			}
			original := agentMessage(1, 0)
			original.Question = steps[0].question
			var replay ac.PublicSubmissionResponse
			decodePublic(t, b.post(current.ConversationID, original), "PublicSubmissionResponse", &replay)
			if !reflect.DeepEqual(replay.Turn, first.Turn) || replay.Conversation.Revision != int64(len(steps)) || replay.Conversation.AutoLanguage != "es" {
				t.Fatal("old completed replay rolled back current conversation projection")
			}
			stale := agentMessage(99, 0)
			publicFailure(t, b.post(current.ConversationID, stale), 409, "conversation_state_conflict", false)
			posts, _ = a.calls()
			if len(posts) != len(steps) {
				t.Fatal("replay or stale draft reran inference")
			}
		})
	}
}

func TestM2B4PublicStatusReconciliation(t *testing.T) {
	for _, outcome := range []string{"missing", "retained-failure", "invalid-completed"} {
		t.Run(outcome, func(t *testing.T) {
			var queries atomic.Int64
			a := newPrivateHTTP(t, func(w http.ResponseWriter, r *http.Request, run ac.RunRequest) {
				if r.Method == "POST" {
					writePrivateError(w, r, run, 409, "agent_run_in_progress", "admission", true)
					return
				}
				count := queries.Add(1)
				if outcome == "missing" {
					writePrivateError(w, r, run, 404, "agent_run_not_found", "admission", true)
					return
				}
				if outcome == "invalid-completed" {
					result := dialogueSnapshot(run)
					result.Result.ConversationUpdate.BaseRevision++
					writePrivate(w, r, 200, result)
					return
				}
				if count == 1 {
					writePrivate(w, r, 200, ac.RunSnapshot{ContractVersion: ac.PrivateVersion, RunID: run.RunID, State: "processing"})
					return
				}
				if count == 2 {
					writePrivateError(w, r, run, 503, "service_not_ready", "admission", true)
					return
				}
				writePrivate(w, r, 200, ac.RunSnapshot{ContractVersion: ac.PrivateVersion, RunID: run.RunID, State: "failed", Error: &ac.AgentError{Code: "generation_timeout", Message: "PRIVATE retained failure", Retryable: true, Phase: "assessment"}})
			})
			b := newReliabilityStack(t, a)
			current := b.open(t, "aic-28560")
			input := agentMessage(1, 0)
			reply := b.post(current.ConversationID, input)
			switch outcome {
			case "missing":
				publicFailure(t, reply, 503, "agent_unavailable", true)
			case "retained-failure":
				publicFailure(t, reply, 504, "generation_timeout", true)
			default:
				publicFailure(t, reply, 502, "agent_invalid_response", false)
			}
			posts, gets := a.calls()
			want := 1
			if outcome == "retained-failure" {
				want = 3
			}
			if len(posts) != 1 || len(gets) != want {
				t.Fatal("reconciliation resubmitted or queried after a terminal lookup")
			}
			previous := posts[0].At
			for _, get := range gets {
				if get.Run.RunID != posts[0].Run.RunID || get.Trace != posts[0].Trace || get.At.Sub(previous) < 900*time.Millisecond {
					t.Fatal("status recovery changed identity or reset its poll interval")
				}
				previous = get.At
			}
			history := b.history(t, current.ConversationID)
			if history.Conversation.Revision != 0 || len(history.Turns) != 0 || history.PendingRequest == nil || history.PendingRequest.State != "failed" {
				t.Fatal("unsuccessful reconciliation committed a result")
			}
		})
	}
}

func TestM2B4PublicPendingCacheAndFailureAtomicity(t *testing.T) {
	material := materialResultFactory(t)
	record, found := testDataset(t).EvidenceByChunkID("aic-28560-description-01")
	if !found {
		t.Fatal("missing uncited test candidate")
	}
	data, _ := json.Marshal(record)
	var uncited ac.Evidence
	if json.Unmarshal(data, &uncited) != nil {
		t.Fatal("invalid uncited candidate")
	}
	var reject atomic.Bool
	a := newPrivateHTTP(t, func(w http.ResponseWriter, r *http.Request, run ac.RunRequest) {
		result := dialogueSnapshot(run)
		switch run.ConversationState.Revision {
		case 0:
			result = material(run)
			result.Result.EvidenceSnapshot = append(result.Result.EvidenceSnapshot, uncited)
			result.Result.Requests[0].OriginalFragment = "What material is it made of?"
			result.Result.Requests = append(result.Result.Requests, ac.InterpretedRequest{ID: "r2", OriginalFragment: "What is that small object?", Intent: "Identify the object the visitor means.", Kind: "artwork_fact", Resolution: "needs_clarification"})
			result.Result.RequestResults = append(result.Result.RequestResults, ac.RequestResult{RequestID: "r2", Support: "clarification_needed", Text: "Which small object do you mean?", CitationIDs: []string{}})
			result.Result.Answer.Status = "partially_answered"
			result.Result.Answer.Text += "\n\nWhich small object do you mean?"
			id := ac.RequestID("r2")
			result.Result.ConversationUpdate.Memory = ac.MemoryUpdate{ContinuitySummary: "The visitor has one unresolved reference.", PendingUpdate: ac.PendingUpdate{Action: "set", Source: ac.Some("current_message"), RequestID: ac.Some(&id)}}
		case 2:
			result.Result.ConversationUpdate.Memory.PendingUpdate = ac.PendingUpdate{Action: "clear"}
		case 3:
			result = material(run)
		case 4:
			fact := run.Context.CatalogFacts[0]
			encoded, _ := json.Marshal(fact)
			var citation ac.Citation
			_ = json.Unmarshal(encoded, &citation)
			result.Result.Requests[0].Kind = "catalog_fact"
			result.Result.RequestResults[0].Support = "supported"
			result.Result.RequestResults[0].Text = "Catalog title: " + fact.Value
			result.Result.RequestResults[0].CitationIDs = []string{fact.FactID}
			result.Result.Answer = ac.AgentAnswer{Status: "answered", Text: result.Result.RequestResults[0].Text, Citations: []ac.Citation{citation}}
		}
		if reject.Load() {
			result.Result.ConversationUpdate.BaseRevision++
		}
		writePrivate(w, r, 200, result)
	})
	b := newReliabilityStack(t, a)
	current := b.open(t, "aic-28560")
	input := agentMessage(1, 0)
	input.Question = "What material is it made of? What is that small object?"
	var first ac.PublicSubmissionResponse
	decodePublic(t, b.post(current.ConversationID, input), "PublicSubmissionResponse", &first)
	if first.Turn.Answer.Status != "partially_answered" || len(first.Turn.Answer.Citations) != 1 {
		t.Fatal("partial public answer lost supported citation")
	}
	thanks := agentMessage(2, 1)
	thanks.Question = "Thanks"
	var second ac.PublicSubmissionResponse
	decodePublic(t, b.post(current.ConversationID, thanks), "PublicSubmissionResponse", &second)
	posts, _ := a.calls()
	pending := posts[1].Run.ConversationState.PendingClarification
	if pending == nil || pending.OriginalFragment == nil || *pending.OriginalFragment != "What is that small object?" || pending.Question != "Which small object do you mean?" || len(posts[1].Run.ConversationState.VerifiedEvidence) != 1 {
		t.Fatal("public commit did not save focused pending/cache together")
	}
	reject.Store(true)
	bad := agentMessage(99, 2)
	language := ac.LanguagePreference("ko")
	bad.LanguageOverride = &language
	publicFailure(t, b.post(current.ConversationID, bad), 502, "agent_invalid_response", false)
	reject.Store(false)
	abandon := agentMessage(3, 2)
	abandon.Question = "Let's leave that detail aside."
	var third ac.PublicSubmissionResponse
	decodePublic(t, b.post(current.ConversationID, abandon), "PublicSubmissionResponse", &third)
	posts, _ = a.calls()
	if !reflect.DeepEqual(posts[2].Run.ConversationState, posts[3].Run.ConversationState) || posts[3].Run.ConversationState.PendingClarification == nil {
		t.Fatal("invalid update cleared pending, changed language or poisoned the source cache")
	}
	var fourth ac.PublicSubmissionResponse
	decodePublic(t, b.post(current.ConversationID, agentMessage(4, 3)), "PublicSubmissionResponse", &fourth)
	posts, _ = a.calls()
	if posts[4].Run.ConversationState.PendingClarification != nil || len(posts[4].Run.ConversationState.VerifiedEvidence) != 1 || fourth.Conversation.Revision != 4 || fourth.Turn.Answer.Status != "answered" {
		t.Fatal("pending clear or verified cache reuse was lost")
	}
	var catalog ac.PublicSubmissionResponse
	decodePublic(t, b.post(current.ConversationID, agentMessage(5, 4)), "PublicSubmissionResponse", &catalog)
	var last ac.PublicSubmissionResponse
	decodePublic(t, b.post(current.ConversationID, agentMessage(6, 5)), "PublicSubmissionResponse", &last)
	posts, _ = a.calls()
	if len(posts[6].Run.ConversationState.VerifiedEvidence) != 1 || posts[6].Run.ConversationState.VerifiedEvidence[0].ChunkID != "aic-28560-medium" || !catalog.Turn.Answer.Citations[0].Kind.Present {
		t.Fatal("uncited retrieval or catalog records entered the verified retrieval cache")
	}
	// A superseded failed key must be rejected on its stale original revision.
	publicFailure(t, b.post(current.ConversationID, bad), 409, "conversation_state_conflict", false)
	history := b.history(t, current.ConversationID)
	posts, gets := a.calls()
	if history.Conversation.Revision != 6 || len(history.Turns) != 6 || history.PendingRequest != nil || len(posts) != 7 || len(gets) != 0 {
		t.Fatal("failed attempt or stale retry entered completed history")
	}
}

func TestM2B4PublicCapacityDoesNotQueueOrMutateRejectedConversation(t *testing.T) {
	started, release := make(chan struct{}, 2), make(chan struct{})
	var active, executions atomic.Int64
	a := newPrivateHTTP(t, func(w http.ResponseWriter, r *http.Request, run ac.RunRequest) {
		if active.Add(1) > 2 {
			active.Add(-1)
			writePrivateError(w, r, run, 503, "agent_busy", "capacity", true)
			return
		}
		defer active.Add(-1)
		executions.Add(1)
		started <- struct{}{}
		select {
		case <-release:
		case <-r.Context().Done():
			return
		}
		writePrivate(w, r, 200, dialogueSnapshot(run))
	})
	b := newReliabilityStack(t, a)
	one, two := b.open(t, "aic-28560"), b.open(t, "aic-11723")
	jar, _ := cookiejar.New(nil)
	other := &publicBrowser{url: b.url, client: &http.Client{Jar: jar, Transport: b.client.Transport, Timeout: 70 * time.Second}}
	three := other.open(t, "aic-28560")
	releaseOnCleanup(t, release)
	finished := make(chan publicReply, 2)
	go func() { finished <- b.post(one.ConversationID, agentMessage(1, 0)) }()
	go func() { finished <- b.post(two.ConversationID, agentMessage(2, 0)) }()
	waitSignal(t, started)
	waitSignal(t, started)
	input := agentMessage(3, 0)
	publicFailure(t, other.post(three.ConversationID, input), 503, "agent_unavailable", true)
	history := other.history(t, three.ConversationID)
	if history.Conversation.Revision != 0 || len(history.Turns) != 0 || history.PendingRequest == nil || history.PendingRequest.State != "failed" || executions.Load() != 2 {
		t.Fatal("capacity rejection queued work or changed public conversation state")
	}
	close(release)
	for range 2 {
		var result ac.PublicSubmissionResponse
		decodePublic(t, <-finished, "PublicSubmissionResponse", &result)
		if result.Conversation.Revision != 1 {
			t.Fatal("accepted capacity slot failed")
		}
	}
	var result ac.PublicSubmissionResponse
	decodePublic(t, other.post(three.ConversationID, input), "PublicSubmissionResponse", &result)
	posts, gets := a.calls()
	if len(posts) != 4 || len(gets) != 0 || executions.Load() != 3 || result.Conversation.Revision != 1 {
		t.Fatal("capacity recovery repeated or lost the deliberate retry")
	}
}
