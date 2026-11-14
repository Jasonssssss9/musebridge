# Artwork Conversation Skill

Skill ID: `artwork_qa`. Current capability: `artwork-qa-v2`, visitor-policy draft
revision 2. Workflow: `museum_qa_v2`. Prompt package: `museum-prompts-v2`.
Private contract: `agent-run-v2`. Schema: `agent-schema-v2`.

This capability provides bounded, source-aware conversation about the artwork
selected by the application. It combines a LangGraph workflow, versioned prompt
policies, strict model decisions, the existing evidence tool and deterministic
validation. This README documents the capability; it is not automatically loaded
as a model prompt or dynamically discovered as a callable tool.

The v2 workflow is an explicitly selected C-owned implementation. Its controlled
checks use a scripted model with real HTTP retrieval; they do not establish real
model quality, production provider readiness or a complete browser-to-Agent
integration. Go remains the owner of committed conversation state.

## Supported interactions

- English, Spanish, Simplified Chinese and Korean conversation, with English
  retrieval queries and unchanged original source records.
- Up to five completed history pairs plus the current message and bounded
  semantic conversation state; exact old wording is not guaranteed by a summary.
- Direct appreciation, general art discussion and attributed subjective reactions.
- Direct title, listed artist and displayed-date answers from canonical catalog
  facts, with catalog provenance separate from museum excerpt citations.
- Source-backed answers using fresh retrieval or validated cached evidence.
- A partial answer plus one focused local clarification, retaining only the
  unresolved portion for the next turn.
- Persistent language settings, temporary response languages and one-off content
  translations with an exact source binding. Translation alone does not change
  the saved preference.
- Specific evidence gaps with useful continuation, conversational corrections and
  retained explicit expression preferences.
- At most one bounded retrieval refinement when the remaining source scope,
  query identity, call capacity and deadline permit it.

Specific artwork claims require the appropriate released source. General art
knowledge, visitor impressions, history, summaries and translated visitor claims
are not independent museum evidence. Do not infer an artist's motive or mental
state from a viewer's reaction. The skill cannot inspect an image, browse the
web, compare other artworks, invent historical stories or reset a conversation.

## Operations and assets

Every operation includes `core_policy.md`, `memory_policy.md`, the operation
policy and the self-contained output schema. Runtime data is one detached
JSON-encoded message; it is not interpolated into policy.

| Operation | Additional assets under `agent/prompts/v2/` | Output |
| --- | --- | --- |
| plan | `interpret_and_plan.md` | Plan with one permitted route and frozen language decision |
| assess | `assess_and_respond.md` | AssessmentDecision when refinement is permitted; otherwise FinalDecision |
| final | `assess_and_respond.md`, `final_response.md` | FinalDecision; no further retrieval |

Planning can finish a direct, clarification or scope response, so it also receives
memory instructions. Assessment normally assesses evidence and writes the answer
in the same call. Final is needed only after refinement; deterministic answer
assembly does not add a polishing call.

## Tool and function boundaries

The sole external tool is `search_artwork_evidence`. Runtime injects artwork,
release and result limit, and calls the configured private retrieval service.
The model supplies only validated English queries. Canonical evidence validation
rejects foreign works, altered records and invented sources before use.

Catalog projection is a deterministic local function over the pinned seed, not a
new search tool or corpus. Catalog facts never count toward retrieval completeness
or enter the cached retrieval evidence. Other functions validate context, language
calculation, exact translation sources, query links, local pending bindings,
source membership, reply assembly and budgets.

The model returns structured choices; workflow code dispatches allowed actions.
There is no generic ReAct loop, arbitrary code execution or model-controlled
endpoint selection. Scope, call limits and cancellation remain code-enforced.

## State and handoff

Each run starts with an immutable application snapshot. Model context is a
selected projection; LangGraph state lasts one run. ConversationState belongs to
Go across successful turns. C returns a validated memory proposal and base
revision; it does not write a second conversation store or independently commit
language, summary or evidence updates.

The test-only `simulate_commit` helper creates a next snapshot for multi-turn
checks. It is not B's atomic commit, admission, replay, expiry or concurrency
implementation. A failed workflow must not yield a successful memory update.

See the [current design](../../../docs/agent-design.md),
[interface contract](../../../docs/agent-api-contract.md),
[visitor acceptance plan](../../../docs/visitor-acceptance.md) and
[C handoff](../../README.md) for authoritative integration and evaluation rules.

## Preserved v1 implementation

The original `museum_qa_v1` workflow, `museum-prompts-v1` assets and
`agent-schema-v1` remain available for explicit v1 checks. They retain English/
Chinese, three historical pairs, the old evidence-only contract and disabled
refinement. The v1 runtime prompt files remain under `agent/prompts/`; v2 assets
are under `agent/prompts/v2/`. Do not pass a v1 request or schema to v2 implicitly.
The [archived specification](../../../docs/archive/agent-design-v1.md) records
those historical behavior rules.

Neither scripted decisions nor source membership prove semantic entailment,
faithful rewriting, language quality or real latency. These require the separate
real-model and physical-phone acceptance gates. Version prompt changes and
retain independent held-out evaluation; never put gold labels or expected answers
into visitor model context.
