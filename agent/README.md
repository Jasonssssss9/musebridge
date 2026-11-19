# MuseBridge Agent: C v2 workflow

Status: **C's v2 workflow is implemented and verified with a scripted model and
real HTTP retrieval. Full application integration and real-model acceptance
remain open.**

This package implements C's Agent intelligence and evidence responsibilities from
the [v2 design](../docs/agent-design.md), [interface contract](../docs/agent-api-contract.md)
and [team plan](../docs/agent-plan.md), including visitor-policy draft revision 2.
The v2 entry point is explicitly selected; the existing v1 workflow, prompt
assets, contract and tests remain available. The [v1 handoff](README-v1.md)
describes that preserved implementation.

B's [private run service](../docs/agent-service.md) now wraps this workflow with
FastAPI endpoints, authentication, replay, run budgets and capacity management.
The [internal Go state store](../docs/agent-state-store.md) supplies revision-aware
admission and atomic saving. An [opt-in Ollama provider](../docs/ollama-provider.md)
now implements real transport and token budgets, with readiness still blocked by
real workflow acceptance failures. The Go Agent client and public v2 activation
remain pending. C returns a validated result and a proposed
memory update against the input revision.

## 1. Implemented workflow

```text
RunRequest v2
  -> validate scope, catalog, conversation state, five-pair history and cache
  -> build bounded context and request a structured plan
  -> validate language, translation source, requests and query links
      -> direct_response / clarify / out_of_scope -> validate terminal result
      -> answer_from_context -> assess validated cached evidence
      -> retrieve -> real HTTP lookups -> validate and merge evidence -> assess
          -> finish / clarify -> validate terminal result
          -> permitted refine -> one lookup round -> final model operation
  -> derive answer status, resolve citations and bind any local clarification
  -> return detached result fields and base-revision memory proposal
```

The five permitted planner actions are direct_response, retrieve,
answer_from_context, clarify and out_of_scope. Direct conversation and catalog
answers use one model call and no retrieval. Retrieval/reuse normally uses two
model calls; the one refinement path uses three. Deterministic finalization does
not add a polishing call.

The implementation supports the v2 protocol for English, Spanish, Simplified
Chinese and Korean. Original messages/history are retained; only search queries
use English. A scripted provider verifies field handling and routing, not an
actual model's language or query-rewriting ability.

Key visitor behaviors are represented in the workflow:

- Normal appreciation and general art conversation without mandatory retrieval.
- Title, listed artist and displayed-date answers from separate canonical catalog
  records, preserving ranges rather than inventing exact dates.
- Source-backed replies using fresh retrieval or previously validated evidence.
- A supported answer alongside one local clarification, with pending memory
  focused on the unresolved request rather than the whole assembled reply.
- Persistent language settings, temporary reply language and one-off translation
  as separate decisions. Translation binds exact supplied or retained text and
  pins it through later context fitting; a summary is not an exact source.
- Explicit semantic memory proposals for topics, summaries, expression preferences
  and pending questions, with application-owned revisions and persistence.
- Bounded refinement only for an unresolved clear artwork-fact request, never for
  catalog data, unclear referents or an already complete retrieval scope.

Subjective interpretation, useful replies when evidence is absent and correction
of misunderstandings are specified in the v2 prompts and semantic fixtures. Their
quality still requires real-model evaluation. Code does not claim to prove every
natural-language assertion is supported merely because its citation is valid.

## 2. Local checks and controlled demos

Run from the repository root in WSL. Python 3.12 is the tested environment. The
existing pinned Agent dependencies are reused; no model weights or GPU setup are
needed for controlled checks.

```bash
cd /home/jason/projects/musebridge
.venv/bin/python -m unittest discover -s agent/tests -v
python3 -m unittest discover -s knowledge/tests -v
.venv/bin/python -m pip check
```

