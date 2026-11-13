# MuseBridge documentation

The root README introduces the project. This directory provides entry points to its setup, design and collaboration documents. Module-specific guides retain detailed commands and implementation notes.

| Guide | Purpose |
| --- | --- |
| [Getting started](getting-started.md) | Compose startup, native development and verification |
| [System design](design.md) | Stage-one target behavior, module boundaries and later decisions |
| [API and data contract](api-contract.md) | Request/response formats, identity, evidence, errors and retries |
| [Local LLM contract proposal](llm-contract-proposal.md) | Draft for team review: LLM-first question understanding, bounded retrieval, clarification, evidence authority and deadlines; not implemented |
| [Implementation plan](plan.md) | Owners, stable task IDs, dependencies and acceptance checkpoints |
| [Go backend guide](../api/README.md) | Artwork, conversation and question APIs, fixture answers, configuration and Go tests |
| [Data and retrieval guide](../knowledge/README.md) | Published seed, Python retrieval, private HTTP service and Docker |
| [Retrieval evaluation](../eval/README.md) | Diagnostic suite, metric definitions and recorded baselines |

## Team ownership

| Contributor | Responsibility |
| --- | --- |
| Jeremiah Lim | Mobile frontend, visitor experience and phone walkthrough |
| Jinqing Zhang | Go API, question orchestration and system integration |
| Jason Zichen He | Source preparation, retrieval and evidence evaluation |

The design, contract and plan describe the intended local-demo behavior. Consult the getting-started and module guides for what is currently runnable. Conversation recovery, question submission, real retrieval, reviewed fixture answers and request replay are implemented. The local stack has a Compose startup path and a separate physical phone walkthrough. Cloud model generation follows the local stage. The [web guide](../web/README.md) covers the frontend's mock and live modes.

The local LLM proposal is a separate review document. It does not replace `local-demo-v0.1` or imply that the application already calls a real model.
