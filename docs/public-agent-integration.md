# Public Go Agent integration (M2-B3)

Status: **Public v2 routing is implemented and checked through real Go HTTP,
FastAPI, workflow and retrieval services with a scripted provider.** The separate
controlled Qwen entry point is available for A/B/C integration. Its startup
lifecycle is tested with a simulated provider; this checkpoint does not claim
browser acceptance, real-model quality, measured latency or production readiness.

## Select one public contract

| Go configuration | Frontend configuration | Contract / behavior |
| --- | --- | --- |
| `GENERATION_MODE=fixture` (default) | `npm run dev:live` | Existing `local-demo-v0.1`, fixed reviewed answers |
| `GENERATION_MODE=agent` | `PUBLIC_CONTRACT=agent npm run dev:live` | `agent-demo-v0.3`, private Agent execution |

Go selects the contract at startup. Public request fields cannot select a model,
provider, private endpoint, release or execution profile. There is no fallback
from a failing Agent to fixture answers. Old fixture conversations are not
imported. In-memory sessions expire after 24 hours and are lost on Go restart.

Both profiles retain the artwork route, exact Origin validation, host-only
HttpOnly visitor cookie and server-generated trace IDs. Agent mode provides:

| Route | v2 behavior |
| --- | --- |
| `GET /api/artworks/{slug}` | Existing released artwork card |
| `POST /api/conversations` | Strict `{contract_version, artwork_id}`; create/restore the artwork's v2 conversation and refresh its cookie |
| `GET /api/conversations/{conversation_id}/messages` | Authoritative conversation, completed turns and nullable original pending submission |
| `POST /api/conversations/{conversation_id}/messages` | Strict five-field v2 request; original-run execution/recovery, result validation and atomic `{conversation, turn}` |

