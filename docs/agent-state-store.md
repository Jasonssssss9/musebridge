# Go Agent conversation state store

B integration phase 2 is implemented as an internal Go store. It provides v2
conversation creation/recovery, request admission, frozen run inputs, terminal
state derivation, atomic commits and replay. Public HTTP routing and the Agent
client are later integration work; the runnable application still uses fixture
generation. No model or private Agent HTTP service starts in this phase.

The [v2 interface contract](agent-api-contract.md) defines the behavior. The
[phase-1 boundary layer](contracts/README.md) supplies types and validation.

## Identity and lifetime

The existing `conversation.Store` owns both strategies under one mutex. They
share anonymous visitor identity, while conversation keys include artwork,
release and public contract. Opening fixture and Agent conversations for one
artwork yields different IDs. Legacy methods reject Agent IDs and Agent methods
reject fixture IDs.

An Agent conversation starts at revision 0, Auto preference, English automatic
fallback, null last language/topic/pending clarification, empty summary and
empty evidence cache. Each has a fixed 24-hour expiry. Only creation/recovery
renews visitor identity; history, submissions and commits do not extend the
conversation's expiry. State and history disappear together on expiry or process
restart. There is no database, migration, reset endpoint or durable recovery.

Catalog context contains only released title, artist, date and their provenance.
`catalog.Dataset.ArtworkOrigin` selects the actual source association. The store
retains a detached three-record projection, not another collection of passages.
Displayed dates keep their exact released ranges.

## Internal API

| Method | Behavior |
| --- | --- |
| `OpenAgent(token, dataset, artworkID)` | Create/restore the owned v2 scope and return its public projection |
| `AgentHistory(token, conversationID)` | Return all completed turns, current public metadata and original pending retry payload |
| `BeginAgent(token, conversationID, submission)` | Reserve a new run or return a completed replay/conflict |
| `CompleteAgent(attempt, result)` | Validate and atomically commit one turn plus derived state |
| `FailAgent(attempt, failure)` | Retain a failed pending submission without changing committed history/memory |

`BeginAgent` expects an already decoded, trimmed public message. It checks the
typed shape defensively. An accepted attempt contains detached `RunRequest` data
and a Go deadline. The store independently retains the original run; modifying
the returned request or deadline cannot change admission identity or commit
guards. The latest `min(5, revision)` complete pairs, catalog, semantic state,
language choice and source cache are fixed for that run.

The Go attempt has 60 seconds and the private Agent deadline is admission plus
55 seconds. Execution uses the store's process monotonic clock independently of
its wall-clock TTL. The later orchestration layer must enforce cancellation;
the store rejects late results and lazily marks an expired pending attempt
failed on history/admission, so a missed callback cannot leave it busy forever.

## Admission and replay ordering

After ownership and static validation:

1. Changed payload under an existing logical key is `idempotency_conflict`.
2. An identical completed key returns its original turn before current-revision
   and busy checks. The accompanying conversation projection is current.
3. An identical processing key returns `request_in_progress`.
4. An uncompleted key with stale expected revision is `conversation_state_conflict`.
5. A superseded failed key with unchanged revision is `request_superseded`.
6. Another active submission produces `conversation_busy`.
7. A new accepted attempt receives a fresh run UUID and immutable base snapshot.

Identity includes contract, exact trimmed wording, nullable language override and
expected revision. A deliberate retry retains that identity but gets a new run
ID. Revision overflow is rejected. A new visitor submission supersedes the old
failed pending key only after all admission checks succeed.

## Atomic completion

The store validates results against its protected run input, derives into local
values, then rechecks reservation, current revision, TTL and monotonic deadline.
The turn, state, completed replay and pending completion publish under the same
lock. Readers cannot observe half an update. Invalid results retain a failed
pending request; technical failures and late/superseded attempts cannot advance
history or memory.

Successful completion:

- Advances sequence/revision exactly once and saves the actual response language.
- Applies validated persistent language settings while keeping temporary replies
  and translations separate from saved preference and Auto fallback.
- Applies proposed topic/summary context without treating it as evidence.
- Keeps, clears or derives one focused pending clarification. A mixed answer
  saves only its unresolved question; repeated clarification preserves the
  original question/quote while updating the focused intent.
- Caches cited retrieval records first, then prior cache, deduplicated and capped
  at eight. Catalog facts and uncited candidates never enter this cache.
- Saves immutable citation snapshots with the turn.

Scope/provenance and deterministic relationship checks do not establish semantic
support or real model quality. The authenticated Agent and its release/evaluation
checks retain those responsibilities.

## Verification

From `api/`, run `go test -race ./...`. New tests exercise both artworks and
contract isolation, exact retry identity, old completed replay, stale failed
keys, supersession, dropped-response recovery via history, five-pair projection,
language effects, focused clarification, cited-only cache ordering and catalog
exclusion. Shared fixtures cover memory transitions, original-quote retention
and first commit. Concurrent reservation/completion/history checks verify one
saved turn and consistent projections. Injected clocks test expiry and the final
post-validation deadline guard without sleeping.

The store also rejects invalid typed UTF-8 before serialization can replace it.
Whole-message scope/clarification results cannot discard independently answerable
parts. Existing fixture, catalog, retrieval and HTTP regressions remain enabled.
