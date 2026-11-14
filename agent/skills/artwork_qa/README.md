# Artwork Question Answering

Skill ID: `artwork_qa`. Skill version: `artwork-qa-v1`.
Prompt version: `museum-prompts-v1`.
Workflow: `museum_qa_v1`. Contract: `agent-run-v1`.

This skill answers questions about the conversation's current artwork using the
released museum evidence. The backend fixes the artwork, source release, original
question and answer language before the workflow starts. The model cannot change
those values. Questions and answers may use English or Simplified Chinese;
retrieval queries use English and source excerpts remain unchanged.

## Supported interactions

- Interpret a direct question or a follow-up using the active artwork and up to
  three completed conversation turns.
- Separate independently answerable requests while preserving their original
  fragments, specificity, causal intent and scope.
- Return grounded answers, explicit evidence gaps, partial answers, focused
  clarification or guidance about the current artwork's scope.

History is reference context, never museum evidence. Catalog title and artist
identify the subject but do not add citable facts. The skill does not inspect
images, browse the web, answer from model memory or compare multiple artworks.

## Operations and assets

| Operation | Policy files under `agent/prompts/` | Output schema |
| --- | --- | --- |
| `plan` | `core_policy.md`, `interpret_and_plan.md` | `Plan` |
| `assess` | `core_policy.md`, `assess_and_respond.md` | `AssessmentDecision`, or `FinalDecision` when refinement is disabled |
| `final` | `core_policy.md`, `assess_and_respond.md`, `final_response.md` | `FinalDecision` |

The normative schema is `docs/contracts/agent-v1.schema.json`. Runtime inputs use
`PlanInput`, `AnswerInput` and `FinalInput`. The prompt builder puts static policy
and the output schema in the system message, with one JSON-encoded user data
message. It never evaluates templates containing visitor text or evidence.

The only external tool is `search_artwork_evidence`. It searches the server-owned
artwork and release; the model supplies only validated queries. The adapter
validates returned sources before accepting them into the run snapshot. There
is no arbitrary URL, filesystem, shell or Python tool.

## M1 execution

The C M1 workflow performs planning, real retrieval, evidence validation and
assessment through injected provider/tool ports. A fake model verifies branches
without downloading weights or calling a real model. Refinement is disabled;
assessment receives `allow_refinement: false` and `FinalDecision`. A future
refinement branch must implement the approved call limits and acceptance gates
before it can be enabled.

A finish result maps each planned request to its own support status, concise
text and current-run citation IDs. Runtime code derives public status, joins
request texts and resolves citations from accepted evidence. Missing support
must remain explicit; a related passage does not establish the requested fact.
Technical failures remain failures and are never turned into empty search
results or unsupported-answer text.

## Context and verification

Context preparation preserves the original question and requested answer
language, projects only the documented fields, and rejects inconsistent history.
Sizing is a runtime-injected policy for the pinned model and chat template.
Oldest whole turns may be dropped; questions, plans and accepted excerpts may
not be truncated. Required inputs that still do not fit cause
`agent_context_limit`. The fake sizer in tests is not a production tokenizer.

Context and prompt tests cover immutable snapshots, language, history order and
mode/status constraints, complete-turn trimming and literal handling of hostile
text. Workflow tests verify supported, partial, insufficient, clarification and
scope branches with real seed retrieval. Model quality and deployment readiness
remain later gates; passing fake-model tests does not establish either.

Behavior changes require versioned prompts and reviewed evaluation cases. Do not
embed expected answers, test labels, evidence topic whitelists or secrets in
model inputs. No chain-of-thought transcript is requested or retained.
