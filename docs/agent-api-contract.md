# MuseBridge Agent v2 Interface Contract

Status: **Normative visitor-policy revision 2. C workflow implemented; production transport, application integration and real-model acceptance pending.**

Public contract: `agent-demo-v0.3`. Private contract: `agent-run-v2`. Workflow: `museum_qa_v2`. Schema: `agent-schema-v2`. State format: `conversation-state-v1`. Prompt package: `museum-prompts-v2`. Profile: `local-ollama-v2`.

Read this with [the v2 design](agent-design.md), [JSON Schema](contracts/agent-v2.schema.json) and [synthetic examples](contracts/agent-v2.examples.json). The schema defines structural constraints; the relationship and semantic rules below are equally normative. Examples are synthetic and do not claim observed LLM performance.

Revision 2 defines the v2 target selected by the new C workflow. It adds local clarification, canonical catalog facts, explicit translation tasks and subjective-discussion policy. All v2 consumers must use the same revision of the documents, schema and fixtures; the version names below do not imply compatibility with the earlier draft.

The preserved C M1 entry point reads the unchanged v1 artifacts. The new MuseumQAWorkflowV2 explicitly reads revision-2 v2 artifacts and uses injected provider, sizing, budget and retrieval ports; see the [C runbook](../agent/README.md). The [fixture API contract](api-contract.md) continues to govern fixture mode. Consumers must explicitly migrate; a deployment must not reinterpret a v1 message as v2.

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
| `context` | `{artwork_title, artist, catalog_facts, recent_turns}` |
| `conversation_state` | Exact immutable snapshot defined in section 4 |
| `execution` | `{workflow_id: museum_qa_v2, profile_id: local-ollama-v2, deadline_at}` |

`artwork_title` and `artist` are nonempty strings of at most 200 code points and must equal the configured manifest metadata. `catalog_facts` contains exactly the three canonical records defined in section 6.1, ordered title, artist, date_display. Go constructs them from the pinned release; the Agent independently reconstructs and compares every field before planning. Neither the browser nor the model supplies authoritative catalog records. The conversation ID/scope in state must equal the envelope. A supported but unconfigured scope or wrong manifest metadata is `agent_scope_mismatch`.

`recent_turns` must be exactly the latest `min(5, revision)` committed pairs. Their sequences are contiguous and end at `conversation_state.revision`; IDs are unique. Go owns authenticity. The Agent validates these relationships before token fitting, then may drop oldest whole pairs for a model operation.

A `HistoryTurn` contains `turn_id`, positive `sequence`, `question` (1–500), actual `answer_language`, completed `answer_status`, `answer_mode: llm` and `answer_text` (1–2,000). All belong to this v2 conversation. The latest historical answer_language must equal conversation_state.last_answer_language; validate this before token fitting. Do not send pending attempts, fixture-mode turns, older complete turns or another conversation.

Go sets `deadline_at` to admission plus 55 seconds. On new-run admission, the Agent converts remaining wall-clock time to a monotonic deadline, capped at 55 seconds, and rejects expired requests. Clock synchronization is required; Go's final deadline remains the commit guard.

### 3.2 RunResult

| Field | Meaning |
| --- | --- |
| `scope` | Exact input scope |
| `answer_language` | Validated primary response language |
| `language_decision` | Frozen planner language decision, including any translation task |
| `answer` | `AgentAnswer`: derived status, natural text and canonical Citation records; no mode |
| `requests` | Validated interpreted requests; empty only for early whole-message clarify/scope |
| `request_results` | One result per request for finish/direct, including any local clarification; empty for whole-message clarification or early scope |
| `evidence_snapshot` | Canonical accepted retrieval-cache-plus-tool union, at most eight |
| `catalog_snapshot` | All three canonical catalog facts in title/artist/date_display order, even if none is cited |
| `reused_chunk_ids` | IDs from validated input retrieval cache in its order; at most eight unique |
| `executed_queries` | Actual ordered queries, 0–4 |
| `conversation_update` | `{base_revision, memory: MemoryUpdate}` |
| `execution` | Version/model identity, counts and duration |

