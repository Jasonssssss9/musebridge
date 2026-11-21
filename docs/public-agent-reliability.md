# Public Agent reliability checks (M2-B4)

Status: **Implemented as repeatable automated public HTTP checks.** Existing Go
admission, reconciliation and state guards pass the expanded cases; no production
deadline, contract, model policy or application behavior was changed for tests.
This is backend reliability evidence, not browser UI acceptance, real Qwen
evaluation, production activation or persistence across Go restarts.

Use the [M2-B3 integration configuration](public-agent-integration.md) to connect
A's browser. The [v2 contract](agent-api-contract.md) remains authoritative.

## One-command verification

From the repository root, with Go 1.27+, Python 3.12 and the already installed
pinned Agent dependencies:

```bash
go -C api test -race -tags=integration -count=1 -timeout=150s -v \
  -run '^TestM2B4' ./internal/httpapi
```

The complete check takes about 75 seconds, including one actual production-budget
wait of about 60 seconds. It starts and stops loopback HTTP listeners and the
existing scripted-provider/real-workflow/retrieval Python services. It requires
no GPU, Ollama, tokenizer, Docker or external network after prerequisites exist.
Missing Python prerequisites or unavailable test services fail rather than
silently substituting fixture answers. `M1_PYTHON` can select the existing
interpreter as documented in the [test-service guide](m1-test-services.md).

For a fast development iteration, add `-short`. That explicitly skips the
production-budget wait and therefore does not reproduce the full M2-B4 gate.
No HTTP endpoint, environment variable or production constructor was added to
shorten timeouts.

## Coverage and observable outcomes

| Boundary / case | Required observation |
| --- | --- |
| Thirteen dependency failures | Public status/code/retryability match authentication, readiness, capacity, context, retrieval and generation policies; no raw diagnostic/phase/run ID escapes |
| Concurrent duplicate and different messages | Twelve competing requests distinguish `request_in_progress` from `conversation_busy`; changed payload under the same key is `idempotency_conflict`; only admitted work runs |
| Disconnect and refresh while processing | History retains the original processing submission; the other artwork remains usable; accepted work finishes after disconnect and replays once |
| Invalid completed results | Wrong run, artwork, release, base revision, language, catalog, cached evidence or citation cannot overwrite the prior turn, revision, memory or cache |
| Four languages and Auto | Each artwork completes a predefined 12-turn state script covering all four languages, selector changes, a one-turn setting, a persistent setting, translation and Auto fallback |
| Context and old replay | Private input contains the latest five complete pairs; replay of turn 1 preserves that turn and returns the current projection; stale new drafts never execute |
| GET reconciliation | Processing and a transient lookup failure can precede retained failure; a missing run or invalid completed lookup stops without another POST |
| Pending and cache atomicity | A partial reply retains its supported citation and focused clarification; thanks keep pending; rejected updates change nothing; explicit abandonment clears pending; cached support remains available |
| Cache membership | Only cited retrieval passages persist; an uncited description and a cited catalog record do not enter the retrieval cache |
| Capacity rejection | A synthetic two-slot Agent rejects a third Go submission promptly; that conversation stays at revision 0 and succeeds once on deliberate retry after capacity frees |
| Lost private response, real workflow | Both artworks recover the original run by GET after a physical socket drop; one private POST, one GET, two model decisions and one real retrieval each |
| Lost public response after real commit | Both artworks recover one saved turn through history and replay after a physical socket drop; two model decisions and one real retrieval each, with no extra execution |
| Production recovery budget and late response | POST child wait is 56 s, first GET follows after 1 s, recovery ends near 59.75 s; no automatic replacement run; a late old response cannot replace a deliberately retried turn |

The first rows use a controlled private Agent HTTP implementation with real
public Go handlers and the real Go Agent client. Complete replies and error
envelopes are synthetic. Its two-slot capacity is a deterministic public-path
fault boundary, not a change to the actual Agent's eight-run limit. Existing
private-service tests independently cover that limit and inference gating.

Language scripts provide explicit decisions and expected state labels. Synthetic
reply text does not measure actual output language, translation fidelity or model
understanding. No independent visitor held-out questions are read or used. Those
tasks remain A/C and M2-B5/B6.

The cache test inspects the next original private RunRequest to check detached
memory/cache state; private semantic state is not added to public history.

## Physical response-loss boundaries

### Private Agent response

A test proxy forwards the original authenticated POST to actual FastAPI and reads
its completed response, then closes the incoming TCP connection without forwarding
the response. Go observes real transport loss. GET requests reach the retained
original run normally. The proxy checks the original run/trace identity and the
one-second delay before the first GET.

The Python audit verifies two actual scripted model decisions and one HTTP lookup
per artwork. Citations are `aic-28560-medium` or `aic-11723-medium` in
`aic-seed-v0.1`. A public retry adds no model or retrieval call. The test helper
creates an authenticated proxy client without exposing the harness token.

### Public response after commit

A test wrapper buffers the real public handler response after its atomic commit,
then closes the first connection. The simulated HTTP visitor sees network failure.
History returns the saved turn and cleared pending request. Reopening restores
the same conversation; resending the original logical message returns the exact
saved turn. The Python audit confirms no repeated model/retrieval operations.
There is no application header, admin route or runtime failure switch.

## Production-budget observation

The slow case leaves a synthetic private POST unresolved beyond its 56-second
child wait and answers GETs with processing. It does not shorten the existing
55-second Agent deadline or 60-second Go attempt. This private fixture deliberately
violates normal run completion to expose Go's own defensive recovery bound.

Go ends reconciliation about 250 ms before its original attempt deadline and
returns 503 `agent_unavailable`, retryable. This is an uncertain outcome, not a
confirmed Agent execution timeout. Valid Agent `request_timeout`,
`generation_timeout` and `retrieval_timeout` errors remain 504, checked separately.
Actual parent deadline expiration still takes precedence, including during
validation/commit, as covered by the existing internal tests.

At failure, history has no completed turn and revision is 0. A deliberate identical
logical retry starts a new run and commits revision 1. Only then is the old private
response released. History and the accepted answer stay unchanged. This verifies
late-arrival protection; it does not claim remote inference stops on TCP cancellation.

## Implementation and regression scope

| File | Role |
| --- | --- |
| [HTTP helpers](../api/internal/httpapi/reliability_helpers_test.go) | Cookie-backed simulated visitor, synthetic private HTTP boundary and safe response checks |
| [Public reliability tests](../api/internal/httpapi/agent_reliability_test.go) | Errors, concurrency, state/cache, language scripts and status recovery |
| [Workflow and budget checks](../api/internal/httpapi/agent_recovery_integration_test.go) | Physical TCP loss at both boundaries and unchanged production timing |
| [Python harness helper](../api/internal/testsupport/m1services/services.go) | Test-only authenticated client through a loopback fault proxy |

Full backend regression:

```bash
go -C api test -race -tags=integration -count=1 -timeout=150s ./...
go -C api vet ./...
python3 -B scripts/generate_agent_contracts.py --check
```

Python production code, schemas, released sources, frontend behavior and dependency
locks are unchanged. Actual Python services run in the Go checks. Browser rendering,
mobile hardware, real Qwen semantics/timings and formal readiness retain their own
acceptance records.
