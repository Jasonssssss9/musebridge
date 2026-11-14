# MuseBridge Agent v2 Interface Contract

Status: **Normative implementation target; documentation and contract fixtures only.**

Public contract: `agent-demo-v0.3`. Private contract: `agent-run-v2`. Workflow: `museum_qa_v2`. Schema: `agent-schema-v2`. State format: `conversation-state-v1`. Prompt package: `museum-prompts-v2`. Profile: `local-ollama-v2`.

Read this with [the v2 design](agent-design.md), [JSON Schema](contracts/agent-v2.schema.json) and [synthetic examples](contracts/agent-v2.examples.json). The schema defines structural constraints; the relationship and semantic rules below are equally normative. Examples are synthetic and do not claim observed LLM performance.

The implemented C M1 code still reads the unchanged v1 artifacts. The [fixture API contract](api-contract.md) continues to govern fixture mode. Consumers must explicitly migrate; a deployment must not reinterpret a v1 message as v2.

## 1. Conventions and ownership

| Item | Rule |
| --- | --- |
| Text | UTF-8 JSON; lengths count Unicode code points; no surrogates or control characters except tab, LF and CR |
| Required prose | Nonempty after Unicode whitespace trimming unless explicitly allowed empty; do not rewrite source excerpts |
| Original message | Public field remains `question` for continuity; it accepts statements, greetings and instructions as well as questions |
| Strict input | All required fields present; reject unknown request/model/state fields, duplicate keys, nonfinite numbers, trailing data and type coercion |
| Integers | JSON integers only; booleans, strings and integral floating-point values are not accepted as integer fields |
| IDs | Canonical lowercase UUIDs for run, conversation, turn and logical message; evidence IDs remain case-sensitive strings |
| Timestamps | RFC 3339 UTC with `Z`; sequence/revision determine order |
| Response openness | Extra metadata is allowed only in schema-open response objects; never reinterpret extra model fields as instructions |
| Trace | `X-Request-ID`, 1–128 visible ASCII characters, correlated across dependencies |
| Cache | HTTP responses use `Cache-Control: no-store` |
| Source release | `aic-seed-v0.1`, existing canonical evidence and retrieval strategy |
| JSON nesting | Reject structures deeper than 64 levels; bounded metadata cannot evade validation |

Go owns visitor identity, conversation state, accepted message identity and atomic persistence. The Agent owns semantic interpretation and evidence-based execution. The browser never supplies authoritative artwork scope, history, source snapshots or conversation memory.

Supported response language values are `en`, `es`, `zh-CN` and `ko`. `LanguagePreference` also permits `auto`. `und` and `other` are planner detection values only, never response languages.

## 2. Public application contract

### 2.1 Routes and compatibility

| Route | v2 request / response |
| --- | --- |
| `GET /api/artworks/{slug}` | Existing catalog request and `{artwork: Artwork}` response |
| `POST /api/conversations` | `PublicCreateConversationRequest` -> `{conversation, created}` |
| `GET /api/conversations/{conversation_id}/messages` | `{conversation, turns, pending_request}` |
| `POST /api/conversations/{conversation_id}/messages` | `PublicMessageRequest` -> `{conversation, turn}` |

Public POSTs retain exact Origin validation and the existing `musebridge_visitor` cookie policy. Creation alone establishes/refreshes the opaque HttpOnly session cookie. The browser never receives a service token or calls Agent/model endpoints directly. HTTPS uses Secure cookies; the explicitly configured local HTTP profile follows the baseline exception.

Public POST bodies are at most 8 KiB and uncompressed UTF-8 JSON. Existing methods, media-type validation, unknown-path behavior and source URL restrictions remain. An unsupported well-typed public contract receives `409 agent_contract_mismatch`; malformed fields receive `400 invalid_request`.

A new v2 conversation is selected by visitor, artwork and contract version. It is permanently bound to its artwork, release and contract. Do not silently attach v2 state to a fixture/v1 conversation. Switching versions creates a new conversation; old histories are not automatically imported or reinterpreted.

### 2.2 Conversation creation and projection

`PublicCreateConversationRequest` has exactly:

~~~json
{
  "contract_version": "agent-demo-v0.3",
  "artwork_id": "aic-28560"
}
~~~

`PublicConversation` contains the five baseline fields plus v2 metadata:

| Field | Type and meaning |
| --- | --- |
| `conversation_id` | UUID |
| `artwork_id`, `dataset_version` | Fixed scope IDs |
| `created_at`, `expires_at` | Server timestamps; expiry remains exactly 24 hours after creation |
| `contract_version` | `agent-demo-v0.3` |
| `revision` | Integer 0–2,147,483,647; latest committed turn sequence |
| `language_preference` | `auto` or supported language |
| `auto_language` | Supported language used for automatic fallback; initially `en` |
| `last_answer_language` | Actual latest response language; null only before any committed turn |

The public projection does not expose summaries, pending clarification content or cached evidence as a second client-owned state store. Sources remain available through normal turn citations.

At revision 0, language preference is `auto`, automatic fallback is `en` and last response language is null. Restore does not extend expiry. Missing/expired/unowned conversations retain `404 conversation_not_found`.

### 2.3 Message submission

All five fields below are required:

~~~json
{
  "contract_version": "agent-demo-v0.3",
  "client_message_id": "33333333-3333-4333-8333-333333333333",
  "question": "This painting is beautiful. What is it made of?",
  "language_override": null,
  "expected_revision": 0
}
~~~

| Field | Rule |
| --- | --- |
| `contract_version` | Must match this conversation |
| `client_message_id` | Logical submission UUID; preserve across retries |
| `question` | String; Go trims surrounding Unicode whitespace once, then requires 1–500 code points |
| `language_override` | Null to inherit stored preference; `auto` or a supported language for an explicit UI selection |
| `expected_revision` | Conversation revision on which the visitor composed this submission |

Do not send an explicit fixed language on every request merely to report the last answer's language. With no changed selection, send null. UI changes apply only to new submissions. An input message may itself request a response language; the Agent resolves that according to section 4.

Payload identity is the exact tuple `(contract_version, trimmed question, language_override, expected_revision)`. JSON key order and transport trace IDs are irrelevant; internal whitespace, punctuation, letter case and null versus an explicit language remain significant.

Go validates authentication/ownership and static shape, then checks any existing logical key and its original payload:

1. Same key with a changed payload: `409 idempotency_conflict`, even if the original completed.
2. Same key and identical completed payload: replay the saved turn before checking the current revision.
3. Same key still processing: `409 request_in_progress`; reconcile through history.
4. Any remaining uncompleted submission, including a failed or superseded key, with stale `expected_revision`: `409 conversation_state_conflict`.
5. A superseded failed key with no revision change retains `409 request_superseded`.
6. A different active submission in this conversation receives `409 conversation_busy`.
7. Otherwise reserve the attempt against the current revision and immutable context snapshot.

A stale retry must not quietly acquire the meaning of a newer conversation. After state conflict, load history, review the draft and deliberately submit a new UUID with the new revision. Do not automatically replace it.

### 2.4 Completed turns, history and pending requests

`PublicTurn` retains `turn_id`, `sequence`, `client_message_id`, `question`, `dataset_version`, `created_at` and `answer`. It adds the original nullable `language_override`; `answer_language` now records the actual resolved supported language, not a request preference.

`PublicAnswer` contains `status`, `mode: llm`, `text` and `citations`. Mode is server assigned. Statuses and citation rules are in section 5. Existing fixture answers retain their separate baseline mode/status contract.

A successful submission returns `{conversation, turn}`. On completed replay, `turn` is identical to the stored original, while `conversation` is the current authoritative projection. The browser must not roll back a newer revision or language preference using an older turn. This response does not rerun inference or advance state.

History returns the full completed turn list in sequence order and the current conversation projection. The private Agent context selects only the latest five pairs from that history. Public history is not the model's prompt window.

`PendingRequest` contains `contract_version`, `client_message_id`, `question`, `language_override`, `expected_revision`, `state` and `last_error`. Processing has a null error; failed has a public error. It always preserves the original retry payload. It does not contain an uncommitted assistant answer or memory patch.

Every successful response status, including `conversational`, `insufficient` and clarification, is HTTP 200 and creates one completed turn. Technical errors create none. Source cards appear only when citations exist; factual coverage metadata must not replace the normal response with a canned failure screen.

## 3. Private Agent transport