Early direct responses include their requests/results and can cite catalog facts. A whole-message assessment clarification retains the plan's requests but has no result parts or citations; it is used only when no safe independent answer can be completed. Do not use it to discard an answerable independent part. A local clarification is a finish/direct result part and can coexist with answered parts and citations. Early whole-message clarification/scope has no requests/results.

`ExecutionRecord` has `workflow_id`, `profile_id`, `prompt_version`, `schema_version`, `retrieval_strategy_version`, `model_provider`, `model_name`, `model_digest`, `model_calls`, `retrieval_calls`, `refinement_rounds` and `duration_ms`. Use the versions at this document's start, `legacy-facet-v0.1` and the actual verified Ollama identity. Successful counts are 1–3 model calls, 0–4 retrieval calls and 0–1 refinement rounds; duration is 0–55,000 ms. Nonempty identity/version strings are at most 200 code points.

Validate query count against executed queries, reused IDs against input retrieval cache and catalog_snapshot against the canonical catalog. Resolve citation_ids only from the evidence_snapshot or catalog_snapshot. C verifies actual tool provenance, batch limits, translation source binding and semantic execution. Go independently checks available structural relationships, canonical catalog identity, cache consistency, citation membership, language calculations, pending binding and counts. A returned snapshot is not an independent tool transcript; Go trusts the authenticated Agent for fresh-tool provenance and does not claim to prove semantic entailment.

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
| `pending_clarification` | Null or `{original_question, original_fragment, unresolved_intent, question, created_sequence}` |
| `verified_evidence` | 0–8 canonical Evidence records; current artwork has at most two |

A pending record's original_question is 1–500 code points; original_fragment is null or a nonempty literal substring of that original question, at most 500; unresolved_intent is null or a standalone English description of the unresolved portion, at most 1,000; question is the actual clarification text, 1–2,000. Its creation sequence is positive and no greater than revision. Null fragment/intent identify whole-message clarification without a narrower binding. A focused intent can coexist with a null fragment when a whole-message pending item becomes more specific. It can remain older than the five-turn window. All state text is context, never a factual evidence source. There is at most one pending clarification, not a queue of unanswered requests.

Initial state is revision 0, preference auto, auto language English, null last language/topic/pending, empty summary and empty cache. Any nonzero revision requires a last answer language. State cannot switch owner or scope.

### 4.2 LanguageDecision, translation and deterministic resolution

The planner returns:

~~~json
{
  "detected_language": "en",
  "explicit_instruction": null,
  "translation": null,
  "answer_language": "en"
}
~~~

`detected_language` is a supported language, und or other. It describes substantive visitor content, excluding quoted source text, isolated artwork titles and language-control text. Neutral acknowledgements, pure settings and pure translation commands use und. The target language of a translation is not the detected input language.

A non-null `explicit_instruction` has preference (Auto or supported language), original_fragment (literal current-message substring, 1–500), purpose and persistence. The only valid purpose/persistence pairs are conversation_setting/conversation and turn_response/turn. An ordinary instruction such as “Answer in Spanish” is a conversation setting unless explicitly limited to one response. A request to transform specified content is instead represented by translation, even when it does not say “only this time.” Quoted instructions are data.

A non-null `translation` has target_language (one of the four supported output languages), original_fragment (literal current-message instruction, 1–500) and source (TranslationSource or null). At most one translation target/source is supported in a message. This field never changes the saved preference. A separate, unambiguous conversation setting may coexist: “From now on use Spanish; translate your last answer into Korean” produces Korean for this turn and saves Spanish for later turns.

| TranslationSource | Exact binding |
| --- | --- |
| `{kind: history_answer, sequence}` | Entire answer_text of that retained completed turn |
| `{kind: history_excerpt, sequence, excerpt}` | Nonempty exact substring of that retained answer_text, at most 2,000 code points |
| `{kind: current_quote, excerpt}` | Nonempty exact substring of the current visitor message, at most 500 code points |
| null | Exact source is unavailable or genuinely ambiguous; only a whole-message clarification may complete this run |

