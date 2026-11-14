# MuseBridge Visitor Conversation Acceptance Plan

Status: **Designed, not executed. No v2 model, browser or deployment result is claimed by this document.**

Suite specification: visitor-acceptance-v1. This plan supplements the [Agent design](agent-design.md), [v2 interface contract](agent-api-contract.md) and [implementation plan](agent-plan.md). The contract defines wire behavior; this document defines what a museum visitor should experience. If implementation reveals an unresolved contract choice, record it and resolve the shared specification before changing a gold expectation.

## 1. What acceptance must establish

A visitor should be able to open an artwork page, understand a short explanation, express a reaction, ask a follow-up, change the requested language or level of detail, correct a misunderstanding and recover from an interruption. A technically valid response does not pass if it ignores the visitor's intent, invents a connection, repeatedly asks unnecessary questions or loses the thread.

The first release remains limited to the two scoped artworks, the released museum records, four conversation languages and text input. It has no image understanding, location awareness, cross-artwork comparison or open web search. Acceptance checks useful handling of those limits; it does not quietly add the missing capabilities.

The four priority journeys are V01-V04. They cover trusted basic facts, a partial answer with local clarification, subjective interpretation, and translation versus persistent language settings. The remaining journeys test the surrounding museum experience.

## 2. Preparation and source-based expectations

Use the pinned aic-seed-v0.1 release and its declared metadata projections. Do not obtain additional facts from a model to construct expected answers.

| Scope | Existing information relevant to these journeys |
| --- | --- |
| aic-28560, The Bedroom | Catalog title, artist and date; the released material record; the released description of color, brushwork, perspective and possible energy |
| aic-11723, Woman at Her Toilette | Catalog title, artist and date range; the released material record; the released description of brushwork and suggested background patterns |
| Neither scope | No released proof of an exact pigment, a flower species, an artist's specific motive or a visitor's pointed-at object merely because a related term appears |

The existing dates are display values. Preserve a range as a range rather than inventing a precise year. A fact available in the catalog must follow the contract's metadata provenance rules; it is not automatically supported by an unrelated description chunk.

Before each run, record the artwork, release, contract/prompt/profile versions, selected language preference, starting revision, cached source state and the relevant previous turns. Use controlled visitor accounts and synthetic messages. Never use a real visitor's private conversation as an unrestricted evaluation artifact.

For each turn, reviewers label the original intent, answerable facts, unresolved reference if any, expected language and preference effect, permitted source basis, expected memory change and prohibited claims. Freeze these expectations before inspecting model output. Permit multiple natural phrasings and valid routes where the contract allows them; do not require exact output strings.

## 3. Visitor journeys

### V01. A short visit and basic orientation

Visitor sequence:

1. "I only have a minute. Tell me about this artwork."
2. "Who made it, and when?"
3. "Explain that without technical terms."
4. Repeat with the other artwork and its date range.

Expected experience:

- Start with a brief, grounded orientation rather than requiring the visitor to choose a database topic.
- Use verified catalog facts and available museum description within their authorized provenance. Do not add an unsupported biography or claim that the work is famous for an undocumented reason.
- Preserve date uncertainty and source distinctions. The artist, title and date must not be borrowed from the other work.
- Simplify the explanation while preserving its meaning and evidence. Avoid presenting a rewritten answer as a new museum quotation.

A lack of a basic fact in the Agent payload is an integration failure if the contract requires that released fact to be supplied; it is not evidence that the museum has no information.

### V02. Answer what is clear and clarify only what is unclear

Visitor sequence:

1. "What is it made of, and what is that little object for?"
2. "Thanks."
3. "I mean the object I described near the bed."
4. "Then just explain the material simply."

The tester supplies no image and no earlier description in this initial version. The second request is therefore genuinely unresolved. Run a variant with an earlier visitor description to test whether context removes the ambiguity.

Expected experience:

- Answer the supported material request with its source while asking a focused clarification about the unidentified object.
- Keep only the unresolved question pending. Do not turn ambiguity into a claim that the source lacks an answer, and do not block the already answerable part.
- Acknowledgement preserves the pending item; it must not trigger another retrieval or repeat the whole material answer unnecessarily.
- Do not pretend to have received a description that is absent. Ask for the missing description when needed.
- The final explicit change of focus clears or supersedes the pending issue and continues with the requested material explanation.

