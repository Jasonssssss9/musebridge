# M2 public-path development results

These checks submit real visitor messages through the Vite origin and Go API.
Go calls the Python Agent, the pinned Qwen model and the HTTP retrieval service.
The browser runs desktop Chrome at a 390 × 844 viewport. Raw replies, provider
decisions and conversation state stay in the private run directories.

## Final public candidate

All 48 scheduled turns reached the visitor. The final source manifest is
`8d78d5a0b962f50c0e5b219064996564b28ae2d7f9dd311a157c1a7a9aee3c6b`.
It passed 47/48 machine checks, 47/48 answer-text reviews and 48/48 memory
reviews. The eight-turn conversation passed throughout, including recalling
the artist in Chinese and resuming Spanish for the next answer.

The remaining failure involved a Spanish visitor who quoted the label’s
brushstroke description and asked whether Morisot literally painted with a
feather. The guide treated the preamble as another question, returned partial
status and included a sourced but unnecessary reference to patterned
furnishings. It correctly left the literal tool unverified. Its memory quoted
the visitor and recorded the guide’s source check and the local gap. The
development review found no unsupported claims about either painting in the
48 public answers. Each commit was observed in history, and replay returned all
48 saved messages without another model call. Private trace counts matched
public executions on all 48 turns.

## Development coverage

The expanded public suite contains 25 distinct scenarios, 37 journeys and 48
scheduled turns. The first 24 repeat the original smoke suite. The eight-turn
journey crosses the five-pair history limit, changes the interface language and
requests a different language for one answer. A dependent turn blocked by a
failed prerequisite remains in every scheduled denominator.

| Product snapshot | Scheduled | Delivered | Public machine checks | Answer text | Next-state memory | Blocked | Desktop browser |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Starting commit `055b437` | 24 | 22 | 22 | — | — | 0 | 7/8 |
| First expanded candidate | 48 | 39 | 38 | 37 | 38 | 6 | 10/10 |
| Planning experiment | 48 | 33 | 32 | 32 | 32 | 6 | Not repeated |
| Language and context revision | 48 | 44 | 40 | 40 | 40 | 1 | 10/10 |
| Compound and conversational revision | 48 | 47 | 45 | 45 | 45 | 1 | 10/10 |
| Scoped-memory revision | 48 | 46 | 45 | 46 | 46 | 1 | 10/10 |
| Catalog follow-up and normalized passage selection | 48 | 48 | 47 | 47 | 48 | 0 | 10/10 |
| Spatial evidence check | 48 | 48 | 47 | 47 | 48 | 0 | 10/10 |
| Final candidate with capacity and language fixes | 48 | 48 | 47 | 47 | 48 | 0 | 10/10 |

Machine checks cover status, answer language, scoped citations and revision.
The suite separately verifies full committed history, replay without further
inference, private call counts and atomic failure. A coding agent reviewed the
actual answers and proposed memory against the question, conversation and
museum sources. Missing, rejected and blocked turns earn no semantic credit.
The independent visitor evaluation requires its own sealed suite and reviewers.

## Changes tested

- On `055b437`, each artwork’s second material question used a retrieved
  excerpt as a catalog citation. Both failed validation. The first expanded
  candidate passed all 24 original checks. Its new cases exposed a fabricated
  palette in a response to a visitor feeling calm, a missed perspective
  description and two invalid clarification plans.
- The planning experiment fixed the two clarifications and retrieved the
  perspective excerpt. Several ordinary requests regressed. For example, a
  request for the title and artist elicited only the title. The language and
  context revision recovered these routes, but three compound requests still
  assigned two facts the same source passage.
- The compound and conversational revision completed both artworks’ mixed
  material/duration questions and kept the duration gap local. It also handled
  unrelated weather and the visitor’s personal impression appropriately. A Spanish factual preamble still became a second request, and a Chinese
  question asking which painter was mentioned elicited a definition of the word
  “painter.” The latter blocked the eighth
  turn. All 47 committed turns passed history and replay.

