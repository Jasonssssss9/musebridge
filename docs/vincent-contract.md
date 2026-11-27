# Talk with Vincent interface contract

**Status:** Agreed implementation target. The deployed gallery uses `agent-demo-v0.3`. The iPhone prototype verified recording, playback and local English transcription. End-to-end Vincent conversations, historical-source validation and speech playback from committed turns still require implementation and acceptance.

This contract defines the English conversation on Getty’s *Irises* (`getty-103jnh`) and preserves the published 20-work release (`getty-gallery-v2`). The [voice contract](vincent-voice.md) defines the audio bytes, HTTP errors and phone controls. The [schema changes](contracts/agent-v3.patch.json) and [wire examples](contracts/vincent-examples.json) specify the exact JSON. For fields and relationships inherited below, use the [Agent v2 contract](agent-api-contract.md).

## 1. What each contributor delivers

| Boundary | Producer → consumer | Agreement |
| --- | --- | --- |
| Entry and UI | B (web and visual design) → A (Go and audio) | A visitor opens the artwork guide or chooses **Talk with Vincent**. B asks Go to create or restore the selected mode; Go returns the authoritative conversation. |
| A recorded question | A → B | A exposes recording, local Whisper transcription and media playback state. B shows the recognized words, progress, controls and citations. One successful recording submits one ordinary message. |
| A conversation turn | A → C (Agent and evidence) | Go freezes the question, scope, revision, language and context. C selects the role and executes the existing bounded model/tool workflow. |
| A completed answer | C → A → B | C returns model-authored answer, evidence and memory proposal. Go validates and commits atomically. Both subtitles and speech use the exact saved `turn.answer.text`. A and C select one English delivery preset together. |

## 2. Entry, identity and compatibility

- The ordinary artwork page stays at `/a/{slug}`. The **Talk with Vincent** button on *Irises* opens `/a/getty-103jnh/vincent` under the configured **public HTTPS origin**. Direct navigation, refresh and Back use the same rules as a button click. An HTTP gallery can link across origins to this HTTPS entry; the new origin establishes its own visitor cookie. Keep all calls from the voice page on that HTTPS origin.
- In a mode-aware deployment, the two catalog GETs (`/api/artworks` and `/api/artworks/{slug}`) add an ordered `conversation_modes` array to each artwork card. Return `["guide","vincent"]` only for the enabled Irises release and `["guide"]` elsewhere. An older card without this property means guide only. Go derives it from release and feature configuration; adding an entry does not change the gallery data file. A configured capability is not a live provider health check.
- Compatibility matrix: existing fixture clients keep `local-demo-v0.1`; existing Agent clients keep `agent-demo-v0.3` and their strict request shapes. A mode-aware client uses `agent-demo-v0.4`. The Go handler selects the appropriate decoder using the version, then validates the entire body. Sending `conversation_mode` under v0.3 is a 400. A well-typed unsupported version is 409 `agent_contract_mismatch`. A fresh installation of the old build cannot run these new routes.
- A conversation key comprises visitor, public contract, artwork, dataset release, mode and role pack version. Within that key, reopening restores the same unexpired conversation. Changing scope creates or restores a different ID. IDs, turns, five-turn windows, verified caches, language state, pending clarifications and retries cannot cross that boundary. Existing v0.3 histories remain available through v0.3; upgrading a client to v0.4 starts a separate history. Preserve the original fixed 24-hour lifetime.
- B keys in-flight opens, last-session pointers and restored UI by origin, public contract, artwork and mode, and checks the returned scope before rendering. Pending drafts and recordings also belong to their conversation IDs. Leaving a page stops recording/playback and invalidates outstanding UI callbacks. Go can still finish an already accepted question.
- The model, visitor wording, input method and TTS settings cannot change a conversation's role. A visitor can type in Vincent mode if the microphone is unavailable. A spoken or typed request to impersonate someone else is ordinary visitor input. `conversation_mode` is distinct from the existing `turn.answer.mode`, which remains `llm` for model answers.