Also run a successful resolution variant where the visitor supplies a description supported by the source. The system should continue the pending request without treating the short reply as an unrelated standalone topic.

### V03. A personal reaction, a factual inference and a correction

Visitor sequence:

1. "This room feels lonely to me."
2. "Why might I feel that way?"
3. "Does that mean the artist felt lonely when making it?"
4. "No, I mean peaceful, not lonely. I am talking about my reaction."

Expected experience:

- Acknowledge the visitor's stated feeling without needing museum proof of that feeling.
- Discuss the visitor's supplied observations and conditional general explanations. Do not invent visible objects or claim to inspect the image.
- Distinguish a visitor's reaction from a documented artist intention or psychological state. A plausible interpretation does not become a historical fact.
- Honor the correction immediately and update the summary accordingly. Do not retain both loneliness and peacefulness as equally current visitor preferences.
- Avoid formulaic praise, repeated apologies or an obligatory question after every reply.

Add a variant in which the visitor asks for an invented story. Extended fictional narratives are deferred in this release; offer an observation prompt or a clearly framed discussion of the visitor's own interpretation instead. Do not invent historical events or store fiction as factual memory.

### V04. Translation is a task, not automatically a new conversation setting

Visitor sequence, with Chinese as the established preference:

1. Ask for a short explanation in Chinese.
2. "Translate your previous answer into Korean for my friend."
3. Give a neutral acknowledgement.
4. Ask a substantive follow-up in Chinese.
5. "From now on, answer in Spanish."
6. Ask a further question without specifying a language.

Expected experience:

- The translation request targets the actual previous answer and changes only the output required for that task unless the visitor also explicitly changes the ongoing preference.
- The neutral acknowledgement and subsequent follow-up use the unchanged Chinese preference. The later persistent instruction changes future responses to Spanish.
- Preserve the original factual support, uncertainty and citations when translating an evidence-backed answer. A translation is not an independent source.
- If the exact requested text is unavailable, ask the visitor to identify or supply it; do not present a summary as a verbatim translation. If a faithful translation cannot fit the response budget, ask for a shorter passage or an explicitly requested summary rather than silently omitting content.

Variants: retain an English technical term in a Chinese explanation; explain a quoted language instruction without obeying it; ask for Korean for this reply only; explicitly request bilingual output according to the contract's supported output policy; request an unsupported language. Do not misclassify a retained name or term as conflicting language settings.

### V05. Corrections and expression preferences survive normal conversation

Visitor sequence:

1. "Keep it simple, please. I am explaining this to my child. Use two or three sentences."
2. Ask about material.
3. "I meant how paint is applied, not which pigment it contains."
4. Ask about brushwork.
5. Give a short acknowledgement.
6. Ask for a general explanation of an art term.
7. "Continue with the same simple explanation."
8. "Now give me a little more detail."

Expected experience:

- Remember explicit expression preferences within this conversation, using the bounded semantic memory defined by the design.
- Replace corrected intent rather than accumulating mutually inconsistent interpretations.
- Keep the simpler style after the original instruction leaves the five-pair window, where the summary retains that relevant request.
- Allow the later explicit request for more detail to supersede the earlier brevity preference.
- Do not infer a child's identity, abilities or personal profile from this limited context. No cross-conversation personalization is claimed.

### V06. Five recent pairs do not imply perfect recall

Create at least eight completed turns about the current artwork, including a topic change and a numbered explanation near the start. Then ask:

- "Go back to the second point you mentioned at the start."
- "Quote exactly what you said."
- "I mean the point about the brushwork."

Expected experience:

- Use the latest five complete pairs plus the bounded summary and validated sources.
- Resolve an older reference when the available context establishes it; ask a focused question when it does not.
- Do not invent an exact earlier sentence or claim that a lossy summary preserves its wording.
- Once the visitor identifies the topic, continue with evidence-backed content without pretending to have recovered the unavailable quotation.

Include a controlled small-context run that drops older whole pairs during fitting. Judge the resulting memory limitation honestly; never silently truncate required evidence or the current question to preserve a longer chat window.

### V07. A source gap, useful general discussion and an invalid inference

Visitor sequence:

1. "Which exact blue pigment was used here?"
2. "That is okay. Explain generally how painters mix blue colors."
3. "So that is how this painting was made?"
4. "What can you actually confirm about its material?"