The scoped-memory revision passed 45/48 public machine checks. A Spanish
visitor asked whether a featherlike brushstroke description meant Morisot
used an actual feather. The reply distinguished the description from the
unverified tool, and memory preserved the visitor's statement and the scoped
gap. The planner nevertheless counted the preamble as a second request and
returned partial status with a citation. On the long conversation's seventh
turn, the generated catalog answer identified Van Gogh, but its plan attached
a conversational acknowledgement to the factual request. Validation rejected
that turn atomically and the eighth turn was blocked.

The next revision completed all eight continuity turns. It named Van Gogh in
Chinese on turn seven, returned to Spanish for turn eight and kept the unknown
sale price open. The Spanish feather question still prompted a relevant sourced
description and an unnecessary reference to patterned furnishings. Its memory
record accurately distinguished the visitor’s words from the guide’s source
check. Browser and collector checks passed on this revision.

## Retrieval diagnostics

Three separate paint-handling probes used the ordinary model and public route.
On the language and context revision, the planner preserved phrases such as
“handling of the paint,” “the way the artist handled the paint” and “facture
or visible marks left by the painter.” The retrieval service returned valid,
empty results and the model chose to finish all three times. On the same
snapshot, asking how the museum describes the brushwork returned the correct
description for each artwork at rank one.

The scoped-memory revision retrieved the right description and answered two of
the three paint-handling probes. Its remaining query named Berthe Morisot in
place of “the artist.” All three finished without refinement. Reading the active artist from the scoped
catalog record then allowed all three probes to return the correct description.
The final public rerun returned thick and broken brushwork for both Bedroom
queries and soft, feathery background brushstrokes for Morisot. Each answer
cited its own description. Restoration handling and another person’s use of
paint remained unsupported in focused retrieval tests.

The spatial and paint-handling changes were checked against all nine historical
retrieval suites. The starting commit, snapshot immediately before each change
and updated retriever produced identical per-case results for 60 whole-query
cases and 96 compound cases in both full and isolated modes.

## Provider qualification

The initial 16K provider check delivered all 17 answers, but only 14 met every
machine expectation. Two language-neutral inputs acquired a detected language
and a Spanish preference was treated as a one-turn instruction. The separate
five-pair capacity test also failed. Given five prior questions with answers of
2,000 characters each, the planner kept only the last two pairs.

The final provider run passed **17/17 machine cases** (13 published development
and four bundled comparison cases) and all seven infrastructure checks.
All 21 planning, 12 assessment and one separately exercised final-generation
response passed the full schema. On the same five long pairs, planning retained
all five at 13,261 server prompt tokens (13,321 estimated). Assessment retained
all five at 10,828 tokens (10,888 estimated). Each request fit the 16,384-token
context with 2,048 output tokens and a 512-token safety reserve.

The compact system prompt is chosen under token pressure. Offline comparison
of 78 operations from the public journeys and 62 from the separate 32-turn
Agent study produced byte-identical system messages, user messages and provider
JSON schemas before and after the fitter revision. All 140 ordinary inputs kept
their original history and token counts.

The provider report remains pending semantic review and its private marker
is inactive. The bundled provider cases are separate from the independent
visitor evaluation in the team plan.

## Measurement and reproduction

The final candidate passed all ten browser checks. They cover the source quotation and URL on the correct work,
visible medium terminology in all four languages, conversation restoration,
artwork switching, the waiting state and recovery after losing a response that
Go had committed. The two development collector questions use published
material prompts. Both messages were saved and restored via the public history endpoint and
matched their private Agent results.

Warm public latency measures the HTTP request through Go and the Agent at the
browser origin. The nearest-rank p95 includes completed submissions with a
measured route. The final candidate measured **5.736 seconds**
for 18 direct turns and **6.349 seconds** for 30 retrieval/reuse turns. None
chose refinement, so a refined-path p95 is unavailable. Each private report includes the source-manifest digest,
model identity and case-level timings.

See the [runtime instructions](m2-runtime.md) for reproduction and the
[team plan](agent-plan.md) for formal acceptance.
