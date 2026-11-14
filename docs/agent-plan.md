# MuseBridge Agent Implementation Plan

Status: **C's v1 M1 foundation is implemented. The v2 conversation target adds pending interface and runtime work; team M1 integration and acceptance remain open.**

This plan defines three milestones and the new A/B/C responsibilities. It follows the [Agent architecture](agent-design.md) and [v2 interface contract](agent-api-contract.md), including protocols, call limits and [release gates](agent-design.md#13-evaluation-and-release-gates). It supersedes the earlier personal assignments for new Agent work. The [fixture-stage plan](plan.md) remains a historical reference.

**C M1 v1 implementation checkpoint:** the LangGraph foundation, context and prompt assets, real retrieval adapter, release-manifest validation and scripted-model tests are implemented. See the [C M1 handoff and local checks](../agent/README.md). This checkpoint remains valid for v1. The v2 target additionally requires four languages, optional retrieval/direct dialogue, five completed history pairs and application-owned ConversationState. Adaptation to v2 and its tests are pending; do not report the existing v1 tests as v2 acceptance. The full M1 gate remains open until A/B deliverables, production interfaces and cross-service checks below are verified.

The [visitor acceptance plan](visitor-acceptance.md) defines published journey families, multilingual coverage, reviewer expectations and completion records. These are designed cases, not measured results or an independent held-out set.

The architecture and contract examples are already documented. The current checkout still runs fixture generation. Reuse the existing frontend, Go conversation lifecycle, source release and retrieval tests rather than rebuilding them.

## 1. Milestones

| Milestone | Outcome | Main dependencies |
| --- | --- | --- |
| M1. Establish the Agent foundation | Compatible interfaces, a working service/workflow skeleton and verified model access | Existing architecture and contracts |
| M2. Complete and validate the Agent | Full bounded workflow, integrated application behavior and measured answer quality | M1 service, tool and provider interfaces |
| M3. Deploy and complete acceptance | A repeatable phone experience and complete delivery evidence | M2 acceptance and the chosen deployment |

These are acceptance stages, not three sequential work queues. A, B and C work in parallel against shared interfaces. Assign calendar dates after checking team availability and the submission deadline.

### M1. Establish the Agent foundation

**Goal:** prove that the components can work together before tuning model behavior.

| Role | Deliverable |
| --- | --- |
| A | Update frontend types, parsing and mocks for conversational/model answers, partial answers, clarification, scope guidance, four language choices plus Auto, and revision-aware submissions. Prepare the visitor journeys, source display mocks and independently authored held-out questions; cover partial answers with clarification and temporary translation tasks. |
| B | Implement v2 wire types, Go's Agent client, private FastAPI v2 run endpoints, service authentication, run identity, revision snapshots and the provider interface. Supply the released catalog projections and verify the actual model endpoint, structured output, model identity and basic timing. |
| C | Adapt the completed v1 LangGraph foundation to v2 context/decision contracts, five historical pairs and explicit dialogue/reuse routes. Define local clarification and terminal memory proposals, metadata grounding, translation semantics and subjective-discussion policy while preserving the real retrieval adapter and manifest checks. |

**M1 is complete when:**

- Shared v2 shape and runtime relationship examples pass in the relevant consumers; semantic fixture expectations have separate model evaluation.
- A contract-test request crosses Go -> Agent -> the real retrieval service and returns a valid result using a controlled model substitute.
- Both artwork scopes are tested; a malformed or foreign evidence record is rejected.
- A can render every new answer state, mixed answer/clarification, catalog provenance and language/revision metadata from mocks without waiting for real generation.
- Shared examples distinguish metadata facts from descriptive evidence, task-specific translation from persistent settings, and visitor interpretation from artwork facts. The four priority journeys have reviewed expected outcomes before model testing.
- The actual model route, identity, four-language structured-output probes, v2 context/output sizing and basic timing are recorded. A fake-model test is not presented as evidence of real model quality.
- B and C have one agreed provider interface, one runtime budget implementation and one tool interface.

The model service is prepared on the teammate's host or another approved inference host. C does not need to host a model locally.

### M2. Complete and validate the Agent

**Goal:** deliver the complete AI behavior and integrate it with the existing application.

| Role | Deliverable |
| --- | --- |
| A | Connect the browser to the updated application API. Verify response labels, citations, all four languages, language switching, normal dialogue, clarification replies, waiting, state-aware refresh and artwork switching. Run browser recovery checks and the visitor acceptance journeys; report the deferred reset limitation honestly. |
| B | Complete Go result validation and atomic turn/ConversationState saving, expected-revision conflicts, Agent run replay/reconciliation, deadline/capacity handling and provider failures. Align application, Agent, browser and proxy budgets. |
| C | Complete language-aware routing, faithful English query generation, direct conversation, support assessment, useful gap replies, validated evidence reuse, semantic memory updates, local clarification, catalog facts, task-specific translations, subjective interpretation, one bounded refinement and mandatory stopping. Run the shared workflow through the evaluation harness and analyze failures. |

**M2 is complete when:**

- A real request travels through browser -> Go -> Agent -> retrieval/model -> Go -> browser for both artworks.
- Direct dialogue/early clarification uses one model call and no retrieval; evidence retrieval/reuse uses two; refinement uses at most three.
- Four-language contextual and mixed social/factual messages, missing evidence and scope guidance follow the specification. Five-turn history, pending clarification, automatic fallback and one-turn language overrides retain their defined meaning.
- Query refinement preserves the original intent and stops when the complete scoped release is already available.
- Unknown citations, wrong releases, foreign evidence, invalid model output and exceeded budgets cannot become successful answers.
- Duplicate submissions, lost responses, stale retries and late results do not create duplicate turns or advance/overwrite conversation state.
- The independent model-quality evaluation meets the specification's thresholds, with actual counts, failures and versions recorded.
- The four priority visitor policies pass their designated cases in every supported language. All remaining visitor journey families have recorded outcomes, including corrections, style preferences, unavailable exact history, source-gap continuation, scope guidance, request/response limits and waiting/recovery. Deferred capabilities are not reported as passed.
- Existing fixture regression checks still pass.

Use a controlled integration environment while quality work is in progress. Promote Agent mode for normal use only after the release gates pass. If a held-out failure informs a change, retain it as regression data and obtain fresh held-out cases for the next generalization claim.

### M3. Deploy and complete acceptance

**Goal:** make the accepted system reproducible on the actual phone and deployment.

| Role | Deliverable |
| --- | --- |
| A | Run both artwork QR flows on a physical phone. Verify source links, all four answer languages, mid-conversation language changes, five-turn follow-ups and interrupted-request recovery. Execute the visitor acceptance plan on the actual deployed phone path, recording perceived waiting as well as timing. Coordinate the final walkthrough and video assembly. |
| B | Verify the Agent-to-model route from the deployed environment, including containers where used. Finalize startup, configuration, warm-up and troubleshooting instructions. Record model/GPU identity and deployment logs. |
| C | Run the final evaluation and regression checks against the release candidate. Publish the evidence-quality report, known limitations and workflow/prompt/schema versions. Prepare the Agent explanation for the project report. |

**M3 is complete when:**

- A physical phone completes both artwork journeys and the applicable visitor acceptance scenarios; a desktop mobile viewport alone does not close this item.
- Visitor outcomes distinguish implemented behavior, known failures and deferred capabilities. Published design scenarios do not count as blind tests, and successful HTTP responses do not by themselves establish useful conversation.
- Another teammate can start the system and reproduce the checks using the documented instructions.
- Model outages and interrupted requests produce the agreed errors and recovery behavior.
- Final evaluation, deployment and phone results identify the tested commit and configuration.
- GitHub changes, test/evaluation logs and each person's video contribution are ready.
- Any confirmed course requirements, including a hosted/cloud GPU run, have their own recorded evidence. A teammate-hosted local GPU run is not automatically equivalent.

## 2. A/B/C responsibilities

| Role | Primary responsibility | Main implementation areas |
| --- | --- | --- |
| **A — Frontend and visitor acceptance** | User-facing behavior, browser integration, independent visitor questions and physical-phone acceptance | `web/`, browser tests, phone checklist and user-facing delivery evidence |
| **B — Application and Agent infrastructure** | Go API, private Agent transport, run lifecycle, provider connectivity, model operations and deployment | `api/`, `agent/app.py`, `agent/contracts/`, `agent/runtime/runs.py`, `agent/runtime/budgets.py`, `agent/providers/`, Compose and deployment instructions |
| **C — Agent intelligence and evidence** | LangGraph workflow, context interpretation, prompts, tools, evidence support and semantic evaluation | `agent/workflows/`, `agent/skills/`, `agent/prompts/`, `agent/tools/`, `agent/runtime/context.py`, `agent/runtime/validation.py`, `knowledge/` and `eval/` |

The Agent remains one coherent Python module even though B and C contribute different layers. AI decisions stay in the Agent; B's infrastructure work must not move planning or model calls back into Go.

B's validation covers transport, admission, run identity and application persistence. C's validation covers plans, request/query links, evidence and model-answer consistency. Both use the shared contracts. C uses B's budget and provider interfaces rather than creating a second timeout or capacity system.

B coordinates wire-contract changes. C specifies model semantics and reviews the Agent/tool shapes; A reviews public answer and recovery changes. Keep schemas, examples and affected consumers synchronized.

### Shared acceptance work

- A authors held-out visitor questions early, using the coverage requirements in the architecture specification and the [visitor journey families](visitor-acceptance.md#3-visitor-journeys). Published journey scripts are development/regression material; held-out variants must be independently authored and frozen before C inspects them.
- B checks their expected evidence and outcomes against the released source material. Freeze these labels independently of planner output before C runs the held-out evaluation.
- C owns the evaluation runner and diagnostics. All three review failed or disputed cases against the original question and source, including conversational repair and the visitor's next step rather than only exact answer text.
- Use at least 16 eight-turn scripts, with the language distribution and journey coverage in the [visitor acceptance plan](visitor-acceptance.md#4-language-and-script-coverage). Preserve the existing 60-scenario, four-language single-turn held-out requirement; do not count related translations as independent scenarios.
- B leads system fault and concurrency checks; A verifies the resulting browser behavior; C checks that failures never become unsupported factual answers.
- Each person records their own contribution and relevant GitHub/test evidence. A assembles the final video; this does not make A responsible for explaining everyone else's technical work.

## 3. Handoffs and parallel work

| Producer | Consumer | Required handoff |
| --- | --- | --- |
| B | C | Provider interface, verified model profile, run input and shared deadline/capacity utilities |
| C | B | Workflow entry point and terminal result contract; versioned prompts, tool behavior and validation expectations |
| B and C | A | Public answer/error examples, working Go endpoint and the selected waiting/recovery profile |
| A and B | C | Frozen independent questions and source-based expected outcomes |
| C | A and B | Evaluation results, failure categories, source limits and release recommendation |
| A and B | Team | Physical-phone evidence, deployment instructions and reproducible system checks |

A starts against mocks. B starts against a controlled workflow substitute. C starts against fake provider responses and the existing retrieval service. Nobody needs to wait for the whole system before beginning their implementation.

Existing source content remains unchanged during the first Agent integration. The catalog-fact projection exposes already released title, artist and date values under the new provenance rules; it adds no museum facts. Source expansion, new retrieval algorithms, a reset/new-visitor endpoint and extra tools are separate changes with their own contract and evaluation. A reset capability remains deferred until specified and implemented.

## 4. Mapping to the architecture packages

The P1-P8 packages in the architecture document remain implementation units, not additional milestones.

| Package | Lead | Support | Main milestone |
| --- | --- | --- | --- |
| P1. Contract fixtures | B | C for AI/tool semantics; A for public responses | M1 |
| P2. Agent core | C for graph/context; B for service/run infrastructure | Review the shared boundaries together | M1-M2 |
| P3. Evidence tool | C | B for service connectivity | M1 |
| P4. Model integration | B for provider/profile; C for prompts/model behavior | A for language/visitor feedback | M1-M2 |
| P5. Agent evaluation | C | A authors independent questions; B reviews gold and runtime results | M2 |
| P6. Application integration | B | C for Agent results; A for client behavior | M2 |
| P7. Browser integration | A | B for recovery; C for evidence semantics | M1-M2 |
| P8. Deployment acceptance | B for deployment; A for physical-phone acceptance | C for final quality checks | M3 |

Implementation commits should identify the relevant package and checks. Record milestone completion only when its evidence exists; finishing code or merging a branch alone does not close acceptance.