History references must be present in the actual fitted planning input, not merely in the browser transcript or a lossy summary. Runtime resolves the exact source and pins it for all later operations as `ResolvedTranslation {target_language, source, text}`. Its source is non-null and text is the bound original, 1–2,000 code points. Do not translate a summary as if it were the requested original. Once selected, the source cannot be dropped during later history fitting. If required pinned input cannot fit, return agent_context_limit; do not substitute another passage.

Apply these rules exactly:

1. Candidate preference is language_override when non-null, otherwise the saved preference.
2. Effective preference is explicit_instruction.preference when present, otherwise the candidate.
3. A non-null translation selects answer_language=target_language for this response. Otherwise a fixed effective preference selects the response language; Auto uses supported detected language or saved auto_language.
4. A conflicting turn_response instruction and translation target require clarification; do not resolve it by arbitrarily choosing one. Null the disputed instruction/translation fields and use the ordinary candidate preference with the same Auto rule. Unsupported targets and incompatible multiple translation targets follow this rule. A clear independent conversation setting may remain and determine the clarification language; no disputed setting is persisted.
5. Freeze the resulting answer_language for later operations. A null translation source with a clear supported target asks for the missing passage in that target language; it does not change the saved preference.
6. On successful commit, save explicit_instruction.preference only for conversation_setting; otherwise save the candidate. Translation itself has no persistence effect.
7. Save last_answer_language as the actual primary output language. Update auto_language only from supported substantive detected input; otherwise preserve it.

The primary output language permits original artwork names, source quotations and necessary foreign terms. “Explain in Chinese, keeping the term impasto in English” is not a language conflict. Full parallel bilingual answers and multiple simultaneous translation targets are outside this release; clarify one primary target rather than silently selecting or dropping one. Source cards retain original English excerpts.

Translation must preserve attribution, uncertainty, negation and the distinction between quoted claims and established facts. Rephrasing a prior source-backed answer requires the same canonical support for repeated facts. Catalog-only content uses catalog_fact; content with noncatalog artwork claims uses artwork_fact and the evidence route. Translating visitor-supplied text may use conversation with clear attribution to the supplied text; translation does not certify its claims and must not add a museum citation or populate verified memory. Pure general conversation can be translated directly.

The response envelope still has a 2,000-code-point total and a verified model output budget. Do not silently summarize, omit clauses or split one translation across invented requests to fit. When a faithful requested translation cannot fit, ask the visitor to select a shorter passage; this is a scope/length clarification, not missing museum evidence. Actual source fidelity and language inference require semantic evaluation; schema/substring checks alone cannot prove them.

### 4.3 MemoryUpdate and pending operations

`MemoryUpdate` has exactly active_topic, continuity_summary and pending_update. Only the terminal model operation proposes it; refine decisions do not. No extra summarizer call is required.

| Pending operation | Effect |
| --- | --- |
| `{action: keep}` | Copy the old pending record, including null |
| `{action: clear}` | Remove the old pending record |
| `{action: set, source, request_id: null}` | Whole-message clarification; source is current_message or pending_clarification |
| `{action: set, source, request_id: rN}` | Local clarification bound to exactly that request/result |

A whole-message clarify requires set with request_id=null. A finish/direct response with a clarification_needed part requires set with its exact request ID. A terminal reply without any clarification permits only keep or clear. At most one result can request clarification. The linked result must correspond to a needs_clarification request and contain no citations. Nonblank clarifying text is the question saved in pending state; never save the whole assembled mixed answer as the clarification question.

Go derives the new pending record:

- original_question is the current original message for current_message, or the prior pending original_question for pending_clarification. The latter requires an existing pending record.
- For a linked current_message request, copy its literal original_fragment and standalone intent into original_fragment and unresolved_intent.
- For a linked pending_clarification request, retain the prior original_fragment and use the new focused request intent. The new message fragment need not occur in the old original question and must not be copied into that old quote field.
- For whole-message clarification from current_message, set fragment and intent to null. For whole-message clarification from pending_clarification, preserve both prior focus fields.
- question is the linked clarification part's text, or the whole-message clarification text when request_id is null. created_sequence is the new committed turn sequence, assigned by Go.

