# Go and Python Agent boundary contracts

B integration phase 1 is implemented: v2 wire types, strict codecs, pure snapshot
checks and error mappings. The [Go state store](../agent-state-store.md) and
[private run service](../agent-service.md) implement phases 2 and 3. Production
inference and the application client remain later phases.
Agent mode remains disabled in the runnable application.

## Sources and generation

The [interface contract](../agent-api-contract.md) defines behavior. The
[schema](agent-v2.schema.json), [examples](agent-v2.examples.json) and
[error mappings](agent-v2.errors.json) provide shared machine-readable inputs.
The error mappings mirror section 7.3; their array fields are private status,
public fallback status, public code and retryable. Update the specification and
mapping together when changing policy.

From the repository root:

```bash
python3 scripts/generate_agent_contracts.py
python3 scripts/generate_agent_contracts.py --check
```

The generator uses Python's standard library and `gofmt`. It emits Go types and
embedded schema/error snapshots under `api/internal/agentcontract/`, plus Python
TypedDicts under `agent/contracts/models.py`. Edit the canonical sources and
regenerate; tests and `--check` detect drift. The Go binary needs no repository
files or external schema fetches at runtime. It uses the pinned
[JSON Schema validator](https://github.com/santhosh-tekuri/jsonschema/tree/v6.0.3),
with format assertions, external loading disabled and an adapter for the schema's
Unicode escapes/nonblank regex semantics.

## Go boundary

```go
validator, err := agentcontract.DefaultValidator()
if err != nil {
    return err
}
var run agentcontract.RunRequest
if err := validator.Decode("RunRequest", body, &run); err != nil {
    return err
}
if err := validator.CheckRunRequest(run); err != nil {
    return err
}
```

`Decode`, `Validate` and `Encode` enforce the named schema and JSON rules.
Destinations use the generated type for that definition. Go union containers
share fields; the schema selects legal combinations. `Optional[T]` retains
absent/present variant fields. Required nullable fields use pointers without
omission. Use `Some[*RequestID](nil)` for an explicit-null whole-clarification
request link. Initialize required arrays as empty slices rather than nil.

`DecodePublicMessage` performs the public question's sole surrounding-whitespace
trim; `DecodePublicCreate` handles creation. Both classify malformed input as 400
and a well-typed unsupported public version as 409. They do not reserve work.

`CheckState`, `CheckRunRequest`, `ResolveLanguage`, `DeriveStatus` and
`CheckRunResult` check supplied snapshots without changing them. Checks cover
history, scope, base revision, canonical catalog equality with the input,
language calculations, query links/counts, cache consistency, text/citation
assembly and pending clarification. Source-manifest validation and current
ownership/revision/deadline checks remain responsibilities of their owners.

## Python boundary

```python
from agent.contracts import ContractCodec
from agent.contracts.validation import check_run_request, check_run_result

codec = ContractCodec()
run = codec.decode_run_request(body)
run = check_run_request(run, manifest)
# Execute the injected workflow in the later service layer.
validated_result = check_run_result(run, result)
```

TypedDicts provide static types; the codec performs runtime validation. It reuses
C's `ContractValidatorV2` and strict JSON parser. Snapshot validation reuses C's
context, source, language, answer and pending rules. Dictionaries are detached.
No competing workflow, conversation store or semantic answer table is created.

## Strictness and error policy

- Limit public request bodies to 8 KiB and private bodies to 256 KiB.
- Distinguish missing required fields from explicit null. Null language override
  inherits; `auto` explicitly changes the selection.
- Reject duplicate keys, invalid UTF-8/surrogates, forbidden controls, nonfinite
  numbers, trailing content and JSON nesting beyond 64 levels.
- Reject decimal/exponent spellings for integer fields. Count Unicode code
  points. Preserve excerpts and internal user wording.
- Reject unknown closed-object fields. Open response metadata remains inert;
  Python preserves it and Go discards it using exact-name typed projection.
  Case-insensitive JSON aliases cannot overwrite canonical fields.
- Keep catalog field/value citations separate from excerpts and retrieval cache.
- `MapAgentFailure` / `map_agent_failure` use fixed safe public prose. Invalid
  HTTP/code pairings become `agent_invalid_response`. An in-progress run marks
  reconciliation before its unavailable fallback, never another POST execution.
- Go's `MapRunFailure` handles a retained failed GET snapshot carried by HTTP
  200, using the same embedded policy and validating retryability. It returns
  an error for malformed or contradictory retained failures and never forwards
  diagnostic prose. This is a Go client helper, not a new wire contract.
- Go's `SafeAgentFailure` canonicalizes public Agent failure tuples from the
  shared policy for submission orchestration, discarding diagnostic prose and
  rejecting invalid status/code/retryability/reconciliation combinations.

These checks cannot prove fresh-tool provenance, semantic entailment, language
detection, translation fidelity, real model identity, successful persistence or
latency. The authenticated Agent and later release gates retain those duties.

## Verification

```bash
python3 scripts/generate_agent_contracts.py --check
cd api
go test -race ./...
cd ..
.venv/bin/python -m unittest agent.tests.test_contracts -v
.venv/bin/python -m unittest discover -s agent/tests -q
.venv/bin/python -m unittest discover -s knowledge/tests -q
```

Use the pinned environment in [the Agent runbook](../../agent/README.md).
Both languages consume all 100 structural examples. Go additionally consumes
shared language, status and history-window cases. Boundary tests cover required
nulls, integer lexemes, Unicode, metadata, tampered snapshots, catalog citations,
pending links, safe errors and generation drift. Existing fixture and C v1/v2
regressions remain enabled. The remaining runtime/semantic fixtures are not all
claimed as phase-1 acceptance.
