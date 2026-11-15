# Private Agent run service

Status: **B phase 3 is implemented. Service/lifecycle tests and the real HTTP
chain through C's v2 workflow and retrieval pass with controlled model decisions.
Production inference and Go-to-Agent integration remain pending.**

This implements the private `agent-run-v2` boundary in the
[interface contract](agent-api-contract.md#3-private-agent-transport). The public
application continues to use fixture generation. The standalone service starts
without a model provider and deliberately reports not ready; it cannot generate
visitor answers until a verified provider/profile/sizer is supplied in phase 4.
No test provider is selected by a production environment variable.

Phase 4 now provides [explicit Ollama configuration](ollama-provider.md). Its
readiness probes have exposed unresolved workflow failures, so the production
capability gate remains closed. The factory still defaults to the disabled shell.

## Run the infrastructure shell

Use Python 3.12 and the pinned dependencies, from the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r agent/requirements.txt
export AGENT_SERVICE_TOKEN="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')"
.venv/bin/python -m uvicorn agent.app:create_app --factory \
  --host 127.0.0.1 --port 8002 --workers 1 \
  --timeout-graceful-shutdown 65 --no-access-log
```

Supply the same token to the future Go private client through private
configuration. The example generates a new token for the current shell; existing
clients must receive any rotated value. Never commit it or expose it to a
browser. The factory rejects missing, short or non-visible-ASCII configuration;
the minimum encoded length is 43 characters, suitable for 32 random bytes encoded
as unpadded base64url. Length validation cannot establish actual randomness.

The service is private. Bind loopback for native development; later deployment
must keep the Agent off public browser/proxy routes. Use **one worker and one
service instance** with this in-memory registry. Multiple workers/replicas would
not share run identities, results or inference capacity. Automatic docs, OpenAPI,
trailing-slash redirects and FastAPI telemetry/exporter auto-configuration are
disabled. There is no CORS or cookie middleware.

| Route | Behavior |
| --- | --- |
| `GET /healthz` | 200 with `{"status":"ok"}` while the HTTP service is live |
| `GET /readyz` | 503 until the injected readiness check confirms the verified profile and capability record; a successful probe performs no new inference |
| `POST /internal/v2/agent/runs` | Authenticate, validate, admit once and wait for a terminal result; 200 only for completed work |
| `GET /internal/v2/agent/runs/{run_id}` | Authenticate and return a retained processing/completed/failed snapshot; unknown or evicted IDs return 404 |

Only the two infrastructure probes may omit authentication and trace headers.
All other requests require one Bearer authorization and exactly one
`X-Request-ID` containing 1–128 visible ASCII characters. Authentication precedes
run lookup. Every response carries `Cache-Control: no-store` and a safe trace;
invalid trace headers receive a generated ID rather than being echoed.

Run POSTs accept uncompressed UTF-8 JSON with at most 256 KiB in the complete
body. The optional charset must be UTF-8. Duplicate identity/content headers,
unknown query parameters/fields, malformed or duplicate-key JSON, nonfinite
numbers, excessive nesting and type coercion are rejected. Both declared lengths
and streamed bytes are bounded; body reading has a 10-second ceiling. Responses
are validated and bounded to 256 KiB before a completed result is retained.
Errors use the shared safe envelope and [HTTP mappings](contracts/agent-v2.errors.json),
without provider text, prompts or stack traces.

## Run identity and ownership

After authentication and static shape validation, admission checks an existing
run ID before new-run deadlines, supported profiles, readiness or capacity:

| Existing identity | Outcome |
| --- | --- |
| Same parsed body, processing | 409 `agent_run_in_progress`; no additional execution |
| Same parsed body, completed | Return the retained result, even after its deadline or during a dependency outage |
| Same parsed body, failed | Replay the retained failure and mapped non-2xx POST status |
| Different parsed body | 409 `agent_run_conflict` |

Object key order, JSON whitespace and trace headers do not change identity.
Arrays retain their order. The original message, language override, complete
state/history/catalog snapshot, workflow/profile and original deadline do change
identity. Input and returned snapshots are detached from callers' mutable data.
Static malformed input still fails validation before this identity comparison.

The registry owns accepted work. Closing/cancelling the POST connection does not
cancel the run. A client that loses a response should GET the original ID within
its original attempt budget; it must not automatically submit a fresh ID or
restart an unknown run. GET returns 200 for all retained states, including
failed, whereas POST failures always use the error envelope.

Terminal results remain in memory for at most 600 seconds and at most 1,000
records; expiry/oldest-record cleanup occurs on registry activity. Active work
is never evicted to make room. There is no persistent recovery across process
restart and no guarantee of exactly-once GPU execution across restarts or network
partitions. Go still owns conversation revisions and the final atomic commit.

## Deadlines, inference and shutdown

New-run admission converts `deadline_at` from UTC to a monotonic remaining
budget, capped at 55 seconds. It rejects expired input and reuses C's canonical
scope/catalog/state/history/cache checks. This budget is passed into the
workflow, which checks it around external operations.

The process admits at most eight active runs. One shared inference gate wraps
the injected provider across planning, assessment, final generation and callers
explicitly sharing that gate, including future direct evaluation. Waiting for
the slot counts against each model operation's absolute deadline. C selects
10 seconds for planning and 15 for assessment/final generation, also bounded by
the parent run. The inference slot is released between operations, including
during retrieval. No wrapper retry, format repair or fallback is added.

Run supervision records terminal timeout/failure without waiting indefinitely
for cancellation acknowledgement. A still-running workflow keeps its capacity
slot; a still-running provider operation keeps the shared inference slot.
Cancellation is requested, late exceptions are consumed safely, and late results
cannot replace a terminal failure. This controls application acceptance; it does
not prove a remote GPU immediately stopped computing. The phase-4 Ollama provider
supplies cancellation-aware, bounded HTTP transport as well.

Shutdown stops new admission, allows up to 65 seconds to finish, then records
failure and cancels outstanding work within that drain budget. Retained results
can still be read/replayed while the registry exists. The ASGI lifespan closes
the registry; Uvicorn also stops accepting connections during server shutdown.

## Wiring responsibilities

| File | Responsibility |
| --- | --- |
| `agent/app.py` | FastAPI factory, raw ASGI auth/trace boundary, strict body handling, routes and safe HTTP errors |
| `agent/runtime/runs.py` | Atomic admission, immutable identity, task supervision, retained snapshots, capacity and shutdown |
| `agent/runtime/budgets.py` | Monotonic parent budget and shared provider gate |
| `agent/runtime/service_errors.py` | Fixed safe messages and shared private HTTP policy |
| `agent/service.py` | Explicit dependency injection into C's `MuseumQAWorkflowV2` and observed execution metadata |

`create_workflow_runtime` requires a model implementing `complete_structured`, a
matching profile, tokenizer-compatible prompt sizer, loaded `EvidenceManifest`,
retrieval tool, actual `ModelIdentity` and a verified readiness callback. It
creates one gate and one registry. The underlying model interface and completion
requirements remain those in `agent/workflows/ports.py`. The gate does not supply
provider transport, model identity discovery, token counting or output parsing.
The runtime measures duration and validates result bindings before retention.

Tests explicitly inject `FixtureProfile`, `ScriptedModel`, a synthetic sizer and
clearly synthetic model name/digest. The contract's provider discriminator is
`ollama`; this literal in test results is not evidence of an Ollama request.
Production modules do not import those test substitutes. Default readiness stays
false; phase 4 must establish the pinned model, v2 structured-output reliability,
16K context, output budgets and four-language probes before changing that state.

## Verification and remaining work

```bash
.venv/bin/python -B -m unittest \
  agent.tests.test_run_service_runtime \
  agent.tests.test_agent_http_service \
  agent.tests.test_agent_service_integration -q
.venv/bin/python -B -m unittest discover -s agent/tests -q
.venv/bin/python -B -m unittest discover -s knowledge/tests -q
.venv/bin/python -m pip check
python3 -B scripts/generate_agent_contracts.py --check
```

The focused checks cover duplicate/changed submissions, failed/completed replay,
lost POST responses, actual HTTP retrieval for both artworks, input boundaries,
safe errors, capacity, retention, hard cancellation, late results, shared
inference and shutdown. Loopback listeners are temporary and closed by tests.

Recorded phase-3 verification: 38 focused tests pass; the complete Agent suite
passes 213 tests and the knowledge suite passes 139. `pip check` and the shared
contract generation check pass. All 49 pinned Python distributions match the
installed versions. These are controlled execution results, not measurements of
the local Qwen model.

Phase 4 supplies the real Ollama provider/profile/sizer; its workflow capability
acceptance remains open as documented in the provider guide.
Phase 5 connects Go's private client, reconciliation and public v2 routing to
the existing [atomic state store](agent-state-store.md). Phase 6 completes
cross-service, deployment, real-model and phone acceptance. This phase does not
close team M1 or establish model answer quality.