| Item | Selected contract |
| --- | --- |
| Routes | `POST /internal/v2/agent/runs`; `GET /internal/v2/agent/runs/{run_id}` |
| Body | UTF-8 JSON, `application/json`; optional UTF-8 charset only |
| Limits | Request and response each at most 256 KiB, uncompressed; accommodates five history pairs and bounded memory |
| Authentication | `Authorization: Bearer <AGENT_SERVICE_TOKEN>`, at least 32 random bytes |
| Trace | Exactly one valid `X-Request-ID`; echo on responses |
| Exposure | Private service only; no public browser proxy, visitor cookies or credentialed CORS |
| Redirects | Disabled for clients and automatic trailing-slash handling |
| Unknown input | Reject unknown query parameters, duplicate headers where identity matters, malformed JSON and undocumented fields |
| Methods | 405 with `Allow`; unknown routes 404 |
| Infrastructure | Private `/healthz` and `/readyz` may omit service auth/trace; no visitor access |

Authenticate before run lookup. For invalid/missing trace headers, return a safe generated trace ID rather than echo untrusted text. Disable deployed automatic OpenAPI/docs endpoints. TLS verification is required for HTTPS dependencies. Do not return stack traces or raw provider bodies.

Infrastructure response shapes are also fixed. A live `/healthz` returns `{"status":"ok"}`. Ready returns the following only after its verified profile and four-language probes are recorded; otherwise use 503 service_not_ready. Readiness itself does not execute inference on each probe.

~~~json
{
  "status": "ok",
  "contract_version": "agent-run-v2",
  "workflow_id": "museum_qa_v2",
  "profile_id": "local-ollama-v2",
  "dataset_version": "aic-seed-v0.1",
  "state_version": "conversation-state-v1",
  "supported_languages": ["en", "es", "zh-CN", "ko"],
  "history_turn_limit": 5
}
~~~

### 3.1 RunRequest

| Field | Type and rule |
| --- | --- |
| `contract_version` | `agent-run-v2` |
| `run_id` | UUID for this accepted attempt |
| `conversation_id`, `client_message_id` | Application-owned UUIDs |
| `question` | Original already-trimmed message, 1–500 code points |
| `language_override` | Original public nullable preference |
| `scope` | `{artwork_id, dataset_version}`, nonempty IDs of at most 128 code points |
| `context` | `{artwork_title, artist, recent_turns}` |
| `conversation_state` | Exact immutable snapshot defined in section 4 |
| `execution` | `{workflow_id: museum_qa_v2, profile_id: local-ollama-v2, deadline_at}` |

`artwork_title` and `artist` are nonempty strings of at most 200 code points and must equal the configured manifest metadata. The conversation ID/scope in state must equal the envelope. A supported but unconfigured scope or wrong manifest metadata is `agent_scope_mismatch`.

`recent_turns` must be exactly the latest `min(5, revision)` committed pairs. Their sequences are contiguous and end at `conversation_state.revision`; IDs are unique. Go owns authenticity. The Agent validates these relationships before token fitting, then may drop oldest whole pairs for a model operation.

A `HistoryTurn` contains `turn_id`, positive `sequence`, `question` (1–500), actual `answer_language`, completed `answer_status`, `answer_mode: llm` and `answer_text` (1–2,000). All belong to this v2 conversation. The latest historical answer_language must equal conversation_state.last_answer_language; validate this before token fitting. Do not send pending attempts, fixture-mode turns, older complete turns or another conversation.

Go sets `deadline_at` to admission plus 55 seconds. On new-run admission, the Agent converts remaining wall-clock time to a monotonic deadline, capped at 55 seconds, and rejects expired requests. Clock synchronization is required; Go's final deadline remains the commit guard.

### 3.2 RunResult

| Field | Meaning |
| --- | --- |
| `scope` | Exact input scope |
| `answer_language` | Validated resolved language |
| `language_decision` | The frozen planner language decision |
| `answer` | `AgentAnswer`: status, natural text and full canonical citations; no mode |
| `requests` | Validated interpreted requests, or empty for early clarify/scope |
| `request_results` | One result per request, or empty for any clarification and early scope |
| `evidence_snapshot` | Canonical accepted cache-plus-retrieval union, at most eight |
| `reused_chunk_ids` | IDs from the validated input cache in its order; at most eight unique |
| `executed_queries` | Actual ordered queries, 0–4 |
| `conversation_update` | `{base_revision, memory: MemoryUpdate}` |
| `execution` | Version/model identity, counts and duration |

Early direct responses include their nonfactual requests/results. An assessment clarification retains the plan's requests but has no result parts or citations. Early clarification/scope has no requests/results. The evidence snapshot still records validated cache even if no citation is needed.

