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
Types, fields, references and union branches remain in the grammar. C's original
schema remains in the prompt and validates every returned decision, including
all omitted assertions. This is preparation before generation, without retries,
post-generation repair or an alternative answer path.

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

This uses real model decisions and a temporary real retrieval HTTP service for
both artworks. It covers four response languages, direct conversation, full
schemas, five-pair history, Unicode sizing and a final-operation diagnostic.
The diagnostic's explicit natural-language query is labeled separately from
model-planned queries and cannot convert a failed end-to-end case into a pass.
The published questions are development probes, not an independent quality set.
Read the generated responses; machine checks cannot prove factual support or
natural language quality.

The CLI exits nonzero when acceptance fails, keeps the failures in its private
report and does not produce a valid readiness attestation. An earlier attestation
is invalidated before retesting. **Current local acceptance has not passed:**
model-planned queries can miss the rule-based retriever, proposed refinements can
repeat previous queries, and unsupported artwork assertions need semantic review.
These failures remain open work under the shared B/C plan. Changing the retrieval
algorithm or weakening source validation is not part of this provider change.

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
no inference. A background metadata-only identity check can recover transient
connectivity, while model/provider changes invalidate readiness. Startup failures
require operator correction and restart; they never select a test provider.

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
plan. Browser/Go activation is still phase 5.
