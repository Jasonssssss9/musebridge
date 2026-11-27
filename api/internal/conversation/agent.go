package conversation

import (
	"encoding/json"
	"strings"
	"time"

	ac "github.com/Jasonssssss9/musebridge/api/internal/agentcontract"
	"github.com/Jasonssssss9/musebridge/api/internal/catalog"
)

const AgentAttemptBudget = 60 * time.Second
const AgentRunBudget = 55 * time.Second

type agentRecord struct {
	stateContract string
	// mode and packVersion are server-owned. v0.3 conversations leave both
	// empty (guide). v0.4 sets "guide" or "vincent"; only Vincent has a pack.
	mode, packVersion string
	// trusted resolves released gallery evidence for v3 result validation.
	trusted  func(chunkID string) (catalog.Evidence, bool)
	catalog  agentCatalog
	state    ac.ConversationState
	turns    []ac.PublicTurn
	requests map[ac.UUID]*agentRequest
	pending  ac.UUID
}

type agentRequest struct {
	submission ac.PublicMessageRequest
	state      string
	run        ac.RunRequest
	deadline   time.Time
	lastError  ac.PublicError
	turn       ac.PublicTurn
}

// AgentAttempt exposes detached transport inputs. Commit uses the protected run
// in the store, not caller-editable request fields or the exported deadline.
type AgentAttempt struct {
	Request          ac.RunRequest
	Deadline         time.Time
	conversationID   string
	messageID, runID ac.UUID
}

// OpenAgent shares anonymous identity with fixture mode but binds a separate
// conversation to the public contract and immutable released catalog view.
func (s *Store) OpenAgent(token string, dataset *catalog.Dataset, artworkID string) (string, ac.PublicConversation, bool, *Failure) {
	return s.OpenAgentMode(token, dataset, artworkID, ac.PublicVersion, "")
}