## 3. Browser ↔ Go

All public Agent JSON endpoints inherit canonical lowercase UUIDs, exact POST Origin, same-origin host-only HttpOnly visitor cookie, server-generated `X-Request-ID`, `Cache-Control: no-store`, uncompressed UTF-8 and the 8 KiB request limit. Use secure cookies on HTTPS. Only the binary audio upload has a different size limit.

| Method and route | Request | Successful response |
| --- | --- | --- |
| `POST /api/conversations` | `contract_version`, `artwork_id`, optional `conversation_mode` | 200 `{conversation, created}` on creation or restore |
| `GET /api/conversations/{id}/messages` | Visitor cookie | 200 `{conversation, turns, pending_request}` with the full saved history |
| `POST /api/conversations/{id}/messages` | The five message fields shown below | 200 `{conversation, turn}` after commit or replay |
| `POST /api/conversations/{id}/transcriptions` | Raw short audio clip | 200 `{"text":"…","language":"en"}`; no turn or revision yet |
| `POST /api/conversations/{id}/turns/{turn_id}/speech` | Empty body | 200 `audio/mpeg` for this owned, committed turn; no change to history |

**Create.** In v0.4, omitted mode defaults to `guide`; explicit `null`, unknown modes or client-supplied pack, prompt or scope fields produce 400 `invalid_request`. Check that the artwork exists (404 `artwork_not_found`) before eligibility. A known artwork/release that does not offer Vincent or a disabled Vincent feature produces 409 `conversation_mode_unavailable`. Missing, expired and unowned conversation IDs all produce 404 `conversation_not_found`.

~~~json
{"contract_version":"agent-demo-v0.4","artwork_id":"getty-103jnh","conversation_mode":"vincent"}
~~~

Every v0.4 public conversation projection, including those inside history and replay responses, carries `conversation_mode` and `reference_pack_version`. For guide these are `"guide"` and `null`. For Vincent they are `"vincent"` and `"vincent-irises-v1"`. The server supplies both. Full bodies with IDs and timestamps are in the [shared examples](contracts/vincent-examples.json).

**Submit.** Go retrieves the role from the owned conversation. Preserve the five-field request; do not send the role, spoken-answer text, previous turns or evidence from the browser.

~~~json
{"contract_version":"agent-demo-v0.4","client_message_id":"33333333-3333-4333-8333-333333333333","question":"Why do they look blue today?","language_override":null,"expected_revision":1}
~~~

A capture is bound to the revision and conversation the visitor was responding to. A successful transcription is visible immediately and is submitted once. On a lost response, read history and reconcile the original logical message ID before retrying. A retry carries the identical question, override and expected revision. A revision conflict preserves the unsent question for review. A fresh recording is a new logical question. Replaying audio or recovering history cannot rerun the Agent.

**Language.** Guide retains the four-language v2 rules. Vincent initializes `language_preference` and `auto_language` to `en`, with `last_answer_language` initially null and `en` after every commit. The Vincent browser submits `language_override:null`. Explicit `"en"` is also valid; other recognized selections produce 400 `voice_language_unsupported` before admission. Unknown enum values are malformed. A visitor may type in another language: the Agent can answer in English or explain the English-only voice option and point to the multilingual guide. The planner still records a literal in-text language instruction or translation request faithfully; the fixed Vincent output language overrides the guide's usual precedence. Do not save a non-English preference or represent a UI setting as visitor speech.

Use the existing `{request_id,error:{code,message,retryable}}` error envelope. Both new create/language errors above have `retryable:false`. Existing stale revision, busy, timeout, completed replay and pending recovery rules continue to apply. Private Qwen or retrieval failures keep their existing safe public errors. A missing historical detail can produce a successful conversational or insufficient turn. A provider failure cannot stand in for missing evidence.