The [v2 contract](agent-api-contract.md#2-public-application-contract) is
authoritative. All five submission fields are required, including nullable
`language_override`. Supported selector values are null, `auto`, `en`, `es`,
`zh-CN` and `ko`. Only surrounding Unicode question whitespace is trimmed;
then 1–500 code points are required. UUIDs use lowercase canonical text.

Bodies are at most 8 KiB, including whitespace, and uncompressed UTF-8 JSON.
Missing/unknown/duplicate fields, invalid Unicode, controls, trailing JSON and
integer coercions fail before Agent execution. A structurally valid unsupported
public contract returns 409 `agent_contract_mismatch`; malformed input is 400.
Origin/media-type/size failures retain 403/415/413. Technical failures create no
turn or revision and remain visible as failed pending submissions. A successful
replay skips inference and preserves the saved turn with the current projection.

## Native integration configuration for A

Use the existing Python 3.12 virtual environment and pinned Agent dependencies,
Go 1.27+, and the installed frontend dependencies with Node 20+. Real Qwen also
needs the already prepared tokenizer, pinned model and a running Ollama server;
see the [provider guide](ollama-provider.md). These commands install or download
nothing. All terminals below start in the repository root.

Create one temporary, owner-only token file outside the repository:

```bash
.venv/bin/python -B - <<'PY'
import os, secrets, tempfile
fd, path = tempfile.mkstemp(prefix="musebridge-m2-", suffix=".env")
with os.fdopen(fd, "w") as stream:
    stream.write("export AGENT_SERVICE_TOKEN=" + secrets.token_urlsafe(32) + "\n")
print(path)
PY
```

Only the path is printed. In the **Agent and Go terminals**, run
`source /tmp/musebridge-m2-REPLACE.env`, using that actual path. Both processes
must receive the same token. Never put it in browser variables, command arguments,
logs or Git. Remove this exact temporary file after stopping both services.

In terminal 1, start real retrieval:

```bash
.venv/bin/python -B -m knowledge.http_service --host 127.0.0.1 --port 8001
```

In terminal 2, load the private token file, then explicitly select controlled
real-model integration:

```bash
export AGENT_INTEGRATION_MODE=controlled-ollama
export AGENT_TOKENIZER_PATH=/absolute/path/to/the/existing/tokenizer.json
export OLLAMA_BASE_URL=http://127.0.0.1:11434
export RETRIEVAL_BASE_URL=http://127.0.0.1:8001
.venv/bin/python -B -m uvicorn agent.testing.integration_app:create_app --factory \
  --host 127.0.0.1 --port 8002 --workers 1 --no-access-log \
  --timeout-graceful-shutdown 65
```

The separate `agent.testing.integration_app` factory runs real Ollama, the real
workflow and real retrieval. It requires explicit opt-in and service auth; checks
the pinned model/server/tokenizer identity, warms the model, enforces token and
stage budgets, and checks model identity periodically. Startup/model failures
leave it unready. It does **not** require or create a passing semantic capability
record. Its readiness means controlled infrastructure availability only. Do not
use it as production activation or count it as evidence that release gates pass.

The normal `agent.app:create_app` factory remains unchanged: default disabled,
and explicit Ollama mode still requires a valid capability attestation. For a
formally approved service, use that factory and the provider guide instead.

In terminal 3, load the same private token file, then start Go:

```bash
export GENERATION_MODE=agent
export AGENT_BASE_URL=http://127.0.0.1:8002
export PUBLIC_BASE_URL=http://localhost:8080
export COOKIE_SECURE=false
go -C api run ./cmd/server
```

`go -C api` uses `api/` as the working directory for the default seed path.
`AGENT_BASE_URL` must be an HTTP(S) origin without credentials, path, query or
trailing slash. `AGENT_SERVICE_TOKEN` is required in Agent mode and must encode
at least 32 random bytes as 43+ visible ASCII characters. Invalid configuration
fails startup without printing its values. Go makes no direct retrieval/model
calls in this mode. Environment files are not automatically loaded by Go.

In terminal 4, start A's existing frontend:

```bash
cd web
PUBLIC_CONTRACT=agent API_TARGET=http://127.0.0.1:8000 npm run dev:live
```

Open <http://localhost:8080/a/aic-28560> and
<http://localhost:8080/a/aic-11723>. The browser calls only relative public Go
APIs through Vite. It needs no service token, model URL or private Agent URL.
If the browser uses a different origin, set Go's `PUBLIC_BASE_URL` to exactly
that origin. Do not mix `localhost` and `127.0.0.1`. Keep private services on
loopback for this native setup. Existing Compose remains the fixture setup;
changing only the web build's contract does not switch the Compose Go service.

## Readiness, timing and recovery

```bash
curl -fsS http://127.0.0.1:8000/readyz
curl -fsS http://127.0.0.1:8001/readyz
curl -fsS http://127.0.0.1:8002/readyz
```

Go `/readyz` means its local catalog is loaded, not that Agent/Qwen/retrieval or
quality gates pass. Check each service separately. Health routes remain private
and are not proxied by the web app. The controlled Agent checks model identity;
an individual retrieval failure is handled on the actual run.

| Boundary | Budget |
| --- | ---: |
| Agent execution from Go admission | 55 s |
| Go accepted attempt, including recovery/commit | 60 s |
| Agent-mode Go HTTP write and graceful drain | 65 s |
| Existing browser wait / history read | 70 s / 10 s |
| Existing Vite / nginx proxy | 75 s |

The [submission service](agent-submission.md) owns one private POST and bounded
GET reconciliation of the original run. A disconnected browser does not cancel
accepted work. History exposes processing and terminal state; a completed retry
returns the saved turn. Different artwork histories stay separate. A stale draft
returns 409 and must be reviewed before a new logical submission. Failed attempts
cannot add evidence or advance revision. SIGTERM/Ctrl+C drains Go for up to 65 s
in Agent mode; fixture write/drain limits remain 15 s / 10 s.

## Repeatable no-GPU verification

With the documented Python dependencies already installed, run from the root:

```bash
go -C api test -race -tags=integration \
  -run '^TestM2B3PublicAgentIntegration$' -count=1 -timeout=120s -v ./internal/httpapi
```

This command starts and closes temporary actual Go HTTP, FastAPI and retrieval
listeners. Only the model decisions are scripted. It checks both artwork cards,
cookie-backed v2 creation/restore, material answers with the correct medium ID,
revision/history, completed replay, stale rejection, artwork switching, and
foreign-evidence rejection for both artworks. Normal cases make two model calls
and one lookup each; invalid-evidence cases make one model call and one lookup,
return 502 `retrieval_invalid_response` and remain at revision 0. Replays,
restores and conflicts add no inference. Missing services fail the test.

For a manual no-GPU connection check, the existing
[test-service launcher](m1-test-services.md#start-endpoints-for-another-client)
can replace terminals 1 and 2. Use its printed `agent_url` as Go's
`AGENT_BASE_URL` and share its token privately. That provider intentionally
supports only the initial English material question documented there; it cannot
test general questions, language quality or conversational follow-ups.

Additional checks:

```bash
go -C api test -race ./...
go -C api vet ./...
.venv/bin/python -B -m unittest agent.tests.test_integration_app -v
python3 -B scripts/generate_agent_contracts.py --check
```

Public-path fault coverage continues in M2-B4. Actual Qwen/browser acceptance,
call counts, model/profile identity, latency and disputed-answer review remain
M2-B5/B6 and A/C work. No independent visitor questions are used by these
deterministic checks.