Interpret a short reply such as “the brushwork” against this focused pending request, not all already answered parts of the original message. Preserve pending through incidental thanks, clear it when resolved or deliberately abandoned, and retain the original question through repeated clarification. A new focused question may replace the one pending item, but must not silently discard another still-active issue: ask one prioritization question when necessary.

Summaries retain explicit simplicity/length preferences while relevant; a later explicit instruction overrides them. Corrected interpretations replace obsolete ones rather than being appended as equally current facts. Attribute visitor impressions and supplied quotations, retain uncertainty, and never turn them into museum facts. A summary cannot provide an exact lost quotation or certify an artist's motive. These are semantic memory obligations in addition to schema limits.

### 4.4 Commit algorithm and source cache

A completed RunResult is eligible only if its conversation scope, language calculation, answer assembly and `conversation_update.base_revision` match the original run, and the Go attempt is still active before its deadline.

Under the conversation lock or equivalent transaction, recheck that current revision equals base revision. Assign the next sequence/turn ID, derive state, and atomically store the turn, state, replay record and pending-request completion. If any check fails, store none of them.

Derive state from the previous snapshot and validated result:

- Preserve state version, conversation ID, artwork and release.
- Set revision to base + 1; reject overflow rather than wrap.
- Apply section 4.2 language calculations.
- Apply the proposed topic/summary and validated pending operation.
- Cache only current answer citations of retrieval Evidence type first, then previous cached Evidence; deduplicate canonical chunk IDs and keep at most eight. CatalogFact citations are excluded and are reconstructed from the pinned release on each run.
- Never persist uncited raw candidates or model-created source objects.

The Agent verifies the cache against its immutable source manifest before every model call sequence. A source record changed under the same ID, foreign scope, wrong release or corrupt cache fails with `retrieval_invalid_response` in context phase. No partial memory repair or success is returned.

Changing topics does not change artwork or erase canonical evidence by itself. Scope switching creates/restores a separate conversation. State/history expire together and are lost together on Go restart. Application contract upgrades also create a new conversation; no silent migration is specified.

## 5. Model inputs and decisions

### 5.1 Input projections

`PlanInput` contains exactly original_question, language_override, artwork_context, catalog_facts, retained recent_turns, conversation_context, cached_evidence, complete_scope and query_policy.

- artwork_context retains artwork_title and artist for orientation. catalog_facts contains all three separately typed canonical values.
- conversation_context contains language preference, auto language, last answer language, active topic, continuity summary and focused pending clarification.
- cached_evidence contains only validated retrieval records. complete_scope considers only retrievable manifest chunks, never catalog facts.
- query_policy specifies English search, at most four requests, at most two initial queries and current-artwork-only scope.

`AnswerInput` contains original_question, resolved answer_language, artwork_context, catalog_facts, retained recent_turns, conversation_context, requests, executed_queries, evidence, complete_scope, allow_refinement and translation. translation is null or runtime-resolved ResolvedTranslation; it is independent of any subsequently shortened history window. `FinalInput` fixes allow_refinement=false.

Do not send service credentials, visitor/session identity, conversation/run IDs, state revisions or gold labels. The provider receives rendered system policy and output schema plus one JSON data message. Sequence numbers identify retained translation sources without exposing conversation identifiers.

### 5.2 Requests and queries

`InterpretedRequest` has exactly:

| Field | Rule |
| --- | --- |
| id | Sequential r1–r4 in message order |
| original_fragment | Literal nonempty current-message substring, at most 500 code points; fragments may overlap |
| intent | Standalone English interpretation, at most 1,000 code points; preserve remaining ambiguity rather than inventing a referent |
| kind | conversation, general_art_knowledge, catalog_fact, artwork_fact or outside_scope |
| resolution | clear or needs_clarification |

At most one request is needs_clarification. Group genuinely related ambiguities into one faithful request, or ask a whole-message prioritization question if they cannot be handled with one pending item. A request for title, named artist or displayed date is catalog_fact. Other work-specific facts remain artwork_fact, including biography, exact pigments and historical motives. Personal reactions and attributed subjective discussion use conversation; general explanations without work-specific assertions use general_art_knowledge.

