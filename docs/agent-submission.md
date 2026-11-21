# Go Agent submission service

Status: **M2-B2 is implemented as an internal Go service. Admission, one POST,
bounded GET reconciliation, validation and atomic commit/failure are connected.
Public HTTP routing and integration configuration are now supplied by
[M2-B3](public-agent-integration.md).**

`api/internal/agentchat` composes the existing
[state store](agent-state-store.md) and [private client](agent-client.md).
Question interpretation, retrieval and generation remain in the Python Agent.

## Internal entry point

```go
service, err := agentchat.New(store, privateClient, logger)
if err != nil {
    return err
}
response, failure := service.Submit(
    requestContext, visitorToken, conversationID, message, requestID,
)
```

`message` is the existing `agentcontract.PublicMessageRequest`. The future public
handler owns Origin, cookie, content-type/body checks and Unicode trimming.
The store defensively validates the typed request and its conversation ownership.
The service also rejects invalid trace IDs and a nil context before admission.
It neither creates a conversation nor selects a different artwork or release.

`Submit` returns a `PublicSubmissionResponse` or a safe `conversation.Failure`.
It does not expose private run IDs, provider bodies or semantic memory in errors.

## Admission, execution and recovery

1. `BeginAgent` resolves completed replay and conflicts, or reserves a new
   attempt with its original run ID, five-pair context, state and deadlines.
2. The service detaches accepted work from browser cancellation and uses the
   store's original attempt deadline. A completed replay performs no transport.
3. It freezes an encoded request and decodes an independent copy for each
   operation. A transport cannot change later GET inputs or commit validation.
4. It calls `Run` once. A validated completed result proceeds to commit. A known
   admission/dependency failure is retained without another execution.
5. An uncertain POST, an in-progress response or a child transport timeout starts
   GET reconciliation for the original run. There is no automatic POST retry.
6. Processing or transient query failure waits for another bounded query.
   Completed results are checked against the original snapshot. A retained
   failure, missing run or invalid result ends the attempt.
7. `CompleteAgent` atomically saves the turn, state, cited evidence, replay record
   and completion marker. `FailAgent` changes pending failure information while
   preserving committed history and memory.

The service independently validates snapshot shape, run ID and result bindings
even when an injected transport bypasses client validation. The store rechecks
its protected reservation, revision, expiry and monotonic deadline at commit.
Late operation returns have no path to the store.

| Observed outcome | Action |
| --- | --- |
| Completed POST / GET | Validate, then attempt one guarded atomic commit |
| Known POST failure without reconciliation | Retain the canonical safe failure |
| POST connection loss, panic or child timeout | Query the same original run |
| GET processing | Wait within the same budget and query again |
| Retryable GET transport/query error | Retry GET within the same budget |
| Valid `agent_run_not_found` | Fail unavailable; do not recreate or resubmit |
| Retained failed GET snapshot | Retain its safe mapped failure; stop querying |
| Malformed, foreign or wrong-run result | Fail `agent_invalid_response`; commit no turn or evidence |
| Reconciliation window exhausted | Fail `agent_unavailable`; no automatic new execution |
| Original Go deadline exceeded | `request_timeout` takes priority |

`agentclient.Error.RunMissing` identifies only a valid mapped 404
`agent_run_not_found` envelope, separately from a retained terminal failure.
The service uses the shared `SafeAgentFailure` policy to canonicalize public
status/code/retryability, ignoring any diagnostic prose from an injected client.
Impossible combinations are invalid responses.

## Budgets and cancellation

| Boundary | Default |
| --- | ---: |
| Original Go attempt, including validation and commit | 60 seconds from admission |
| Agent execution deadline | Original admission + 55 seconds, unchanged |
| One POST wait | At most 56 seconds and the remaining Go budget |
| One GET wait | At most 2 seconds and the remaining reconciliation budget |
| Wait after each preceding operation before a GET | 1 second, including the first query |
| Reserved final validation/commit/failure margin | 250 ms before the Go deadline |

The POST cap leaves recovery time. The reconciliation window ends 250 ms before
the original deadline so an exhausted recovery can be recorded safely. A child
operation timeout does not reset or exhaust the whole attempt by itself. A
parent timeout observed during validation or failure recording always wins.

Calls are bounded even if an injected transport ignores cancellation. A buffered
result channel accepts a late return without blocking its sender; the late value
is discarded. This protects application state, not an assertion that a remote
model immediately stopped computing. The actual Agent still owns its retained
run, capacity and execution deadline.

Browser cancellation after admission does not cancel accepted work. The public
HTTP handler can return the saved outcome or let browser history recovery find
it. This remains in-memory lifecycle handling, without durable restart recovery.

## Verification

From the repository root:

```bash
go -C api test -race ./internal/agentchat ./internal/agentclient ./internal/agentcontract
go -C api test -race -tags=integration \
  -run '^TestM2B2RealAgentRecoveryAndFailedRun$' -count=1 -timeout=120s -v \
  ./internal/agentchat
go -C api test -race -tags=integration -count=1 -timeout=120s ./...
```

Unit checks cover replay, busy/stale/idempotency conflicts, browser cancellation,
immutable requests, processing, transient GET errors, missing and terminal runs,
child/parent deadlines, late uncooperative calls, invalid scope/evidence and safe
logs. One test closes a real HTTP socket after accepting POST and recovers using
the existing Go GET client.

The tagged check uses real FastAPI, registry, v2 graph and HTTP retrieval, with
the existing test-only model script. It deliberately discards POST outcomes at
the Go transport-return boundary, then reads the original retained run:

- Both artworks: one POST, one GET, two model calls, one retrieval, one committed
  turn and revision 1. Completed replay adds no downstream work.
- Foreign-evidence run: one POST, one GET, one model call and one retrieval;
  the retained failure reaches Go with no committed turn/state/evidence change.

The live injection boundary is distinct from the physical socket-loss unit test.
These checks do not measure Qwen answer quality or exercise the browser route.
The test-only process/audit helper is shared with M1 under the `integration` tag;
production builds do not import it. Python prerequisites and `M1_PYTHON` are
unchanged from the [test-service guide](m1-test-services.md).

## Observability and next step

Safe completion logs include trace/run identity, artwork/release, base revision,
logical POST/GET counts, public status/code and total duration. They omit visitor
tokens, question text, provider diagnostics, memory and service credentials.
Inference/retrieval counts come from the Agent, separately from HTTP call counts.
Model/profile identity and full performance evidence remain part of M2-B5.

M2-B3 connects this service and `OpenAgent`/`AgentHistory` to public v2 routes,
supplies explicit integration configuration and aligns server/browser/proxy
budgets. The server defaults to fixture mode; `GENERATION_MODE=agent` explicitly
selects v2. See the [integration guide](public-agent-integration.md) for setup
and the separate public HTTP check.

The [M2-B4 reliability suite](public-agent-reliability.md) additionally exercises
physical private/public connection loss with real Python services, public
concurrency/state behavior and the actual unchanged production recovery budget.
