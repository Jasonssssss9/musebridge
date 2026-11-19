# Follow-up planning on the pinned 9B model

The revised planner delivered all 16 scheduled follow-ups across two sets of visitor
dialogues. Independent review passed the reply itself on five of eight regression
follow-ups and three of eight held-out follow-ups. Two held-out replies made
unsupported claims about the artwork, including one that persisted in memory.

## What changed

With retained history, the model chooses between two tasks in one planning call.
For one or two clear questions about the current artwork beyond its catalog fields,
it selects an exact passage from the current message, writes a standalone English
search question per fact, and chooses fresh retrieval or verified cache. The
Agent assigns ordered request and query IDs and validates the resulting plan.
For other tasks the model returns the regular plan, including conversation,
translation, catalog questions, mixed requests and clarification.

The model sees a numbered copy of the current sentences in its user data. An
index selects the original source text. Two questions sharing a sentence use
distinct exact excerpts. Invalid selections fail validation. A selected passage
can still correspond to the wrong intent. The same limits on model calls, time,
context, query count, artwork scope and evidence apply on both paths.

## Study design

Each set contains eight two-turn dialogues covering both artworks in English,
Spanish, Simplified Chinese and Korean. The [regression set](../agent/testing/planner_comparison_cases.json)
comes from the [original study](planner-baseline.md). The [held-out set](../agent/testing/planner_followup_holdout.json)
was frozen before the planning changes, with SHA-256
`e28ab91fd330dd47594fa3f5fa012396fc5025bfde960d4ea996a1f099b2623b`.
Its questions and gold labels were excluded from development. Both the previous
implementation at `ba395c3` and the candidate ran the held-out set.

All runs used real Ollama `qwen3.5:9b-q4_K_M`, digest prefix `56671c2ab938`,
a 16,384-token context, 2,048 output tokens per call, temperature zero and
thinking disabled. The workflow used the real scoped HTTP retriever. On turn
two it received the actual first reply and the state that reply proposed.
Frozen English control queries ran separately and never entered the dialogue.

First we replayed the 16 original fitted planning inputs, including their exact
histories, without re-running answer generation. The original planner passed
8/8 first turns and 2/8 follow-ups. The selected candidate passed 8/8 and 8/8
on those same inputs. We used the original set to develop the planner. The next
table comes from full end-to-end runs and includes semantic review.

## End-to-end results

Each row has eight scheduled turns. Intent passes when the accepted plan preserves
all and only the current requests and their intended referents. Body passes when
the delivered answer covers the scoped requests, uses the expected language and
preserves the source's meaning. Memory covers the full next state, including
inherited goals, support links and open questions. An undelivered turn earns
zero passes on those dimensions. Each semantic verdict is a rubric judgment.

| Set and turn | Version | Delivered | Intent | Body | Memory | Body and memory |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Regression, first | Previous | 8 | 7 | 5 | 7 | 5 |
| Regression, first | Revised | 8 | 7 | 6 | 7 | 6 |
| Regression, follow-up | Previous | 2 | 2 | 2 | 1 | 1 |
| Regression, follow-up | Revised | 8 | 6 | 5 | 4 | 4 |
| Held out, first | Previous | 8 | 8 | 6 | 8 | 6 |
| Held out, first | Revised | 8 | 8 | 6 | 8 | 6 |
| Held out, follow-up | Previous | 1 | 1 | 1 | 1 | 1 |
| Held out, follow-up | Revised | 8 | 5 | 3 | 4 | 3 |

On the revised planner the median elapsed time per delivered follow-up was
5.69 seconds for regression and 5.66 seconds for the first held-out run,
measured with the model warm. First-turn responses also varied between runs
with the same first-turn planning policy. Changes to those scores cannot be
attributed to the follow-up decision format.

After updating the capability probe to record both the raw decision and the
canonical plan it graded, we ran the same held-out dialogues again. All 16
turns retained their intent, body and memory verdicts. Four first-turn planning
outputs changed wording. Every follow-up plan and every retrieved sequence of
source IDs matched the first run. Median follow-up time was 5.63 seconds on the
repeat. This checks reproducibility on the same questions.

## What visitors experienced

- **Evidence reused correctly.** On F01 the Agent used its verified excerpt to
  distinguish an unsupported claim about color coverage from the museum's
  qualified interpretation of the whole picture. On P08 the Korean follow-up
  distinguished what the marks evoke from whether the painter used real feathers.
- **Wrong route.** P03 asked whether the label agreed with the visitor's personal
  impression. The direct reply denied a museum interpretation that was in the
  available description and carried that denial into memory. On F02 the Agent
  routed a medium-field question to general art, incorrectly reported a missing
  catalog value and speculated about the work.
- **Related evidence overread.** On F08 the cached excerpt located a feature in
  the background. The answer claimed it was on the left and memory kept that
  claim as supported. A valid citation ID proves source identity only.
- **Wrong or incomplete task.** P02 treated the purpose of drawing to scale as
  a separate request for dimensions. P04 revived an earlier question about the
  painter's process. F04 merged ownership and visual description, then left
  both unanswered despite having evidence for the visual part.
- **Query mismatch.** On F03 the generated wording missed an available medium
  record. Across the held-out set, independent English controls found the expected
  source ID for all 12 supported parts. Five of eight unsupported specifics
  returned a related passage, so an assessment still has to check what it says.

These runs support keeping the 9B model for development. A useful next check is
whether a visitor-facing claim, including a negative claim about the collection,
is entailed by the cited passage and carried into memory with the same scope.
Use fresh questions to evaluate that change. Service readiness remains a separate
acceptance decision.

## Reproduction

Run from the repository root with the verified tokenizer and authorized Ollama
endpoint. Each report path must be new and outside the checkout. The replay
input is the private full report from the original study. The public fixture
alone does not contain its historical model inputs.

```sh
.venv/bin/python -B -m agent.testing.replay_plans \
  --baseline ../local-model-results/planner-baseline.json \
  --base-url http://127.0.0.1:11434 --tokenizer ../local-model-assets/qwen35-tokenizer.json \
  --report ../local-model-results/followup-replay.json

.venv/bin/python -B -m agent.testing.planner_comparison \
  --case-file agent/testing/planner_followup_holdout.json \
  --base-url http://127.0.0.1:11434 --tokenizer ../local-model-assets/qwen35-tokenizer.json \
  --report ../local-model-results/followup-heldout.json
```

Use `agent/testing/planner_comparison_cases.json` to reproduce the end-to-end
regression run. Compare each reply and its full next state with the parts,
evidence IDs and review notes in the frozen fixture. Raw model inputs, decisions
and transcripts stay in private reports.
