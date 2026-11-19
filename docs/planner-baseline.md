# Two-turn visitor study with the 9B planner

The pinned 9B workflow delivered all eight first replies and two of eight
follow-ups. Only the Chinese conversation about colors, pigment and support
gave the visitor a complete answer on both turns. The six failed follow-ups
stopped during planning, before the answer model could use a source or write
new memory. The study tested one model configuration and one run per dialogue.

## Setup

Eight [frozen dialogues](../agent/testing/planner_comparison_cases.json) cover
English, Spanish, Simplified Chinese and Korean. Each language has one dialogue
per artwork. Their 16 turns ask about 12 supported facts, seven unsupported
specifics and one personal reaction. Each factual part has its own prewritten
English retrieval query and expected source IDs. The forms and judgments were
set before model inference.

Both planning and answering used `qwen3.5:9b-q4_K_M` with Ollama 0.40.16,
a 16,384-token context, a 2,048-token output budget per call, temperature zero
and thinking disabled. The underlying application was at Git commit `9cfb4c3`.
The real Agent workflow used Auto language selection and the real HTTP
retriever on release `aic-seed-v0.1`. Each dialogue began with empty history.
The second request received the actual first answer, its evidence cache and its
simulated committed state. The simulation used the same state contract without
starting the Go store. No second turn needed to be skipped. The experiment used
no extra inference retries and made no change to service readiness.

## Outcomes

| Measure | First turns | Second turns | Total |
| --- | ---: | ---: | ---: |
| Plan accepted and reply delivered | 8/8 | 2/8 | 10/16 |
| Accepted plan preserved all intended requests | 7/8 | 2/2 | 9/10 |
| Visitor-facing answer passed | 5/8 | 2/8 | 7/16 |
| Full next-turn memory passed | 7/8 | 1/8 | 8/16 |
| Both answer and memory passed | 5/8 | 1/8 | 6/16 |

The intent row counts accepted plans. The other rows count all scheduled turns.
A scoped gap can be a faithful record of an unsuccessful lookup even when
the release contained a fact the visitor should have received. Memory review
includes inherited goals and the current source cache. For P07 the current
follow-up entry correctly keeps ruler use open, but it inherits a spurious
second goal from the previous plan. Raw model drafts rejected by validation
never reached the visitor or memory.

Median submission time was 5.57 seconds for the 10 delivered turns (2.49–7.04)
and 3.79 seconds for the six rejected turns (2.82–5.40). Timing covers Agent
submission and local state simulation on successful turns. It excludes warm-up,
the reference queries and review. It also excludes mobile browser and Go API
latency.

## What happened in each dialogue

| Dialogue | First turn | Follow-up |
| --- | --- | --- |
| P01 · English · The Bedroom | Described the marks as thick and broken; retained the caption goal and source. | Recognized both questions but requested clarification while clearing the pending question. The plan failed schema validation even though the source for the marks was already cached. |
| P02 · English · Woman at Her Toilette | The generated query missed the bedspread and wallpaper. The reply and memory reported a local gap. | The visitor referred to two objects the previous answer had never named. The planner marked two requests as needing clarification, exceeding the one-clarification limit and leaving no clear artwork fact for its retrieval route. |
| P03 · Spanish · The Bedroom | Responded naturally to the visitor's feeling of calm and attributed it to the visitor. | Treated a request for the label's interpretation as a clarification within a direct reply. The plan had no clear request eligible for that route. |
| P04 · Spanish · Woman at Her Toilette | Described the suggested floral pattern and left the artist's petal-by-petal process open. | Picked a direct reply for a clear artwork fact despite having the relevant excerpt in the cache. That route cannot answer the request. |
| P05 · Chinese · The Bedroom | Correctly described the colors and cited the source. | Kept the specific blue pigment open and separately cited the material record for canvas. Both turns passed. |
| P06 · Chinese · Woman at Her Toilette | Corrected the mural assumption by giving the documented medium. | Understood the question about weave density but marked it as needing clarification under direct reply. There was no clear request eligible for that route. |
| P07 · Korean · The Bedroom | Turned the request for the label's wording about the lines into an additional request for a general description. The query lost the direction detail and returned nothing. | Correctly left ruler use open. The full state still carried the extra goal from turn one. |
| P08 · Korean · Woman at Her Toilette | Produced a keyword list and missed the source locating the feathery brushwork in the background. | Correctly distinguished what the marks evoke from actual feather use. The plan passed schema validation but inserted spaces in the quoted Korean visitor fragment and failed the exact-source check. |

Five follow-up failures were contradictions between the chosen plan branch
and its required fields. All five outputs were parseable JSON. The sixth was
the literal-fragment mismatch in P08. Two rejected follow-ups, P01 and P04,
already had the relevant excerpt in the previous turn's verified cache.

## English retrieval controls

All 19 factual controls ran independently of the dialogue through the same
scoped HTTP tool with `top_k=2`. They never entered the model prompt, cache or
next turn. The social reaction had no lookup.

| Gold requests | Frozen control result |
| --- | --- |
| 12 supported facts | 11/12 returned the expected source ID. |
| 7 unsupported specifics | Three returned no candidate. Four returned a related description that did not establish the requested fact. |

Eight supported parts reached an actual generated lookup. On those same eight
parts the generated queries found the source in 5/8, and the frozen English
queries found it in 8/8. Four other supported parts belonged to turns rejected
during planning, so they do not count as generated retrieval misses.

The controls were written with knowledge of the source. In particular,
the P07 control used *receding*, which supplies the direction the visitor
wanted to learn. After scoring the frozen run, four additional English
wordings tested dependence on such cues. These checks are separate from the
frozen scores.

| Case | Additional query | Returned source |
| --- | --- | --- |
| P02 | What background objects does the museum say the strokes suggest in Woman at Her Toilette? | Morisot description |
| P03 | What mood might the picture The Bedroom by Vincent van Gogh suggest, according to the museum? | Van Gogh description |
| P07 | In which direction do the room's lines appear to run in The Bedroom by Vincent van Gogh? | Empty |
| P08 | In which part of Woman at Her Toilette does Morisot use the brushwork the museum calls feathery? | Morisot description |

## Decision and reproduction

The first planning experiment worth testing is a simpler choice between three
situations. Distinguish a clear factual question from an ambiguous referent
or a conversational remark. An unknown answer belongs to the first situation when
the question itself is clear. Keep the strict validation and source binding.
Test whether this separation reduces rejected follow-ups, including when the
previous turn already supplied a relevant excerpt. P07 also warrants a
retrieval check using ordinary directional wording that supplies no answer.
A larger planner was outside this run; its quality and latency are unmeasured.

From the repository root, use the pinned tokenizer and a fresh private report
path outside the checkout. See the [Ollama operator guide](ollama-provider.md)
for the provider configuration.

```sh
.venv/bin/python -B -m agent.testing.planner_comparison \
  --base-url http://127.0.0.1:11434 \
  --tokenizer ../local-model-assets/qwen35-tokenizer.json \
  --report ../local-model-results/planner-baseline.json
```

The report contains the fitted model inputs, plan validation results, tool
returns, wall times, replies, proposed states and separate manual controls.
A successful command indicates the probe completed with the same application
fingerprint. Use the case rubrics to review semantic quality. These dialogues
are now regression material.

The frozen fixture has SHA-256 digest `749c071ad574d51bbd0f60fdec281db5d17c5a14a3437efb60733ac1b21ff003`.