If creating the environment for the first time:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r agent/requirements.txt
```

The v2 demo starts the existing retrieval HTTP server on an ephemeral loopback
port, runs the same workflow and shuts the server down. Its result is explicitly
labeled mock-model-real-retrieval. It emits controlled decisions, not a response
from an installed or remotely connected LLM.

```bash
.venv/bin/python -m agent.testing.demo_v2 --scenario medium
.venv/bin/python -m agent.testing.demo_v2 --scenario dialogue
.venv/bin/python -m agent.testing.demo_v2 --scenario catalog
.venv/bin/python -m agent.testing.demo_v2 --scenario partial
.venv/bin/python -m agent.testing.demo_v2 --scenario reuse
.venv/bin/python -m agent.testing.demo_v2 --scenario refine
.venv/bin/python -m agent.testing.demo_v2 --scenario translation
.venv/bin/python -m agent.testing.demo_v2 --artwork-id aic-11723 --scenario medium
```

Each scenario accepts aic-28560 or aic-11723. All fourteen artwork/scenario smoke
combinations passed in this implementation. These are fixed scenarios, not a
general visitor-question CLI or a measured LLM demonstration. The original
`python -m agent.testing.demo` remains the v1 diagnostic path.

### Evaluate compound questions and evidence gaps with Qwen

From the repository root, use the tokenizer and model endpoint described in the
[Ollama guide](../docs/ollama-provider.md). For example:

```bash
.venv/bin/python -B -m agent.testing.stage1 \
  --suite development --base-url http://127.0.0.1:11434 \
  --tokenizer ../local-model-assets/qwen35-tokenizer.json \
  --report ../local-model-results/stage1-development.json