For a short contextual reply, preserve a current-message fragment while expressing the focused pending intent. Do not fabricate a current quote from earlier history. Resolve obvious references; clarify only ambiguity that materially affects the response. A corpus gap is not ambiguity.

Preserve all meaningful requests. Closely related questions may share a request if the answer and support remain auditable. When more than four independent requests cannot be faithfully grouped, use a whole-message prioritization clarification before searching; keep the original message in pending state. Never silently omit the fifth request or emit an invalid oversized plan.

A SearchQuery has id q1–q4, 1–4 unique request_ids and trimmed English query of 1–1,000 code points. Link only clear artwork_fact requests. Fresh retrieval covers every such request; catalog, conversational, outside and needs_clarification requests are never searched. Related clear facts may share a query. Initial IDs start at q1; refinement continues after executed IDs.

Queries are unique under NFKC, case folding and collapsed whitespace. Preserve causality, specificity, uncertainty, negation and named subjects. No fixed topic list or ASCII test establishes semantic fidelity.

### 5.3 Plan actions

Every Plan includes `language_decision`.

| Action | Other required fields | Structural / runtime rule |
| --- | --- | --- |
| `direct_response` | `requests`, `results`, `memory_update` | No clear artwork_fact request; at least one clear conversation/general/catalog request; may include one local clarification and outside guidance |
| `retrieve` | `requests`, `queries` | At least one clear artwork_fact request; 1–2 initial queries covering clear artwork_fact requests only; forbidden when retrieval cache is complete |
| `answer_from_context` | `requests` | At least one clear artwork_fact request and nonempty validated retrieval cache; no initial queries |
| `clarify` | `text`, `memory_update` | Whole-message ambiguity, source/length/language clarification or prioritization; pending set with request_id=null |
| `out_of_scope` | `text`, `memory_update` | Scope guidance; pending update keep or clear |

All populated request/result arrays have 1–4 entries. Whole-message text is 1–2,000 code points in the primary response language. Clarification/scope text may guide the conversation but must not introduce unsupported artwork facts. An outside-only message should use the scope action. If an independent clear part can be answered, use a result-bearing route with local clarification rather than blocking the whole message. Source-unavailable translation, incompatible output languages and ungroupable request overflow are whole-message constraints and may require clarification before any content answer.

Example direct dialogue:

~~~json
{
  "action": "direct_response",
  "language_decision": {
    "detected_language": "en",
    "explicit_instruction": null,
    "translation": null,
    "answer_language": "en"
  },
  "requests": [
    {
      "id": "r1",
      "original_fragment": "This painting is beautiful.",
      "intent": "Respond to the visitor's appreciation of the current painting.",
      "kind": "conversation",
      "resolution": "clear"
    }
  ],
  "results": [
    {
      "request_id": "r1",
      "support": "not_applicable",
      "text": "What about it appeals to you most?",
      "citation_ids": []
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

AssessmentDecision is finish, clarify or refine. Finish has results and memory_update. Whole-message clarify has text and memory_update with pending set/request_id=null. Refine retains reason_code, request_ids and queries; it never creates a memory update. FinalDecision permits only finish or whole-message clarify. No later operation may change language, original requests, their declared resolution, scope, release or translation source.

A RequestResult has request_id, support, text (1–2,000 code points) and citation_ids. The assembled answer, including blank-line separators, must also fit 2,000 code points and the verified model output budget; per-part limits are not separate additive budgets.

| Planned request | Allowed result and sources |
| --- | --- |
| Any needs_clarification request | clarification_needed; no citations; one focused question |
| Clear catalog_fact | supported with canonical CatalogFact IDs; insufficient with no citations only when the requested specificity exceeds the displayed value |
| Clear artwork_fact | supported with canonical retrieval Evidence IDs, optionally catalog IDs for accompanying catalog facts; insufficient with no citations |
| Clear conversation/general_art_knowledge | not_applicable; no citations |
| Clear outside_scope | out_of_scope; no citations |

Catalog metadata cannot support a noncatalog claim. A supported artwork_fact result requires at least one retrieval Evidence citation that actually supports its noncatalog content; provenance alone does not establish entailment. A mixed title-and-technique overview may use artwork_fact with both source types, or separate auditable requests. Do not combine independent supported and unsupported factual asks into one result merely to hide a gap.

Results cover every request exactly once in order. At most one clarification part is allowed, with an exactly linked pending set. Prefer answering clear parts first when their original order permits; do not add a second unnecessary question in other parts. Whole-message clarification is not a substitute for a safe partial answer.

An insufficient part identifies the unconfirmed fact and may continue with relevant general information. It must not claim the museum has no other information. Subjective discussion may refer to the visitor's stated observation with explicit attribution and conditional language; it must not add unseen details or imply historical intent. Creative fictional narratives are deferred; an observation prompt or general discussion is allowed without presenting imagined events as history.

Refinement reason codes remain no_candidates, poor_candidate_relevance and uncovered_request. Link only selected existing clear artwork_fact IDs. Refine once at most, only with incomplete retrieval scope and sufficient time. Catalog facts and ambiguous referents are not retrieval gaps. A planning omission cannot be repaired by silently adding requests. If assessment discovers that a supposedly clear request is not interpretable, it must not change the frozen plan or disguise ambiguity as missing evidence: whole-message clarification is permitted only when no safe independent part can be completed; otherwise the plan is a model-validation failure and must be counted as such in evaluation.

### 5.5 Final assembly invariants

1. For finish/direct, join part text in request order with one blank line. Enforce the aggregate 2,000-code-point bound. No unvalidated preamble, footer or polishing call is added.
2. Resolve citation_ids to canonical Citation records: chunk_id identifies retrieval Evidence; fact_id identifies CatalogFact. Their namespaces cannot overlap. Deduplicate in first-use order, at most eleven records (eight retrieval plus three catalog).
3. Count clear catalog_fact and artwork_fact as factual requests. With at least one supported fact and any insufficient, outside or clarification part, derive partially_answered.
4. With supported facts and no gap/outside/clarification, derive answered. Conversational parts do not downgrade it.
5. Without supported facts, any clarification part derives clarification_needed; otherwise any factual request derives insufficient.
6. Without facts or clarification, any outside part derives out_of_scope; otherwise derive conversational.
7. A whole-message clarification derives clarification_needed with no parts/citations; a direct scope action derives out_of_scope with no parts/citations. An assessment whole-message clarification retains planned requests only for validation/diagnostics.
8. answered and partially_answered require citations; all other statuses require an empty citation list. A mixed fact-plus-local-clarification answer is partially_answered, not a citation-bearing clarification_needed.
9. The pending binding must match the one clarifying part, or be null for whole-message clarification. The model cannot set public mode, source records/URLs, run identity, saved revision or provider identity.

For example, “What material is this, and what is that small thing?” can produce a supported material part with its Evidence citation and an uncited local clarification for the second request. Its public status is partially_answered; pending.question contains only the second part's question. If the first part were just a conversational acknowledgement, the same local clarification would instead produce clarification_needed with no citations.

C validates request semantics, canonical sources, exact translation binding and memory proposals. Go checks scope, returned requests/results, deterministic language/status/text calculations, source identity, pending binding and base revision before committing. Shape validity and source membership alone do not prove that generated claims are supported; real-model evaluation remains required.

## 6. Retrieval tool contract

### 6.1 Canonical catalog facts

The current pinned seed already contains title, artist and date_display for each artwork. Expose exactly those three values as CatalogFact records. This is a deterministic projection of the existing release, not new museum prose, a live API lookup or a retrieval chunk. Do not modify the seed or its SHA-256 for this documentation change.

CatalogFact has exactly kind, fact_id, artwork_id, dataset_version, field, value, source_id, source_title, source_url, source_locator, attribution and normalization:

- kind is catalog. field is title, artist or date_display; value is the exact corresponding seed artworks value, not a model translation or inferred date.
- fact_id is `<artwork_id>-catalog-<field>`. This namespace is reserved and disjoint from chunk IDs.
- artwork_id and dataset_version match the run. source_id/title/url come from the seed's associated source; attribution is that source's institution.
- source_locator is `artworks[artwork_id=<artwork_id>].<field>` in the pinned seed. It identifies the released catalog field, not a purported quotation location on the linked website.
- normalization is exactly `Released catalog metadata; not a verbatim museum excerpt.` No fabricated excerpt, license or license URL is added.

The canonical sequence is title, artist, date_display. Go and Agent independently derive all twelve fields from the same validated release. Missing/malformed required values prevent readiness; mismatched input catalog data is agent_scope_mismatch before model execution. A forged returned catalog record is an invalid Agent result and cannot commit. Rendering labels it as catalog metadata, with its original field/value and source link, rather than as a quoted passage. Additional human-facing translations belong in the answer, never the canonical record.

Preserve displayed date ranges and uncertainty: `1875–80` does not establish a single exact year. Artist labels establish the listed creator, not nationality, biography, motives or attribution history. Titles do not establish the identity of every depicted person/object. A general “When was it made?” can be supported by the displayed range. An exact-day or exact-year request beyond that range is insufficient; do not mark it answered merely by giving a less precise range. An insufficient part must not introduce an uncited actual date value. If the visitor also asks for the available catalog date, answer that separate request with its own supported catalog part and citation. Never invent precision or silently weaken the original question.

Citation is a disjoint union of the unchanged retrieval Evidence projection and CatalogFact. A retrieval citation must not carry the reserved catalog kind discriminator; objects mixing the two shapes are rejected. Catalog records are always available to planning/answer generation, never sent to /retrieve, never stored in verified_evidence and never counted in retrieval_calls, reused_chunk_ids or complete_scope. complete_scope still means all retrievable chunks for the current artwork, currently two. A catalog-only answer uses direct_response, one model call and zero retrieval calls.

### 6.2 Existing evidence lookup

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

Retain the legacy service's 8 KiB request and 64 KiB response limits, strict UTF-8 JSON, uncompressed bodies, no redirects, exact trace echo and at most three seconds for preparation, full HTTP read and validation. Calls are sequential. Stop the remaining batch when canonical retrieval coverage becomes complete, recording only the actually executed prefix. Do not retry automatically.

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
| A | v2 requests, language/translation outcomes, catalog versus excerpt source cards, mixed answered/clarifying replies, revision-aware recovery and visitor journeys |
| B | Private v2 transport/provider, state snapshots and source cache, contract-bound conversations, atomic state/turn commit, state conflict, replay/reconciliation, verified model profile and deadlines |
| C | Five-turn context, local clarification, catalog provenance, translation binding, subjective-discussion policy, five routes, query fidelity, source validation, semantic memory and multilingual visitor evaluation |

The examples file labels structural cases separately from runtime relationships and semantic expectations. Consumers must not claim schema validation proves language interpretation, source support or conversation quality. Cross-field tests need original run/plan/evidence/state together.

At minimum, test:

- Four supported languages; Auto, fixed preference, explicit instruction, one-turn instruction and neutral acknowledgement.
- Quoted language instructions, unsupported target language and conflicting instructions.
- Five history pairs accepted; six, missing latest turns, duplicate IDs and wrong sequence order rejected.
- Direct appreciation, general concept, mixed appreciation plus fact, missing evidence with useful continuation and unsupported subject guidance.
- Cached evidence reuse with no fresh query, complete-cache stopping, wrong-scope/modified cache rejection.
- Keeping pending clarification through thanks, resolving it, abandoning it and asking again without losing the original request.
- Atomic first commit, duplicate replay, changed-payload conflict, stale failed retry, cancellation and rejected late result.
- Canonical catalog questions for both works, preserved date ranges, catalog forgery rejection and no catalog effect on retrieval completeness/cache.
- Answered facts plus one local clarification, exact pending focus and a short follow-up that does not repeat answered parts.
- One-off content translation, simultaneous persistent setting plus translation, unavailable exact source, pinned source, source attribution and length overflow.
- Subjective reactions, correction, general-to-specific inference traps and all [visitor acceptance journeys](visitor-acceptance.md).
- All old fixture/C M1 checks while v2 remains disabled.

The C workflow selects this v2 schema and prompt package and returns validated C-owned result fields. It does not implement the public/private HTTP endpoints, a production model provider, Go state persistence or deployment. Scripted-model checks cannot close those integration or semantic acceptance gates.
