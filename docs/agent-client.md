# Go private Agent client

Status: **M1 integration step 1 is implemented: a reusable Go POST client with
controlled HTTP/TLS boundary tests. A live Go-originated FastAPI/retrieval test
is still pending. Public application routing remains on the fixture path.**

`api/internal/agentclient` implements the POST boundary defined by the
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

## Request and response boundary

Before I/O the client encodes, detaches and validates the full original request.
It sends exactly one POST to `/internal/v2/agent/runs`, with one Bearer header,
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
that the Agent accepted the POST. This step adds no GET polling, automatic POST
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
They exercise authenticated wire data, unchanged deadlines, retained replay,
detachment, concurrent reuse, all shared error mappings, scope/citation failures,
strict responses, actual/declarative body bounds, redirects, TLS trust and
connection/deadline/cancellation failures. No GPU, Ollama, Python service or
tokenizer asset is used. These tests do not close the live M1 handoff.

The next implementation step supplies a test-only Python launcher with scripted
model decisions and real FastAPI/workflow/retrieval services. The Go client will
then exercise both artwork scopes and a documented foreign-evidence injection.
That later check must record actual retrieval calls, the rejection boundary,
trace IDs and conversation commit/isolation outcomes before M1 can close.