```

Use a new private report path for each run. Each case goes through the v2
workflow and real HTTP retrieval. The runner checks the overall status, response
language field, ordered support decisions and exact citation IDs. Read the
replies against the cited excerpts, then read the proposed English memory:
a missing fact must stay open and must not become a claim about the museum's
entire collection. Raw model decisions and lookup results are in the report.
The runner never issues a capability record.

The first additional set (`--suite fresh`) is now regression material. The
subsequent `--suite confirmation` set was reserved for the final comparison.
All three sets are available for future regression runs. A new claim about
unseen visitor questions requires separately authored examples.

On the pinned `qwen3.5:9b-q4_K_M` configuration, the first stage produced
the following results. Each cell shows baseline (`b973064`) → final candidate.
Full review includes the visitor-facing answer and proposed conversation memory.

| Test set | Status, language field and citations | Full source and language review |
| --- | --- | --- |
| Development (6) | 2 → 5 | 1 → 4 |
| Additional regression (6) | 4 → 6 | 3 → 4 |
| Independent confirmation (6) | 3 → 6 | 1 → 2 |

Across the three sets, the retriever returned all required evidence for 14 of
14 questions with supported facts, up from 9 of 14. Five of six independent
confirmation answers passed the visitor-facing review. Three of those five
still proposed a memory that overstated what the museum knows. These results
describe one run of each set on this model configuration.

### Check answer wording and conversation memory independently

For the scope and uncertainty follow-up, repeat the three former suites as
regressions. Run the first eight additional cases with the same command, replacing
the suite arguments with
`--suite confirmation --case-file agent/testing/stage1_scope_holdout.json`,
and choose a fresh report path. These eight cases became regressions after
their first independent run. For the six questions reserved until the last
implementation freeze, use `agent/testing/stage1_scope_independent.json` with
the same suite argument. Both files include source-based grading criteria.
Review the actual reply and the memory proposed for the next turn separately. Count a run that returns neither as a failure in both
columns. Report machine checks separately.

For supported answers, the prompt can draw on short translations of the
accepted English excerpts. The wording guide distinguishes physical features
from the possible effect of a picture and from what marks suggest. A new or
edited excerpt uses the ordinary evidence path until corresponding translations
are available. The canonical source remains the citation for every claim.

When a factual part is marked insufficient, the answer identifies that part and
says what the guide could verify in this turn. It quotes the visitor's own
fragment when suitable. Across reply languages it refers to the part by
position. The model still writes the supported and conversational parts.

For a turn made up entirely of clear factual requests, the workflow records the
question and its support decision after validating the answer. Answered questions
carry their source IDs; the exact wording, including any qualifier, stays in the
verified evidence or catalog. An open question describes what was unverified in
that turn. The handoff keeps a previous complete note when it fits the existing
memory budget. Other kinds of turns and pinned translations continue to use the
model's semantic memory. The simulator can apply this update to the next
request without starting the public API or a persistent database.

A Qwen run on the frozen implementation produced the following results. The
review used the same source-based rubric for each case and counted a missing
reply against both scores.

| Set | Replies delivered | Visitor-facing answer passed | Next-turn memory passed |
| --- | ---: | ---: | ---: |
| Previous implementation, original 18 | 17/18 | 14/18 | 13/18 |
| Current implementation, same 18 | 18/18 | 18/18 | 18/18 |
| First eight new questions, before reuse | 7/8 | 5/8 | 6/8 |
| Same eight, used for regression | 8/8 | 7/8 | 7/8 |
| Final six, held out until the last freeze | 5/6 | 4/6 | 5/6 |

One regression still combined a question about a depicted pattern with a
question about a surviving fabric sample. In the last six cases, the planner
changed punctuation in a Spanish quotation of the visitor's words and failed
literal-fragment validation. An English compound query missed the supported
description of thick marks. That answer omitted an available fact, while its
memory correctly left the question open for that turn.

Two separate two-turn conversations used actual first-turn replies and
simulated state commits. Both first turns delivered locally scoped answers and
memory. One missed available evidence. Both follow-ups failed plan validation
before an answer or new memory was produced. This run does not establish
multi-turn readiness.

## 3. Package map

| Location | Responsibility |
| --- | --- |
| `workflows/museum_qa_v2.py` | Five routes, per-run state, bounded refinement and detached outcome |
| `workflows/ports.py` | Existing shared provider/profile/budget/tool interfaces; unchanged ownership |
| `runtime/context_v2.py` | V2 state/history checks, model projections and whole-pair context fitting |
| `runtime/validation_v2.py` | V2 schema and semantic relationships, language/translation, pending binding and answer assembly |
| `prompts/v2/` | Versioned common, memory and operation policies plus the JSON message builder |
| `skills/artwork_qa/README.md` | Capability, limits, assets and preserved-v1 distinction |
| `tools/catalog.py` | Deterministic catalog facts from the same verified release snapshot |
| `tools/evidence.py` | Immutable canonical retrieval records and associated released source metadata |
| `tools/retrieval.py` | Existing read-only real HTTP retrieval adapter |
| `testing/v2.py` | Scripted v2 model, synthetic profile, fixture helpers and test-only next-state simulation |
| `testing/demo_v2.py` | Controlled v2 smoke scenarios with real retrieval |
| `tests/` | V1 regression plus v2 contract, context, source, prompt and workflow checks |

Versions are museum_qa_v2, museum-prompts-v2, agent-schema-v2, artwork-qa-v2,
evidence-tool-v1 and legacy-facet-v0.1. Conversation snapshots use
conversation-state-v1. The shared v2 schema/examples identify draft revision 2.
No competing wire-model package is added under B's `agent/contracts/`.

## 4. Integration handoff to B

### Workflow entry point

Construct one reusable workflow with the production dependencies supplied by B:

```python
from agent.tools.evidence import EvidenceManifest
from agent.tools.retrieval import RetrievalTool
from agent.workflows.museum_qa_v2 import MuseumQAWorkflowV2