`ExecutionRecord` has `workflow_id`, `profile_id`, `prompt_version`, `schema_version`, `retrieval_strategy_version`, `model_provider`, `model_name`, `model_digest`, `model_calls`, `retrieval_calls`, `refinement_rounds` and `duration_ms`. Use the versions at this document's start, `legacy-facet-v0.1` and the actual verified Ollama identity. Successful counts are 1–3 model calls, 0–4 retrieval calls and 0–1 refinement rounds; duration is 0–55,000 ms. Nonempty identity/version strings are at most 200 code points.

Validate query count against executed queries, reused IDs against input cache, scope/release on every source and cited IDs against the accepted union. C must ensure that every new source record originated in this run's accepted tool responses and that the actual route/query batches obey the workflow. Go checks the exposed structural relationships, cache consistency, scope, citations and counts, and trusts the authenticated Agent for fresh-tool provenance and semantic execution. A returned snapshot is not an independent tool-call transcript; Go does not claim to reconstruct or prove that execution.

### 3.3 Run snapshots and replay

`RunSnapshot` contains `contract_version`, `run_id`, `state`, `result` and `error`.

| State | Result | Error |
| --- | --- | --- |
| `processing` | null | null |
| `completed` | RunResult | null |
| `failed` | null | AgentError |

A successful POST waits for completion and returns 200; it never returns processing as a success. A failed POST returns the error envelope and mapped non-2xx status. GET returns 200 for any retained snapshot, including failed.

After authentication/static validation, check existing run identity before new-run readiness, deadline or capacity. Same semantic body replays the retained result/error or returns `agent_run_in_progress`; changed body is `agent_run_conflict`. Identity includes question, language override, state, history, profile and deadline. Arrays preserve order. Transport whitespace/key order and trace headers do not change identity.

Retain terminal runs up to ten minutes, capped at 1,000; never evict active runs for capacity. Accepted work belongs to the run record and survives the inbound connection closing until cancellation/deadline. Go may GET-reconcile at one-second intervals within its original attempt. Unknown/evicted runs do not automatically restart; fail as unavailable and allow a deliberate visitor retry.

## 4. Conversation state and language

### 4.1 Canonical ConversationState

| Field | Type and constraints |
| --- | --- |
| `state_version` | `conversation-state-v1` |
| `conversation_id` | Exact owner UUID |
| `scope` | Fixed artwork/release |
| `revision` | Integer 0–2,147,483,647; equals latest committed turn sequence |
| `language_preference` | Auto or supported fixed language |
| `auto_language` | Supported fallback language; initially English |
| `last_answer_language` | Supported language; null only at revision 0 |
| `active_topic` | Null or nonblank English text, at most 300 code points |
| `continuity_summary` | English contextual summary, 0–1,000 code points |
| `pending_clarification` | Null or `{original_question, question, created_sequence}` |
| `verified_evidence` | 0–8 canonical Evidence records; current artwork has at most two |

A pending record's original question is 1–500 code points, clarification text 1–2,000 and creation sequence positive and no greater than revision. It can remain older than the five-turn window. All state text is context, never a factual evidence source.

Initial state is revision 0, preference auto, auto language English, null last language/topic/pending, empty summary and empty cache. Any nonzero revision requires a last answer language. State cannot switch owner or scope.

### 4.2 LanguageDecision and deterministic resolution

The planner returns:

~~~json
{
  "detected_language": "en",
  "explicit_instruction": null,
  "answer_language": "en"
}
~~~

`detected_language` is one supported language, `und` or `other`. It describes the main substantive visitor content, not an isolated artwork name or a quoted phrase. Language-neutral acknowledgements and pure language-control commands use `und`.

A non-null `explicit_instruction` contains:

| Field | Rule |
| --- | --- |
| `preference` | Auto or supported language |
| `original_fragment` | Literal nonempty substring of the current original message, at most 500 code points |
| `persistence` | `turn` or `conversation` |

Apply these rules exactly:

1. `candidate_preference = language_override` when non-null; otherwise saved preference.
2. The explicit instruction's preference, when present, is the effective preference for this answer; otherwise use the candidate.
3. A fixed effective preference determines the output language. Under auto, use a supported detected language; otherwise use saved `auto_language`.
4. The returned `answer_language` must equal that calculation. Assessment/final cannot change it.
5. On commit, save the instruction's preference only when its persistence is conversation; otherwise save the candidate preference.
6. Set `last_answer_language` to the actual response language.
7. Set `auto_language` to the supported detected substantive language, when one exists; otherwise retain it.

An explicit request without temporary wording defaults to conversation persistence; “for this reply only” uses turn persistence. A quoted instruction inside a translation task is data, not automatically a preference change. Incompatible simultaneous language instructions require clarification.