Expected experience:

- State the specific unresolved fact briefly without claiming the museum has no further information anywhere.
- Follow the move to general art knowledge and explain it without unnecessary artwork retrieval.
- Do not promote the preceding general explanation into proof about this work.
- Return to the material source when asked what is confirmed.
- Keep useful dialogue distinct from invented certainty. An unavailable retrieval service must produce technical recovery behavior, not this successful source-gap path.

### V08. Pointing, walking and artwork switching

Visitor sequence:

1. "What is that tiny object in the upper corner?"
2. "I am pointing at it."
3. "Now I am looking at the next painting. What about this one?"
4. Open the other supported artwork page.
5. "Compare it with the previous one."

Expected experience:

- Explain the specific missing visual input and ask for a description when useful. Never claim to see pointing, the visitor's room or a supplied image that does not exist.
- Keep the active artwork visibly identifiable. A natural-language claim that the visitor moved does not silently change the server's scope.
- Guide the visitor to scan or open the other artwork, then use its separate conversation and sources.
- Handle unsupported comparison with a concise scope explanation and a useful next step. Do not mix sources across the two conversations.
- Apply late responses only to the conversation that produced them.

### V09. Refresh is not a new visitor

Actions:

1. Establish a fixed language and a topic preference.
2. Refresh the same artwork page.
3. Hand the phone to another tester.
4. The new tester asks, "Can I start a fresh conversation?"

Expected experience:

- Refresh restores the same valid conversation with its committed language and history.
- Do not claim to have reset the conversation, erased history or created isolation through a model reply alone.
- If the release has no specified reset/new-conversation action, record that limitation plainly. The current create-or-resume behavior must not be mistaken for a reset feature.

A dedicated reset/new-visitor workflow is deferred unless a separate public contract and browser action are approved. This journey checks honest behavior and documents the product gap; it does not require an undocumented endpoint or a simulated reset that leaves prior state active.

### V10. More questions and detail than one reply can hold

Visitor sequence:

1. "Tell me the artist, date, material, what is happening, and why it is famous."
2. Choose a subset after the system asks which part to prioritize, if the requests cannot fit the contract's bounded plan.
3. "Explain that in detail; I have a few minutes."
4. "Continue."

Expected experience:

- Do not silently drop an independent request or return a technical error merely because the visitor asked too much at once.
- Apply the contract's bounded-request policy: combine genuinely related requests only when all meaning is preserved. If more than four independent requests remain, ask a whole-message prioritization question and preserve the original request in pending context; do not execute an arbitrary first four.
- Respect the output limit with an honest concise explanation and an optional continuation. Do not label a very short answer comprehensive.
- A continuation advances the explanation without repetitive openings, recycled questions or new unsupported facts.
- The fact that a visitor asks why the work is famous does not establish its fame or an available documented reason.

### V11. Sources remain understandable during rewriting

Visitor sequence:

1. Ask a supported material or description question.
2. "Where does that come from?"
3. "Explain the museum's wording more simply."
4. Ask for that explanation in another supported language.

Expected experience:

- Show the appropriate source for the specific factual claim, including catalog provenance when applicable.
- Keep source excerpts in their released English form and distinguish them from generated explanations or translations.
- Preserve attribution and uncertainty. For example, a source's suggestion must not become a definite assertion after simplification.
- Do not attach a source card to unrelated general advice merely to make it look authoritative.

### V12. Waiting, interruption and recovery on a real phone

Actions:

1. Send a brief appreciation and a factual question separately.
2. Observe warm and cold start behavior without silently combining their timings.
3. Interrupt connectivity during a request, then restore it and refresh.
4. Deliberately retry according to the recovery UI.
5. Change artwork while a response is still pending.
6. Repeat under the declared concurrent-user capacity and with a dependency unavailable.

Expected experience:

- Show that a request is in progress without misleading claims about which internal step is currently running.
- Preserve the draft, committed history and the original retry identity. Do not duplicate a turn, silently rebase an old question or advance language/memory after failure.
- Distinguish technical failure from an ordinary answer with missing evidence. Keep the visitor's next recovery action understandable.
- Measure time until the full answer is visible, separately for direct dialogue, source reuse, retrieval and refinement. Record abandoned waits and perceived sluggishness as well as timeout compliance.
- Meet the versioned latency gates in the design. Passing those gates alone does not establish a pleasant short-chat experience; reviewers must record when a brief acknowledgement feels disproportionately slow.

