# Vincent on Getty's *Irises*

Vincent uses the private v3 Agent and real hybrid retrieval. The visitor speaks to an imagined Vincent in English. Qwen decides when to search, writes the spoken reply and proposes the next memory. Python selects the authorized evidence collection from the conversation scope, runs the tools and checks the result.

## What a search returns

The scope is `getty-103jnh`, `getty-gallery-v2`, `vincent`, `vincent-irises-v1`. The index contains seven passages from the gallery release and four from the [reference pack](vincent-evidence.md). They include Getty records, team-authored looking suggestions and excerpts from Vincent's letters. The ordinary artwork guide retains its own index.

A query searches the eligible passages by weighted word matching and by cosine similarity with OpenAI `text-embedding-3-small`. The ranker combines the two rankings and returns up to two records per query. An embedding failure raises a retrieval error. The knowledge service owns the embedding credential and keeps its Vincent vector cache separate from the gallery cache. The Agent verifies excerpts, source identity, attribution and rights against both release digests. A closely related record may still leave a visitor's specific question unanswered.

## Running the private integration

With the existing [gallery environment](getty-gallery.md) configured, set `MUSEBRIDGE_ENABLE_VINCENT=1` before starting `scripts/run-gallery.sh`. This enables the private text Agent and Vincent knowledge endpoints. The separate `VINCENT_ENABLED` setting configures the Go voice feature. The Qwen origin is `OLLAMA_BASE_URL`. In the WSL setup the SSH tunnel exposes the teammate's Ollama on `http://127.0.0.1:11434`.

| Service | Entry point | What to verify |
| --- | --- | --- |
| Knowledge | `GET /readyz/vincent` | Both release IDs and the `X-Retrieval-Strategy: openai-hybrid-v1` header |
| Knowledge | `POST /retrieve/vincent` | English query and server-bound artwork scope, with up to two canonical records |
| Agent | `GET /internal/v3/agent/readyz` | Bearer authentication, trace header, selected model and ready role index |
| Agent | `POST /internal/v3/agent/runs` and `GET /internal/v3/agent/runs/{run_id}` | The same retained result on recovery. Another version cannot read the run. |

Use a fresh output filename on each replay. Run the first command with the **knowledge** Python environment and the embedding credential. Run the second with the **Agent** Python environment against that knowledge service; it needs the pinned tokenizer and Ollama connection.

```bash
python -m agent.testing.vincent_hybrid_eval --stage retrieval \
  --output /tmp/vincent-retrieval-replay.json

python -m agent.testing.vincent_hybrid_eval --stage workflow \
  --base-url "$OLLAMA_BASE_URL" --tokenizer "$AGENT_TOKENIZER_PATH" \
  --retrieval-url "$RETRIEVAL_BASE_URL" --output /tmp/vincent-workflow-replay.json
```

## Reading the acceptance results

The [13 visitor turns](../agent/testing/vincent_hybrid_cases.json) form seven conversations. Each turn has a separate control query and a semantic rubric. Only the visitor question and actual committed history enter the Agent. If a turn fails, the runner submits the next question with the last successful state and flags the missing prior turn. The workflow test uses the real run registry, Qwen, embeddings and knowledge service. It simulates the Go commit between turns. A separate [HTTP receipt](../eval/reports/vincent-hybrid-http.json) exercises the actual private FastAPI boundary.

The [direct retrieval comparison](../eval/reports/vincent-hybrid-retrieval.json) bypasses the planner. All nine questions with designated evidence found every required passage in the top two when the visitor's text was used verbatim. Seven of nine did so with the independent control queries. Both forms of the unrelated question returned a passage when forced through search. Two additional probes asked about details the collection does not establish. All 24 ranked responses contained only canonical in-scope records, and all six scope checks passed.

The [turn-by-turn assessment](vincent-hybrid-results.md) separates delivery, evidence availability, spoken answers, citations and memory. These are development results for the tested questions. A green readiness response means the service can accept work.
