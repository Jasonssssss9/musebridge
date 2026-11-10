# MuseBridge documentation

The root README introduces the project. This directory provides entry points to its setup, design and collaboration documents. Module-specific guides retain detailed commands and implementation notes.

| Guide | Purpose |
| --- | --- |
| [Getting started](getting-started.md) | Current implementation, prerequisites, native startup and basic checks |
| [System design](design.md) | Stage-one target behavior, module boundaries and later decisions |
| [API and data contract](api-contract.md) | Request/response formats, identity, evidence, errors and retries |
| [Implementation plan](plan.md) | Owners, stable task IDs, dependencies and acceptance checkpoints |
| [Go backend guide](../api/README.md) | Artwork API, retrieval client, configuration and Go tests |
| [Data and retrieval guide](../knowledge/README.md) | Published seed, Python retrieval, private HTTP service and Docker |
| [Retrieval evaluation](../eval/README.md) | Diagnostic suite, metric definitions and recorded baselines |

## Team ownership

| Contributor | Responsibility |
| --- | --- |
| Jeremiah Lim | Mobile frontend, visitor experience and phone walkthrough |
| Jinqing Zhang | Go API, question orchestration and system integration |
| Jason Zichen He | Source preparation, retrieval and evidence evaluation |

The design, contract and plan describe the intended local-demo behavior. Consult the getting-started and module guides for what is currently runnable. Conversation endpoints, answer generation and the complete phone journey remain upcoming work.