This separate automatic fallback prevents temporary language leakage. Starting with auto/English, “Korean for this reply only” can produce Korean while leaving auto fallback English; a following neutral “OK” then receives English. The actual last answer remains correctly recorded as Korean until the next turn commits.

Unsupported or mutually conflicting explicit output targets are handled by clarification using the ordinary candidate preference and automatic fallback, with explicit_instruction=null for the unresolved request. Do not choose or persist one disputed language merely to ask for clarification. Do not create an invalid language code or silently ignore the request while completing a factual answer. Literal-fragment checks cannot prove semantic intent; language and instruction interpretation must pass model evaluation.

### 4.3 MemoryUpdate and pending operations

`MemoryUpdate` has exactly `active_topic`, `continuity_summary` and `pending_update`.

| `pending_update` | Effect at a successful commit |
| --- | --- |
| `{action: keep}` | Copy previous pending record, including null |
| `{action: clear}` | Set pending record to null |
| `{action: set, source: current_message}` | Create pending from this run's original message and final clarification text |
| `{action: set, source: pending_clarification}` | Preserve the earlier pending original question and replace its clarification text |

Every terminal clarification requires set. A non-clarification terminal permits only keep or clear. Referencing pending_clarification requires a prior pending record. A new pending record's creation sequence is the next committed sequence, assigned by Go.

A short answer such as “the brushwork” must be interpreted with the pending original question and surrounding context. Acknowledgements normally keep unresolved clarification; resolution or clear abandonment clears it. Repeated clarification must preserve the original unresolved request when it remains the subject.

Only the terminal model operation proposes memory. Refine decisions do not. No extra mandatory summarizer call is allowed. Shape limits are checked; summaries remain untrusted and are not used as proof of artwork facts.

### 4.4 Commit algorithm and source cache

A completed RunResult is eligible only if its conversation scope, language calculation, answer assembly and `conversation_update.base_revision` match the original run, and the Go attempt is still active before its deadline.

Under the conversation lock or equivalent transaction, recheck that current revision equals base revision. Assign the next sequence/turn ID, derive state, and atomically store the turn, state, replay record and pending-request completion. If any check fails, store none of them.

Derive state from the previous snapshot and validated result:

- Preserve state version, conversation ID, artwork and release.
- Set revision to base + 1; reject overflow rather than wrap.
- Apply section 4.2 language calculations.
- Apply the proposed topic/summary and validated pending operation.
- Cache current answer citations first, then previous cached records; deduplicate canonical chunk IDs and keep at most eight.
- Never persist uncited raw candidates or model-created source objects.

The Agent verifies the cache against its immutable source manifest before every model call sequence. A source record changed under the same ID, foreign scope, wrong release or corrupt cache fails with `retrieval_invalid_response` in context phase. No partial memory repair or success is returned.

Changing topics does not change artwork or erase canonical evidence by itself. Scope switching creates/restores a separate conversation. State/history expire together and are lost together on Go restart. Application contract upgrades also create a new conversation; no silent migration is specified.

## 5. Model inputs and decisions

### 5.1 Input projections

`PlanInput` contains exactly:

- `original_question`, `language_override`, `artwork_context` and retained `recent_turns`.
- `conversation_context`: language preference, auto language, last answer language, active topic, continuity summary and pending clarification.
- `cached_evidence`: canonical records verified for this run.
- `complete_scope`: whether that cache contains every manifest record in scope.
- `query_policy`: English search, maximum four requests, maximum two initial queries and current-artwork-only scope.

`AnswerInput` contains `original_question`, resolved `answer_language`, `artwork_context`, retained `recent_turns`, `conversation_context`, `requests`, `executed_queries`, accepted `evidence`, `complete_scope` and `allow_refinement`. Query history can be empty on cache reuse. `FinalInput` fixes allow_refinement to false.

Do not send service credentials, visitor/session identity, conversation/run IDs, state revisions or gold evaluation labels. The provider receives rendered system policy and output schema plus one JSON data message.

### 5.2 Requests and queries

`InterpretedRequest` has exactly:

| Field | Rule |
| --- | --- |
| `id` | Sequential `r1`–`r4` in original message order |
| `original_fragment` | Literal nonempty substring of current message, at most 500 code points; may overlap another fragment |
| `intent` | Standalone English interpretation, at most 1,000 code points |
| `kind` | `conversation`, `general_art_knowledge`, `artwork_fact` or `outside_scope` |

