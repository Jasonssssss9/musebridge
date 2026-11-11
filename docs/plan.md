# MuseBridge implementation plan

## Scope and ownership

The first stage delivers a local, phone-ready museum guide for two Art Institute of Chicago works. A visitor scans a QR code, opens the matching artwork page, asks a supported question, and checks the answer against the original museum source. The demonstration also covers a limited follow-up and shows what happens when the guide lacks evidence or a service becomes unavailable.

For the local build, use a React and TypeScript web app, a Go application API, and a Python retrieval service. Python will search the selected artwork’s records in a versioned JSON fixture using keywords and configured topic aliases. Go will generate deterministic fixture answers from eligible evidence. The local stage needs no model runtime, embedding service, or PostgreSQL. The cloud GPU milestone follows the local stage.

| Owner | Responsibility | Primary implementation area |
| --- | --- | --- |
| Jeremiah Lim | Mobile visitor experience, browser API integration, and phone demonstration | `web/` |
| Jinqing Zhang | Go API, conversation state, retrieval integration, fixture generation, and local startup | `api/`, `compose.yaml`, and [getting-started instructions](getting-started.md) |
| Jason Zichen He | Source records, JSON fixture, Python retrieval, and evidence-based test expectations | `knowledge/` and `eval/` |

Use the [design](design.md) for architectural decisions and the [API contract](api-contract.md) for request and response details. Jeremiah Lim, Jinqing Zhang, and Jason Zichen He review any contract change together before adapting their respective implementations. The paths and launch commands in this plan describe the intended implementation.

Task IDs retain their original prefixes for continuity: `A1`–`A7` belong to Jeremiah Lim, `B1`–`B8` to Jinqing Zhang, and `C1`–`C6` to Jason Zichen He. The owner column names the contributor responsible for each task or acceptance check.

