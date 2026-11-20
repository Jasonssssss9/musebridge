# Visitor acceptance (role A)

Owner: Jeremiah Lim (role A in the [Agent plan](../docs/agent-plan.md)).

This folder holds A's M2 acceptance material:

| Path | What it is | Status |
| --- | --- | --- |
| `visitor-heldout/v0.1/single-turn.json` | 60 independent single-turn scenarios, each in four languages (240 trials) | Draft, not reviewed, not frozen |
| `visitor-heldout/v0.1/multi-turn.json` | 16 multi-turn visitor scripts, 154 visitor turns | Draft, not reviewed, not frozen |
| `visitor-heldout/v0.1/review/*.md` | One review sheet per language, generated from the JSON | Waiting for reviewers |
| `browser-acceptance-record.md` | Record for A's browser integration, visitor-experience and recovery checks | Not run: needs B's public v2 routes (M2-B3) |
| `check_heldout.py` | Structural checks and the freeze manifest | Passing on the draft |
| `render_review.py` | Regenerates the review sheets from the JSON | |

Nothing here has been run against a model. None of it is a result.

## What the held-out suite covers

It follows [agent-design.md section 13.2](../docs/agent-design.md#132-model-and-conversation-evaluation) and [visitor-acceptance.md section 4](../docs/visitor-acceptance.md#4-language-and-script-coverage).

### Single-turn scenarios

Each scenario starts a fresh conversation (revision 0, preference Auto, Auto fallback English, empty evidence cache) and sends one message.

| Category | Required | IDs | Expected status |
| --- | ---: | --- | --- |
| Direct conversation and general art | 12 | H01–H12 | conversational (H11 may also be answered) |
| Fully supported facts | 16 | H13–H28 | answered |
| Mixed social/fact or supported/gap | 12 | H29–H40 | answered, partially_answered or insufficient as labelled |
| Unsupported facts | 8 | H41–H48 | insufficient (H47 may also be out_of_scope) |
| Genuine ambiguities | 6 | H49–H54 | clarification_needed |
| Scope | 6 | H55–H60 | out_of_scope |

Both artworks appear (31 Bedroom, 29 Woman at Her Toilette). Language behavior is spread across categories: a one-reply instruction (H10), a persistent setting (H12), a quoted-phrase translation (H09), the language selector (H27), a translation with nothing to translate (H51), a bilingual request (H53) and an unsupported language (H54).

The 12 scenarios marked `risk_repeat` are the ones to run three times each: H04, H10, H20, H23, H34, H41, H42, H44, H45, H46, H48 and H53. They target the places a model is most likely to fill gaps from memory (pigments, versions, the sitter's identity, the artist's feelings, exact dates, acquisition year, named colors, invented background objects, movement membership) or mishandle language rules.

### Multi-turn scripts

| Script | Type | Language(s) | Artwork | Families |
| --- | --- | --- | --- | --- |
| S01 | Monolingual | English | The Bedroom | V01, V02, V06, V07 |
| S02 | Monolingual | English | Woman at Her Toilette | V03, V05, V10 |
| S03 | Monolingual | Spanish | Woman at Her Toilette | V01, V02, V11 |
| S04 | Monolingual | Spanish | The Bedroom | V03, V07, V06 |
| S05 | Monolingual | Chinese | The Bedroom | V01, V02, V05 |
| S06 | Monolingual | Chinese | Woman at Her Toilette | V03, V11, V10 |
| S07 | Monolingual | Korean | Woman at Her Toilette | V01, V02, V07 |
| S08 | Monolingual | Korean | The Bedroom | V03, V08, V09 |
| S09 | Switching | zh → ko → es | Woman at Her Toilette | V04 |
| S10 | Switching | en → es → ko → Auto | The Bedroom | V04 |
| S11 | Switching | es → en → zh → ko | Woman at Her Toilette | V04 |
| S12 | Switching | ko → en → es (selector) → Auto | The Bedroom | V04 |
| S13 | Switching | en, es; both artworks | Both | V08 |
| S14 | Switching | zh → en, ko once | Woman at Her Toilette | V12, V04 |
| S15 | Switching | es, ko under Auto | Woman at Her Toilette | V09, V04 |
| S16 | Switching | all four | The Bedroom | V04, V06, V11 |

V01–V04 are covered in every language; V05–V12 at least once. The scripts also cover the fifth/sixth-turn boundary, thanks during a pending question, an abandoned and several resolved pending questions, source reuse, gaps, artwork isolation, catalog questions, one-off translation versus persistent settings, subjective corrections, retained style preferences, unavailable exact quotations, pointing and comparison, request overflow, refresh, a stale revision, reset requests and story requests. Browser actions (refresh, a dropped connection, switching artwork while waiting, a second tab) are written as separate steps or attached to the turn they affect.

These are new wordings of the published journey families. They are not copies of the V01–V12 scripts, which remain development material.

## Labels

Every scenario and every script turn carries these labels, written before any model output exists.

| Label | Meaning |
| --- | --- |
| `intent` | The visitor's goal in plain English (single-turn) |
| `status` / `status_any` | The public status the reply must have; `status_any` lists acceptable alternatives where the contract allows more than one valid route |
| `routes` | Plan actions that are acceptable (single-turn) |
| `request_kinds` | The request kinds a faithful plan should contain (single-turn) |
| `retrieval` | `none`: no lookup should happen; `required`: fresh retrieval is needed; `either`: retrieval or reuse of verified evidence are both fine |
| `citations.required` | Source IDs the reply must cite |
| `citations.any_of` | Groups where at least one ID from each group must be cited (used for overviews) |
| `citations.allowed` | Further IDs the reply may cite. Any cited ID outside required, any_of and allowed is a failure |
| `answer_language` | The language the reply must be written in |
| `preference_after` | The saved language preference after the turn |
| `auto_language_after` | The Auto fallback language after the turn |
| `pending_after` | `none`, `local` (clarification bound to one request), `whole` (whole-message clarification), `kept`, `cleared`, or a combination where either is acceptable |
| `must` / `must_not` | Behavior the reply must show or avoid, including prohibited claims |
| `language_override` | The Answer language selector value sent with the message (null means unchanged) |

Multi-turn language labels were computed from each turn's detected language and instruction using the rules in [agent-api-contract.md section 4.2](../docs/agent-api-contract.md#42-languagedecision-translation-and-deterministic-resolution). A turn marked `und` is a neutral acknowledgement or a pure language or translation command, which the contract says does not move the Auto fallback. That produces some outcomes a reviewer may find surprising, for example S15 turn 9, where "감사합니다." gets a Spanish reply because the last substantive message was Spanish. If the team decides that rule is wrong, change the contract first, then the labels.

Source IDs are the four retrieval chunks in `knowledge/data/aic-seed-v0.1.json` plus the six catalog facts `<artwork_id>-catalog-{title,artist,date_display}` from contract section 6.1.

## How this was authored

The suite was drafted by A with an AI coding assistant, working only from the public documents (agent-design.md, agent-api-contract.md, visitor-acceptance.md, agent-plan.md) and the released seed. The prompts in `agent/prompts/`, C's development and holdout cases in `agent/testing/` and `eval/`, and any recorded model output, including the November 18 run, were not opened while writing it.

Two things follow from that:

- **It still needs human review before it counts as independent.** The plan requires reviewers competent in each language and a check of the source and state labels by B. Until those reviews are signed off, treat the suite as a draft.
- **Shared tooling is a correlation risk.** If C also uses the same AI assistant to write prompts, the suite and the prompts could share blind spots. Reviewers should keep that in mind when they edit wording.

## Before freezing

1. **A reviews every scenario and turn** for realism and for whether the expected outcome is right. Edit the JSON, then rerun `python3 acceptance/check_heldout.py` and `python3 acceptance/render_review.py`.
2. **Language reviewers** go through `review/es.md`, `review/zh-CN.md`, `review/ko.md` and `review/en.md`, and sign the table at the bottom. Reviewers should read the language well enough to judge whether a visitor would naturally say it.
3. **B checks the source and state labels** against the seed and contract: required and allowed citations, `retrieval`, `pending_after`, and the language columns.
4. **Do not show C the suite or ask C to comment on it** until it is frozen. C can see the category counts in this README.

## Freezing

After the reviews, from the repository root:

```bash
python3 acceptance/check_heldout.py          # must say "structure: ok"
python3 acceptance/render_review.py
# change "status" in both JSON files to "frozen", rerun the check, then:
python3 acceptance/check_heldout.py --freeze # writes visitor-heldout/v0.1/MANIFEST.json
git add acceptance
git commit -m "acceptance: freeze visitor held-out suite v0.1"
git tag visitor-heldout-v0.1
```

After that, `python3 acceptance/check_heldout.py --verify` confirms the files are unchanged. A frozen file is never edited. If a held-out case informs a prompt or workflow change, record it as regression data and write fresh cases (v0.2) for the next generalization claim, as the design requires.

## Using the suite in the evaluation run

C owns the runner. For each single-turn variant, start a new conversation on the scenario's artwork, set the selector to `language_override` if it is not null, send `text`, and score the reply against the labels. For each script, run the steps in order on one conversation per artwork, performing the browser steps as written. Score each turn on the dimensions in [visitor-acceptance.md section 5](../docs/visitor-acceptance.md#5-scoring-and-evidence) and report counts with denominators by language, artwork, category or family, and route.

Keep all four language variants of a scenario together in the same split. They are one scenario, not four.
