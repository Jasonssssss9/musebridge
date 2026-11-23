# Model-authored guide responses and memory

The [follow-up on result slots, routing and source wording](#follow-up-result-slots-routing-and-source-wording) covers the hybrid-retrieval workflow.

This development check tests the restored v2 workflow on the two released Art Institute of Chicago works. The planning model chooses the route, requests and queries. A terminal model decision supplies the visitor’s text, support values, source IDs and semantic memory. The graph executes tools and validates the decision. The high-level architecture and the private wire contract are described in [Agent design](agent-design.md#62-local-clarification-and-response-assembly) and [Agent API](agent-api-contract.md#54-assessment-final-decisions-and-response-parts).

## Procedure

The [question set](../agent/testing/model_authorship_journeys.json) includes eight earlier questions and eight new wordings across English, Spanish, Chinese and Korean. Each conversation started with a fresh anonymous session and Auto language. Two second turns depended on the first turn. The first authoring check used 14 turns. For the final run, two previously published standalone questions about the room’s mood and the artist’s own feelings were included so that each question could be assessed even if the preceding overview failed. The prompts and routing implementation were frozen for the final run. These cases are development material. Independent acceptance remains open.

The runner called the Go API through the web proxy, the Python workflow, the local HTTP retrieval service and the real Ollama provider. It checked public history, idempotent replay and revision integrity after technical failures. A trace comparison checked visible text against the terminal model output and private semantic memory against the same decision. A separate qualitative review read each delivered reply and its English memory against the question and the released passage.

| Item | Final run |
| --- | --- |
| Model | `qwen3.5:9b-q4_K_M`, digest `56671c2ab9385f9cfcb404638e32cd62d88e3501d44822208363c010179a3c90` |
| Inference | 16,384-token context, up to 2,048 output tokens per operation, temperature 0, thinking disabled |
| Evidence | `aic-seed-v0.1`, unchanged seed SHA-256 `3703fd4c9fcefb8e9fba20f23a5cb70e612c62ef7cb3f594a15d319ebc18d6ac` |
| Question set SHA-256 | `de984f59e94ea3ac62009d07187a13eae28d6a2be5e8a4b6b34ee99e085712a0` |
| Implementation fingerprint at startup | `03095675ad62f41835dd75cf7ca7053f40179e05ec7831db6a71f5b25afe5458` |

## Delivery and authorship

| Group | Scheduled | Sent | Delivered | Expected status, language and citation IDs | Rejected | Blocked by preceding turn |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Earlier questions | 8 | 7 | 3 | 2 | 4 | 1 |
| New wordings | 8 | 8 | 5 | 5 | 3 | 0 |
| Total | 16 | 15 | 8 | 7 | 7 | 1 |

For all eight delivered turns, the public body matched the real model’s terminal text, including the two prescribed newlines between distinct answer parts. The workflow returned all eight model-written memory proposals unchanged. All eight public histories and idempotent replays matched. The second Morisot pattern turn received the preceding model-written topic and summary in committed revision 1, although its own plan failed validation. All seven rejected submissions left the existing history and revision intact.

## Semantic review

A body passes when it answers the requested task naturally, stays within the supplied evidence and preserves its subject and degree of certainty. A memory passes when it gives the next turn a usable English account of the topic, established outcome or precise open question. A partial grade marks a useful but incomplete answer or handoff. Reviewers scored the body and memory separately, including when a machine status was correct. These are development judgments, not independent multilingual certification.

| Delivered question | Body | Memory | Reason |
| --- | --- | --- | --- |
| Earlier Bedroom caption | Fail | Pass | Three empty searches led to a claim that the museum’s current records lack a brushwork description. The released passage does describe the marks. Memory kept this question open, although it omitted the caption goal. |
| Earlier Morisot background, Spanish | Pass | Partial | The answer naturally says the strokes suggest a floral bedspread and wallpaper. The English handoff says the description supports the answer but omits what the marks suggest. |
| Earlier Morisot floral motif and individual petals, Spanish | Pass | Fail | The reply distinguishes a suggested pattern from unverified technique. The handoff is in Spanish and upgrades the motif to a confirmation. |
| New Bedroom lines and mood, Chinese | Partial | Partial | The second sentence retains a hedge. The first asserts unease as an effect of the picture, and the translation sounds clinical. The English handoff mentions the energy but loses its tentative status. |
| New Bedroom personal reaction, Korean | Fail | Pass | The visitor expressed comfort. The source-free reply added details about the walls, colors and repose. The English memory correctly attributed comfort to the visitor. |
| New Morisot wall pattern, English | Fail | Partial | The response identifies the suggested motif but adds that the marks blend with the bedspread. The handoff treats the floral pattern as confirmed. |
| New Morisot wallpaper manufacturer, Spanish | Fail | Fail | The maker remains open, but the answer calls the bedspread a canopy and contains a grammatical error. The handoff is in Spanish. |
| New general brushwork definition, English | Partial | Partial | The core definition is clear. Its suggestion that marks can reveal the artist’s emotional state needs qualification. Memory records the question but no learned outcome. |

Strict body grades were **2 pass, 2 partial and 4 fail**. Memory grades were **2 pass, 4 partial and 2 fail**. No delivered turn fully passed both grades on this small set. Older and newer runs sometimes phrased answers or notes differently under the same model settings. A single clean reply is insufficient to establish readiness.

## Rejections and next work

- Five planning decisions failed before delivery. The Bedroom overview, the painter’s feelings, the Morisot artist query and a follow-up about painting real flowers produced invalid direct plans for their listed request kinds. The pointing question combined a search with an unresolved referent. The overview’s dependent follow-up was skipped.
- The earlier Spanish mood question reached retrieval but its refinement failed the query-link or uniqueness check. The new support-and-brush-size question retrieved the material record but the finish decision did not cover all planned requests in order.
- The caption case reached the final answer with empty evidence. Its first generated query was an English keyword string. Compare generated questions with scoped retrieval on the same query before considering changes to the retrieval implementation.
- For a later iteration, first measure whether the model can reliably emit a valid route and terminal decision. Then repeat the attribution, uncertainty, translation and memory checks using fresh visitor questions. This run does not qualify the model for deployment.

## Repeat this check

Use the project’s pinned Python environment and tokenizer, with the existing Go and Node toolchains available. Set the Ollama URL to the authorized endpoint and use a new private report directory outside the repository. The report option below retains raw model decisions for these published development questions.

```sh
python -m agent.testing.m2_smoke \
  --ollama-url "$OLLAMA_BASE_URL" \
  --tokenizer "$AGENT_TOKENIZER_PATH" \
  --journey-file agent/testing/model_authorship_journeys.json \
  --report-dir "$REPORT_DIR" \
  --private-decisions
```

The command reports a failure when a scheduled turn is rejected, blocked or misses its machine expectations. In the private trace, join each public request ID to its terminal `private_model_decision` and `private_workflow_result`. Compare the model text and memory fields directly. Follow this with a reading of the actual reply and next-state memory against the original museum excerpt.


## Follow-up: result slots, routing and source wording

The November 23 development run used the real Qwen 3.5 9B model, the hybrid OpenAI retrieval service and the public web proxy. Planning chose the requests. The finish grammar bound each result to one request from that plan. The model wrote every answer and private memory. The same final validator checked the returned decision before Go committed it.

All 13 questions in the [development suite](../agent/testing/finish_routing_journeys.json) received HTTP 200 and passed history and replay checks. Eleven matched the preselected status, language and citation IDs. Four compound turns across English, Spanish and Chinese each returned exactly two ordered results. The Spanish and Chinese replies carried English memory. The original four-turn baseline on hybrid retrieval delivered two replies and rejected both answerable controls for invalid result IDs. The final run delivered all four.

| Question or group | Final observation |
| --- | --- |
| Bedroom caption about the marks | Both prose and memory describe thick and broken brushwork. Neither assigns a mood to brushwork alone. |
| Morisot’s background furnishings | Both retain the relationship: the soft, feathery strokes suggest a floral-patterned bedspread and wallpaper. |
| Specific flower model | Empty retrieval. The reply says the guide could not verify the detail from the material available here. Memory leaves the question open. |
| Brush manufacturer for the wall covering | The guide asked whether the visitor meant a depicted brush or the artist’s tool. Memory retained the ambiguity. This was a reasonable clarification, although the original label expected insufficient evidence. |
| Fresh-session “Who made that?” | The guide identified Morisot from the catalog. It assumed the visitor meant the whole work and saved that interpretation as settled. The label called for clarification. |
| Overall visual impression in The Bedroom | Both prose and memory preserve the group of features and the museum’s uncertainty: colors, brushwork and receding lines together might suggest nervous energy. |
| Other source gaps and ordinary routing | The Spanish mixed question separated a supported material from an unverified brush maker. The Chinese supplier reply still referred broadly to the absence of a record and added medium information; memory limited the gap to the available evidence. The flight question routed outside scope, but the reply implied that the guide was physically present. |

The first development panel had an incorrect expected language of English for the Spanish question. The final suite corrects this to Spanish. The case itself, its intended status and its citations did not change. The final set contains previously used questions, so these results are development evidence rather than an independent acceptance score. Exact-label success does not establish grounding or memory quality.

To reproduce, run the existing real-provider smoke command in hybrid mode with `--journey-file agent/testing/finish_routing_journeys.json --private-decisions`, a fresh private report directory and the verified tokenizer. Read the public text alongside the search events and committed memory. The runner returns a nonzero exit when a turn misses an expected label, including a plausible alternative clarification.
