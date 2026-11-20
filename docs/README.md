# MuseBridge documentation

The root README introduces the project. This directory provides entry points to its setup, design and collaboration documents. Module-specific guides retain detailed commands and implementation notes.

| Guide | Purpose |
| --- | --- |
| [Getting started](getting-started.md) | Compose startup, native development and verification |
| [System design](design.md) | Stage-one target behavior, module boundaries and later decisions |
| [API and data contract](api-contract.md) | Request/response formats, identity, evidence, errors and retries |
| [Agent v2 architecture](agent-design.md) | Four-language conversation, optional English retrieval, direct responses, five-turn context and conversation state; C core implemented, application/model integration pending |
| [Agent v2 interface contract](agent-api-contract.md) | Public/private requests, model decisions, language policy, state updates, citations, errors and recovery |
| [Agent v2 schema](contracts/agent-v2.schema.json) | New machine-readable target; [shape and runtime/semantic examples](contracts/agent-v2.examples.json) define acceptance cases |
| [Go/Python boundary implementation](contracts/README.md) | B phase 1: generated wire types, strict codecs, pure snapshot validation, shared error mappings and checks |
| [Go Agent state store](agent-state-store.md) | B phase 2: versioned sessions, frozen attempts, atomic turn/state saving, pending clarification, evidence cache and replay; internal integration layer |
| [Go private Agent client](agent-client.md) | M1 POST transport and M2-B1 single GET status queries; original-request validation, safe retained failures and controlled/live-service checks |
| [Go Agent submission service](agent-submission.md) | M2-B2: admission, one POST, bounded GET reconciliation, browser-cancellation ownership and atomic state/turn completion; public routing pending |
| [M1 test services](m1-test-services.md) | M1 integration step 2: standalone real FastAPI/workflow/retrieval servers with provider-only scripting, HTTP audit and foreign-evidence injection |
| [Go-originated M1 integration](m1-go-integration.md) | M1 integration step 3: one-command Go-to-Python acceptance for both artwork scopes, foreign-evidence rejection, atomic saving and trace/evidence output |
| [B M1 integration handoff](m1-handoff.md) | M1 integration steps 4–6: final tested commit, reproduction command, observed trace/evidence table, rejection boundary and completed backend regressions |
| [Private Agent run service](agent-service.md) | B phase 3: FastAPI endpoints, auth, run replay, cancellation, shared inference and capacity; controlled HTTP checks, default not ready |
| [Local Ollama provider](ollama-provider.md) | B phase 4: pinned Qwen transport, tokenizer budgets, grammar conversion, real probes and gated readiness; workflow acceptance remains open |
| [C Agent implementation](../agent/README.md) | Runnable v2 workflow, scripted scenarios, real retrieval and B handoff; [preserved v1 runbook](../agent/README-v1.md) |
| [Archived Agent v1 design](archive/agent-design-v1.md) | Preserved reference for the unchanged [v1 schema](contracts/agent-v1.schema.json) and [v1 examples](contracts/agent-v1.examples.json) used by current code |
| [Visitor acceptance journeys](visitor-acceptance.md) | Twelve realistic visitor journey families, multilingual scripts, expected behavior and deferred capabilities; designed, not yet executed |
| [Agent implementation plan](agent-plan.md) | Three milestones, A/B/C responsibilities, handoffs and completion criteria |
| [Fixture-stage implementation plan](plan.md) | Historical task IDs, assignments and local verification records |
| [Go backend guide](../api/README.md) | Artwork, conversation and question APIs, fixture answers, configuration and Go tests |
| [Data and retrieval guide](../knowledge/README.md) | Published seed, Python retrieval, private HTTP service and Docker |
| [Retrieval evaluation](../eval/README.md) | Diagnostic suite, metric definitions and recorded baselines |

## Current fixture-stage ownership

| Contributor | Responsibility |
| --- | --- |
| Jeremiah Lim | Mobile frontend, visitor experience and phone walkthrough |
| Jinqing Zhang | Go API, question orchestration and system integration |
| Jason Zichen He | Source preparation, retrieval and evidence evaluation |

The design, contract and plan describe the intended local-demo behavior. Consult the getting-started and module guides for what is currently runnable. Conversation recovery, question submission, real retrieval, reviewed fixture answers and request replay are implemented. The local stack has a Compose startup path and a separate physical phone walkthrough. Cloud model generation follows the local stage. The [web guide](../web/README.md) covers the frontend's mock and live modes.

The current Agent target is visitor-policy draft revision 2 of `agent-workflow-v2`, with public `agent-demo-v0.3` and private `agent-run-v2`. The runnable fixture baseline remains `local-demo-v0.1`; C provides an explicit v2 workflow alongside the preserved v1 entry point. B has added the private FastAPI service, internal Go state store and opt-in Ollama provider. Real local probes expose unresolved workflow/retrieval and grounding failures, so model readiness remains closed. Public v2 routing and dialogue-quality acceptance remain pending. Historical ownership above does not assign the new work. The [Agent implementation plan](agent-plan.md) defines the new A/B/C responsibilities and three milestones.
