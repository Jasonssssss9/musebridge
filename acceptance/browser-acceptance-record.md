# Browser acceptance record (role A, M2)

Owner: Jeremiah Lim. This record covers A's three browser deliverables in M2: connecting the v2 frontend to Go, checking the visitor experience, and testing waiting and recovery with B.

**Status: not run.** These checks need the public v2 Go routes (M2-B3 in the [Agent plan](../docs/agent-plan.md)), which are planned but not yet available. The frontend side is ready: `PUBLIC_CONTRACT=agent npm run dev:live` in `web/` talks to Go once those routes exist. Mock-mode results do not count here.

Record one outcome per row: **not run**, **passed**, **failed** (with what appeared instead) or **deferred** (with the reason). A desktop browser in a phone-sized window is acceptable for M2; the physical-phone walkthrough is M3.

## Run details

| Field | Value |
| --- | --- |
| Date | |
| Tester | |
| Tested commit | |
| Go integration configuration (from B) | |
| Agent service and model profile | |
| Model identity (name, digest) | |
| Browser and window size | |
| Frontend command | `PUBLIC_CONTRACT=agent npm run dev:live` |
| `PUBLIC_BASE_URL` used by Go | |

## 1. Both artwork pages work end to end

Completion condition: a visitor can open each page, ask a question, inspect its sources, refresh, and continue the same conversation. Switching artworks keeps their histories and sources separate.

| # | Check | The Bedroom | Woman at Her Toilette | Notes |
| --- | --- | --- | --- | --- |
| 1.1 | The page opens from `/a/<slug>` and shows title, artist and date | not run | not run | |
| 1.2 | A new conversation is created: in the browser's network tab, `POST /api/conversations` returns `revision` 0, `language_preference` auto and `auto_language` en | not run | not run | |
| 1.3 | A factual question returns a reply with its source card; "Open original source" opens the museum record | not run | not run | |
| 1.4 | Refresh restores the same turns, revision and language settings | not run | not run | |
| 1.5 | A follow-up after refresh continues the same conversation: same `conversation_id`, `revision` one higher | not run | not run | |
| 1.6 | Switching to the other artwork shows only that artwork's history and sources | not run | not run | |
| 1.7 | Switching back restores the first artwork's history unchanged | not run | not run | |

## 2. Visitor experience: statuses, sources and languages

Completion condition: the browser displays the returned answer and sources correctly. A partial answer keeps the citation for its supported part. A temporary translation or language choice has the expected effect on the next turn.

Use the held-out IDs as prompts where listed. The pass condition here is what the browser shows; whether the model's answer is good is scored separately in the held-out evaluation.

| # | Check | Suggested prompt | Outcome | Notes |
| --- | --- | --- | --- | --- |
| 2.1 | Conversational reply shows no source card and no status note | H01 | not run | |
| 2.2 | Answered reply shows a passage card with the English excerpt | H18 | not run | |
| 2.3 | Catalog answer shows "Catalog record" cards, not quotations | H26 | not run | |
| 2.4 | Partially answered reply keeps the source for the supported part and shows "Partly answered" | H31 | not run | |
| 2.5 | Answer plus local clarification shows the source and the question together | H37 | not run | |
| 2.6 | Clarification-needed reply shows "Needs a detail from you" and no source | H49 | not run | |
| 2.7 | Insufficient reply reads as a normal reply, not an error box | H41 | not run | |
| 2.8 | Out-of-scope reply shows "Outside this guide" | H55 | not run | |
| 2.9 | Each language: a reply in English, Spanish, Chinese and Korean renders with the correct "Answered in" label | H13 in all four | not run | |
| 2.10 | Auto follows the language the visitor writes in | S10 turns 9–10 | not run | |
| 2.11 | Selector change is sent once; the next message sends null and keeps the saved preference | S12 turns 5–6 | not run | |
| 2.12 | One-reply instruction: next turn returns to the saved preference | S09 turns 7–8 | not run | |
| 2.13 | Translation of the previous answer does not change the saved preference | S10 turns 2–3 | not run | |
| 2.14 | Persistent switch ("from now on") changes the selector to the new saved preference | S09 turn 5 | not run | |
| 2.15 | Long reply (near 2,000 characters) wraps and scrolls without breaking the layout | any detailed request | not run | |

## 3. Waiting and recovery, with B

Completion condition: the browser reconciles with Go's history, displays a completed turn once, and lets the visitor review a stale draft before resubmitting. B provides the fault injection.

| # | Check | How | Outcome | Notes |
| --- | --- | --- | --- | --- |
| 3.1 | Waiting state appears; after 8 seconds the long-wait message appears | Slow model reply | not run | |
| 3.2 | Dropped response: the answer is recovered from history and shown once | B drops the response after commit | not run | |
| 3.3 | Refresh while processing: the page shows the pending question, then the answer once | Refresh mid-request | not run | |
| 3.4 | Duplicate submission: double tap or retry does not create a second turn | Tap Send twice; retry after a drop | not run | |
| 3.5 | Retryable failure (503/504) offers "Try again"; retry reuses the same message ID, language choice and revision | B makes the model unavailable | not run | |
| 3.6 | Non-retryable failure (502) shows the request ID and offers only "Edit question" | B returns an invalid model result | not run | |
| 3.7 | Stale revision: a turn from a second tab causes 409; the draft returns to the box and nothing is resent | Two tabs | not run | |
| 3.8 | Late reply after switching artworks never appears on the other artwork | Switch while waiting | not run | |
| 3.9 | 70-second wait ends with "Check again"; checking later shows the answer once | B delays past 70 s | not run | |
| 3.10 | Technical failures are never worded as the museum lacking information | Any of 3.5–3.6 | not run | |

## 4. Held-out evaluation hand-off

| Item | Value |
| --- | --- |
| Suite version and MANIFEST.json hash | |
| Frozen on | |
| Language reviewers signed off (es, zh-CN, ko, en) | |
| B label review signed off | |
| Handed to C for the evaluation run on | |

## Known limitations to report

- There is no reset or new-visitor control; the contract defers it. Report this separately, not as a failure (visitor-acceptance.md V09).
- The model-answer label "AI guide response" is a placeholder until the team agrees on wording.
