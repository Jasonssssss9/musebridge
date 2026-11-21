# Local Ollama provider

Status: **B phase-4 provider infrastructure is implemented. Real local calls,
token accounting and structured generation have been exercised. Workflow
readiness acceptance remains open; the default service stays disabled/unready.**

The provider implements the asynchronous port in `agent/workflows/ports.py` and
reuses the [private run service](agent-service.md). It does not enable public Go
Agent routing or frontend v2 mode. Those are B phase 5. Readiness and eventual
visitor-quality release are separate gates; an HTTP 200 alone proves neither.

## Pinned profile

`agent/providers/profile.py` defines the initial verified implementation target:

| Setting | Value |
| --- | --- |
| Profile | `local-ollama-v2` |
| Model | `qwen3.5:9b-q4_K_M` |
| Model digest | `56671c2ab9385f9cfcb404638e32cd62d88e3501d44822208363c010179a3c90` |
| Ollama | `0.40.16` |
| Context / reserved output | 16,384 / 2,048 tokens per operation |
| Safety reserve | 512 tokens |
| Sampling | temperature 0; presence penalty 0 |
| Thinking / streaming / server truncation | disabled |
| Model residence | `keep_alive: 30m`; explicit unload remains available |

Model, digest, server, tokenizer, renderer and budgets are validated together.
Another model/server/profile needs an explicit reviewed configuration and fresh
probes. The provider never pulls weights, silently chooses another tag, follows
redirects or downloads assets during a visitor request. HTTPS uses normal
certificate verification; proxy environment variables are not inherited.

## Prepare the tokenizer explicitly

Install Python 3.12 dependencies with the existing lock file. No Ollama Python
SDK, Transformers model loader, inference runtime or additional model weights are
installed by these commands:

```bash
.venv/bin/python -m pip install -r agent/requirements.txt
.venv/bin/python -m agent.providers.prepare \
  --output ../local-model-assets/qwen35-tokenizer.json
```

