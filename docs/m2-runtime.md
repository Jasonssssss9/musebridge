# M2 real-model runtime checks (B5)

Status: **Controlled real-Qwen execution is available through Vite, public Go,
private FastAPI, the workflow and real retrieval. Development failures remain.**
This guide reproduces infrastructure observations; it does not grant independent
visitor-quality acceptance or change formal provider readiness.

## Prerequisites

Use the existing pinned Qwen/Ollama profile, verified tokenizer, Python 3.12
environment, Go 1.27+, Node 20+ and the checked-in dependencies. See the
[provider guide](ollama-provider.md) and [public integration setup](public-agent-integration.md).
Ollama must already be running. No command below pulls a model or starts a system
service. If frontend dependencies are missing, install the existing lock with
`npm ci` in `web/`; no additional browser package is required by the runtime CLI.

For a hybrid run, complete the [embedding setup](../knowledge/README.md#run-hybrid-retrieval)
in the launcher terminal first. This installs the splitter and provides the API
key to retrieval. Use the same Qwen model and tokenizer.

Run from the repository root, using a new directory outside the checkout:

```bash
.venv/bin/python -B -m agent.testing.m2_smoke \
  --tokenizer /absolute/path/to/existing/tokenizer.json \
  --report-dir ../m2-runtime-new-run
```

The CLI builds Go, starts temporary loopback retrieval/Agent/Go/Vite processes,
warms the existing model at 16K context and sends published development journeys
through Vite's actual public proxy. Every scenario starts with an independent
visitor; follow-ups retain their actual successful first answer and Go state.
If a prerequisite turn fails, its dependent follow-up is recorded as blocked,
not silently sent with different context. Requests are sequential and failures
are not automatically resubmitted to the model. A nonzero exit retains failures.

Optional filters and explicit private diagnostics:

```bash
.venv/bin/python -B -m agent.testing.m2_smoke \
  --tokenizer /absolute/path/to/existing/tokenizer.json \
  --report-dir ../m2-followup-new-run \
  --case-prefix aic-28560-followup --case-prefix aic-11723-followup \
  --case-prefix language-switch --private-decisions
```

The full runner schedules 25 development scenarios across 37 journeys and 48
visitor messages. Language and artwork variants of a scenario share its ID.
One eight-turn conversation crosses the fifth/sixth-turn history boundary, changes
the interface language mid-conversation and requests a one-reply language switch.
A failed prerequisite blocks each dependent turn. The summary counts those turns
against the scheduled total. Runtime success includes committed history, replay,
model/tool counts and a clean credential scan. Warm latency is measured from
public message submission to its HTTP response. The report gives nearest-rank
p95 and completed-turn counts per route. Failures and blocked turns remain in
the main table; an unobserved route has no p95.

These cases are development/regression material, not independent held-out data.
Do not point the runner at A's draft independent suite or report the development
pass rate as the agreed independent quality metric.

## What is recorded

| Artifact | Meaning |
| --- | --- |
| `report.json` | Original development messages, public responses/traces, expected status/language/source IDs, atomicity, replay/history and joined operation counts |
| `agent-events.jsonl` | Per-trace workflow/model/retrieval metadata, actual chat attempts, accepted token counts, durations and fixed model identity |
| `source-manifest.json` | Input source hashes; the report also pins the starting Git commit and manifest hash |
| `connection.json` | Temporary local URLs/profile while services are active; no token; removed on shutdown |
| `api.log`, `agent.log`, `retrieval.log`, `web.log` | Private local process diagnostics |
| `musebridge-api` | The Go binary built for this run; removable after the run |

Directories use owner-only permissions and evidence files are created with mode
0600. Existing output directories/files are rejected. Service credentials are
generated per run, passed through private backend environments only, stripped
from the frontend environment and scanned out of text artifacts. Never copy raw
reports, model decisions, local paths or credentials into Git.

Normal metadata omits prompts, questions, source excerpts and semantic memory.
`--private-decisions` explicitly enables **development-only** run snapshots and
validated model decisions and canonical workflow results in the owner-only
evidence file, for diagnosing failed plans and reviewing actual next-state memory. It never enables production logging. Public visitor routes cannot select
this option. Reports containing these snapshots remain private.

`workflow_returned` is an executor observation before the registry's final check;
the public Go response/history establishes whether it became a committed turn.
Model HTTP attempts are counted even when a later decision is invalid. This
distinguishes attempted inference from successfully validated completions.

## Actual browser automation on Windows/WSL

Use `--hold-seconds 600` to keep the owned stack available after the CLI journeys.
Read its private `connection.json` for `web_url`. On Windows, run the checked-in
script with an already installed Chrome or Edge:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File web/scripts/m2-browser.ps1 `
  -BaseUrl http://127.0.0.1:REPLACE_PORT `
  -OutputDirectory C:\path\to\an\existing\private\output-directory
```

From WSL, convert the script/output paths with `wslpath -w`. The file includes a
UTF-8 BOM for Windows PowerShell 5.1 so Chinese and Korean test messages remain
unchanged. This process-only execution policy does not alter Windows policy.
The script uses Chrome DevTools directly, a unique temporary browser profile and
a phone-sized viewport. It installs nothing and removes its profile on exit.

The script checks rendered reply text and its language tag against the saved turn,
and verifies both localized material terms, the active artwork’s source link
and the quoted museum passage. This narrow wording check applies only to the
known medium questions.
It also records real form submission, refresh, follow-up,
artwork isolation, language changes and switch-back. A browser-only fault hides
one successful HTTP response after the real commit. The check observes the waiting
indicator and requires exactly one recovered turn from public history. Outputs are
`browser-report.json` and `browser.png` outside Git. Cases that return model errors
stay failed. This is automated development evidence, not A's independent browser
sign-off or a physical-phone check. Physical-phone acceptance belongs to M3.

While held, create a file named `release` in the run directory to finish early.
Otherwise the bounded hold expires. Owned processes are stopped on normal exit
or interruption; the pre-existing Ollama server is not stopped. The model retains
the pinned 30-minute keep-alive. Clean up an exact run directory only after its
children have stopped and its evidence is no longer needed.

## Readiness and remaining quality

The controlled factory's HTTP routes now read the runtime installed by lifespan
from application state. Positive readiness and authenticated POST/GET regression
checks protect against the initial empty shell being retained by route closures.
The normal factory and its capability attestation requirements are unchanged.

HTTP 200, valid JSON and a real citation ID do not establish faithful question
interpretation, source entailment or safe memory. Follow-up and mixed-language
planning failures require workflow review. Preserve these results and reproduce
them before changing prompts; reclassify any used independent case as regression.

See the [public development results](m2-public-results.md) for measured outcomes and the [M2 handoff checklist](m2-handoff.md) for reviewer dependencies and formal
closure. The [B4 reliability suite](public-agent-reliability.md) remains the
deterministic public-path fault gate.
