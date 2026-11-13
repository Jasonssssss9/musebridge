# MuseBridge documentation

The root README introduces the project. This directory provides entry points to its setup, design and collaboration documents. Module-specific guides retain detailed commands and implementation notes.

| Guide | Purpose |
| --- | --- |
| [Getting started](getting-started.md) | Compose startup, native development and verification |
| [System design](design.md) | Stage-one target behavior, module boundaries and later decisions |
| [API and data contract](api-contract.md) | Request/response formats, identity, evidence, errors and retries |
| [Agent architecture and contracts](agent-design.md) | New implementation target: independent Python/FastAPI/LangGraph Agent, workflow, protocols, prompts, deployment and acceptance; not yet implemented |
| [Agent schema and examples](contracts/agent-v1.schema.json) | Machine-readable wire contracts; [valid and invalid examples](contracts/agent-v1.examples.json) accompany the specification |
| [Implementation plan](plan.md) | Owners, stable task IDs, dependencies and acceptance checkpoints |
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

The Agent specification replaces the earlier LLM proposal as the development target. The runnable fixture baseline remains `local-demo-v0.1`; the target public contract is `agent-demo-v0.2`. The new Agent endpoints, modes and framework are not implemented by this documentation change. Historical ownership above does not assign the new work: allocate the module-based packages in [section 14 of the Agent specification](agent-design.md#14-implementation-and-migration) before implementation.