The explicit preparation command downloads only the public tokenizer from
[Qwen's pinned repository revision](https://huggingface.co/Qwen/Qwen3.5-9B/tree/c202236235762e1c871ad0ccb60c8ee5ba337b9a).
It verifies 12,807,982 bytes and SHA-256
`5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42`.
Existing different files are rejected. Keep model assets, local capability
records and raw probe reports outside the Git repository.

`QwenPromptSizer` checks this file and compares its vocabulary IDs and ordered
BPE merge rules against the actual GGUF metadata from `/api/show`. It supports
only the workflow's system/user text envelope, without tools, images or assistant
prefills. Its wrapper follows the pinned
[Ollama Qwen3.5 renderer](https://github.com/ollama/ollama/blob/v0.40.16/model/renderers/qwen35.go).
It takes the larger raw/NFC BPE token count and adds 64 tokens for renderer/parser
differences. It is a tokenizer-based conservative envelope, not a character ratio
or a claim of exact equality with server counts.

C already includes the complete authoritative output schema in the system
message; it is counted there. The separate generation grammar is not counted a
second time as prose. C may remove oldest whole history pairs to fit, while
required source/context remains. The provider independently checks the fitted
input plus output/safety reserves before inference and checks server prompt
usage against the bound. `truncate: false` prevents server-side prompt removal.

## Structured generation and errors

Requests use the documented [non-streaming chat API](https://docs.ollama.com/api/chat).
`agent/providers/grammar.py` converts the generation schema for Ollama's grammar
compiler: unsupported string patterns/formats, conditional assertions and
`allOf` are omitted from the **generation grammar only**. This avoids a converter
behavior where pattern-only string branches become object-shaped output.
Types, fields, references and union branches remain in the grammar. The full
model-facing schema stays in the prompt and validates the raw decision, including
all omitted assertions. For a follow-up, C binds the selected visitor text,
expands the decision, and independently validates the canonical workflow plan.
Invalid decisions follow the existing generation-error path.

The provider bounds request and complete response bodies to 256 KiB, requires
uncompressed UTF-8 JSON and strictly parses both the HTTP envelope and the JSON
inside `message.content`. Duplicate keys, nonfinite values, malformed Unicode,
extra decision fields, unsupported structures, wrong roles, thinking/tool output,
wrong model, a non-stop finish, missing/invalid usage and exhausted output budgets
are rejected. Version/tag digest checks occur before and after each completion.
Local model tags must remain immutable during operation; these checks are not a
cryptographic transcript proving which GPU weights executed.

The existing shared inference gate covers all model operations and includes slot
waiting in the caller's deadline. C retains its 10-second planning and 15-second
assessment/final caps within the 55-second run budget. Startup verification and
empty-prompt model preloading have separate bounded operator budgets. Returned
completions contain validated data, model identity, finish reason, input/output
tokens and available server/client durations. Routine service execution does not
log full prompts or responses. Only the explicitly invoked probe records its
synthetic visitor inputs and real outputs to private files.

## Capability probes and opt-in startup

Start the already installed Ollama server yourself. The provider cannot install
or start it. Then explicitly run the controlled local probe:

```bash
.venv/bin/python -B -m agent.testing.verify_ollama \
  --tokenizer ../local-model-assets/qwen35-tokenizer.json \
  --report ../local-model-results/ollama-probes.json \
  --capability-record ../local-model-results/ollama-capability.json
```

The probe runs the 13 original development cases, four separately authored
visitor questions and five operation diagnostics. Each visitor case goes through
the workflow and the real HTTP retrieval service. The standalone final-response
diagnostic supplies its own query. Its success is reported outside the visitor
totals.

The private report joins each synthetic question to the model attempts, proposed
English queries, retrieval candidates, validated citations, final text and memory
update by request ID. Failed attempts retain any available model output. A failed
operation can leave later observations unavailable. Inspect the attempt and
lookup records to distinguish a rejected plan, an empty search and an unsupported
answer. The five-pair capacity check compares the turn IDs actually sent and the
server's token count against the pinned budget.

Running the probe invalidates its previous capability record immediately. The CLI
returns a nonzero status while semantic review is pending, including when every
machine check has passed. Read the plan, query, retrieved excerpts, answer and
memory in each case. Review each pending dimension as `pass` or `fail` and retain
`not_applicable` where the report has no applicable observation. The review JSON
contains `report_sha256` (the SHA-256 of the unedited report), `cases` keyed by
case name with the same fields as each `semantic_review`, and optional `notes`
keyed by case name. Include a `final_diagnostic` object with the three verdicts
listed under `final_diagnostic_review`. Keep a private copy of the unreviewed
report if you need to reproduce the review hash. Applying the review updates
the report without calling the model again:

```bash
.venv/bin/python -B -m agent.testing.verify_ollama \
  --report ../local-model-results/ollama-probes.json \
  --review ../local-model-results/ollama-review.json \
  --capability-record ../local-model-results/ollama-capability.json
```

Any failed machine check or semantic judgment keeps the gate closed. A passing
review requires the matching live run, implementation and unexpired observations.
Keep the synthetic reports and review files outside Git. Repeated use of these
published cases makes them development regressions for future releases.

A passing attestation binds model metadata, profile, tokenizer, prompt/provider
implementation, schema and source release. It is valid for at most 24 hours and
must be regenerated after relevant changes. It is a trusted local operator file,
not a signed certificate and not browser-supplied state.

Only after successful probes, start the private service with explicit opt-in:

```bash
export AGENT_PROVIDER=ollama
export OLLAMA_BASE_URL=http://127.0.0.1:11434
export AGENT_TOKENIZER_PATH=../local-model-assets/qwen35-tokenizer.json
export AGENT_CAPABILITY_RECORD=../local-model-results/ollama-capability.json
export RETRIEVAL_BASE_URL=http://127.0.0.1:8001
# Supply AGENT_SERVICE_TOKEN privately as described in the service guide.
.venv/bin/python -m uvicorn agent.app:create_app --factory \
  --host 127.0.0.1 --port 8002 --workers 1 --no-access-log \
  --timeout-graceful-shutdown 65
```

Without `AGENT_PROVIDER`, the existing disabled shell remains. Explicit Ollama
mode requires both local asset paths. Startup verifies model/tokenizer identity,
the capability attestation and the loaded 16K context. Missing, failed, stale or
mismatched attestations keep the service unready. Infrastructure probes perform
no inference. Each readiness and admission check reads the current record. Revocation, removal or
expiry therefore takes effect on the next check. A background metadata-only
identity check can recover transient
connectivity, while model/provider changes invalidate readiness. Startup failures
require operator correction and restart; they never select a test provider.

## November 18 workflow evaluation

The November 14 baseline and this run used the pinned Qwen model, two artworks
and the same four museum excerpts. The new run used a local Agent and retrieval
service with a loopback SSH tunnel to the model host. Each model call used the
production planning or generation deadline. Prompt revisions were tested against
the development questions. The final six scenarios were separately labeled and
kept out of prompt development.

| Visitor set | Machine result | Review of meaning and evidence |
| --- | --- | --- |
| November 14 development baseline, 13 cases | 4/13 met the old status and language criteria | 2/12 delivered answers stayed grounded. One run returned no answer. |
| Same 13 development cases, November 18 | 13/13 met the expanded checks | All 13 bodies stayed grounded. 12/13 passed all dimensions. One memory update inferred that a thank-you ended the conversation. |
| Four additional visitor questions | 3/4 met the expanded checks | 2/4 passed all dimensions. One plan lost an explicit language instruction. A different answer generalized an empty lookup to the entire museum collection and wrote its memory in Spanish. |
| Six independently labeled scenarios | 2/6 met the scenario checks | Two passed fully, one answered part of the question correctly but omitted its evidence citation, and three failed. |

All nine material questions in the original suite, including the five-turn
history case, retrieved and cited the correct artwork's medium. All four
thank-you messages received a short response in the selected language with
no retrieval. The scored 17-case probe made 33 model calls including the
five separate diagnostics. All returned schema-valid completions. One plan
failed the workflow's language consistency check before retrieval. Go's
contract validator accepted all 17 inputs and all 16 delivered results.

The independent set exposed failures that a citation ID or matching status
cannot detect. One compound question mixed supported material and unknown
duration in a single insufficient result. One reaction to a prior turn failed
language and support validation. A natural question about lines creating depth
missed the available perspective excerpt, and the answer overstated what the
empty lookup established. A cached material follow-up chose a direct response
instead of answering from verified context. An explicit one-turn language
switch and a question about an unsupported premise both worked.

The capability record remained invalid after review. The public browser route
still uses the fixture. These counts describe this model run and these small
sets; they are not estimates of quality across museum visitors.

## Controlled M2 application integration

The [M2-B3 guide](public-agent-integration.md) supplies explicit public Go v2
configuration and the separate `agent.testing.integration_app:create_app`
entry point for A/B/C integration. That entry point uses real Qwen, sizing,
identity and workflow validators without claiming a passing semantic capability
record. The normal production factory and its attestation gate are unchanged.
This is a later infrastructure checkpoint; the November 18 quality results above
remain unresolved observations, not public-browser acceptance.

## Checks and remaining acceptance

```bash
.venv/bin/python -B -m unittest agent.tests.test_ollama_provider \
  agent.tests.test_ollama_bootstrap -q
.venv/bin/python -B -m unittest discover -s agent/tests -q
.venv/bin/python -m pip check
python3 -B scripts/generate_agent_contracts.py --check
```

Unit tests simulate malformed/truncated output, model drift, transport/stream
timeouts, cancellation, bounds, grammar conversion, tokenizer integrity and
readiness failures without downloading assets or needing a GPU. Real probe
failures remain distinct from a passing deterministic test suite. Resolve the
recorded B/C workflow failures and rerun probes before claiming model readiness;
complete independent semantic and phone/deployment acceptance under the team
plan. Public Go routing is available by explicit opt-in; real browser/Qwen
acceptance and formal release activation remain pending.
