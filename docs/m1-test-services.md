# M1 test services

Status: **B M1 integration step 2 supplies a standalone Python service harness.
It runs the real private FastAPI service, v2 workflow and retrieval HTTP service
with a scripted model at the provider boundary. Go-originated acceptance and
the final M1 trace table are still pending.**

The [Go POST client](agent-client.md) is implemented separately. This harness
provides temporary endpoints for its next integration step. It does not enable
the public browser route or establish Qwen readiness or answer quality.

## Prerequisites and repeatable check

Use Linux/WSL, Python 3.12, the checked-in seed and existing pinned dependencies.
From the repository root, if the environment has not already been installed:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r agent/requirements.txt
```

Run all harness checks with one command:

```bash
.venv/bin/python -B -m unittest agent.tests.test_m1_services -v
```

This command starts real loopback HTTP listeners, sends requests with a Python
HTTP client, verifies the cases below and closes the services. It needs no GPU,
Ollama, tokenizer asset, Docker or external network after dependencies are
installed. It is a Python harness check; it does not replace the required
Go-originated M1 command.

| Check | Observed requirement |
| --- | --- |
| The Bedroom, `aic-28560` | Completed, answered; actual HTTP lookup returns and answer cites `aic-28560-medium` from `aic-seed-v0.1` |
| Woman at Her Toilette, `aic-11723` | Completed, answered; actual HTTP lookup returns and answer cites `aic-11723-medium` from the same release |
| Foreign evidence, either scope | HTTP response contains the other artwork's medium; real tool rejects it with 502 `retrieval_invalid_response`; retained run is failed with no result; no assessment model call occurs |
| Retrieval stopped | No fabricated answer; real connection failure produces 503 `retrieval_unavailable` |
| Missing authentication / unsupported question | No answer shortcut and no retrieval; normal private errors |
| Concurrent scopes and retained replay | Independent scripted decisions; completed replay makes no extra model or retrieval calls |
| CLI startup, SIGTERM and task cancellation | Ephemeral endpoints become ready, then close; startup errors stay safe and service tokens stay out of output |

## Start endpoints for another client

The standalone launcher remains in the foreground until Ctrl+C or SIGTERM.
Use a new state directory outside the checkout. The parent must exist; existing
state directories are never reused or overwritten.

```bash
export AGENT_SERVICE_TOKEN="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')"
state_root="$(mktemp -d -t musebridge-m1.XXXXXX)"
.venv/bin/python -B -m agent.testing.m1_services --state-dir "$state_root/services"
```

Both listeners bind `127.0.0.1` with OS-assigned ports. After actual HTTP readiness
checks, the launcher prints one `services_ready` JSON object and writes the same
object to `connection.json`. It contains `agent_url`, `retrieval_url`, mode, fault,
dataset/contract versions and the synthetic model name. It contains no token.
The client must receive the same `AGENT_SERVICE_TOKEN` privately; do not echo it
or put it in a command argument, report or committed configuration.

Send an initial English v2 `RunRequest` with this exact question:

```text
What material is this painting made of?
```

Use either released artwork, an empty history/evidence cache, a valid original
conversation snapshot, fresh run/message IDs and the original unexpired Agent
deadline. The real FastAPI boundary checks Bearer auth, trace headers, admission,
identity and contracts. The fixed planner asks about the supplied artwork title.
The real workflow executes HTTP retrieval. Assessment uses the accepted passage
in its actual model input and cites its scoped medium ID. Other questions or
model routes deliberately fail; this is not a general-purpose test chatbot.

The model name is `SYNTHETIC-M1-TEST-ONLY`, with an explicitly synthetic digest.
The existing wire contract still requires provider `ollama` and profile
`local-ollama-v2`; those two literals in this test result do not imply Ollama was
contacted. Token sizing is synthetic. Harness readiness confirms test-service
availability only. Production provider selection, capability probes and default
unready behavior are unchanged; no production module imports this launcher.

## Foreign-evidence injection and audit

For a separate rejection environment, start a new directory with:

```bash
.venv/bin/python -B -m agent.testing.m1_services \
  --state-dir "$state_root/foreign" --fault foreign-evidence
```

Only this test launcher installs `M1RetrievalHandler`, a subclass of the real
retrieval server's handler. The real `/retrieve` request first executes the
normal retrieval function. Immediately before HTTP response serialization, the
test handler replaces its evidence list with the other artwork's released
medium record. The original seed, dataset, request scope and Agent validators
remain unchanged. All successful retrieval responses in this fault-mode
environment are corrupted; use a separate normal environment for happy cases.

`RetrievalTool.search` parses the actual HTTP body, then
`EvidenceManifest.validate_candidates` rejects the foreign chunk ID for the
active artwork. The workflow never reaches assessment or produces a successful
answer. The private Agent records a failed run and maps the error normally.
No Go conversation store participates in this step; atomic turn/revision/cache
assertions belong to the later Go integration check.

The owner-only state directory contains `audit.jsonl` and, while ready,
`connection.json`. Files use mode 0600; the directory uses 0700. Each audit entry
is flushed for a separate test process to inspect. Recorded fields include trace
ID, active artwork, dataset, original and transmitted evidence IDs, the fault
boundary, model operation and tool acceptance/error code. It records no service
token, authorization header, visitor prompt, raw model body or source excerpt.

On shutdown both servers close, `connection.json` is removed, and the audit ends
with `services_stopped`. The private audit remains for inspection. Once the
launcher has exited and its report is no longer needed, remove its exact
temporary state directory. Runtime artifacts must not be copied into the repo.

Implementation: [test-only launcher](../agent/testing/m1_services.py),
[checks](../agent/tests/test_m1_services.py). Next: originate both normal cases
and the rejection case in Go using the reusable client and actual conversation
snapshots, then provide the repeatable Go command and trace/evidence table.
