# Rehearsing the voice of Vincent

This is a text rehearsal for the English *Irises* conversation. It tests how the existing Qwen model handles a letter, a present-day museum finding, a looking suggestion and a visitor’s interpretation when it already has the relevant passages. The gallery still serves its existing guide. The private v3 routes, automatic retrieval, phone audio and subtitles have their own integration checks in the [Vincent contract](vincent-contract.md).

## How a turn works

The [rehearsal runner](../agent/testing/vincent_rehearsal.py) verifies the two release digests and selects the passages specified by the case. The user message contains the actual question, catalog context, current excerpts, the last five question-and-answer pairs and the last model-written memory. Only previously cited records enter the conversation’s eight-item evidence cache. Each new conversation starts empty. Rubrics and expected answers never enter the prompt. The [role prompt](../agent/prompts/vincent/role.md) asks for spoken English, distinguishes the owners of source claims and asks for a concise English memory.

One real model call writes the reply, citations and memory. The provider checks the model identity and full output schema. The runner checks that the cited IDs were available, saves the unedited output, and ends that conversation if a turn fails. These simplified fields are a rehearsal projection. The full Agent contract also defines planning, request status, pending clarifications, publication and replay.

## Development results

The [eight development turns](../agent/testing/vincent_development_cases.json) were used during prompt design. The [frozen development transcript](../eval/reports/vincent-development.qwen.json) contains the actual answers, citations, memories and token counts. Qwen ran via Ollama as `qwen3.5:9b-q4_K_M` with temperature zero, thinking disabled, a 16,384-token context and a 2,048-token output ceiling. The reports include the model digest and exact prompt, question-set and evidence digests.

A body passes when it answers the question in context, attributes the claim audibly where needed, and stays within the passage. Memory is graded separately for ownership, scope and the detail needed on the next turn. Citation selection checks the relevant record IDs. A correct ID cannot make an unsupported statement true.

| Conversation and turn | Spoken answer | Citation IDs | Memory | What the transcript shows |
| --- | --- | --- | --- | --- |
| Original color | Fail | Pass | Fail | The letter has sections for Theo and Jo. This passage addresses Theo. The answer says he told both, and memory adds a visitor impression the visitor never expressed. |
| Why blue today? | Fail | Pass | Pass | The answer names Getty and fading geranium lake. It leaves out how red mixed with blues produced violet, the link needed to explain the visitor’s observation. |
| White bloom | Fail | Pass | Fail | After acknowledging uncertainty, the answer invents a wish to show “quiet strength.” Memory extends a gap in the supplied passages to historical evidence in general. |
| Follow-up about intention | Fail | Pass | Fail | The answer turns the preceding invention into a motive and attributes the words to the visitor. Memory repeats the overbroad gap. |
| A study from nature | Fail | Pass | Fail | The letter lists *Irises* in a group of studies. Calling the works unfinished sketches goes beyond the passage. |
| Seven versions? | Fail | Pass | Fail | Both outputs turn seven studies into sketches of seven different irises. The letter lists *Irises* as one work in the group. |
| Noticing the leaves | Pass | Pass | Pass | The answer receives the observation as a personal reading. The memory attributes the observation to the visitor. |
| Looking more closely | Pass | Pass | Pass | The guide invites the visitor to trace a leaf and compare it with a petal, and cites the looking exercise. |

On this eight-turn development set, **2/8 spoken answers**, **8/8 citation selections** and **3/8 memories** passed that review. All eight passed structural validation. The small set is a diagnostic for this character and this evidence release. Fluency and valid citation IDs alone missed the errors that matter most for an artist persona.

## Independent check

The [independent suite](../agent/testing/vincent_holdout_cases.json) contains three two-turn conversations. Its author sealed the questions, source IDs and review criteria before the prompt was frozen. Its provenance records the original suite digest and the conversion to the runner input. The [execution record](../eval/reports/vincent-holdout.qwen.json) records the outcome separately from the development set.

The first run stopped during provider admission with `generation_timeout`. It produced **zero of six** planned answers. A subsequent check found the Windows endpoint used by the WSL forward had no listener, and the configured GPU host did not answer SSH before authentication. This run has no semantic score. The frozen prompt and independent suite are ready for the same host when the connection returns. The development findings already rule out enabling the persona for visitors.

## Repeating the check

Use the project’s model environment with the pinned tokenizer and the same authorized Ollama endpoint. From the repository root, set `OLLAMA_BASE_URL` to that endpoint and `QWEN_TOKENIZER_PATH` to the local pinned tokenizer file. Run each suite separately with an output file outside the checked-in observations.

~~~sh
python -m agent.testing.vincent_rehearsal \
  --cases agent/testing/vincent_development_cases.json \
  --output /tmp/vincent-development.json \
  --base-url "$OLLAMA_BASE_URL" --tokenizer "$QWEN_TOKENIZER_PATH"

python -m agent.testing.vincent_rehearsal \
  --cases agent/testing/vincent_holdout_cases.json \
  --output /tmp/vincent-holdout.json \
  --base-url "$OLLAMA_BASE_URL" --tokenizer "$QWEN_TOKENIZER_PATH"
~~~

Review each answer as the visitor would hear it before looking at its citation IDs. Then compare each claim and the proposed memory with the exact passages and the preceding turns. In particular, distinguish material change from artistic intent, a letter’s overall addressees from the addressee of a passage, and a visitor’s observation from Vincent’s testimony. A future v3 integration should run these questions through the actual planner and retriever as a separate test.