For a contextual reply, preserve a fragment from the current message while expressing the resolved prior request in `intent`. Do not fabricate a current-message quote from earlier history.

A `SearchQuery` contains `id: q1..q4`, 1–4 unique `request_ids` and trimmed English `query` of 1–1,000 code points. Initial IDs start at q1; refinement continues the executed sequence. Only artwork_fact IDs may be linked. Fresh retrieval covers all artwork_fact request IDs; no conversation/general/outside ID is queried. Related factual requests may share a query.

Queries are unique under NFKC + casefold + collapsed whitespace. Preserve causal questions, specificity, uncertainty, negation and named entities. No fixed topic list or ASCII test can establish semantic query quality.

### 5.3 Plan actions

Every Plan includes `language_decision`.

| Action | Other required fields | Structural / runtime rule |
| --- | --- | --- |
| `direct_response` | `requests`, `results`, `memory_update` | No artwork_fact requests; at least one conversation/general request; outside parts may receive scope guidance |
| `retrieve` | `requests`, `queries` | At least one artwork_fact request; 1–2 initial queries; forbidden when cache is complete |
| `answer_from_context` | `requests` | At least one artwork_fact request and nonempty validated cache; no initial queries |
| `clarify` | `text`, `memory_update` | Genuine ambiguity/capability clarification; pending update must set |
| `out_of_scope` | `text`, `memory_update` | Scope guidance; pending update keep or clear |

All request/result arrays have 1–4 entries. Direct text is 1–2,000 code points in the resolved response language. Clarification/scope text may guide the conversation but must not introduce unsupported artwork facts. An outside-only message should use the scope action.

Example direct dialogue:

~~~json
{
  "action": "direct_response",
  "language_decision": {
    "detected_language": "en",
    "explicit_instruction": null,
    "answer_language": "en"
  },
  "requests": [
    {
      "id": "r1",
      "original_fragment": "This painting is beautiful.",
      "intent": "Respond to the visitor's appreciation of the current painting.",
      "kind": "conversation"
    }
  ],
  "results": [
    {
      "request_id": "r1",
      "support": "not_applicable",
      "text": "What about it appeals to you most?",
      "citation_chunk_ids": []
    }
  ],
  "memory_update": {
    "active_topic": "The visitor's reaction to the painting",
    "continuity_summary": "The visitor expressed appreciation for the painting.",
    "pending_update": {"action": "keep"}
  }
}
~~~

This is a synthetic protocol example. It does not require every conversational reply to ask a follow-up question.

### 5.4 Assessment, final decisions and response parts

`AssessmentDecision` is finish, clarify or refine:

| Action | Fields |
| --- | --- |
| `finish` | `results: RequestResult[1..4]`, `memory_update` |
| `clarify` | `text`, `memory_update` with pending set |
| `refine` | `reason_code`, `request_ids`, `queries` |

`FinalDecision` permits only finish or clarify. No later operation can change language, original requests, scope or release.

`RequestResult` contains `request_id`, `support`, `text` (1–450 code points) and `citation_chunk_ids`.

| Planned kind | Allowed support | Citations |
| --- | --- | --- |
| `artwork_fact` | `supported` or `insufficient` | Supported: 1–8 unique accepted IDs; insufficient: empty |
| `conversation`, `general_art_knowledge` | `not_applicable` | Empty |
| `outside_scope` | `out_of_scope` | Empty |

Results cover every request exactly once in order. A model cannot cite current artwork evidence to upgrade an outside request, or use a conversational label to bypass artwork-fact grounding. Structural kind checks and semantic evaluation both apply.

An insufficient part briefly identifies the unconfirmed fact and may continue with a relevant general explanation or conversational direction. It must not invent the fact, pretend the museum has no other information, or treat a general material property as proof of an artist's choice.

Refinement reason codes remain `no_candidates`, `poor_candidate_relevance` and `uncovered_request`. Linked queries cover exactly the selected existing artwork_fact IDs. Refine only once, with incomplete accepted scope and sufficient budget. A semantic planning omission cannot be repaired by silently adding a new request in refinement.

### 5.5 Final assembly invariants

1. For finish/direct results, join part text in request order using exactly one blank line.
2. Deduplicate cited IDs in first-use order and resolve full canonical records from this run's accepted union.
3. If any artwork facts are supported and any fact is insufficient or any outside request exists, derive partially_answered.
4. Otherwise, if artwork facts exist and all are supported, derive answered.
5. If artwork facts exist and none is supported, derive insufficient.
6. Without artwork facts, any outside request derives out_of_scope; otherwise derive conversational.
7. A terminal clarification derives clarification_needed; a direct scope action derives out_of_scope.
8. Clarification and direct scope have no parts/citations. No uncited preamble, footer or additional summarizing model call is added.
9. `answered` and `partially_answered` require at least one citation; all other statuses require an empty citation list.
10. The model cannot set public mode, source URLs, run identity, saved revision or provider identity.