## 4. Language and script coverage

The suite uses English instructions and review notes. Visitor utterances must be authored naturally in English, Spanish, Simplified Chinese and Korean; mechanically translating isolated English keywords is not sufficient. Reviewers competent in the relevant language check meaning, tone and the intended language-setting effect before freezing a case.

Illustrative equivalents of a simplicity request are "Please explain it more simply," "Explícalo de forma más sencilla, por favor," "请讲得简单一点。" and "좀 더 쉽게 설명해 주세요." These are seeds for authoring, not proof of model performance or held-out independence.

Use the design's minimum 16 multi-turn scripts, each with at least eight meaningful visitor turns:

- Eight monolingual scripts: two in each supported language.
- Eight switching scripts: cover all four languages, both persistent switches and task-specific translations, temporary instructions and neutral acknowledgements.
- Cover V01-V04 in every language across the scripts. Cover every remaining family in at least one script, and repeat language-sensitive failures in all relevant languages.
- Include both artworks, empty and populated evidence memory, the fifth/sixth-history boundary, a correction, an abandoned pending question and a resolved pending question.
- Do not pad a script with meaningless acknowledgements merely to satisfy the turn count.

These published journeys are development and regression material. Independently author held-out variants for the existing 60-scenario, four-language single-turn set and the multi-turn acceptance runs. Keep translations and paraphrases of one scenario in the same split. Do not call a published script or a revised case that informed the prompt a blind test.

## 5. Scoring and evidence

Score each relevant visitor turn on the following dimensions. Use pass/fail with a short reason and the exact affected claim or reference, not a single unqualified overall score.

| Dimension | Passing behavior |
| --- | --- |
| Intent and continuity | Answers the current request, resolves references supported by context, honors corrections and avoids unnecessary clarification |
| Factual support | Every artwork-specific claim uses authorized metadata or evidence; no fabricated detail, provenance or inference |
| Language | Correct primary output language; proper temporary/task/persistent effect; names and quotations remain appropriate |
| Pending clarification | Answerable parts survive; only the unresolved issue is saved; thanks, resolution and abandonment have the intended effect |
| Usefulness | Clear, appropriately brief explanation or practical next step; a source gap does not automatically close the conversation |
| Memory honesty | Explicit preferences and corrections are handled; missing exact historical wording is not reconstructed as a quote |
| Product scope | No pretend vision, silent artwork switch, unsupported comparison or false conversation reset |
| Recovery | No duplicate commit, stale-state reinterpretation, lost confirmed preference or cross-conversation late answer |
| Experience | Readable response and sources, understandable waiting/recovery, no excessive repetition or unnecessary work |

Report counts and denominators by language, artwork, journey family and route. Retain the design's numerical release gates; this plan adds visitor-oriented coverage rather than weakening those thresholds. Designated cases for the four priority policies must pass their annotated behavior in each language. Unsupported artwork claims, cross-artwork evidence, false reset claims and duplicate commits require resolution before release, even when an aggregate score passes.

For every failed or disputed case, retain the synthetic transcript, relevant source IDs, expected and actual language/state effects, timing, commit and profile versions, reviewer conclusion and follow-up decision. Separate failures in intent, available knowledge, protocol, model generation and browser experience. A fluent reply may still fail support; an accurate fact may still fail the visitor's request.

## 6. Ownership and completion record

A owns natural visitor scripts, independent language review coordination and the physical-phone walkthrough. B reviews source/state expectations and owns recovery, concurrency and transport evidence. C runs the common Agent workflow evaluation, diagnoses semantic failures and proposes prompt or workflow corrections. All three review disputed outcomes against the original visitor goal and released sources.

Record one outcome for each required case: not run, passed, failed or deferred with a reason. Do not label a deferred capability passed. Report the new-visitor reset gap separately from required supported behavior until its contract is approved. A documentation review, scripted model or schema check cannot close a real-model or physical-phone item.

M1 prepares cases, fixtures and consumer expectations. M2 executes model and integrated conversation checks. M3 repeats the required visitor journeys on the actual deployed phone path and records known limits. See the [implementation plan](agent-plan.md) for milestone owners and the [design release gates](agent-design.md#13-evaluation-and-release-gates) for quantitative acceptance.
