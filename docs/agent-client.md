# Go private Agent client

Status: **M1 POST transport and M2-B1 single GET status queries are implemented,
with controlled HTTP/TLS tests and real Python completed/failed lookup checks.
Public application routing remains on the fixture path. Automatic reconciliation
and accepted-work orchestration remain M2-B2 work.**

`api/internal/agentclient` implements the POST and GET boundaries defined by the
[private Agent contract](agent-api-contract.md#3-private-agent-transport). It
uses the existing `agentcontract` types, codec, snapshot validators and error
mapping. It has no dataset copy, canonical answer registry or model dependency.

## Construct and call

Create one client from private application configuration and reuse it across
requests. An origin has no credentials, query or fragment; an optional trailing
slash is accepted. The token follows the FastAPI factory's visible ASCII and
minimum encoded length rule. Generate it from at least 32 random bytes and keep
it out of source control and logs.

The intended application call sequence is:

```go
client, err := agentclient.New(agentOrigin, serviceToken)
if err != nil {
    return err
}
defer client.CloseIdleConnections()

// attempt comes from the existing Store.BeginAgent admission.
ctx, cancel := context.WithDeadline(parent, attempt.Deadline)
defer cancel()
snapshot, err := client.Run(ctx, attempt.Request, requestID)
```

The surrounding application owns accepted-work cancellation, the original
attempt context and the decision to commit or fail the attempt. It must retain
the same snapshot and run ID for recovery. This package neither creates a
conversation nor updates its state. Later M2 wiring will connect it to the
[existing atomic state store](agent-state-store.md).

## Query an existing run: M2-B1

`client.Get(ctx, originalRun, requestID)` performs a status query to
`GET /internal/v2/agent/runs/{run_id}`. Supply the original `RunRequest`, not a
new snapshot assembled from newer conversation state. The client detaches and
validates it before I/O, checks its canonical UUID before constructing the path,
and binds any completed result to that original scope, revision, language,
catalog and evidence context. No request body or refreshed deadline is sent.
Authentication, trace echo, TLS, redirects, strict JSON and response-size rules
are shared with POST.

HTTP 200 does not establish a completed answer:

| GET outcome | Go return value |
| --- | --- |
| Valid `processing` | Snapshot with `State == "processing"`, nil result/error; Go error is nil |
| Valid `completed` | Fully validated snapshot/result; Go error is nil |
| Valid retained `failed` | Zero snapshot and safe `*agentclient.Error` with `RunState == "failed"` |
| Invalid response, lookup/admission error, network/context failure | Zero snapshot and safe error; `RunState` is empty |

For a retained failure, `agentcontract.MapRunFailure` uses the same embedded
policy as POST error envelopes. It obtains the private failure status from the
policy instead of treating HTTP 200 as an error-status pairing. Shape and
retryability are checked, and an `agent_run_in_progress` error in a terminal
failed snapshot is rejected as inconsistent. Provider messages and phases are
not retained. `Error.Failure` carries only the mapped safe application failure.

An intended caller distinguishes a terminal failure from an unsuccessful lookup:

```go
snapshot, err := client.Get(ctx, attempt.Request, requestID)
if err != nil {
    var failure *agentclient.Error
    if errors.As(err, &failure) && failure.RunState == "failed" {
        // The Agent retained a terminal failure; inspect failure.Failure.
    }
    return err
}
switch snapshot.State {
case "processing":
    // No result to commit. The future reconciler owns waiting and its budget.
case "completed":
    // The original request has been checked; the store still guards commit.
}
```

An elapsed Agent admission deadline may still allow retrieval of a retained run.
The original Go attempt deadline continues to bound encoding, I/O and validation;
an already exhausted context prevents network access. Context failure takes
priority over a simultaneously observed result or retained failure.

A 404 `agent_run_not_found` is mapped to the documented safe unavailable error;
it never starts the model or creates a replacement run. GET connection loss
leaves the run outcome unknown. This method adds no application-level retries,
polling, POST resubmission, run ID generation or conversation updates. M2-B2 must
decide whether another query fits within the original attempt budget.

## Request and response boundary

Before I/O both methods encode, detach and validate the full original request.
`Run` sends one POST to `/internal/v2/agent/runs`, with one Bearer header,
one visible ASCII `X-Request-ID` of 1–128 characters, UTF-8 JSON and explicit
uncompressed encoding. Caller edits after sending cannot rebind response
validation. The request's `execution.deadline_at` is transmitted unchanged.

The client does not perform new-run deadline admission. An Agent may replay an
already completed run after that Agent deadline; retained replay must precede
new-run deadline checks. The original Go attempt context still bounds the call,
including encoding, response reading and validation. A defensive 60-second
ceiling prevents an unbounded call when a caller supplies no deadline; it does
not replace the original accepted-attempt budget or grant another 60 seconds.

The private HTTP transport disables environment proxies, automatic redirects
and decompression. HTTPS retains standard certificate validation. Responses
must provide UTF-8 JSON, exact trace echo, `Cache-Control: no-store` and an
uncompressed body. Both declared and actual response lengths are bounded to
256 KiB. Shared codecs reject duplicate keys, malformed Unicode, excessive
nesting, trailing JSON and coercion.

A successful POST must be HTTP 200 with a completed snapshot, the original
run ID, a result and no error. `CheckRunResult` binds scope, dataset, base revision,
language, catalog, evidence/citations and execution relationships to the detached
request. A processing or failed snapshot in a successful POST is rejected.
Schema-open response metadata is discarded as specified by the existing codec.
Evidence provenance and natural-language entailment remain the authenticated
Agent's responsibilities; the client does not mirror source passages as gold
answers.

## Safe failures and recovery

Use `errors.As(err, &clientError)` with a `*agentclient.Error` to read its
`Failure`, an existing `agentcontract.Failure` containing the safe public status,
code, message, retryability and reconciliation flag. Valid private error envelopes
are bound to the trace and, when present, the run ID before `MapAgentFailure`
validates the private HTTP/code/retryability combination. Null error run IDs are
allowed for admission failures. Upstream diagnostic messages are never copied
into the returned Go error or its causes.

`Error.RunState` is set to `failed` only after a valid failed GET snapshot. It
does not expose raw upstream error data and is empty for ordinary POST failures,
malformed failed snapshots, unknown runs and network failures.

| Outcome | Fallback classification | Reconciliation |
| --- | --- | --- |
| Malformed input/internal response, wrong run, scope or source relationships | 502 `agent_invalid_response` | No automatic recovery |
| Valid private failure envelope | Existing shared mapping | As specified by the mapping |
| Connection loss, interrupted body or the defensive transport ceiling | 503 `agent_unavailable` | Query the same run before using this fallback |
| Original caller deadline exceeded | 504 `request_timeout` | Original budget is exhausted |
| Original caller cancellation | Safe unavailable failure, preserving `context.Canceled` | Accepted work may still need reconciliation |

Context identity is preserved for `errors.Is` with `context.Canceled` or
`context.DeadlineExceeded`. Parent context failure takes priority over other
observed outcomes. Errors retain no raw network URLs, authorization values or
provider bodies. Client string formatting redacts its configuration.

`Failure.Reconcile` is a signal to the future application reconciler, not proof
that the Agent accepted the POST. These methods add no GET polling, automatic POST
retry, new run ID, readiness bypass, fixture fallback or model call. Retryable
does not authorize an automatic resubmission. A final failure/commit decision
must follow bounded reconciliation under the original attempt deadline.

## Verification and next step

From `api/`:

```bash
go test -race ./internal/agentclient
go test -race ./...
```

The tests use shared synthetic v2 fixtures and temporary Go HTTP/TLS servers.
They exercise authenticated POST/GET wire data, unchanged deadlines, retained replay,
detachment, concurrent reuse, all shared error mappings, scope/citation failures,
strict responses, actual/declarative body bounds, redirects, TLS trust and
connection/deadline/cancellation failures, all three GET states and retained
failure-policy validation. These unit tests use no GPU, Ollama, Python service
or tokenizer asset.

The [step-2 test services](m1-test-services.md) supply a standalone Python launcher
with scripted model decisions and real FastAPI/workflow/retrieval services.
The [step-3 Go integration check](m1-go-integration.md) exercises both artwork
scopes and the documented foreign-evidence injection. It records actual lookup
IDs, the rejection boundary, Go trace IDs and conversation commit/isolation
outcomes. Public application wiring and bounded GET reconciliation remain M2 work.
That live check now also queries completed and failed records with the Go GET
client and confirms the original evidence/model-call counts remain unchanged.