## 4. Go ↔ Python Agent

| Field / route | v0.4 target |
| --- | --- |
| Private run and reconciliation | `POST /internal/v3/agent/runs`; `GET /internal/v3/agent/runs/{run_id}` |
| Identity | `agent-run-v3`, `museum_qa_v3`, `museum-prompts-v3`, `agent-schema-v3`, `conversation-state-v2`; model profile remains `local-ollama-v2` |
| Scope | Exactly `artwork_id`, `dataset_version`, `conversation_mode`, `reference_pack_version` |
| Request, result, retained snapshot | Use `RunRequest`, `RunResult` and `RunSnapshot` in the effective v3 schema. A successful POST and status GET have different envelopes as in v2. |
| Readiness | Add private `GET /internal/v3/agent/readyz`, using the v3 `ReadinessResponse` on 200 or `503 service_not_ready`. Check the configured prompt, pinned releases and model profile; the four advertised languages describe guide mode. Existing v2 probe and run routes keep their version. |
| Auth and limits | v3 run, status and versioned readiness require the existing service Bearer token and one trace header. Retain the 256 KiB private JSON cap, one model worker, 55 s Agent deadline, 60 s Go attempt and bounded recovery of the original run. |

For example, the same server-owned object must occur in Vincent's run scope, state scope and eventual result:

~~~json
{"artwork_id":"getty-103jnh","dataset_version":"getty-gallery-v2","conversation_mode":"vincent","reference_pack_version":"vincent-irises-v1"}
~~~

Go freezes scope, actual question, most recent five complete pairs, revision, verified cache, catalog facts and deadline at admission. C chooses role policy **before the first model call**, using that frozen scope in the planning, answer and final input projections. Direct responses, retrieval decisions, clarification, final prose and memory all use the same role. The model decides when to retrieve and writes every visitor reply and semantic memory update. Python executes tools and validates results. The greeting is a UI cue and is never inserted as a prior conversational turn.

Go and Python validate equality of the expected scope across the request/state/result and bind it to ownership, run identity and revision. Private scope/pack mismatch uses the inherited `agent_scope_mismatch` behavior; reject invalid or foreign evidence. The same run ID cannot execute a different mode or payload. Keep the original error mappings, idempotency, atomic commit, maximum one refinement, tool budgets, cache of at most eight verified excerpts and cited-only cache admission. Media failures never modify this state. The [v2 generator guide](contracts/README.md) identifies the Go/Python validators and generated types that the implementation must version together.

## 5. Source identity and character behavior

The [reviewed evidence and speaking boundaries](vincent-evidence.md) document the four shipped reference passages. The [reference pack](../knowledge/data/reference-packs/vincent-irises-v1.json) follows v3's `VincentReferencePack` definition. C owns this versioned pack alongside the immutable Getty release. Register its manifest on both sides of the private boundary. The manifest binds pack version to a pinned SHA-256 digest and lists canonical evidence records, approved source URLs, source author or institution, attribution, language/edition, rights, locator and the supported passage. An edition change requires a new pack version. No conversation silently adopts a changed pack. The client cannot submit arbitrary reference text, URLs, authors, rights or chunk IDs.