manifest = EvidenceManifest.load()
workflow = MuseumQAWorkflowV2(
    model=structured_provider,
    profile=verified_profile,
    prompt_sizer=verified_prompt_sizer,
    manifest=manifest,
    retrieval=RetrievalTool(manifest, base_url=configured_retrieval_origin),
)
outcome = await workflow.run(
    validated_run_request,
    request_id=validated_trace_id,
    budget=admitted_run_budget,
)
```

The request is a JSON-compatible dictionary matching RunRequest in the
[v2 schema](../docs/contracts/agent-v2.schema.json). Go supplies the immutable
conversation snapshot, canonical catalog and exactly the latest min(5, revision)
complete pairs. C independently checks shape, scope, source identity, history
relationships and the whole cached evidence set before any model call.

B owns authentication, strict inbound HTTP decoding, contract selection, history
authenticity, run admission, replay, cancellation ownership, fixed conversation
expiry and conversion from UTC admission time to one monotonic run deadline.
A valid-shaped historical fixture deadline is not an admission test.

The injected budget still exposes check_active() and remaining_seconds(). C checks
it before and after external work and before returning. B must actively cancel
expired runs; C's checks do not replace the runtime timer. Cancellation propagates
without becoming an insufficient-evidence reply or a successful memory update.

### Structured model interface

The unchanged port is:

```python
completion = await model.complete_structured(
    operation=operation,  # plan, assess or final
    messages=messages,
    output_schema=output_schema,
    profile=verified_profile,
    deadline=absolute_monotonic_deadline,
    trace_context={"request_id": request_id},
)
```

The output schema is self-contained with reachable definitions. Planning uses
Plan. Assessment uses AssessmentDecision only when refinement is permitted;
otherwise it uses FinalDecision. Final always uses FinalDecision. C reads a
detached completion.data and independently validates its shape and relationships.

B owns the real HTTP provider, model identity, shared inference slot, strict outer
and inner JSON decoding, bounded reads, finish/truncation checks, usage and timing.
Failures raise the shared WorkflowError. There are no silent retries, format
repair calls or fallback provider. Do not import ScriptedModel, FixtureProfile,
fixture_prompt_sizer or simulated commits into production handlers.

The v2 starting profile is 16,384 context tokens, 2,048 planning output tokens,
2,048 answer output tokens and a 512-token safety reserve. These are design
values to verify on the actual model. C uses the injected tokenizer/template-aware
sizer; the fixed test sizer is not a production token estimate. Required context,
catalog, evidence and any pinned translation source are never silently truncated.
Only the oldest whole historical pairs may be removed. Required input that still
does not fit fails with agent_context_limit.

Model stage caps are ten seconds for planning and fifteen seconds for assessment
or final generation, bounded by the parent run. The tool retains its three-second
complete-call cap. Provider slot wait, transport, reading and decoding all count
inside the supplied deadline.

### Outcome and execution record

WorkflowOutcomeV2.result_fields() returns detached copies of:

- scope, answer_language and the frozen language_decision;
- answer, requests and request_results;
- evidence_snapshot, catalog_snapshot and reused_chunk_ids;
- executed_queries;
- conversation_update with base_revision and the validated semantic memory proposal.

These are the C-owned RunResult fields. WorkflowOutcomeV2.execution_fields()
returns workflow/prompt/schema/retrieval versions and actual model/retrieval/
refinement counts. B adds the verified profile_id, model_provider, model_name,
model_digest and measured total duration_ms, validates the full RunResult and
wraps the run snapshot. A scripted diagnostic does not invent an Ollama identity
merely to look like a production response.

Go independently validates exposed result relationships and decides whether the
active attempt can commit against the unchanged base revision before its deadline.
Go atomically stores the turn, state and replay result. C never advances the
conversation itself. Technical failure, cancellation, stale state and rejected
late results must leave committed history and memory unchanged.

The `agent.testing.v2.simulate_commit()` helper derives a next state/history pair
only for controlled multi-turn C tests. It has no admission, locking, replay,
expiry, persistence or HTTP behavior and must not be used as a production store.

## 5. Evidence, catalog and refinement rules

The original release remains knowledge/data/aic-seed-v0.1.json, with SHA-256:

```text
3703fd4c9fcefb8e9fba20f23a5cb70e612c62ef7cb3f594a15d319ebc18d6ac
```

EvidenceManifest verifies one byte snapshot and retains immutable artwork,
source and evidence projections. Catalog facts are derived from that same checked
snapshot, not from a later live museum fetch. Their IDs, exact field values,
source links, released-seed locators and normalization labels are canonical.
Catalog citations are not invented quotations and never enter the retrieval
cache or count toward retrieval completeness.

The existing HTTP tool and retrieval algorithm remain unchanged. Runtime injects
artwork_id, dataset_version and top_k=2. The model cannot choose another scope or
endpoint. Tool/source validation rejects wrong releases, foreign works, altered
canonical records, malformed responses and invalid identities. Every cached
retrieval record is checked before use; invalid cache does not produce a partial
repair or an answer from unverified memory.

The current scope contains two retrievable records per artwork and three catalog
facts. Completeness is determined only from the retrievable manifest IDs. A
complete cache routes clear artwork facts through evidence reuse, while catalog
questions still use direct_response. If a lookup completes the scoped union,
remaining lookups in that planned batch stop; only executed queries/counts appear
in the outcome.

There are at most two initial queries and one refinement round of at most two
queries: four retrieval calls and three model calls maximum. Before assessment,
refinement requires at least 38 seconds remaining and incomplete retrieval scope.
After its decision, the workflow rechecks the actual lookup cost plus fifteen
seconds for final generation and two seconds for completion. Query IDs and
normalized texts remain unique; refinement preserves original request IDs and
intent. An invalid or disallowed refine decision fails validation rather than
starting another loop.

Accepted source provenance is deterministic. Whether a passage supports every
claim, a query preserves the visitor's meaning or a translation is faithful still
requires the real-model evaluation defined in the design.

## 6. Verification boundaries and remaining work

The controlled suites cover v2 shapes and relationships, four-language protocol
handling, catalog and retrieval source isolation, exact translation bindings,
five-turn context, local clarification, memory proposals, direct/reuse/retrieval
routes, bounded refinement, time budgets, malformed outputs and cancellation.
Real HTTP smoke scenarios exercise both artwork scopes. Existing v1 Agent and
knowledge regressions remain part of the checks.

Recorded verification at C's v2 checkpoint, before B's boundary/service additions:

| Check | Result |
| --- | --- |
| Agent unit/integration suite | 164 passed: 83 preserved v1 tests and 81 new v2 tests |
| Knowledge regression suite | 139 passed |
| Dependency consistency | pip check passed |
| Real-retrieval controlled smoke | All 14 combinations passed: seven scenarios across both artworks |

The [private service guide](../docs/agent-service.md#verification-and-remaining-work)
records the later B phase-3 checks and current aggregate suite totals.

These checks use predetermined model decisions. They do not establish actual
language detection, reference resolution, query quality, semantic support,
subjective-discussion quality, translation quality, real token sizing, inference
latency or resistance to prompt injection by an LLM.

Remaining integration belongs to the shared plan:

- B has implemented the private run service/lifecycle and internal Go state
  store and the opt-in provider/profile/sizer. Real workflow readiness and Go
  Agent/public HTTP integration remain to be accepted.
- A supplies v2 browser behavior, source rendering, language/recovery handling and
  independent visitor scripts.
- C integrates the real provider and measures the workflow on independently
  labeled multilingual and multi-turn cases, diagnosing semantic failures.
- The team executes the [visitor journeys](../docs/visitor-acceptance.md), full
  application checks and physical-phone acceptance before claiming readiness.

Routine diagnostics are counts, versions, phase timings and retained history size.
Full messages, queries, summaries and source payloads belong only in deliberate
synthetic evaluation artifacts, not routine logs. No LangGraph checkpoint owns
business history; ambient tracing remains suppressed and raw debug/verbose
logging must be disabled.
