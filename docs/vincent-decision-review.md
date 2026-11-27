# Vincent follow-up and grounding comparison

## What changed

Vincent still uses the gallery’s hybrid retriever and the same Getty and letter releases. Qwen now chooses among a smaller set of private actions. For a factual follow-up it resolves the visitor’s present intent and chooses the source perspective for a new English search. An explicit restatement can reuse an already confirmed answer when the request, prior answer and source text all support that choice. A direct acknowledgment has its own writing step. That writer receives the visitor’s statement without the historical passages. Catalog replies see the selected identity or date field.

For factual answers the model selects the source, identifies passage or locator spans, and states the finding before composing its reply and continuity note. A reference must belong to the exact record. That structural check cannot establish that the words prove the model’s interpretation. The model can report a specific gap or request one further search. Source qualifiers are serialized consistently so a duplicated qualifier does not count as a new query. The public v3 result format is unchanged.

## Evidence and method

Both suites contain 13 English visitor turns across seven conversations. The original suite served as the development set. A separate evaluator prepared and hashed the new questions, controls and rubrics before the new implementation was evaluated. The implementation team did not use new question text or new-suite semantic findings to modify production code. An intermediate comparison was also run. Its aggregate new-suite delivery count was known before the last original-suite iteration. Neither corpus is a population estimate.

The before runs used an isolated export of commit `3df1dde`. The two final runs shared the recorded code and prompt fingerprint `9adfd3ad53f44155` (abbreviated). All four runs used the real teammate-hosted `qwen3.5:9b-q4_K_M`, the same model digest, temperature zero, thinking disabled and the same knowledge service with live OpenAI query embeddings. The Agent received only the question, actual conversation state and eligible evidence. Each definitive run submitted each question once. The semantic reviewer graded saved model output against the frozen rubric and canonical record, separately from automatic schema checks. A failed turn contributed no synthetic history. The runner used the real private registry and simulated the Go commit between successful turns.

- Original suite: [questions](../agent/testing/vincent_hybrid_cases.json), [before transcript](../eval/reports/vincent-decision-original-before.qwen.json), [after transcript](../eval/reports/vincent-decision-original-after.qwen.json).
- New suite: [questions](../agent/testing/vincent_visitor_confirmation_cases.json), [before transcript](../eval/reports/vincent-decision-fresh-before.qwen.json), [after transcript](../eval/reports/vincent-decision-fresh-after.qwen.json).
- [Semantic review and per-turn notes](../eval/reports/vincent-decision-review.json), [independent private HTTP receipt](../eval/reports/vincent-decision-http.json).

## Outcomes

| Measure | Original before | Original after | New before | New after |
| --- | ---: | ---: | ---: | ---: |
| Reply delivered | 10/13 | 11/13 | 8/13 | 12/13 |
| Appropriate route | 8/13 | 11/13 | 8/13 | 10/13 |
| Appropriate route on reference-dependent follow-ups | 2/4 | 3/4 | 2/5 | 3/5 |
| Correct spoken content | 3/10 | 7/11 | 4/8 | 7/12 |
| Adequate displayed citations | 2/10 | 7/11 | 4/8 | 8/12 |
| Accurate memory | 6/10 | 7/11 | 6/8 | 7/12 |
| Correct spoken content and adequate citations | 2/10 | 7/11 | 4/8 | 7/12 |

Across all submissions, 14/26 produced a reply with both correct content and adequate source coverage, up from 6/26. On new wording the number of correct memory updates rose by one, but accuracy among delivered updates fell from 75% to 58%. The held-out data therefore do not demonstrate an improvement in memory reliability.

Delivery and routing use all 13 submitted turns in each suite. Spoken answers, citations and memory can only be read on delivered turns. The paired counts use the number actually delivered for that run. Source availability means every designated record was in the actual lookup results or previously verified cache. Source cards and factual grounding are checked independently: a claim can be correct by chance, and a real source can be cited for the wrong claim.

Of the nine original turns requiring a particular record, that record was available in six before and all nine after. It was cited in three before and seven after. For the eight new turns with designated evidence, availability rose from three to six and expected citations from one to four. In the original suite structured status, language and required-citation checks rose from 5/13 to 9/13; on the new suite they rose from 4/13 to 7/13.

## What visitors actually heard

In the original color exchange Vincent answered “I described the color of those flowers as violet.” The reply cited letter 772 and the memory kept the same word. The follow-up named conservator Devi Ormond and explained that the red geranium lake mixed with blue had faded. The first turn’s memory was replaced with the new conservation finding. On a personal observation, Vincent replied “I am glad you see that pale flower as brave.” No historical motive or visual detail was added.

In the new conversations the model distinguished a pigment fading from every trace disappearing, and preserved the distinction in memory. It also read the shipment letter’s date as “14 or 15 July” instead of presenting July 14 as certain. The independent HTTP check returned “a lilac bush” with a link to letter 772. Readback and replay produced the identical saved result without another generation.

## Remaining failures

- The correct shipment letter was available in the original suite, but Vincent described all seven studies as studies of irises. The letter lists *Irises* among the shipment. The same unsupported scope reached memory.
- A follow-up about who submitted the painting was treated as a request to repeat a previous answer. Its reuse proof failed validation, so no answer was published. Both suites also contained a looking question where the model proposed a duplicate refinement despite already seeing useful evidence.
- On a reconstruction question, a passage about irreversible light damage did not establish an exact pigment recipe. The final reply drew a conclusion about the recipe anyway. On a new art-influence question the model linked flower placement to a conservation finding even though the retrieved passage concerned color change.
- The service verified the citation IDs and span membership. Contextual entailment, precise attribution and the decision to leave a question open still need semantic review.

The code passed 406 Agent tests and 190 knowledge tests. The authenticated HTTP check passed all 11 assertions. These checks cover the private text path, including replay and version isolation.