| Evidence kind | Appropriate use in Vincent mode |
| --- | --- |
| `artist_letter` | A verified letter supports a first-person paraphrase of what Vincent wrote. Attribute quotes to the author and exact edition. Start with [letter 772 to Theo and Jo](https://vangoghletters.org/vg/letters/let772/letter.html). |
| `museum_record` | The existing Getty record supports artwork facts. A separate Getty conservation record supports later findings and names Getty as the source. Give it its own title, URL, locator and verified rights. See [Getty’s research on *Irises*](https://www.getty.edu/projects/van-goghs-irises-a-closer-look/). |
| `guide_note` | A MuseBridge note can invite the visitor to follow a leaf or look at the white bloom. It cannot establish the artist's private intention. |
| Catalog citation | Keep the existing typed field/value record and provenance for title, artist and displayed date. A catalog citation is separate from excerpt evidence. |

**Retrieval boundary.** Keep `evidence-tool-v1` and the current four-field private gallery retrieval request. Bind a resolver to the validated run scope, outside model-authored query arguments. In guide mode search the existing gallery only. In Vincent mode the same logical tool also searches the local versioned artist-reference passages, alongside eligible gallery passages. Rank candidates for each English query, deduplicate by canonical record identity and return at most the existing `top_k=2` total per query. Count the logical query once and record the actual strategy in execution metadata. Distinguish a lookup error from an empty candidate set. No runtime web lookup is required.

Pack chunk/source IDs must be unique against the base gallery and its reserved catalog namespace. For this reference pack, the v3 Evidence schema pairs `CC-BY-NC-SA-4.0` with `https://creativecommons.org/licenses/by-nc-sa/4.0/` for the English letters and the Getty/Smarthistory transcript. The UI must preserve this specific credit and license link. The trusted source URLs are the four exact URLs in the pinned manifest. Both services load the same UTF-8 JSON manifest with exactly `reference_pack_version`, `artwork_id`, `dataset_version` and `records` (an array of complete, explicitly typed v3 `Evidence` objects). A companion SHA-256 file pins the manifest bytes. The source title, locator and attribution identify the author or institution, date and edition when relevant. Store an English quotation only with verified rights for that edition; check the modern translation separately from the historical manuscript. Resolve every citation field against the pinned record. Extend both validators’ source and rights policies for these reviewed records. A guide run cannot see a pack-only museum record or letter.

B uses the evidence kind for the source-card heading: **Artist’s letter**, **Museum source** or **MuseBridge guide**. Catalog facts keep their existing field/value display. Show the excerpt exactly as stored, its language, source title, locator, credit, rights and original HTTPS link. An attributed paraphrase in the spoken answer stays distinct from an exact quotation on the card. A researcher’s interpretation or a visitor’s observation cannot become first-person historical testimony in an answer or in memory.

The role speaks in natural English to one visitor. An observation can get a direct response. A concrete historical claim needs eligible evidence, including evidence reused from the validated cache. Where a detail cannot be established, acknowledge the specific gap and continue the conversation. The public answer body is model-authored. Screens, speech and replay all read that one saved body; source labels and URLs are displayed separately.

## 6. Acceptance and handoff

| Check | Expected result |
| --- | --- |
| Mode matrix | Open guide and Vincent for the same visitor/work, then refresh or go Back. Each restores its own UUID, history and revision. A new visitor cannot use either ID. An unsupported artwork cannot create Vincent. |
| Existing clients | v0.3 exact create still succeeds; adding mode under v0.3 fails. Fixture and the other gallery pages, QR codes and guide flows continue to work. |
| Two genuine turns | Ask what color the irises were, then ask “Why do they look blue to me now?” Resolve the referent from Vincent history. Distinguish the artist's description from modern conservation findings; show the appropriate sources. |
| Attribution and unsupported intent | Discuss a visitor’s interpretation of the white flower. A follow-up must preserve whose idea it was. Inject a foreign or altered source in a controlled test; validation fails without a new revision. |
| Language and scope | Vincent answers in English, including after a conflicting in-text request. Switch to the normal guide and confirm its own language preference and evidence remain intact. |
| Lost response and media outage | Recover a committed turn by its original message ID, then retry audio. The turn, citations and revision are identical; no new Qwen call. The [phone checklist](vincent-voice.md) covers real capture, playback, denial, stop and replay. |
| Handoff | A verifies transport, error handling and true-device audio; B verifies navigation, source cards and real playback states; C verifies authentic Qwen answers, source attribution and semantic memory with unseen wording. Record observed acceptance separately from the synthetic wire examples. |