The Compose stack serves the live frontend, Go API and Python retrieval service. The packaged browser and evidence checks passed. The physical phone walkthrough remains open. See the [verification record](#local-verification-record) for the observed results.

## Parallel course track

Jinqing Zhang starts the B8 course check at kickoff and carries it through submission. The handoff mentions at least twice as many interviewees as team members, which would mean six interviewees for this three-person team, and Saturday demo videos. Verify both against the current rubric, including who counts as an interviewee, which Saturdays apply, and the exact deadlines and submission destinations. Verify the GPU evidence requirement and what the team may record or quote. Keep these items provisional until the rubric is checked.

Jeremiah Lim starts recruiting visitors and coordinating demo recording. Jason Zichen He helps review source permissions, interview consent, and evaluation materials. Keep recruitment and source review moving while the local build is in progress.

## Stage 1. Local demonstrable prototype

The stage ends with a repeatable demonstration on a physical phone. Each checkpoint below has a handoff that the next owner can test without relying on an unfinished service.

| Checkpoint | Required handoff | Review |
| --- | --- | --- |
| Freeze interfaces and fixtures | Browser examples, retrieval examples, stable IDs, and a reviewed case list | Jeremiah Lim confirms the visitor flow, Jinqing Zhang confirms API behavior, and Jason Zichen He confirms evidence scope. |
| Build in parallel | Frontend against canned replies, Go against a retrieval substitute, and Python against the JSON fixture | Each owner can show their own checks while the other services are still under development. |
| Integrate and test | Running stack, test results for the same fixture revision, and a QR walkthrough | Jinqing Zhang checks startup and recovery, Jason Zichen He checks evidence, and Jeremiah Lim runs the phone demonstration. |

### Checkpoint 1. Freeze the interfaces and fixtures

Agree on the shared examples before coding against each other’s services. Jeremiah Lim can build against browser API examples, Jinqing Zhang can build against a controlled retrieval substitute, and Jason Zichen He can test Python directly against the same fixture.

| Task | Owner | Dependencies | Artifact and acceptance |
| --- | --- | --- | --- |
| A1. Sketch the mobile flow | Jeremiah Lim | Agreed visitor flow and artwork slugs | Sketch the two deep links, chat entry, source link, pending turn, retry, and the two answer fallback states. Use `/a/aic-28560` and `/a/aic-11723` in the sketches and on the QR cards. |
| B1. Freeze service contracts | Jinqing Zhang | Initial review with Jeremiah Lim and Jason Zichen He | Review the browser endpoints and `POST /retrieve` together. Provide example bodies for an answered request, insufficient evidence, an unsupported question, and a service failure. Agree on visitor ownership, retry semantics, time budgets, and version validation. |
| C1. Freeze the evidence and evaluation examples | Jason Zichen He | The two selected artworks and their museum records | Prepare the dataset and test fixture shapes. Verify each source URL, record its attribution and reuse status, and pair each frozen case with its expected status and any required citation. Review the intended matches and language aliases with Jinqing Zhang. |

The planned data file is `knowledge/data/aic-seed-v0.1.json`. The planned frozen case file is `eval/aic-seed-v0.1.tests.json`. Keep their artwork and chunk identifiers stable across all three services. Pin `aic-seed-v0.1` in the Go API and the retrieval service before connecting them. Publish any later evidence change under a new version.

| Artwork and slug | Medium chunk | Description chunk |
| --- | --- | --- |
| The Bedroom (1889), `aic-28560` | `aic-28560-medium` | `aic-28560-description-01` |
| Woman at Her Toilette (1875–80), `aic-11723` | `aic-11723-medium` | `aic-11723-description-01` |

#### Frozen retrieval and answer cases

Keep the six canonical questions verbatim. Add the same brushwork question as two separate isolation calls. The artwork in the route and conversation governs evidence scope even when the query text is identical. The English wording and scripted follow-up are separate end-to-end checks.

| Case | Artwork | Question | Expected fixture outcome |
| --- | --- | --- | --- |
| `bedroom-q1` | `aic-28560` | 这件作品用了什么材料？ | `answered`, citing `aic-28560-medium` |
| `bedroom-q2` | `aic-28560` | 馆方认为，画面里的哪些元素可能让人感到紧张？ | `answered`, citing `aic-28560-description-01` for the museum’s interpretation |
| `bedroom-q3` | `aic-28560` | 梵高画完芝加哥收藏的这一版，实际花了多少小时？ | `insufficient`, with no unsupported claim about how long the work took |
| `toilette-q1` | `aic-11723` | 这件作品用了什么材料？ | `answered`, citing `aic-11723-medium` |
| `toilette-q2` | `aic-11723` | 馆方如何描述这件作品的笔触？ | `answered`, citing `aic-11723-description-01` |
| `toilette-q3` | `aic-11723` | 画中女子的真实姓名是什么？ | `insufficient`, with no invented identity |
| `same-query-brushwork` on The Bedroom | `aic-28560` | 馆方如何描述这件作品的笔触？ | `answered`, with `aic-28560-description-01` ranked first and cited for the thick, broken brushwork |
| `same-query-brushwork` on Woman at Her Toilette | `aic-11723` | 馆方如何描述这件作品的笔触？ | `answered`, with `aic-11723-description-01` ranked first and cited for the soft, feathery brushstrokes |

For each call, check every retrieved record against the requested artwork ID. Exercise all four chunks across the cases. The Chinese questions exercise configured keyword and topic-alias matching to English source records. Keep broader multilingual retrieval as a later evaluation target.

For the two `insufficient` cases, an empty expected evidence list means the fixture has no source that supports an answer. A retriever may still return an in-scope chunk with overlapping terms. Preserve the case expectations when testing with that kind of result.

Jason Zichen He starts with the two official museum records and reviews reuse terms before extracting passages. Build the source and chunk records, freeze the expected test file from those records, run direct Python retrieval, and then hand the verified release to Jinqing Zhang for integration. Jason Zichen He can review and share provisional identifiers and test expectations at the first checkpoint so Jinqing Zhang and Jeremiah Lim can build against them before the release is ready.

Before parallel work starts, complete the following handoff.

- Jeremiah Lim and Jinqing Zhang agree which fields the browser stores to recover a pending turn. Jinqing Zhang supplies examples with matching and conflicting submission IDs.
- Jinqing Zhang and Jason Zichen He confirm an example hit’s artwork, chunk ID, rank, source record, and dataset version. They also confirm a valid empty hit list and the behavior for an invalid version.
- Jason Zichen He records the evidence required to support each answered case. The Go API uses those IDs to gate its fixed responses, and Jeremiah Lim uses the same IDs in the canned citation examples.

### Checkpoint 2. Build in parallel

| Task | Owner | Dependencies | Artifact and acceptance |
| --- | --- | --- | --- |
| A2. Build the artwork routes | Jeremiah Lim | A1 and the artwork lookup example from B1 | Implement the two phone-sized pages against canned API replies. Both direct links work on reload and display the intended artwork. An unknown slug produces a usable error state. |
| A3. Build the chat and citation states | Jeremiah Lim | A1 and the message examples from B1 | Create or resume the artwork’s conversation, load message history, and render all three answer statuses with a visible fixture label. Source links open the recorded museum URLs. A request visibly becomes pending and can be retried with the original payload and UUID after a failure. |
| A4. Handle reload and retry | Jeremiah Lim | A3 and B1 retry rules | Preserve the recoverable pending turn through reload and reconcile it against server history. Avoid appending a duplicate turn after a completed replay. A failed turn can be retried as submitted or replaced with an edited question and a new UUID. |
| B2. Build browser routes and conversation state | Jinqing Zhang | B1 | Implement artwork lookup, conversation creation, history, and message submission. Store conversations in memory for 24 hours from creation and scope them to their owning visitor and artwork. Automated checks cover invalid inputs, ownership, expiry, and the expected loss of history after a restart. |
| B3. Build the retrieval client and fixture answer path | Jinqing Zhang | B1 and C1 examples | Use a controlled substitute to test the Go client and the fixed answer mapping. Resolve the scripted follow-up before retrieval, validate the reply and its version, and accept only evidence from the requested artwork. Check that fixed answers cite the evidence they actually use. |
| B4. Implement submission and recovery semantics | Jinqing Zhang | B2 and B3 | Keep the request lifecycle consistent across duplicates and dependency failures. A completed request replays its result, an active duplicate returns `409`, and reusing a UUID with changed content returns `409`. A transient failure leaves the question recoverable. A new UUID can supersede the failed attempt. |
| C2. Build the source fixture | Jason Zichen He | C1 | Deliver the two artwork records, source records, and four approved chunks in the versioned JSON file. Each chunk resolves to its own artwork and source. Check source URLs, locators, original language, per-chunk licenses, attribution, and normalization notes. Publish the frozen test file and record the seed SHA-256 in the handoff. |
| C3. Build and test keyword retrieval | Jason Zichen He | B1 and C2 | Implement the Python service, load the JSON fixture, filter by artwork before ranking, and apply the configured keyword aliases. Define a stable tie-break and return at most two hits for the Go requests. Test all eight frozen calls directly, verify both brushwork rankings with `top_k: 1`, and check invalid limits and the version-mismatch response. |

#### Service boundaries used in parallel work

- The frontend uses same-origin browser requests. Load artwork through `GET /api/artworks/{slug}`, create the conversation through `POST /api/conversations`, and read or submit turns through `GET` or `POST /api/conversations/{id}/messages`.
- The frontend submits `client_message_id` as a UUID, the `question`, and an `answer_language` of `en` or `zh-CN`. Retrying the original submission uses the same ID and fields. Editing a failed question creates a new submission.
- The Go API calls `POST /retrieve` with `artwork_id`, `query`, `top_k` set to `2`, and `dataset_version`. Go resolves the limited scripted follow-up, so Python receives a retrieval query with the relevant terms.
- The Go API returns a fixture response with `answered`, `insufficient`, or `unsupported_question` as appropriate. A positive keyword hit alone does not establish that the material answers the question. A dependency or validation failure uses the contract’s error path.
- Apply the agreed budgets throughout the call chain. The Go request budget is 8 seconds, the retrieval budget is 3 seconds, the fixture generation budget is 2 seconds, and the browser timeout is 12 seconds. Use controlled failure injection to verify each relevant boundary.

#### State and evidence handoff

- The Go API records the dataset version on the conversation and completed turn. The frontend should accept a response only for the conversation it belongs to, including when the visitor switches artworks before a pending answer returns.
- The Go API keeps the submitted question distinct from the resolved retrieval query. The history shows what the visitor actually asked, while the retrieval service receives the resolved terms for the supported follow-up.
- The frontend treats the server’s completed history as authoritative when reconciling a pending message. A recoverable failure leaves the original question and ID available for retry.

#### What each owner should be able to show independently

- **Jeremiah Lim** opens both artwork routes and demonstrates the UI with canned API responses. Include a pending turn, a completed response with a source, each answer fallback, and a dependency error. Check the narrow layout with both English and Chinese text.
- **Jinqing Zhang** runs the Go checks with a replaceable retrieval substitute. Return a matching chunk, no hits, a wrong-artwork chunk, a malformed payload, and a delayed response. Assert the expected response and the state of the stored exchange after each case.
- **Jason Zichen He** loads the actual fixture and runs each of the eight retrieval inputs directly against Python. Compare repeat results to check deterministic ranking and confirm that only chunks from the requested work appear.

When an example needs to change, update the shared contract and case expectation before changing the service. Name the affected owner in the handoff and have that owner rerun the relevant check.

### Checkpoint 3. Integrate and test the local stack

First connect the web app to the Go API with the controlled retrieval substitute. Once the browser contract works against the real API, connect the Go API to the Python retrieval service and versioned data. Keep the substitute available for reproducible failure tests.

| Task | Owner | Dependencies | Artifact and acceptance |
| --- | --- | --- | --- |
| B5. Connect the browser and Go API | Jinqing Zhang, with Jeremiah Lim verifying | A2–A4 and B2–B4 | Run both artwork pages with the Go service and its retrieval substitute. Verify history loading, ownership rejection, UUID replay, and recoverable errors from the actual browser. |
| C4. Verify real evidence in the integrated flow | Jason Zichen He, with Jinqing Zhang verifying | B3, C3, and B5 | Run the eight frozen cases through Go with real Python retrieval. Verify the final status, relevant citations, and scope of all returned evidence. Check the English and follow-up cases separately. |
| B6. Package local startup | Jinqing Zhang | B5 and C4 | Add `compose.yaml` and update the [getting-started instructions](getting-started.md). From a clean checkout, the target command `docker compose up --build` starts all three services and serves the web UI on host port `8080`. |
| A5. Verify the physical phone journey | Jeremiah Lim, with Jinqing Zhang verifying network reachability | A4 and B6 | Record a phone scanning a QR code for each artwork, loading the correct page, asking a question, and opening the original source. Exercise refresh, an intentional recoverable error, and retry with the same submission. |

Compose publishes port `8080` on the host and proxy same-origin API traffic to Go on container port `8000`. Go will reach Python on container port `8001`. Keep the two backend ports on the Compose network. Service-to-service addresses belong in container configuration. Browser requests should continue to use relative API URLs.

For the phone test, choose a trusted network that allows the phone to reach the demo host. Verify the host address, inbound port access, and WSL or Docker forwarding as applicable before making the QR codes. Open the same LAN base URL manually on the phone first. Encode that reachable address and the artwork route in each QR code. Use a printed code or a second screen for the scan. Record the actual tested device, browser, and network.

#### Startup and demo handoff

The [setup guide](getting-started.md) maintained by Jinqing Zhang should let Jeremiah Lim start the same local build and run the phone walkthrough independently. The guide includes the startup command and both artwork routes. Use these settings and checks for the handoff.

- Set `PUBLIC_BASE_URL` to the exact LAN origin used by both browsers during the walkthrough. Use `COOKIE_SECURE=false` for that HTTP demo profile and `true` for the later HTTPS profile.
- Mount the same read-only JSON at `SEED_PATH` in the Go API and the retrieval service, and use matching `DATASET_VERSION` values. Record how to confirm the active version and the fixture file’s SHA-256.
- Document the internal `RETRIEVAL_BASE_URL` and the local `GENERATION_MODE=fixture`. Verify both services with private `/healthz` and `/readyz` checks. Keep those routes outside the public proxy.
- Document how to enable and clear the test-only failure and timeout cases. Record request IDs in the run results so Jinqing Zhang and Jason Zichen He can match browser failures with service logs.

Run the walkthrough in this order.

1. Start the stack and open each artwork route on the host using the configured LAN origin. Check that the web app can reach Go and that Go can retrieve both works from the same loaded dataset.
2. Confirm the LAN URL on the phone. If the host page works and the phone page does not, check network isolation and host port forwarding before changing browser code.
3. Scan the code for The Bedroom, ask a supported question, and open its source. Return to the conversation and run the documented follow-up.
4. Scan the other code and use the same brushwork wording on each work. Show the different artwork-scoped citations and open the second source.
5. Run one insufficient-evidence case. Then inject a dependency timeout, confirm the UI’s recovery behavior, clear the failure, and retry the same submission.

#### Integration acceptance

| Check | Owner | Observable result |
| --- | --- | --- |
| Both QR codes and direct links | Jeremiah Lim | Each scan opens the intended work. Refresh leaves the route valid and the conversation in the state the API defines. Switching to the other work uses a separately scoped conversation. |
| Frozen evaluation set | Jason Zichen He | All six original questions and both brushwork calls pass. Both brushwork calls cite the requested work’s own description. No retrieved hit or displayed citation belongs to the other artwork. |
| Follow-up and English wording | Jeremiah Lim, Jinqing Zhang, and Jason Zichen He | The agreed follow-up uses the conversation’s artwork and completed history. Without that history, the same text does not trigger the scripted rewrite. The documented English example separately returns the expected supported answer and citation. |
| Insufficient evidence | Jinqing Zhang and Jason Zichen He | Test a valid empty result with the real retrieval service. Use a controlled retrieval service stub for a known gap with related hits and for a supported fixture whose required chunk is missing. Each yields `insufficient` and empty citations. |
| Unrecognized question with hits | Jinqing Zhang and Jason Zichen He | A question outside the scripted set with keyword hits yields `unsupported_question` and empty citations. The UI renders it separately from insufficient evidence and service failure. |
| Invalid requests and wrong visitor | Jinqing Zhang | An invalid slug, malformed UUID, blank question, and unsupported answer language are rejected according to the contract. Missing identity, an expired conversation, and a foreign conversation return the same `404 conversation_not_found` on history and submit requests. |
| LAN origin and identity | Jeremiah Lim and Jinqing Zhang | Submit through the configured phone origin and create a valid UUID over HTTP. Confirm the expected cookie attributes, missing or mismatched origins returning `403 origin_not_allowed`, and no visitor token in JSON, browser storage, QR links, or logs. |
| Duplicate and concurrent submissions | Jinqing Zhang | Check `409 request_in_progress` for an active duplicate, `409 conversation_busy` for a different key while active, and `409 idempotency_conflict` for changed input on the same key. A failed submission can retry with its original ID. Accepting a new ID after failure causes the old key to return `409 request_superseded`. |
| Lost success response | Jeremiah Lim and Jinqing Zhang | Drop a response after commit. Recover through history or replay and verify the same turn ID and sequence, one stored exchange, and zero additional retrieval or generation calls for the replay. |
| Late reply after switching artworks | Jeremiah Lim and Jinqing Zhang | Switch artworks while a request is pending. The old request may finish in its own conversation, and its reply never appears on the destination page. |
| Reload while a turn is pending | Jeremiah Lim and Jinqing Zhang | After a browser reload, the pending submission is recovered and reconciled with history. A retry uses the original UUID, and the answer appears at most once. |
| Invalid retrieval responses | Jinqing Zhang and Jason Zichen He | Inject a malformed reply, wrong dataset version, wrong-artwork evidence, and a changed excerpt under a valid chunk ID. Each returns `502 retrieval_invalid_response`, with no completed answer or citation. |
| Startup and health | Jinqing Zhang and Jason Zichen He | Check the active seed version at both private readiness endpoints. A missing, invalid, or wrong-version fixture fails startup or readiness. Confirm that the public web proxy exposes neither health endpoint. |
| Retrieval service unavailable | Jinqing Zhang and Jeremiah Lim | Make Python unavailable and confirm `503 retrieval_unavailable`. The Go API stays ready and the artwork catalog still loads. Restore the retrieval service and retry. The completed exchange is present once. |
| Injected timeout and recovery | Jinqing Zhang and Jeremiah Lim | Independently trigger `504 retrieval_timeout`, `504 generation_timeout`, and `504 request_timeout` at their respective boundaries. Hold a browser test response past 12 seconds to verify client-side recovery. The UI retains the pending submission, and retry succeeds after removing the injection. A late result from the failed attempt cannot append another turn. |
| Restart and expiry | Jinqing Zhang | Exercise expiry 24 hours after creation with a controlled clock. After restarting Go, history is reset and the app can open a fresh conversation for the same artwork. |
| Original source access | Jeremiah Lim and Jason Zichen He | Every citation opens its stored source from the phone. Check the displayed excerpt, language, locator, attribution, license, and normalization against the seed, and verify that the evidence supports the claim. |

The local stage is complete when all acceptance checks pass against the same dataset version and implementation revision. Record any failed check as open with an owner and a reproduction step.

Keep three artifacts with the team’s handoff notes.

- Jeremiah Lim records the walkthrough and the tested phone, browser, and LAN setup. Identify the local answer mode as fixture generation backed by the real Python retriever.
- Jinqing Zhang records the implementation revision, startup and private health results, and request IDs and outcomes for invalid requests, retries, and fault injection.
- Jason Zichen He records the dataset version and seed SHA-256, the expected and observed results for all eight frozen cases, and the follow-up and English case outcomes.

#### Local verification record

On November 10, 2026, the M0 changes were tested on Windows with Docker Desktop, WSL Ubuntu and Chrome at a phone-sized viewport. The production web build used the host's LAN HTTP origin. Both private readiness endpoints reported `aic-seed-v0.1`, and both containers reported seed SHA-256 `3703fd4c9fcefb8e9fba20f23a5cb70e612c62ef7cb3f594a15d319ebc18d6ac`.

| Area | Observed result |
| --- | --- |
| Compose and routing | All three services became healthy. Only web port `8080` was published. Both private health paths returned `404` through the web proxy. Missing or mismatched POST origins returned `403`. |
| Evidence and citations | All eight frozen answer checks matched their expected statuses and citation IDs through the public proxy. The separate English material and scoped brushwork follow-up matched their own evidence. Replaying the same UUID returned the same completed turn. |
| Browser and recovery | Both QR images decoded to the correct artwork URLs. The first cross-artwork switch preserved both sessions, refresh restored the correct history, and a real retrieval outage returned `503`. The original UUID completed once after recovery. The browser recovered a dropped success response from history and stopped a stalled history read at the 12-second deadline. Restarting Go restored the saved question as unsent text on reload. |
| Physical phone | Pending. Record scans of both QR codes, source-link access and recovery on the target phone and network using the [walkthrough](../web/PHONE-TEST.md). |

## Stage 2. Cloud GPU demonstration

Start looking into the course and cloud constraints while the local prototype is in progress. Set dates after reviewing the current rubric, team availability, source permissions, and available GPU resources. Keep the local fixture path available for deterministic regression tests.

| Task | Owner | Dependencies | Artifact and acceptance |
| --- | --- | --- | --- |
| B7. Connect GPU-backed generation | Jinqing Zhang | Local integration, confirmed course requirements, and access to the chosen GPU environment | Implement the provider adapter and deploy the inference service. Correlate a public demo request with inference logs, record the model version, and verify the cloud GPU path with an actual artwork question. |
| C5. Expand the evidence and evaluation set | Jason Zichen He | Source reuse review, language reviewers, and stable local fixtures | Add approved records and test cross-language retrieval, follow-up, and unsupported claims on a larger set. Review citation support and separately record retrieval misses and answer failures. |
| A6. Validate the hosted visitor journey | Jeremiah Lim | Hosted API and approved public assets | Update the QR target to the hosted HTTPS artwork page. Verify the flow on the target phones and network, record the course demo, and capture recovery behavior for hosted failures. |

The cloud stage ends with a phone request that traverses the hosted web app, Go API, retrieval service, and GPU-backed inference. Keep the request ID, model version, and relevant deployment log so the team can verify what ran. Record startup and warm request behavior separately if they differ during rehearsal.

## Stage 3. Quality evaluation and product expansion

Freeze a hosted implementation revision and evaluation corpus before comparing systems. Agree on the scoring rubric and which visitors or language reviewers can participate.

| Task | Owner | Dependencies | Artifact and acceptance |
| --- | --- | --- | --- |
| A7. Run visitor workflow testing | Jeremiah Lim, with Jinqing Zhang and Jason Zichen He | Hosted build, defined tasks, and participant recruitment | Run equivalent visitor tasks across MuseBridge, ChatGPT, Claude, and a suitable museum guide where comparable. Record time to first useful answer, setup steps, and completion separately from answer quality. |
| C6. Run answer-quality review | Jason Zichen He, with Jeremiah Lim and Jinqing Zhang | Reviewed questions, matched source packets where possible, and available language reviewers | Compare accuracy, citation support, appropriate responses to missing material, and language quality. Record source access, model settings, and case counts so the comparison can be reproduced. |
| B8. Verify and close course requirements | Jinqing Zhang, with Jeremiah Lim and Jason Zichen He | Starts at kickoff with the current rubric. Final review needs deployment evidence and applicable interview and recording permissions. | Maintain the requirement and deadline checklist from the parallel course track. Before submission, Jeremiah Lim confirms the recordings and Jason Zichen He verifies the evidence and evaluation materials. |

Keep the visitor workflow and answer-quality results separate. For workflow, define what counts as a useful answer before timing and rotate the order of tools when practical. For answer quality, record model settings, assess support for factual claims, and report outcomes by question type and language. Use feature observations for existing guides when equivalent questions or sources cannot be supplied.

Before expanding the scope, review whether search, comparisons, or shared conversations address the next verified need and fit the remaining course schedule. Each addition gets its own acceptance cases and a named owner.

Keep buyer interviews and source permissions on a parallel track. Record who owns the relevant museum budget, what evidence they would need to consider a pilot, and any concrete next step. Obtain permission before recording or quoting an interview.
