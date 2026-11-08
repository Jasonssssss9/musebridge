# Retrieval evaluation

This directory separates the frozen demo fixtures from broader retrieval diagnostics. The diagnostics exercise C's current keyword retriever without a model, embedding service or backend B. They measure returned evidence, not generated answers.

## Files and scope

| File | Purpose |
| --- | --- |
| [aic-seed-v0.1.tests.json](aic-seed-v0.1.tests.json) | Original frozen demo questions and artwork isolation fixtures. |
| [aic-retrieval-v0.1.json](aic-retrieval-v0.1.json) | Additional reviewed questions with semantic evidence labels. |
| [retrieval_eval.py](retrieval_eval.py) | Standard-library validation, evaluation and report CLI. |
| [Top-1 baseline](reports/aic-retrieval-v0.1.top1.json) | Recorded diagnostic results with `top_k=1`. |
| [Top-2 baseline](reports/aic-retrieval-v0.1.top2.json) | Recorded diagnostic results with `top_k=2`, the current default. |

The new suite contains 48 utterances organized as 24 English/Chinese pairs. Each pair expresses one intended question with the same artwork scope and gold evidence. These are 24 paired intents, not 48 independent visitor observations.

The seed remains [aic-seed-v0.1](../knowledge/data/aic-seed-v0.1.json): The Bedroom (`aic-28560`) and Woman at Her Toilette (`aic-11723`), with two chunks per artwork. Each artwork has a medium chunk and a short museum description chunk. Labels use only those four released excerpts. Full museum pages, artwork metadata outside the chunks and general art knowledge are outside this evaluation's evidence scope.

Neither the released seed, the original fixtures nor the keyword retriever was changed to improve these scores. The runtime service does not import the evaluation suite or use its gold labels.

## Run the evaluation

Use Python 3.10 or later from the repository root:

```bash
python3 -m eval.retrieval_eval --top-k 2
```

This prints the full JSON report to stdout. To save a separate candidate report:

```bash
python3 -m eval.retrieval_eval --top-k 2 --output /tmp/musebridge-retrieval-candidate.json
```

With `--output`, the full report is written atomically to the selected path and a compact summary goes to stderr. Stdout is empty. Missing parent directories are created. Choose a new report path to preserve the recorded baselines; the CLI prevents overwriting its seed, suite or implementation inputs, but other existing report paths can be replaced.

Optional `--suite PATH` and `--seed PATH` select another compatible suite and seed. Defaults resolve from the repository location, so invoking the module does not require data paths relative to the working directory. `--top-k` accepts 1 through 5.

| Exit code | Meaning |
| --- | --- |
| 0 | Evaluation completed with valid retrieval responses, even if semantic scores are poor. |
| 1 | At least one retrieval call failed or returned an invalid response. Inspect each result's `issues`. |
| 2 | Invalid arguments, invalid suite/seed, or an input/output failure. |

There is deliberately no quality threshold that makes the current keyword baseline appear to pass. Contract validity and evidence relevance are separate checks.

Run automated checks with:

```bash
python3 -m unittest discover -s knowledge/tests -v
```

The evaluator tests cover independently calculated metrics, bilingual pair validation, malformed labels, provenance and artwork violations, duplicates, CLI behavior and real-suite execution. Existing loader, retrieval and HTTP checks remain part of this command.

## Coverage and labeling

Each category contains eight utterances: four English and four Chinese, balanced across the two artworks. Category names describe the authored diagnostic focus; they are not mutually exclusive explanations of a failure.

| Category | What it probes |
| --- | --- |
| `paraphrase` | Natural variations on medium, perspective, brushwork and background questions. |
| `semantic_gap` | Questions that refer to the evidence without using the current topic aliases, such as asking what a picture is painted on. |
| `multi_topic` | Questions needing both medium and description, or combining an answerable part with an unsupported part. |
| `lexical_confusion` | Recognizable topic words attached to unavailable specifics: oil type, canvas alteration, brush size or the actual wallpaper model. |
| `scope_confusion` | A named other artwork or a comparison while the request remains scoped to one artwork. |
| `evidence_gap` | Missing dimensions, artist intention, ownership or current gallery information. |

Every case records an English rationale explaining its gold choice. Gold is based on what the excerpt supports, rather than what the keyword implementation currently returns.

| `answerability` | Meaning | `relevant_chunk_ids` |
| --- | --- | --- |
| `full` | The released evidence can support all requested factual parts. | All chunks needed for the question. |
| `partial` | Evidence supports an identifiable part, but another part lacks evidence or lies outside the selected artwork. | Only the useful chunks within the selected artwork. |
| `none` | No released, in-scope chunk supports the requested fact. A merely related topic does not qualify. | Empty array. |

For example, medium plus days spent painting is `partial`: the medium chunk helps with the first part, but contains no duration. A comparison can likewise be `partial` when the current artwork's description supports its side only. A query solely about the other named artwork is `none`, even if both works happen to share the same medium. The Bedroom excerpt's possible nervous effect does not establish the artist's intention.

These are offline annotations, not additional C API fields and not replacements for B's public `answered`, `insufficient` or `unsupported_question` statuses. Retrieving all gold for a `partial` case still does not make the entire question answerable. An empty gold list does not claim that the current retriever will return no related chunks.

## Suite schema

The root has exactly `suite_id`, `dataset_version`, `description` and `cases`. Each case has exactly these fields:

| Field | Meaning |
| --- | --- |
| `case_id` | Unique utterance identifier. |
| `pair_id` | Identifier shared by exactly one English and one Chinese case. |
| `category` | One of the six diagnostic categories above. |
| `query_language` | `en` or `zh-CN`. |
| `artwork_id` | The explicit retrieval scope, independent of artwork names inside the question. |
| `query` | The actual visitor-style question; at most 1,000 code points after trimming. |
| `answerability` | `full`, `partial` or `none`. |
| `relevant_chunk_ids` | Unique seed chunk IDs that support all or part of this question. |
| `rationale` | English explanation of the semantic label. |

The validator rejects unknown or out-of-scope gold IDs, duplicate case/chunk IDs, inconsistent answerability, malformed JSON and mismatched bilingual pairs. Pair members must share category, artwork, answerability and the gold set. Validation checks structure and references; human review remains necessary to establish whether a passage actually supports a question.

## Report interpretation

Reports include aggregate metrics, per-language/category/answerability breakdowns and per-case diagnostics. Each case lists retrieved, matched, missed and unexpected IDs. Results also include contract issues and artwork scope violations.

Before semantic scoring, the evaluator checks the response version, evidence list, limit, unique IDs, artwork scope, excerpt and source/license fields against the loaded seed. Additive metadata is allowed by the API contract. A retrieval exception or invalid response receives no semantic credit and is excluded from positive/negative metric denominators. Always report `valid_case_count` and `invalid_case_count` alongside quality scores; a run with invalid cases exits 1.

For valid responses, `full` and `partial` cases are positives; `none` cases are negatives. Let a hit be a returned chunk whose ID belongs to the case's gold set.

| Metric | Definition |
| --- | --- |
| `hit_rate_at_k` | Fraction of positive cases with at least one hit. |
| `precision_at_k` | Mean of hit count divided by requested `k` across positive cases. Unfilled positions count against this nominal-k metric. |
| `recall_at_k` | Mean of hit count divided by gold count across positive cases. Each question has equal weight. |
| `mrr_at_k` | Mean reciprocal rank of the first hit across positive cases; no hit contributes zero. |
| `all_relevant_retrieved_rate_at_k` | Fraction of positive cases for which every gold chunk was retrieved. This is evidence coverage, not complete answerability. |
| `positive_returned_precision` | Total hits divided by total actually returned chunks on positive cases only. It excludes negatives and is not overall precision. |
| `negative_false_positive_rate` | Fraction of negative cases returning any chunk. |
| `scope_violation_count` | Count of returned records that violate the selected artwork scope, including records from invalid responses. |

Ratios with no eligible denominator are JSON `null`. Per-case semantic metrics are also `null` for negative or invalid cases. An invalid case has `negative_false_positive: null` rather than being counted as a successful abstention.

A negative false positive means the returned excerpt cannot answer that question, even if its topic is related. It is neither an HTTP failure nor evidence of an LLM hallucination. No answer generation is evaluated here.

## Recorded keyword baseline

Both checked-in reports contain 48 valid cases, zero invalid cases, 28 positive cases and 20 negative cases.

| Metric | Top 1 | Top 2 |
| --- | --- | --- |
| At least one relevant chunk | 20/28 (71.43%) | 20/28 (71.43%) |
| Mean recall | 64.29% | 67.86% |
| Nominal precision at k | 71.43% | 39.29% |
| MRR | 71.43% | 71.43% |
| All gold retrieved | 16/28 (57.14%) | 18/28 (64.29%) |
| Positive returned precision | 100% | 100% |
| Negative questions returning evidence | 14/20 (70%) | 14/20 (70%) |
| Records violating artwork scope | 0 | 0 |

The 100% positive returned precision does not offset the negative-case failures. It only says that the chunks returned on answerable or partially answerable cases were relevant. Nominal precision is lower at top 2 because many questions need only one chunk and empty positions still count in its denominator.

Top-2 category results make the remaining problems visible:

| Category | Positive cases: all gold found | Negative cases: returned evidence |
| --- | --- | --- |
| Paraphrase | 8/8 | No negative cases |
| Semantic gap | 0/8 | No negative cases |
| Multi-topic | 6/8 | No negative cases |
| Lexical confusion | No positive cases | 8/8 |
| Scope confusion | 4/4 | 4/4 |
| Evidence gap | No positive cases | 2/8 |

The English and Chinese halves happen to have the same aggregate results on these authored pairs. That does not establish equal performance on general visitor language.

The current baseline misses all eight semantic-gap utterances. In the multi-topic group, two questions retrieve the description but miss the required medium. Hard artwork filtering prevents foreign evidence records, yet a question naming the other artwork can still trigger an irrelevant local chunk. These are different failure modes and need separate future remedies.

## Reproducibility and future comparisons

CLI reports record Python and Unicode versions plus SHA-256 hashes of the seed, suite, retriever, loader and evaluator. They omit timestamps and timing measurements. Repeating a run with identical inputs, code and runtime produces the same JSON; use the provenance fields to explain differences.

Keep these reports as the original keyword baseline. For a future retriever change, run the same suite into a new report path, compare aggregate scores and inspect the changed case IDs. Do not adjust gold to match implementation output. If a question, label or evidence scope needs correction, review the change, release a new suite version and retain enough history to explain the comparison.

This is a small authored diagnostic set over four excerpts. It is not held-out evaluation, production accuracy, a representative visitor study, latency measurement or generated-answer assessment. It exposes useful weaknesses before B or a model is available; broader corpus coverage and independently collected questions remain future work.
