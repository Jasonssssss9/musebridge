# Go-originated M1 integration

Status: **B M1 integration step 3 is implemented and passes with the real Go
store/client, private FastAPI service, v2 workflow and HTTP retrieval service.
Only model decisions and token sizing are scripted. The three required cases
pass; the [handoff record](m1-handoff.md) identifies tested commit `6064d01` and
its actual trace/evidence results.**

This verifies B's Go-originated integration item. Team M1 includes other gates;
public browser routing, real-model quality, production readiness and M2 recovery
are separate acceptance work. The application still serves fixture mode.

## Prerequisites and command

Use Linux/WSL, Go 1.27 or later, Python 3.12, the checked-in `aic-seed-v0.1` release
and the pinned dependencies. From the repository root, install Python packages
only if the existing environment is absent:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r agent/requirements.txt
```

Go dependencies are pinned by `api/go.mod` and `api/go.sum`; populate the module
cache before an offline run. Once prerequisites are present, this single command
starts the services, runs all three cases, prints the trace/evidence table and
closes the services:

```bash
go -C api test -race -tags=integration \
  -run '^TestM1GoOriginatedIntegration$' -count=1 -timeout=120s -v \
  ./internal/conversation
```

No GPU, Ollama, tokenizer assets, Docker or external service is needed. The check
uses actual loopback TCP listeners. Missing prerequisites, failed readiness,
unavailable services, missing audit events or invalid responses cause failure;
the test never skips a required case or substitutes a successful Go fixture.
`-count=1` prevents reuse of a cached test result.

By default the launcher uses `.venv/bin/python` from the repository root.
`M1_PYTHON` can select another prepared interpreter, using an absolute path,
a path relative to the repository root, or an executable on PATH. Temporary
state uses Go's test temporary directory, which must be outside the checkout.
Standard `GOCACHE`/`GOMODCACHE` overrides remain available where needed.

The integration build tag keeps Python processes and listeners out of ordinary
Go unit tests. To run all Go checks, including this integration:

```bash
go -C api test -race -tags=integration -count=1 -timeout=120s ./...
```

## Actual request path

```text
Go Store.OpenAgent / Store.BeginAgent
  -> original v2 snapshot, run ID and deadlines
  -> reusable agentclient.Client.Run
  -> authenticated POST /internal/v2/agent/runs
  -> actual FastAPI admission and run registry
  -> actual museum_qa_v2 graph
  -> scripted planning decision at the provider boundary
  -> RetrievalTool -> real HTTP /retrieve -> released dataset
  -> accepted evidence -> scripted assessment using that evidence
  -> actual workflow/result validation -> completed snapshot
  -> Go client validation -> Store.CompleteAgent -> atomic state/turn/replay
```

The request comes from a live Go conversation store using the real clock. It
is not loaded as a prebuilt `RunRequest` fixture. Go sends the same frozen
question, artwork/catalog/state and original 55-second Agent deadline, bounded
by the store's original 60-second attempt context. The existing client supplies
service authentication, trace identity, strict decoding and shared safe errors.

Go checks run identity, completed state, scope/release, language, base revision,
evidence IDs, citations and execution counts against its original request. The
normal checks require two model calls and one retrieval call, each recorded
under the same Go trace. The synthetic model's identity remains explicit.

## Required cases and observations

Every request asks `What material is this painting made of?` in English, starting
from revision 0 with an empty evidence cache and history.

| Case | Expected evidence | Required observation |
| --- | --- | --- |
| The Bedroom, `aic-28560` | `aic-28560-medium` | Actual HTTP lookup and accepted answered result from `aic-seed-v0.1`; one saved turn, revision 1 and exactly that cached evidence |
| Woman at Her Toilette, `aic-11723` | `aic-11723-medium` | Same checks in an independent artwork conversation for the same visitor; the Bedroom conversation remains unchanged |
| Foreign evidence under `aic-28560` | `aic-28560-medium` | Actual HTTP returns `aic-11723-medium`; real tool rejects it; Go gets 502 `retrieval_invalid_response`, no result and no committed state change |

Both medium passages say `Oil on canvas`. The test checks the actual retrieval
HTTP record, scoped evidence/citation IDs, dataset and resulting Go cache; equal
answer text cannot satisfy these assertions. Each successful submission is also
replayed through the real Go store, producing the original saved turn without
changing revision or adding model/retrieval operations.

The rejection case uses a separate initial conversation and a separate
[test-service instance](m1-test-services.md#foreign-evidence-injection-and-audit)
with `--fault foreign-evidence`. The real retrieval function first selects the
correct record. Its test-only handler then substitutes the other artwork's
released medium record immediately before HTTP response serialization.

`RetrievalTool.search` receives that real HTTP body and
`EvidenceManifest.validate_candidates` rejects its foreign chunk ID. No
assessment model call occurs. Go receives the existing safe failure mapping,
then calls `Store.FailAgent`. The complete committed `ConversationState` is
identical before and after: revision 0, no turns, no verified evidence and no
memory update. Only the pending attempt becomes failed, preserving its message
ID and error for recovery. This is not a successful insufficient-evidence turn.

## Output, privacy and handoff

Verbose output includes a short Markdown table with each case, fresh Go trace
ID, expected evidence ID, actual evidence/error and PASS. Trace IDs differ on
every run. A partial table does not establish success; the whole Go command,
including process shutdown and secret checks, must exit zero.

Each harness gets a fresh service token generated by Go and passed only through
the child environment. It is not placed in command arguments or printed. Python
stdout/stderr and evidence audit stay in owner-only temporary files outside the
checkout. Cleanup sends SIGTERM, waits for orderly shutdown, checks the terminal
audit and absence of a stale readiness file, and verifies that the service token
did not enter launcher/audit logs. Go removes its temporary directories after
the test. Preserve the command output outside the checkout when preparing a
handoff report; do not commit private runtime artifacts.

Hand back the tested commit, the command above, the actual three-row table and
the injection/rejection boundary. Uncommitted working-tree checks must be labeled
as such rather than attributed to the parent commit.

The [completed B M1 handoff](m1-handoff.md) records a clean-source verification
on the committed implementation and supplies those items for team review.

Implementation: [Go acceptance cases](../api/internal/conversation/agent_integration_test.go),
[Go process/audit support](../api/internal/conversation/m1_services_integration_test.go),
[reusable Agent client](agent-client.md), [Python service harness](m1-test-services.md).
