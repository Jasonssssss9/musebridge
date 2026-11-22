# Run and score the visitor evaluation

Run these commands from the repository root using the project's Python 3.12
environment. The evaluation sends visitor text and the optional language selection
to the public Go API through Vite. Keep evidence and score sheets in a new directory
outside the checkout. Only share reviewed aggregate results.

## While review is pending

```sh
git worktree add --detach ../visitor-review origin/visitor-acceptance
python3 -B ../visitor-review/acceptance/check_heldout.py
.venv/bin/python -B -m acceptance.run_evaluation plan --suite-root ../visitor-review
```

The schedule comprises 240 distinct single-turn language variants, 96 additional
risk repetitions and 154 messages in 16 scripts. The 12 risk scenarios run in
four languages three times each, counting their original single-turn trial.
Each independent variant and repeat receives a fresh visitor cookie. Each script
retains its cookie and separate conversation for each artwork. Four browser
steps have their own observations. A full run therefore schedules 490 visitor
messages across 352 visitors.

Reviewers complete the language and source/state checks in the
[authored suite instructions](https://github.com/Jasonssssss9/musebridge/tree/visitor-acceptance/acceptance).
Use the reviewed and frozen version of that worktree for evaluation. The runner
accepts either the worktree root or its versioned suite directory. Only a verified
freeze manifest and attributable sign-offs can open the messages for inference. Structural checks and a count of
the schedule can run on the draft.

## Rehearse using public development cases

Start the [controlled Qwen stack](../docs/m2-runtime.md) with
`--private-decisions --hold-seconds 3600`, the pinned tokenizer and the appropriate
`--ollama-url`. Choose an existing development case prefix such as
`--case-prefix aic-28560-material-en`. The stack's owner-only directory
contains the short-lived `connection.json`, `source-manifest.json` and
`agent-events.jsonl`. The runner requires a loopback Vite origin and a successful development trace with the private input, decision and workflow result present before sending questions. The trace
facility records private model decisions and the workflow result for this
controlled run. Restrict access to these files.

```sh
.venv/bin/python -B -m acceptance.run_evaluation run --group development \
  --stack /private/running-stack --output /private/new-development-run
.venv/bin/python -B -m acceptance.score_evaluation /private/new-development-run \
  --output /private/dev-summary.json --template /private/dev-review.json
```

This path runs two published material questions and restores history for each
artwork. It can run before review and reads no independent questions.

## Execute the frozen suite

Use `--group single`, `--group risk` and `--group scripts` in place of
`--group development`. Supply the frozen suite and its real review references
for each independent run, for example:

```sh
.venv/bin/python -B -m acceptance.run_evaluation run --group single \
  --suite-root ../visitor-review --review-record /private/completed-freeze-reviews.json \
  --stack /private/running-stack --output /private/new-single
```

The review record contains a `sha256` map for the frozen `single-turn.json`
and `multi-turn.json`, plus an `approvals` map keyed by `visitor_realism`,
`source_and_state`, `en`, `es`, `zh-CN` and `ko`. Each entry records
`approved: true`, the actual `reviewer`, `date` and a `reference` to the
completed sign-off. The runner checks the file hashes against the external
manifest, verifies both dataset statuses, and records the review hash privately.
If the external manifest already embeds these fields under `reviews`, the
separate record is optional. The collector executes the same in-memory document
bytes it verified. The scorer checks each recorded question, language controls
and expectation against the run's frozen manifest. Hashes bind the evidence;
the team verifies the actual identities and references of the reviewers.
The collector never writes to the external worktree.

Use a fresh output directory for each group.
Alternatively, `--group all` runs them together. The runner requires a
reviewed freeze manifest before sending any of these texts. Use identical
runtime source, model, tokenizer, suite and evaluator for all batches.
Size the server hold duration for the selected batch. Reports record model,
profile, code and manifest identities.

Each accepted response is checked against the public history after commit.
A failed or unverified commit blocks later steps of that visitor script.
A semantic or label mismatch on a delivered turn remains in the history so
the next turn sees what the visitor actually received. Each trial is appended
to an owner-only JSONL ledger. Interrupted runs retain their completed
observations. Rerun an entire incomplete group in a new directory and retain
both attempts separately. Summaries reject duplicate trial IDs and incompatible
run identities.

The four browser steps perform the server-side create/restore and history
checks with the same cookie. Their DOM, visible language controls and perceived
waiting need separate observation in the [external browser record](https://github.com/Jasonssssss9/musebridge/blob/visitor-acceptance/acceptance/browser-acceptance-record.md).
A missing visual observation remains unobserved. Browser automation using a mobile
viewport is available in the runtime guide; the physical phone is evaluated in M3.

## Review and report

```sh
.venv/bin/python -B -m acceptance.score_evaluation \
  /private/new-single /private/new-risk /private/new-scripts \
  --suite-root ../visitor-review --template /private/review.json --output /private/machine-summary.json
# Reviewers complete the private template and save their own copy.
.venv/bin/python -B -m acceptance.score_evaluation \
  /private/new-single /private/new-risk /private/new-scripts \
  --suite-root ../visitor-review --reviews /private/review-completed.json --output /private/reviewed-summary.json
```

In `turns.jsonl`, read the original question, scoped gold labels and public
reply before inspecting the canonical plan, accepted sources and memory.
Compare each factual clause with the passage, including its subject,
spatial qualifiers and strength. Grade the actual reply language separately
from its language field. A citation ID check cannot establish whether the
claim follows from the source.

The proposed next state is computed from the validated result after comparing
it with the committed public turn. A later turn on the same artwork compares
the prediction to the actual state supplied by Go. On the final turn the last
private state is explicitly marked as a projection. Each review row binds its
judgments to the exact source ledger entry by SHA-256. Use true or false for
applicable dimensions, `"n/a"` when a dimension does not apply and null while
a judgment is outstanding. Record the reviewer, supporting notes, number of
answerable factual parts requested and how many the answer completed. Assign
zero to both counts for a reviewed question with no answerable factual parts. For
a failed or blocked turn reviewers can still count the answerable parts from the
gold labels. Such a turn completes zero parts.
Add disputes and their resolution to the notes.

The summary separates planned, missing, delivered, failed, blocked, unobserved
and reviewed outcomes. It reports nearest-rank warm public p95 by direct,
retrieval/reuse and refined path; unclassified/undelivered turns are counted
separately. Breakdowns include test group, scenario family/category, artwork,
visitor language, expected reply language and observed route. Source checks,
request kinds, private call counts and pending labels are linked by trace ID.
Terminal clarification and out-of-scope plans have no structured request kinds
to score. Review first-pass validity, failed trials and missing coverage with
the table of [release targets](../docs/agent-design.md#133-release-targets).
A completed command does not provide an independent reviewer or authorize
activation.

### Reading the release checklist

`release_targets` shows the design threshold beside its numerator, failures,
outstanding observations and applicability. It covers first-pass terminal validity
and response-language/preference correctness at 98%, route and interpretation
at 95%, factual-part completion at 95%, whole and mixed completion at 90%,
conversation usefulness at 90%, semantic state at 95%, and the zero-tolerance
grounding and deterministic state targets. The named language changes require
100% correctness. An explicit selector or instruction cannot be exempted with
an `n/a` mark. Reviewers identify any additional language-change or
gap-continuation cases from the frozen labels when annotating the template.
Language is reported by the visitor's language and by the expected reply language.
Review neutral messages in the overall and expected-reply slices.

An undelivered scheduled turn cannot satisfy a whole-question or response-language
gate. A batch that has not been run remains unobserved. A transport failure
can leave first-pass model structure or source entailment unobservable even when
delivery is known to have failed. Mark grounded and properly cited statements as
separate judgments for every delivered turn, including an answer that correctly
makes no artwork-specific claim. Whole-museum absence claims require affirmative
support. A high numeric field match cannot substitute for the reviewer's reading.

Gate results distinguish an observed failure, missing evidence, and meeting the
target in the measured sample. Empty or all-inapplicable denominators stay
pending. Factual-part rates require a reviewed denominator on every scheduled
trial. For latency the checklist includes per-path observations and unclassified
turns; the latter keep a complete p95 decision pending. The separate regression,
hard deadline, human browser and team release decisions require their own evidence.
The report always leaves formal activation outside the evaluator.