C validates semantic relationships and canonical evidence. Go checks scope, returned requests/results, language calculation, derived text/status, citation membership, counts and the proposed base revision before committing. Neither schema membership nor canonical provenance alone proves every generated claim is entailed; source-based model evaluation remains required.

## 6. Retrieval tool contract

The existing transport and source projection stay compatible:

~~~json
{
  "artwork_id": "aic-28560",
  "query": "What material is The Bedroom made of?",
  "dataset_version": "aic-seed-v0.1",
  "top_k": 2
}
~~~

Tool-level `ToolInput` adds `tool_version: evidence-tool-v1`, `query_id` and `request_ids` to these four fields. The adapter forwards only the four legacy fields and the trace header. Runtime, not the model, inserts scope/top_k.

`ToolResult` contains `tool_version`, `query_id`, `artwork_id`, `dataset_version`, `retrieval_strategy_version: legacy-facet-v0.1` and `candidates` (0–2 Evidence records).

Canonical Evidence fields remain `chunk_id`, `artwork_id`, `source_id`, `source_title`, `source_url`, `source_language`, `source_locator`, `excerpt`, `license`, `license_url`, `attribution` and `normalization`. Compare every field against the immutable manifest. Original excerpts are not translated or rewritten. Compatible extra response metadata is discarded before acceptance.

Retain the legacy service's 8 KiB request and 64 KiB response limits, strict UTF-8 JSON, uncompressed bodies, no redirects, exact trace echo and at most three seconds for preparation, full HTTP read and validation. Calls are sequential. Do not retry automatically.

Duplicate IDs within one result, conflicts across results, wrong query identity, foreign artwork, wrong version or changed canonical fields fail the run. Identical cross-call records deduplicate. Cache records obey the same validation.

Only a successful empty candidate list is an empty lookup. A 5xx/connection error is unavailable; expiry is timeout; malformed or unexpected protocol/status is invalid response. Do not convert any of these technical failures into insufficient evidence.

## 7. Errors, deadlines and recovery

### 7.1 Envelopes

Private errors use `{request_id, run_id, error}`, with null run_id when no valid run identity was established. `AgentError` has `code`, safe English `message` (1–500), boolean `retryable` and `phase`. Phases remain admission, context, planning, retrieval, assessment, final_generation, validation and capacity.

Public errors use `{request_id, error: {code, message, retryable}}`. Go strips internal phases/run identity and never exposes raw prompts, provider responses, memory summaries, credentials or stack traces. The UI uses English diagnostic labels. Normal dialogue text uses the resolved supported language.

### 7.2 Public application errors

| HTTP | Code | Retryable | Action |
| --- | --- | --- | --- |
| 400 | `invalid_request` | false | Correct malformed input |
| 403 | `origin_not_allowed` | false | Correct configured origin |
| 404 | `artwork_not_found`, `conversation_not_found`, `route_not_found` | false | Open valid scope/session/route |
| 405 | `method_not_allowed` | false | Correct method; honor Allow |
| 409 | `agent_contract_mismatch` | false | Align public client and conversation contract |
| 409 | `idempotency_conflict` | false | New UUID required for changed payload |
| 409 | `conversation_state_conflict` | false | Refresh; review draft and deliberately create a new submission |
| 409 | `request_superseded` | false | Restore current state |
| 409 | `request_in_progress`, `conversation_busy` | true | Wait/reconcile, no replacement attempt |
| 413 | `payload_too_large` | false | Reduce payload |
| 415 | `unsupported_media_type` | false | Send uncompressed UTF-8 JSON |
| 500 | `internal_error` | true | Check history before deliberate retry |
| 503 | `service_not_ready` | true | Application startup/readiness is incomplete |

### 7.3 Agent and dependency failures

