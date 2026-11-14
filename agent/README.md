# MuseBridge Agent: C M1 foundation

Status: **C's M1 implementation is ready for integration. The full team M1 gate remains open.**

This package implements C's workflow, context, prompts, retrieval tool and evidence validation from the [Agent design](../docs/agent-design.md) and [delivery plan](../docs/agent-plan.md). It uses LangGraph with an injected provider and run budget. The only runnable provider in this change is an explicitly scripted test substitute.

The application still runs its existing fixture path. This change does not add FastAPI routes, Go integration, model deployment, a production provider or a second run registry.

## 1. Implemented path

```text
RunRequest
  -> validate scope, metadata and bounded history
  -> prepare context and request a structured plan
  -> validate request/query links
      -> clarify or out_of_scope -> assemble terminal answer
      -> retrieve -> call the existing HTTP service for each query
                  -> verify and merge released evidence
                  -> request a structured assessment
                  -> validate support and citations
                  -> assemble terminal answer
```

The normal path uses two model operations and one or two sequential retrieval calls. An early clarification or scope decision uses one model operation and no retrieval. There are no retries or fallback answers.

M1 supplies `FinalDecision` to assessment with `allow_refinement: false`. A returned refinement action fails validation. M2 will add the documented single refinement branch and evaluate its usefulness; this skeleton does not claim to implement that branch.

The workflow can assemble `answered`, `partially_answered`, `insufficient`, `clarification_needed` and `out_of_scope`. The model proposes per-request support. Code enforces request coverage, scope, citation membership, text order and status derivation. A nonempty candidate list does not automatically make an answer supported.

## 2. Local setup and checks

Run these commands from the repository root in WSL. Python 3.12 is the tested environment.

```bash
cd /home/jason/projects/musebridge
python3 -m venv .venv
.venv/bin/python -m pip install -r agent/requirements.txt
.venv/bin/python -m unittest discover -s agent/tests -v
python3 -m unittest discover -s knowledge/tests -v
.venv/bin/python -m pip check
```

`requirements.in` lists direct dependencies. `requirements.txt` pins the complete tested environment, including LangGraph's dependencies. The existing `.venv/` ignore rule keeps the environment out of Git. No model weights, tokenizer, inference server or GPU setup is needed for these checks.

Run the controlled demo:

```bash
.venv/bin/python -m agent.testing.demo
.venv/bin/python -m agent.testing.demo --artwork-id aic-11723
.venv/bin/python -m agent.testing.demo --scenario partial
.venv/bin/python -m agent.testing.demo --scenario clarify
```

The demo starts the existing retrieval HTTP server on an ephemeral loopback port, invokes the same workflow, prints JSON and shuts down its server. Its output is labeled `mock-model-real-retrieval`. It is a diagnostic result, not a public API response or a measured LLM answer. It intentionally has no invented production model identity. It supports fixed scenarios, not arbitrary visitor questions.

The real retrieval tool calls the unchanged `knowledge.http_service` over HTTP. It does not replace that service with fixture answers or fetch museum pages during a request.

## 3. Package map

| Location | Responsibility |
| --- | --- |
| `workflows/museum_qa.py` | LangGraph transitions, per-run state, fixed scope, call bounds and terminal outcome |
| `workflows/ports.py` | Structural expectations for B's provider, profile, budget and the evidence tool |
| `runtime/context.py` | Input projection, history checks and injected prompt sizing |
| `runtime/validation.py` | Shared JSON Schema consumption, strict JSON helpers, plan links and answer assembly |
| `runtime/errors.py` | Safe typed errors for the infrastructure layer |
| `prompts/` | Versioned English policy and JSON-encoded runtime inputs |
| `skills/artwork_qa/` | Skill behavior and constraints |
| `tools/evidence.py` | Immutable release manifest and canonical evidence validation |
| `tools/retrieval.py` | Read-only HTTP retrieval adapter |
| `testing/` | Scripted model, synthetic profile/sizer, test clock and local demo |
| `tests/` | Contract, context, graph, evidence and real-HTTP tests |

