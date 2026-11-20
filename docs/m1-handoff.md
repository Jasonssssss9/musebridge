# B M1 integration handoff

Status: **The Go-originated M1 integration item passes all three required cases.
This record supplies the tested commit, repeatable command, actual trace table
and injection/rejection boundaries. Other team M1 gates and M2 remain separate.**

Owner: Jinqing Zhang (B, Go application backend and system integration).
Verification date: 2026-11-19.

## Tested version

Tested commit: [`6064d01070fb3539379e76f113d31947ae56908d`](https://github.com/Jasonssssss9/musebridge/commit/6064d01070fb3539379e76f113d31947ae56908d)
(`6064d01`, `Verify Go-originated M1 Agent integration`).
The source checkout was clean when the verification below ran. This handoff
document is a subsequent documentation change; its commit is not substituted
for the tested implementation commit.

Dataset: `aic-seed-v0.1`, checked-in seed SHA-256
`3703fd4c9fcefb8e9fba20f23a5cb70e612c62ef7cb3f594a15d319ebc18d6ac`.
The private contract is `agent-run-v2`; the workflow is `museum_qa_v2`.

## Reproduce with one command

Prepare Linux/WSL, Go 1.27 or later, Python 3.12 and the pinned Python/Go
dependencies as described in the [integration guide](m1-go-integration.md#prerequisites-and-command).
The default Python interpreter is the checkout's `.venv/bin/python`;
`M1_PYTHON` can select another prepared environment.

From the repository root:

```bash
go -C api test -race -tags=integration \
  -run '^TestM1GoOriginatedIntegration$' -count=1 -timeout=120s -v \
  ./internal/conversation
```

The command starts temporary loopback services, runs the three cases, prints
their trace/evidence table and shuts the services down. It must finish with
`PASS` and exit code 0, including cleanup and log-secret checks. Missing Python
dependencies, failed startup, unavailable services or missing actual lookup
records cause failure. The test does not skip required cases or reuse a cached
test result. No GPU, running Ollama instance, tokenizer assets or Docker is
required. After dependencies are available, the check needs no external network.

## Observed results

Every case asks `What material is this painting made of?` using an initial
conversation snapshot created by the real Go store. The following IDs come from
a fresh run on the tested commit; future runs generate different trace IDs.

| Case / active artwork | Trace ID | Expected evidence ID | Actual evidence ID or error | Result |
| --- | --- | --- | --- | --- |
| The Bedroom / `aic-28560` | `m1-go-bedroom-dd08805e` | `aic-28560-medium` | `aic-28560-medium`, completed and answered | PASS |
| Woman at Her Toilette / `aic-11723` | `m1-go-toilette-b85fd038` | `aic-11723-medium` | `aic-11723-medium`, completed and answered | PASS |
| Foreign evidence / `aic-28560` | `m1-go-foreign-05dc2d30` | `aic-28560-medium` | `aic-11723-medium` -> 502 `retrieval_invalid_response`; no successful result | PASS |

All evidence comes from `aic-seed-v0.1`. Both medium passages say `Oil on canvas`;
the assertions bind the actual HTTP lookup, evidence IDs, citations and stored
cache to the active artwork. Equal answer text cannot establish isolation.

The normal cases use independent artwork conversations for the same visitor.
Go accepts the original run ID and completed result, verifies the original
scope/release/revision and evidence/citation relationships, then atomically
saves one turn, revision 1 and the cited medium record. Each case records two
model calls and one real retrieval HTTP call under its Go trace. Replaying the
same completed submission preserves the saved turn and state without another
model or retrieval call. The second artwork leaves the first conversation intact.

## Invalid record: injection and rejection

The foreign case uses a separate initial Go conversation and a separate
test-service instance configured with `--fault foreign-evidence`.

1. The real retrieval service executes the scoped `/retrieve` request and selects
   `aic-28560-medium`.
2. The test-only `M1RetrievalHandler._send_json`, installed by `_observe_http` in
   [the service harness](../agent/testing/m1_services.py), substitutes the other
   artwork's released `aic-11723-medium` immediately before HTTP serialization.
   The audit records both the original and transmitted evidence IDs.
3. The real `RetrievalTool.search` consumes that HTTP body.
   `EvidenceManifest.validate_candidates` rejects the foreign chunk ID for the
   active artwork. Assessment is never called.
4. The Agent returns its normal safe failure envelope. The existing Go client
   maps it to 502 `retrieval_invalid_response` and returns no successful snapshot.
   Go calls `Store.FailAgent`; the complete committed conversation state remains
   unchanged: zero turns, revision 0, no verified evidence and no memory update.
   The pending attempt alone becomes failed with its original message ID/error.

The release seed and production retrieval/validation code are unchanged by the
fault. The [Go acceptance test](../api/internal/conversation/agent_integration_test.go)
and [process/audit checks](../api/internal/conversation/m1_services_integration_test.go)
exercise the reusable application client rather than a replacement Go transport.

## Scope and remaining work

The chain uses actual Go conversation admission, original run/attempt deadlines,
Bearer authentication, trace propagation, FastAPI, run registry, LangGraph
workflow, retrieval HTTP and Go result/state validation. Only provider decisions
and token sizing are scripted at the documented model boundary. The synthetic
model name is `SYNTHETIC-M1-TEST-ONLY`. The required wire literals `ollama` and
`local-ollama-v2` in its execution record do not establish real inference.

Fresh service tokens are passed through the child environment and checked for
absence from launcher/audit output. Tokens, raw runtime logs and private host
configuration are not part of this handoff. The command cleans its temporary
state and services before reporting overall success.

This demonstrates B's outstanding Go-originated M1 integration item and provides
the teammate reproduction path. The author verified the run recorded above;
a teammate's independent execution is not claimed. Public browser routing,
bounded GET reconciliation, real-model answer quality and production/phone
acceptance keep their separate M2/deployment checks. Follow the [team plan](agent-plan.md)
for those gates.