| Private HTTP/code | Meaning | Public mapping |
| --- | --- | --- |
| 400 invalid_request; 413 payload_too_large; 415 unsupported_media_type | Invalid internal input | 502 agent_invalid_response / false |
| 401 agent_unauthorized | Invalid service auth | 503 agent_unavailable / false |
| 404 route_not_found; 405 method_not_allowed | Private integration route defect | 502 agent_invalid_response / false |
| 409 agent_contract_mismatch; 409 agent_scope_mismatch | Unsupported internal version/profile/scope | 502 agent_invalid_response / false |
| 409 agent_run_conflict | Same run changed body | 502 agent_invalid_response / false |
| 409 agent_run_in_progress | Same run still running | Internal reconciliation; exhausted/unavailable -> 503 agent_unavailable / true |
| 404 agent_run_not_found | No retained run | 503 agent_unavailable / true; no automatic resubmit |
| 503 agent_busy; 503 service_not_ready | Admission/readiness unavailable | 503 agent_unavailable / true |
| 502 agent_context_limit | Required prompt cannot fit | Same code/status / false |
| 502 retrieval_invalid_response | Invalid cache or retrieval record/protocol | Same code/status / false |
| 503 retrieval_unavailable | Dependency unavailable | Same code/status / true |
| 504 retrieval_timeout | Complete lookup deadline exceeded | Same code/status / true |
| 502 generation_invalid_response | Invalid model decision/format/truncation | Same code/status / false |
| 503 generation_unavailable | Provider unavailable/overloaded | Same code/status / true |
| 504 generation_timeout | Stage deadline, including slot wait | Same code/status / true |
| 504 request_timeout | Total run/application deadline | Same code/status / true |
| 500 agent_internal_error | Unexpected internal failure | 503 agent_unavailable / true |

Go-to-Agent connection loss maps to agent_unavailable after bounded reconciliation; an invalid Agent response maps to agent_invalid_response. Parent timeout wins at the Go deadline. Retryable means a later deliberate retry may be useful; it does not authorize an automatic POST loop.

### 7.4 Budgets and capacity

| Boundary | Limit |
| --- | ---: |
| Go accepted attempt, including reconciliation/commit | 60 seconds |
| Agent from Go admission | 55 seconds |
| Model planning, including direct reply | 10 seconds |
| Model assessment / final | 15 seconds each |
| Complete retrieval lookup | 3 seconds |
| Go HTTP write | 65 seconds |
| Browser active wait/recovery | 70 seconds |
| History fetch | 10 seconds |
| Development/production proxy | 75 seconds |
| Graceful drain | 65 seconds |
| Separate operator preparation | Up to 120 seconds |

Children use the smaller of their cap and remaining parent time. One Agent process initially permits eight active runs and one shared active inference, releasing that inference slot during retrieval. Evaluation must share the same gate or use a reserved window. Do not claim a global GPU cap when other clients bypass it.

Private run reconciliation and browser history polling use one-second intervals after preceding requests complete, within the original deadline. Late results cannot alter turns or state. Unknown outcomes require reconciliation rather than an automatic new logical message.

## 8. Implementation obligations and acceptance examples

| Consumer | Required implementation |
| --- | --- |
| A | v2 request payloads, null-versus-explicit language override, expected revision, four-language selector, conversational status, actual language display, monotonic revision handling, unchanged retry payloads |
| B | Private v2 transport/provider, state snapshots and source cache, contract-bound conversations, atomic state/turn commit, state conflict, replay/reconciliation, verified model profile and deadlines |
| C | Five-turn context, language decision validation, five routes, query fidelity, typed response parts, evidence reuse/validation, semantic memory proposals, multilingual/multi-turn evaluation |

The examples file labels structural cases separately from runtime relationships and semantic expectations. Consumers must not claim schema validation proves language interpretation, source support or conversation quality. Cross-field tests need original run/plan/evidence/state together.

At minimum, test:

- Four supported languages; Auto, fixed preference, explicit instruction, one-turn instruction and neutral acknowledgement.
- Quoted language instructions, unsupported target language and conflicting instructions.
- Five history pairs accepted; six, missing latest turns, duplicate IDs and wrong sequence order rejected.
- Direct appreciation, general concept, mixed appreciation plus fact, missing evidence with useful continuation and unsupported subject guidance.
- Cached evidence reuse with no fresh query, complete-cache stopping, wrong-scope/modified cache rejection.
- Keeping pending clarification through thanks, resolving it, abandoning it and asking again without losing the original request.
- Atomic first commit, duplicate replay, changed-payload conflict, stale failed retry, cancellation and rejected late result.
- All old fixture/C M1 checks while v2 remains disabled.

This document introduces no running endpoint, migration, model installation, code change or deployment. Implementation must explicitly select the v2 schema and new prompt package before these contracts become active.
