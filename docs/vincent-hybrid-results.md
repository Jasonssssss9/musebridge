# Vincent hybrid acceptance

## Scope and method

The team’s [13 prepared visitor turns](../agent/testing/vincent_hybrid_cases.json) cover seven English conversations about Getty’s *Irises*. The final run used real Qwen `qwen3.5:9b-q4_K_M` through Ollama, temperature zero, thinking off, a 16,384-token context and a 2,048-token output ceiling. Each question was submitted once per run. No expected ID, control query or grading instruction was supplied to the model. Development iterations used this same suite, so it measures regression on known cases. See the [raw acceptance trace](../eval/reports/vincent-hybrid-acceptance.qwen.json) for actual questions, queries, passages, answers, source cards, model attempts and memory.

## Results

| Measure | Observation |
| --- | --- |
| Delivery | 13 submitted, 9 delivered, 4 rejected in planning. Every submission had its full intended prior history in this run. A rejected turn published no reply, citations or memory update. |
| Live evidence availability | Of nine turns with designated evidence, six retrieved it on that turn and one had it in verified cache. Two did not obtain it. The seventh actual search was for a possible symbolic intention for which the records give no answer. |
| Spoken facts and attribution | 4 of 9 delivered replies answered the question within the evidence and met its spoken attribution requirements. Separately, 5 of 9 sounded natural for the conversational role. A fluent invented detail fails grounding. |
| Citation coverage | 2 of 9 displayed sources supporting all the factual claims made in the reply. The pigment and lilac answers had the expected source ID and still added unsupported claims. |
| Memory | 3 of 9 retained the right owner, referent and evidentiary scope. One update repeated the previous turn and omitted the conservator’s entire finding. |
| Machine fields | 6 of 13 submissions met the automated status, language-field and expected-citation checks. This check does not read the prose. |

## Transcript review

Spoken content is graded for factual scope, coverage and required attribution. Citations must cover every factual claim, including details added during social or insufficient answers. Memory is graded against the visitor's actual referent and the source. A dash denotes an unpublished turn. The **Evidence** column records the real workflow; the [standalone ranker report](../eval/reports/vincent-hybrid-retrieval.json) is an independent test.

| Visitor topic | Evidence in the workflow | Spoken content | Citations | Memory | Observation |
| --- | --- | --- | --- | --- | --- |
| Word for the flowers’ color | Letter 772 retrieved | Fail | Fail | Fail | Says “violet irises” but claims a specific color name for the finished work is missing. The editorial locator identifies the painting. No citation appears and memory preserves the false distinction. |
| Why the color changed | Getty pigment record retrieved | Fail | Fail | Fail | Correctly mentions fading red mixed with blue but calls violet the color seen today. Getty is unnamed in speech. The cited passage describes the original mixture. Memory repeats the preceding question and omits the new finding. |
| The other garden subject | Letter 772 retrieved | Fail | Fail | Fail | Names the lilac bush but makes violet irises a separate additional subject and calls the garden his own. The letter lists two subjects. Memory repeats the double count. |
| What the shipment number counts | No lookup | — | — | — | Invalid direct plan reused the earlier garden question. No shipment passage entered the run. |
| Who said “full of air and life”? | Getty object description retrieved | Pass | Pass | Pass | Correctly identifies Theo as the speaker and ties September to the submission. Speaking of Vincent in the third person weakens the character voice. |
| Who submitted it? | Correct Getty passage already cached | — | — | — | Both the preceding exchange and the required source were present. The planner split one resolvable follow-up into overlapping requests. |
| The white flower feels brave | Direct conversation | Fail | Fail | Fail | Warm response, then invents a deep-blue sky. The memory credits the visitor but changes one pale flower into several. |
| Did the flower symbolize courage? | Two related letters retrieved | Pass | Fail | Fail | Keeps the artist’s intent open within these letters. It mentions historical garden subjects and studies without showing a source. Memory changes the single flower to the whole work. |
| A looking exercise | No lookup | Fail | Fail | Fail | Interprets looking as physically experimenting on the painting. It invents brushwork and colors. Memory records the misunderstanding. The gallery has a looking note that could have answered the question. |
| What is it painted on? | Getty medium retrieved | Pass | Pass | Pass | Answers canvas, cites the medium record and closes the factual question in memory. |
| Are the effects of light reversible? | Getty exhibition record retrieved | Pass | Fail | Pass | Correctly says changes to some colors are irreversible. The model marks this supported negative answer insufficient and omits the citation. |
| Exact thirds for mixing paint? | No lookup | — | — | — | Duplicated requests in an invalid direct plan prevent a response about the unsupported recipe. |
| Winning lottery number | No lookup | — | — | — | The model chose a direct response and produced an invalid combination of clarification and out-of-scope results. No redirect was published. |

## Integration observations

A separate [authenticated HTTP request](../eval/reports/vincent-hybrid-http.json) asked what other plant Vincent mentioned in the same letter. Qwen answered “a lilac bush” and cited letter 772. The turn used two real model calls and one hybrid lookup. GET recovery and replay returned the identical snapshot with zero additional model calls. A request without credentials returned 401 and a v2 read of the v3 run returned 404. This exercises the private service. A live Go commit, mobile playback and a fresh visitor-quality suite are separate checks.

The remaining development targets are smaller planner decisions for single follow-ups, showing citations for supported subclaims inside partially answered replies, and carrying the referent and speaker through the next memory update. None of the four planning rejections reached the retrieval service. A [preceding live run](../eval/reports/vincent-hybrid-final.qwen.json) also delivered nine replies, but different turns failed, and the pigment explanation and its memory differed. An answer from one run should not be used to grade another. The latest implementation also has a regression test for an eight-record cache receiving new evidence during follow-up lookups.
