# M2 verification record

In controlled integration, a visitor can open either artwork, ask a question in English, Spanish, Chinese or Korean, read a Qwen answer with its sources, and continue the conversation. Go owns the conversation, the Python Agent decides when to retrieve, and Ollama serves the model. Independent visitor-quality acceptance remains open.

The live measurements below apply to the implementation recorded at [69c5333](https://github.com/Jasonssssss9/musebridge/commit/69c53334cf12cfeed59261637f35201465647731). The [offline engineering checkpoint](#offline-engineering-checkpoint) describes the later candidate. The [M3 plan](agent-plan.md#m3-deploy-and-complete-acceptance) assigns its outstanding qualification and release work.

## Public development result

All 48 scheduled messages reached a committed public reply. The automated expectations passed on 47/48, development review passed 47/48 whole answers and 48/48 next-state memories. None were rejected or blocked. The eight-turn conversation answered the artist question in Chinese for one reply and returned to the saved Spanish preference on the next turn. One Spanish question asked whether Morisot used a real feather. The guide correctly left that fact open, but treated the visitor’s brushwork preamble as a second request and added a description of the furnishings.

Desktop Chrome passed all 10 mobile-viewport checks. Replay and history matched all 48 committed messages. Warm public p95 was 5.736 seconds over 18 direct replies and 6.349 seconds over 30 retrieval or cache replies. No public turn used refinement, so its latency remains unmeasured. Three separate paint-handling questions returned the correct description and source for the active work. The development collector also restored both of its published sample turns.

The public source-manifest SHA-256 was `8d78d5a0b962f50c0e5b219064996564b28ae2d7f9dd311a157c1a7a9aee3c6b`. The [public-path results](m2-public-results.md) report separate browser, timing and diagnostic outcomes.

## Candidate and test conditions

The source release was `aic-seed-v0.1` with two artworks. All model-bearing development checks used Ollama 0.40.16 with `qwen3.5:9b-q4_K_M`, digest `56671c2ab9385f9cfcb404638e32cd62d88e3501d44822208363c010179a3c90`. The profile used a 16,384-token context, up to 2,048 output tokens per operation, temperature zero and thinking disabled. The final Agent implementation fingerprint was `a910a582b6037cba0db1c214480881eb2cd700f1eb2726abf3a2299a7cdbc907`. Development runs used their own conversations and private trace directories. The browser check used desktop Chrome at a mobile viewport.

## What changed

- In a single planning call, the model can select one or more catalog fields, the recorded medium, a social reply, a definition, or a clarification for missing translation text or an unidentified detail. The Agent binds this decision to the admitted artwork, effective language and exact visitor message. The English retrieval query remains independent of the reply language.
- For recognized evidence in this release, the model compares each clear factual request with individual source statements. A direct match can supply localized wording and a scoped citation. A related statement alone leaves the question open. The model can still choose the wrong interpretation or miss a supported answer.
- Supported catalog values come from the active artwork record. An ordinary social reaction can receive a localized acknowledgement. Continuity notes distinguish the visitor's own words, supported answers and questions that remain open in this conversation. Recent whole notes survive when the memory limit is reached.
- The public runner follows complete conversations and reports blocked turns. The browser exercises actual material answers, sources, history recovery and response loss. The independent collector joins public messages, private run traces, committed state and separate semantic reviews.

## Agent regression results

| Development measure | Earlier published baseline | Intermediate memory regression | Final candidate |
| --- | ---: | ---: | ---: |
| Delivered | 32/32 | 29/32 | 32/32 |
| Visitor intent preserved | 26/32 | 19/32 | 24/32 |
| Whole answer passed | 20/32 | 22/32 | 23/32 |
| Next-state memory passed | 23/32 | 15/32 | 31/32 |
| Both answer and memory passed | 19/32 | 14/32 | 23/32 |

These are coding-agent reviews against the development rubric. All 32 scheduled turns count. The spatial evidence check changed one answer and its memory in a real 32-turn rerun. The final language and capacity changes preserved all 32 answers, citations and next-state memories on another real run. In Korean, the guide described the suggested furnishings and said it could not verify the wallpaper’s position. The earlier answer had repeated the furnishings description as support for the position.

Remaining failures include reusing a partial cache and missing an available medium, treating a preamble as another question, confusing an appearance with an ownership claim, and resolving a follow-up to the wrong earlier subject. A response can cite a true statement and still miss what the visitor asked.

## Engineering checks

The knowledge tests cover both artworks and include spatial phrasing, media, artwork effects, brush marks and nearby unsupported questions. On all nine historical suites, every expected rank, evidence ID and scored outcome matched the starting checkout. The comparison covered 60 whole-query rows, 96 subquestion rows in the full workflow and the same 96 in isolation. The same rows were reused across modes.

The Agent suite passed all 352 tests and the knowledge suite passed all 148. The generated contract check passed. Go passed all ten tested packages with the integration tag and race detector. The frontend passed all 47 tests and built in both fixture and Agent modes.

The public development runner counts all 48 scheduled messages. The separate 32-turn Agent study reuses two previously published regression sets. Its rubric assesses the delivered answer and continuity memory separately. Neither set estimates performance on new visitors. For reproduction, use the [public runtime instructions](m2-runtime.md) and the [Agent evaluation guide](../agent/README.md).

## Development results

The starting public checkout (`055b437`) rejected both material reminders. A dedicated medium lookup resolved those cases. Expanding the schedule exposed problems with gestures, missing translation text, catalog fields, compound questions and social replies. One intermediate version also regressed on Korean material requests. Each change was checked against the original scenarios as well as the added ones. The [public results](m2-public-results.md) give the denominators, failed dependencies and successful route timings.

On the separate Agent study, the earlier published baseline delivered 32/32 turns. Development review accepted 20/32 whole answers and 23/32 committed memories. An intermediate candidate delivered 29/32, with 22/32 whole answers and 15/32 memories passing. All three rejected plans had copied a historical source excerpt into a field that requires the current visitor's words. In other turns, a factually accurate sentence could still answer a previous question, or a memory could turn a visitor's preamble into a new request. All three subsequently delivered after requiring the chosen sentence to come from the current visitor message and recording the visitor’s own words separately from what the guide checked. A later review also found a relation-specific error. A source that mentioned wallpaper had been accepted for a question about its position on the left. A citation to the correct work did not establish that position. A one-way check now withholds selected evidence for an explicitly requested spatial relation when the selected statements lack its qualifier. It never grants support. Replaying the 30 captured assessments changed only that position check, preserving the separately supported description and leaving the location open.

The Agent study uses `agent/testing/planner_comparison_cases.json` (P01–P08) and `agent/testing/planner_followup_holdout.json` (F01–F08). Each contains eight two-turn dialogues. The F cases were held out in an earlier study and are now development regression cases. To reproduce on the pinned profile, run the following command once for each file with a fresh private report path. Score every scheduled turn, including a rejected or blocked turn, against its visitor intent and permitted sources. Read the complete next-state memory when scoring continuity.

```sh
python -B -m agent.testing.planner_comparison \
  --base-url http://127.0.0.1:11434 \
  --tokenizer /absolute/path/to/tokenizer.json \
  --case-file agent/testing/planner_comparison_cases.json \
  --report /private/results/first-set.json
```

## Provider qualification

The shipped provider verifier exercises published development cases and separate capacity and sizing diagnostics. Its inputs are independent of the sealed visitor evaluation. In the first qualification run, all 17 visitor cases delivered an answer with the expected route, public status, answer language and citations. Fourteen passed every machine criterion. Two pure acknowledgements updated detected language when the contract called for neutral input. An unqualified Spanish response-language instruction was treated as temporary. Schema enforcement, tokenizer sizing, output reserve and the loaded 16K context passed.

The public eight-turn journey retained five ordinary history pairs. A separate stress fixture provided five pairs with 2,000-character replies. The capacity check requires all five exact turn IDs, the full output reserve and the safety margin. The first run retained only the two newest pairs. The fitter now tries a compact rendering of the model’s schema instructions before dropping a history pair. It measures the actual outgoing messages and sends the full authoritative schema to the provider and validator. On the captured five-pair request, the offline prompt estimate fell from 16,122 to 13,311 tokens while retaining the original question, sources and history content.

On the final real run, **17/17 provider cases passed all machine criteria**, including the three language decisions above. All seven infrastructure checks passed. The long-history planning call retained all five pairs at 13,261 server-reported input tokens, and assessment retained them at 10,828. Across the provider suite, the full schema validated 21 planning, 12 assessment and one final response. A development review of the 17 bodies and memories found the material and brushwork descriptions bound to the correct works, five social replies without added artwork claims, and a price question left open only for the current lookup. The separate supplied-query final diagnostic also cited the correct medium. The provider report still awaits operator review. Normal model activation remains closed.

The original and revised fitters also produced byte-identical system messages, user messages and wire grammars on all 140 previously captured operations from the 48-turn public and 32-turn Agent studies. None of those ordinary inputs needed the compact guide.

## Independent acceptance

The independent suite remains on the visitor-acceptance branch. Structural preflight found no issues in 240 language variants, 185 source references and 154 scripted state transitions. Its visitor-language, source and state labels still require attributable human review before the reviewed bytes are frozen. **Zero independent questions were sent to the model.**

The agreed schedule contains 240 single-turn messages, 96 additional risk repetitions and 154 messages in 16 long scripts. It spans 352 fresh conversations, 490 model-bearing messages and four separate browser actions. Missing or blocked turns stay in the appropriate denominators. Independent language, grounding, state, browser review and observed route latency determine release eligibility. The development run does not authorize a readiness record or a release tag.

Use the [public results](m2-public-results.md) for end-to-end measurements, the [evaluation runbook](../acceptance/evaluation-runbook.md) for review and freeze, and the [M2 handoff](m2-handoff.md) for the release gates.

## Offline engineering checkpoint

The model host was unavailable for this revision. The tests below used the released two-artwork corpus, local services and published development cases. The previous live model scores above remain the comparison point for M3.

**Agent planning and evidence.** In a two- to four-sentence factual message, the model can mark each sentence as context, one evidence request, or two separate factual clauses. Only requested facts become English queries. A full plan handles mixed tasks and more than two facts. A factual rewrite can also request a source category absent from an incomplete cache. The Agent then runs the usual scoped lookup with the original rewritten questions. Source-topic matching can trigger retrieval. Support still requires the selected source statement to address the actual claim. A visual description leaves an unsupported claim about ownership, a tool or a particular pigment open.

**Replay of the last live planning decisions.** We held the 32 recorded development inputs, their original model decisions and their incoming conversation states fixed. We compared the chunks actually present at the first historical assessment with the chunks the current planner adapter and retrieval implementation would supply. Twenty-four turns required at least one released evidence chunk. All required chunks were available in **20/24** historically and **24/24** on this replay. Two partial-cache decisions gained a missing medium or description. Two original English queries gained the description through better matching of line direction or qualified language about the work's effect. The replay produced no new answers or memory updates. It cannot measure whether a new model call will choose the intended passage, preserve a historical reference or use a source appropriately.

Reproduce the comparison with the archived combined development report outside the checkout:

```sh
python -B -m agent.testing.replay_evidence_availability --baseline /private/c-verified.json
```

**Public engineering regression.** All 48 public submissions passed their machine checks. Each committed history and replay matched, with zero additional provider invocations on replay. This used the local Go API, Python Agent and HTTP retrieval service. The provider returned archived development decisions, except for the Spanish feather turn, where an explicit planning decision and an explicit assessment exercised the new format. That case verified that the public response and memory keep the unknown tool open. Choosing those decisions unaided is a live-model test. Desktop Chrome passed all 10 browser and recovery checks at a mobile viewport. The Agent passed all 366 tests and the knowledge module passed all 154. The frontend passed 47 tests, built in both modes, and the generated contracts were current. Go passed the race and integration tests in all ten packages with tests.

**Retrieval regression.** Whole-work introductions now retrieve the current work's reviewed description, including when the visitor asks for the label's account of its overall appearance. Targeted tests cover both paintings, quoted titles, qualifiers and nearby unsupported requests about position, ownership or exact materials. Eighteen reports across nine existing suites produced identical row results to the starting checkout: 60 whole queries at each of top-1 and top-2 plus 96 subquestions each in full and isolated mode (312 row evaluations). All 57 archived or scripted public-path lookups retained their candidate order and request classification across 20 distinct artwork/query pairs. These repeated evaluations reuse questions.

**Context budget.** The production fitter and pinned tokenizer retained all five 2,000-character answers in the two-, three- and four-sentence planning probes. The estimated prompts used 13,048–13,148 of 13,824 available input tokens after reserving 2,048 output tokens and a 512-token margin. All 59 archived development planning inputs retained their supplied history. Actual server token counts for the revised prompts still require the live host.

**Independent review and release.** The review tooling can prepare a blank index for frozen files and verify the six attributable approvals against the exact hashes. A's separate 32-row browser record and all independent model messages await their own observations. The previous implementation's model qualification belongs to its recorded fingerprint. The new candidate requires the [M3 qualification, evaluation and phone checks](agent-plan.md#m3-deploy-and-complete-acceptance).