The JSON Schema remains in [the shared contract](../docs/contracts/agent-v1.schema.json). This package does not create competing wire types under B's `agent/contracts/`.

Versions: `museum_qa_v1`, `museum-prompts-v1`, `agent-schema-v1`, `evidence-tool-v1`, and `legacy-facet-v0.1`. The skill version is `artwork-qa-v1`.

## 4. Handoff to B

### Workflow entry point

Create one `MuseumQAWorkflow` with:

- `model`: B's structured provider.
- `profile`: B's verified profile object, exposing `profile_id`, `context_tokens`, `plan_output_tokens` and `answer_output_tokens`.
- `prompt_sizer`: B's verified callable `(messages, output_schema) -> integer token count`.
- `manifest`: `EvidenceManifest.load()`.
- `retrieval`: `RetrievalTool(manifest, base_url=configured_origin)`.

Call:

```python
outcome = await workflow.run(
    validated_run_request,
    request_id=validated_trace_id,
    budget=admitted_run_budget,
)
```

The request is a JSON-compatible dictionary matching `RunRequest`. C checks the schema, trimmed original question, scope, metadata and history relationships. B owns authentication, strict inbound HTTP decoding, supported-contract classification, history authenticity, run admission, replay, cancellation ownership and UTC-to-monotonic deadline conversion. An expired historical fixture deadline is not an admission test.

The injected budget provides:

| Method | Requirement |
| --- | --- |
| `check_active()` | Raise `WorkflowError` on total timeout or propagate cancellation; no further model/tool operation may start |
| `remaining_seconds()` | Return remaining time from the same monotonic deadline used by B's run registry |

C checks this budget before and after external work and before returning. B must actively cancel the run at its deadline; these checks do not replace B's runtime timer. Budget checks execute on the event loop. The provider also enforces its child deadline, and the retrieval adapter enforces its own shorter lookup deadline.

### Model interface

The port follows design section 8.1:

```python
completion = await model.complete_structured(
    operation=operation,              # "plan" or "assess" in M1
    messages=messages,
    output_schema=output_schema,
    profile=verified_profile,
    deadline=absolute_monotonic_deadline,
    trace_context={"request_id": request_id},
)
```

The output schema is self-contained and includes only reachable definitions. The planner receives `Plan`; M1 assessment receives `FinalDecision`. C reads a detached copy of `completion.data` and independently checks its shape and semantic relationships.

B's complete result must retain validated provider/model identity, completion reason, available token usage and durations. The small Protocol describes the fields C consumes; it does not remove the metadata required by the design. Provider failures raise `WorkflowError`, not successful completion objects containing an error.

B owns the real model profile, shared inference slot, HTTP/JSON protocol, finish/truncation checks, token sizing and response limit. Slot wait, network, full reads and decoding must fit the supplied deadline. No automatic retries, format repairs or fallback provider are permitted. C also rejects returned results that are already late.

Initial profile values remain 8,192 context tokens, 768 plan-output tokens and 1,536 answer-output tokens. C reserves the documented 512-token safety margin. The sizer must count the full messages, schema and provider template overhead. Only oldest whole history turns may be removed; the original question, plan and evidence stay intact.

`FixtureProfile`, `fixture_prompt_sizer`, `ScriptedModel` and `TestBudget` are test-only dependencies. Do not import them into production handlers or use their results as evidence of model readiness.

### Outcome and error assembly

`WorkflowOutcome.result_fields()` returns detached `scope`, `answer` and `request_results`.

`WorkflowOutcome.execution_fields()` returns the workflow/prompt/schema/retrieval versions and call counts. B adds `profile_id`, the verified `model_provider`, `model_name`, `model_digest` and measured total `duration_ms`, then validates the complete `RunResult`. B wraps the run snapshot, persists/replays it and maps errors. Go sets the public answer mode.

