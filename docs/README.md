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

The current Agent target is visitor-policy draft revision 2 of `agent-workflow-v2`, with public `agent-demo-v0.3` and private `agent-run-v2`. The runnable fixture baseline remains `local-demo-v0.1`; C now provides an explicit v2 workflow with simulated model decisions and real retrieval, alongside the preserved v1 entry point. The v2 core does not activate new Go/FastAPI endpoints or establish real-model dialogue quality; application/provider integration remains pending. Historical ownership above does not assign the new work. The [Agent implementation plan](agent-plan.md) defines the new A/B/C responsibilities and three milestones, mapped to the architecture's delivery packages.
