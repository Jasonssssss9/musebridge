# M2 candidate handoff and outstanding gates (B6 preparation)

Status: **The public real-model path, browser recovery checks and independent
evaluation tooling have run on development data. Team M2 acceptance remains open.**
No independent reviews, passing capability attestation, release tag or signed-off
candidate commit are fabricated by the tooling.

See the [M2 verification record](m2-verification.md) for the latest measured results
and the [evaluation runbook](../acceptance/evaluation-runbook.md) for independent
review, freeze and scoring commands.

## Candidate inputs and checks

1. Start from the reviewed B4 base and include the B5 routing fix, evidence
   instrumentation and [runtime runner](m2-runtime.md).
2. Record the tested Git commit or explicit dirty-worktree source manifest, model
   name/digest, profile/tokenizer, source release, prompts and implementation
   fingerprint. A source snapshot identifies uncommitted work; it is not a release.
3. Complete backend, frontend and contract regressions with the commands below.
4. Retain the real-model development report, actual browser report, failures,
   readiness and timings privately. Send only reviewed, appropriate summaries to
   teammates. Private reports are not automatically copied into the repository.
5. Resolve semantic failures, finish independent reviews, freeze/evaluate the
   independent suite, then authorize the candidate commit and joint release review.

```bash
go -C api test -race -tags=integration -count=1 -timeout=150s ./...
.venv/bin/python -B -m unittest discover -s agent/tests -q
.venv/bin/python -B -m unittest discover -s knowledge/tests -q
python3 -B scripts/generate_agent_contracts.py --check
cd web
npm test
PUBLIC_CONTRACT=agent npm run build
```

Go tests include B4's approximately 60-second real-budget check. Python checks
do not call real Qwen; the explicit B5 runner supplies that separate evidence.

## A's independent-suite technical preflight

A's `visitor-acceptance` branch is separate. Inspect it in a temporary detached
worktree, without merging, freezing or modifying its questions. Run A's own
structural checker there, then use B's read-only metadata audit:

```bash
git worktree add --detach ../m2-visitor-review origin/visitor-acceptance
python3 -B ../m2-visitor-review/acceptance/check_heldout.py
.venv/bin/python -B -m agent.testing.review_acceptance \
  --suite-root ../m2-visitor-review --report ../m2-label-preflight-new.json
# After checking the worktree remains clean:
git worktree remove ../m2-visitor-review
```

The audit verifies source IDs/artwork scope and language state transitions from
the suite's own annotations. It exposes no question text to model calls or output.
It records hashes so reviewers can identify precisely which draft was checked.
It does not prove that an expected source answers the question, that pending
clarification labels fit the visitor's meaning, or that translations are natural.
Those reviews and owner sign-offs remain required even if all automated checks
pass. The script never grants independent-evaluation readiness or freezes files.

Keep C from inspecting the independent questions until A's review/freeze steps
are complete. Use development cases to fix current model failures. Preserve all
language variants/paraphrases of a scenario in the same evaluation split.

## Summarize the private handoff

```bash
.venv/bin/python -B -m agent.testing.m2_handoff \
  --runtime-report /private/run/report.json \
  --browser-report /private/browser/browser-report.json \
  --label-review /private/m2-label-preflight.json \
  --output /private/m2-handoff-new.json
```

This extracts counts, failed traces, gates and owners without publishing prompts,
replies or memory. It always keeps formal activation closed: machine development
checks cannot replace the missing independent and human reviews.

## Outstanding ownership

| Owner | Required next evidence |
| --- | --- |
| A / language reviewers | Review scenario realism and four-language wording; complete source/state semantic labels with B; freeze the suite; independently record actual browser outcomes |
| C | Reproduce current follow-up/cached-evidence and mixed-language planning errors from development traces; fix faithful routing/support/memory; evaluate actual meaning and citations |
| B | Rerun the affected real public-path checks after fixes; maintain deadlines/identity/atomicity; obtain owner authorization for the tested candidate commit |
| Team | Review disputed source entailment, whole-question completion, language and memory; satisfy independent quality and warm-latency gates before normal activation |

The agreed full evaluation includes at least 24 development scenarios, a separate
60-scenario/four-language independent set (240 trials), 16 scripts of at least
eight meaningful turns and repeated risk cases. The expanded development suite covers 25 scenarios. Independent visitor acceptance still requires the separate frozen set. Use the [architecture release gates](agent-design.md#13-evaluation-and-release-gates)
and [visitor plan](visitor-acceptance.md) for denominators and scoring.

M2 remains incomplete while independent review/evaluation or known model failures
are unresolved. Reproducible deployment and physical-phone acceptance are M3.