`WorkflowError.as_dict()` produces the `AgentError` fields: `code`, `message`, `retryable` and `phase`. Error messages contain no raw visitor input, model body or source payload. Cancellation propagates; C does not turn a cancellation or dependency failure into a completed turn.

Controlled diagnostics include executed queries, completeness, retained-history count and phase timings. Queries can contain visitor text, so they are for deliberate evaluation, not automatic public responses or routine logs. No graph checkpoint or cross-conversation memory is enabled. Ambient LangSmith tracing is suppressed during execution; raw LangChain debug/verbose logging must be disabled. Deployment should also unset legacy `LANGCHAIN_TRACING` and `LANGCHAIN_HANDLER` flags.

## 5. Retrieval and evidence guarantees

The source release remains `knowledge/data/aic-seed-v0.1.json`, with SHA-256:

```text
3703fd4c9fcefb8e9fba20f23a5cb70e612c62ef7cb3f594a15d319ebc18d6ac
```

Startup verifies the hash and existing dataset constraints from one byte snapshot. The manifest stores canonical immutable projections of all four evidence records. It does not retrieve additional material.

For every lookup, runtime injects `artwork_id`, `dataset_version`, `top_k: 2` and the tool version. The model cannot select an endpoint or authorize another artwork. The adapter sends the existing four-field legacy request and converts its response into `ToolResult`.

The tool rejects invalid JSON, schema violations, unexpected statuses/headers, foreign artwork IDs, wrong releases, altered excerpts or attribution, duplicate IDs within one response and mismatched trace IDs. All 12 canonical evidence fields must equal the reviewed release. Compatible extra response metadata is discarded. Identical cross-call records are deduplicated in first-seen order.

The adapter disables redirects and retries, requests uncompressed UTF-8 JSON, bounds the response at 64 KiB and enforces a complete-call deadline of at most three seconds, including validation. JSON validation also bounds nesting at 64 levels. Connection/5xx failures, lookup timeout and invalid responses remain distinct typed errors. A successful empty response remains an empty candidate list.

The evidence union is complete only when its IDs cover every manifest record for the authorized artwork and release. Empty results or the number of returned candidates cannot establish completeness.

Final citations must belong to the accepted current-run union. Code resolves their complete records from that union; model-created URLs or excerpts never enter the answer. These guarantees establish provenance and structural consistency. They do not prove that a cited passage semantically supports every generated claim.

## 6. Acceptance evidence and remaining work

Validation for this implementation:

| Check | Coverage |
| --- | --- |
| Agent tests | 83 passing tests covering context, prompts, canonical evidence, tool faults, strict contracts and graph paths |
| Shared fixtures | All 35 schema examples and all 13 runtime examples applicable to M1 |
| Deferred fixtures | Three refinement runtime examples belong to M2; they are not counted as passing M1 behavior |
| Real retrieval | Both artworks, material and brushwork, empty lookup, merged complete release and partial answers |
| Isolation/failures | Foreign/altered evidence, missing or invented citations, duplicate queries, cancellation, concurrent scopes, expired budgets and invalid model decisions |
| Legacy regression | Existing 139 knowledge tests, including their retrieval evaluations |
| Local smoke | All six combinations of the two artworks and three demo scenarios pass |

These are deterministic correctness checks. The controlled model already knows which decision to return. They do not measure query rewriting quality, referent resolution, semantic support, answer-language quality, token accuracy, real inference latency or resistance to prompt injection by an actual model.

To close the **full team M1**, B still needs the FastAPI/run/provider implementation, verified real model access and the Go -> Agent -> real retrieval contract test; A still needs its assigned UI/mocks and independent questions. B and C must verify the production implementations against the ports above.

C's M2 work is the bounded refinement branch, real-provider integration with B, independently labeled workflow evaluation, and measured planning/support improvements. The existing retrieval algorithm and four evidence records were preserved in this change.