// OpenAgentMode creates or restores the conversation keyed by visitor, public
// contract, artwork, release, mode and role pack. publicVersion must be a
// supported contract; v0.3 takes an empty mode. The caller checks that the
// mode is offered for this artwork before calling.
func (s *Store) OpenAgentMode(token string, dataset *catalog.Dataset, artworkID, publicVersion, mode string) (string, ac.PublicConversation, bool, *Failure) {
	validator, err := ac.ValidatorFor(publicVersion)
	if err != nil {
		return token, ac.PublicConversation{}, false, agentInternal()
	}
	modeAware := validator.ModeAware()
	if modeAware != (mode != "") || (modeAware && mode != ac.ModeGuide && mode != ac.ModeVincent) {
		return token, ac.PublicConversation{}, false, agentInternal()
	}
	projection, failure := projectAgentCatalog(dataset, artworkID, validator)
	if failure != nil {
		return token, ac.PublicConversation{}, false, failure
	}
	packVersion := ""
	if mode == ac.ModeVincent {
		if projection.artworkID != ac.VincentArtworkID || projection.version != ac.VincentDatasetVersion {
			return token, ac.PublicConversation{}, false, conflict("conversation_mode_unavailable", "Talk with Vincent is not available for this artwork.", false)
		}
		packVersion = ac.VincentPackVersion
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	now := s.now().UTC()
	s.expire(now)
	token, visitor := s.identity(token, now)
	key := scope{artworkID: projection.artworkID, datasetVersion: projection.version, contractVersion: publicVersion, mode: mode, packVersion: packVersion}
	if id, exists := visitor.conversations[key]; exists {
		return token, agentConversation(s.conversations[id]), false, nil
	}
	id := newUUID()
	preference := ac.LanguagePreference("auto")
	if mode == ac.ModeVincent {
		preference = "en" // Vincent speaks English (docs/vincent-contract.md)
	}
	item := &record{owner: token, conversation: Conversation{
		ConversationID: id, ArtworkID: projection.artworkID, DatasetVersion: projection.version,
		CreatedAt: now, ExpiresAt: now.Add(Lifetime),
	}, agent: &agentRecord{
		stateContract: publicVersion, mode: mode, packVersion: packVersion, trusted: dataset.EvidenceByChunkID,
		catalog: projection, turns: []ac.PublicTurn{}, requests: make(map[ac.UUID]*agentRequest),
		state: ac.ConversationState{StateVersion: validator.StateVersion(), ConversationID: ac.UUID(id),
			Scope:              ac.ScopeFor(publicVersion, mode, projection.artworkID, projection.version),
			LanguagePreference: preference, AutoLanguage: "en", VerifiedEvidence: []ac.Evidence{}},
	}}
	if err := validator.CheckState(item.agent.state); err != nil {
		return token, ac.PublicConversation{}, false, agentInternal()
	}
	s.conversations[id] = item
	visitor.conversations[key] = id
	return token, agentConversation(item), true, nil
}

func agentConversation(item *record) ac.PublicConversation {
	state := item.agent.state
	conversation := ac.PublicConversation{
		ContractVersion: item.agent.stateContract, ConversationID: state.ConversationID,
		ArtworkID: state.Scope.ArtworkID, DatasetVersion: state.Scope.DatasetVersion,
		CreatedAt: item.conversation.CreatedAt.Format(time.RFC3339Nano), ExpiresAt: item.conversation.ExpiresAt.Format(time.RFC3339Nano),
		Revision: state.Revision, LanguagePreference: state.LanguagePreference, AutoLanguage: state.AutoLanguage,
		LastAnswerLanguage: cloneWire(state.LastAnswerLanguage),
	}
	if state.Scope.ConversationMode.Present {
		// Every v0.4 projection names its server-owned mode and pack.
		conversation.ConversationMode = state.Scope.ConversationMode
		conversation.ReferencePackVersion = cloneWire(state.Scope.ReferencePackVersion)
	}
	return conversation
}

// validatorOf returns the validator of the conversation's public contract.
func validatorOf(item *record) (*ac.Validator, error) {
	return ac.ValidatorFor(item.agent.stateContract)
}

// AgentHistory is the public projection; semantic state never enters this reply.
func (s *Store) AgentHistory(token, id string) (ac.PublicHistoryResponse, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.expire(s.now().UTC())
	item := s.ownedAgent(token, id)
	if item == nil {
		return ac.PublicHistoryResponse{}, false
	}
	s.expireAgentAttempt(item)
	history := ac.PublicHistoryResponse{Conversation: agentConversation(item), Turns: cloneWire(item.agent.turns)}
	if current := item.agent.requests[item.agent.pending]; current != nil {
		history.PendingRequest = &ac.PendingRequest{
			ContractVersion: current.submission.ContractVersion, ClientMessageID: current.submission.ClientMessageID,
			Question: current.submission.Question, LanguageOverride: cloneWire(current.submission.LanguageOverride),
			ExpectedRevision: current.submission.ExpectedRevision, State: current.state,
		}
		if current.state == "failed" {
			failure := current.lastError
			history.PendingRequest.LastError = &failure
		}
	}
	return history, true
}

// VoiceConversation reports the mode and fixed expiry of a live Agent
// conversation owned by token, for the Vincent media routes. Fixture
// conversations are never voice targets.
func (s *Store) VoiceConversation(token, id string) (string, time.Time, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.expire(s.now().UTC())
	item := s.ownedAgent(token, id)
	if item == nil {
		return "", time.Time{}, false
	}
	mode := item.agent.mode
	if mode == "" {
		mode = "guide"
	}
	return mode, item.conversation.ExpiresAt, true
}

// CommittedAnswer returns the saved answer.text of a completed turn in an owned
// Agent conversation. Pending or failed submissions have no turn and are absent.
func (s *Store) CommittedAnswer(token, id, turnID string) (string, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.expire(s.now().UTC())
	item := s.ownedAgent(token, id)
	if item == nil {
		return "", false
	}
	for _, turn := range item.agent.turns {
		if string(turn.TurnID) == turnID {
			return turn.Answer.Text, true
		}
	}
	return "", false
}

func (s *Store) ownedAgent(token, id string) *record {
	item := s.conversations[id]
	if item == nil || item.agent == nil || s.visitors[token] == nil || item.owner != token {
		return nil
	}
	return item
}

// BeginAgent follows the ordered v2 idempotency/revision rules. It freezes the
// exact five-pair history, state, catalog, language choice and attempt identity.
func (s *Store) BeginAgent(token, id string, submission ac.PublicMessageRequest) (AgentAttempt, *ac.PublicSubmissionResponse, *Failure) {
	submission = cloneWire(submission)
	s.mu.Lock()
	defer s.mu.Unlock()
	s.expire(s.now().UTC())
	item := s.ownedAgent(token, id)
	if item == nil {
		return AgentAttempt{}, nil, notFound()
	}
	validator, err := validatorOf(item)
	if err != nil {
		return AgentAttempt{}, nil, agentInternal()
	}
	shape := submission
	shape.ContractVersion = validator.PublicVersion()
	if _, err := validator.Encode("PublicMessageRequest", shape); err != nil || submission.Question != strings.TrimSpace(submission.Question) {
		return AgentAttempt{}, nil, &Failure{400, Error{"invalid_request", "The request is invalid.", false}}
	}
	if submission.ContractVersion != item.agent.stateContract {
		return AgentAttempt{}, nil, conflict("agent_contract_mismatch", "The client and conversation contracts do not match.", false)
	}
	if item.agent.mode == ac.ModeVincent && submission.LanguageOverride != nil && *submission.LanguageOverride != "en" {
		return AgentAttempt{}, nil, &Failure{400, Error{"voice_language_unsupported", "Talk with Vincent is available in English only. Use the artwork guide for other languages.", false}}
	}
	s.expireAgentAttempt(item)
	agent := item.agent
	previous := agent.requests[submission.ClientMessageID]
	if previous != nil {
		if !sameAgentSubmission(previous.submission, submission) {
			return AgentAttempt{}, nil, conflict("idempotency_conflict", "This message ID belongs to a different submission.", false)
		}
		if previous.state == "completed" {
			replay := ac.PublicSubmissionResponse{Conversation: agentConversation(item), Turn: cloneWire(previous.turn)}
			return AgentAttempt{}, &replay, nil
		}
		if previous.state == "processing" {
			return AgentAttempt{}, nil, conflict("request_in_progress", "This submission is still processing.", true)
		}
	}
	if submission.ExpectedRevision != agent.state.Revision {
		return AgentAttempt{}, nil, stateConflict()
	}
	if previous != nil && previous.state == "superseded" {
		return AgentAttempt{}, nil, conflict("request_superseded", "This submission was replaced by a newer question.", false)
	}
	if current := agent.requests[agent.pending]; current != nil && current.state == "processing" {
		return AgentAttempt{}, nil, conflict("conversation_busy", "Another submission is processing in this conversation.", true)
	}
	if agent.state.Revision == 2147483647 {
		return AgentAttempt{}, nil, stateConflict()
	}
	admitted := s.steadyNow()
	run := ac.RunRequest{
		ContractVersion: validator.PrivateVersion(), RunID: ac.UUID(newUUID()), ConversationID: ac.UUID(id),
		ClientMessageID: submission.ClientMessageID, Question: submission.Question, LanguageOverride: cloneWire(submission.LanguageOverride),
		Scope: agent.state.Scope, ConversationState: cloneWire(agent.state),
		Context:   ac.Context{ArtworkTitle: agent.catalog.title, Artist: agent.catalog.artist, CatalogFacts: cloneWire(agent.catalog.facts), RecentTurns: []ac.HistoryTurn{}},
		Execution: ac.RunExecution{WorkflowID: validator.WorkflowID(), ProfileID: ac.ProfileID, DeadlineAt: admitted.UTC().Add(AgentRunBudget).Format(time.RFC3339Nano)},
	}
	for _, turn := range agent.turns[max(0, len(agent.turns)-5):] {
		run.Context.RecentTurns = append(run.Context.RecentTurns, ac.HistoryTurn{TurnID: turn.TurnID, Sequence: turn.Sequence,
			Question: turn.Question, AnswerLanguage: turn.AnswerLanguage, AnswerStatus: ac.AgentStatus(turn.Answer.Status), AnswerMode: "llm", AnswerText: turn.Answer.Text})
	}
	if err := validator.CheckRunRequest(run); err != nil {
		return AgentAttempt{}, nil, agentInternal()
	}
	current := &agentRequest{submission: submission, state: "processing", run: run, deadline: admitted.Add(AgentAttemptBudget)}
	if old := agent.requests[agent.pending]; old != nil && old != previous {
		old.state = "superseded"
	}
	agent.requests[submission.ClientMessageID] = current
	agent.pending = submission.ClientMessageID
	return AgentAttempt{Request: cloneWire(run), Deadline: current.deadline, conversationID: id, messageID: submission.ClientMessageID, runID: run.RunID}, nil, nil
}

func stateConflict() *Failure {
	return conflict("conversation_state_conflict", "The conversation state changed. Refresh and review the message before submitting again.", false)
}

func (s *Store) expireAgentAttempt(item *record) {
	if current := item.agent.requests[item.agent.pending]; current != nil && current.state == "processing" && !s.steadyNow().Before(current.deadline) {
		current.state = "failed"
		current.lastError = publicAgentError(RequestTimeout())
	}
}

func publicAgentError(failure *Failure) ac.PublicError {
	return ac.PublicError{Code: failure.Code, Message: failure.Message, Retryable: failure.Retryable}
}

// activeAgent runs only under the store lock. Store-owned fields are the commit
// guards; public attempt snapshots cannot extend a deadline or change the run.
func (s *Store) activeAgent(attempt AgentAttempt) (*record, *agentRequest, *Failure) {
	s.expire(s.now().UTC())
	item := s.conversations[attempt.conversationID]
	if item == nil || item.agent == nil {
		return nil, nil, notFound()
	}
	current := item.agent.requests[attempt.messageID]
	if current == nil || current.state != "processing" || current.run.RunID != attempt.runID {
		return nil, nil, conflict("request_superseded", "This attempt is no longer active.", false)
	}
	if !s.steadyNow().Before(current.deadline) {
		failure := RequestTimeout()
		current.state = "failed"
		current.lastError = publicAgentError(failure)
		return nil, nil, failure
	}
	if current.run.ConversationState.Revision != item.agent.state.Revision {
		failure := stateConflict()
		current.state, current.lastError = "failed", publicAgentError(failure)
		return nil, nil, failure
	}
	return item, current, nil
}

// FailAgent preserves the original retry payload without changing turns/memory.
func (s *Store) FailAgent(attempt AgentAttempt, failure *Failure) *Failure {
	s.mu.Lock()
	defer s.mu.Unlock()
	_, current, inactive := s.activeAgent(attempt)
	if inactive != nil {
		return inactive
	}
	if failure == nil {
		failure = agentInternal()
	}
	validator, err := validatorOf(s.conversations[attempt.conversationID])
	if err != nil {
		failure = agentInternal()
	} else if _, err := validator.Encode("PublicError", publicAgentError(failure)); err != nil {
		failure = agentInternal()
	}
	if _, _, inactive := s.activeAgent(attempt); inactive != nil {
		return inactive
	}
	current.state = "failed"
	current.lastError = publicAgentError(failure)
	return &Failure{failure.Status, failure.Error}
}

// CompleteAgent validates and derives into local values before publishing any
// state. Turn, memory, replay and pending completion share one locked commit.
func (s *Store) CompleteAgent(attempt AgentAttempt, result ac.RunResult) (ac.PublicSubmissionResponse, *Failure) {
	result = cloneWire(result)
	s.mu.Lock()
	defer s.mu.Unlock()
	item, current, failure := s.activeAgent(attempt)
	if failure != nil {
		return ac.PublicSubmissionResponse{}, failure
	}
	fail := func(failure *Failure) (ac.PublicSubmissionResponse, *Failure) {
		if _, _, inactive := s.activeAgent(attempt); inactive != nil {
			return ac.PublicSubmissionResponse{}, inactive
		}
		current.state = "failed"
		current.lastError = publicAgentError(failure)
		return ac.PublicSubmissionResponse{}, failure
	}
	validator, err := validatorOf(item)
	if err != nil {
		return fail(agentInternal())
	}
	if validator.ModeAware() && !fromRelease(validator, item.agent, result) {
		return fail(invalidAgentResult())
	}
	next, err := nextAgentState(validator, current.run, result)
	if err != nil {
		return fail(invalidAgentResult())
	}
	turn := ac.PublicTurn{TurnID: ac.UUID(newUUID()), Sequence: next.Revision,
		ClientMessageID: current.submission.ClientMessageID, Question: current.submission.Question,
		AnswerLanguage: result.AnswerLanguage, LanguageOverride: cloneWire(current.submission.LanguageOverride),
		DatasetVersion: item.conversation.DatasetVersion, CreatedAt: s.now().UTC().Format(time.RFC3339Nano),
		Answer: ac.PublicAnswer{Mode: "llm", Status: result.Answer.Status, Text: result.Answer.Text, Citations: cloneWire(result.Answer.Citations)},
	}
	if _, err := validator.Encode("PublicTurn", turn); err != nil {
		return fail(invalidAgentResult())
	}
	// Validation consumes budget too. Recheck TTL, active revision and monotonic
	// time immediately before the only assignments to authoritative state.
	if _, _, inactive := s.activeAgent(attempt); inactive != nil {
		return ac.PublicSubmissionResponse{}, inactive
	}
	item.agent.state = next
	item.agent.turns = append(item.agent.turns, turn)
	current.state, current.turn = "completed", turn
	item.agent.pending = ""
	return ac.PublicSubmissionResponse{Conversation: agentConversation(item), Turn: cloneWire(turn)}, nil
}

// fromRelease checks every v3 evidence record against the pinned release:
// the exact Vincent pack record in Vincent mode, otherwise the exact released
// gallery chunk of this artwork. A changed, foreign or invented source fails
// the turn without a new revision.
func fromRelease(validator *ac.Validator, agent *agentRecord, result ac.RunResult) bool {
	if agent.trusted == nil {
		return false
	}
	for _, evidence := range result.EvidenceSnapshot {
		if record, inPack := validator.ReferenceRecord(evidence.ChunkID); inPack {
			if agent.mode != ac.ModeVincent || !sameWire(record, evidence) {
				return false
			}
			continue
		}
		trusted, released := agent.trusted(evidence.ChunkID)
		if !released || trusted.ArtworkID != agent.catalog.artworkID {
			return false
		}
		expected := ac.Evidence{ChunkID: trusted.ChunkID, ArtworkID: trusted.ArtworkID, SourceID: trusted.SourceID,
			SourceTitle: trusted.SourceTitle, SourceURL: trusted.SourceURL, SourceLanguage: trusted.SourceLanguage,
			SourceLocator: trusted.SourceLocator, Excerpt: trusted.Excerpt, License: trusted.License,
			LicenseURL: trusted.LicenseURL, Attribution: trusted.Attribution, Normalization: trusted.Normalization}
		if trusted.SourceKind != "" {
			expected.SourceKind = ac.Some(trusted.SourceKind)
		}
		if !sameWire(expected, evidence) {
			return false
		}
	}
	return true
}

func sameWire(left, right any) bool {
	a, errA := json.Marshal(left)
	b, errB := json.Marshal(right)
	return errA == nil && errB == nil && string(a) == string(b)
}
