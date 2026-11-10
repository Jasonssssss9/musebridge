# Go application API

Owner: Jinqing Zhang (Go backend and system integration).

This implements the Go artwork service, anonymous conversations, bounded question orchestration, reviewed fixture answers and validating retrieval client. The Go and Python services load the same immutable seed. Artwork cards and conversation recovery remain available independently of retrieval, and a separate Go diagnostic command can call the retrieval service.

Implemented routes:

| Method and path | Behavior |
| --- | --- |
| `GET /api/artworks/{slug}` | Return the local artwork card, or `404 artwork_not_found`. |
| `POST /api/conversations` | Create or restore the visitor's artwork conversation; set or refresh the visitor cookie. |
| `GET /api/conversations/{conversation_id}/messages` | Read the owning visitor's conversation and its current history. |
| `POST /api/conversations/{conversation_id}/messages` | Submit or retry a question, retrieve real evidence, and return a completed fixture turn. |
| `GET /healthz` | Private process liveness and the loaded dataset version. |
| `GET /readyz` | Private local readiness; it does not depend on the retrieval service being reachable. |

The B2 public routes and B3 retrieval/fixture answer path are implemented. History exposes completed turns in sequence and the current processing or failed pending request. Submission also implements the server-side reservation, replay and recovery semantics needed by the public contract. Answers remain fixed test responses in `mode: fixture`; cloud model generation, Compose packaging and physical-phone acceptance are later work. See [the API contract](../docs/api-contract.md) for the wire format.

## Run locally

Use Go 1.27 or later. The module uses the standard library and the Go-maintained [`golang.org/x/text/unicode/norm`](https://pkg.go.dev/golang.org/x/text/unicode/norm) package for contract-required NFKC normalization. The first build downloads the version pinned in `go.mod` and verified by `go.sum`. From the repository root:

```bash
cd api
go run ./cmd/server
```

The native profile listens on `127.0.0.1:8000`. The server loads and validates the seed before opening its listener. A missing seed, malformed records, broken references, unsupported generation mode or mismatched dataset version causes a nonzero exit. It does not fetch museum content, start Python, download a model or connect to a database.

From a second terminal:

```bash
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:8000/readyz
curl -sS -i http://127.0.0.1:8000/api/artworks/aic-28560
curl -sS -i http://127.0.0.1:8000/api/artworks/aic-11723
curl -sS -i http://127.0.0.1:8000/api/artworks/missing
```

Stop with Ctrl+C or SIGTERM. The service waits for graceful shutdown for up to ten seconds. Build a standalone executable if needed:

```bash
go build -o /tmp/musebridge-api ./cmd/server
/tmp/musebridge-api
```

The default seed path is relative to the process working directory, so run these commands from `api/` or supply an absolute `SEED_PATH`.

## Create and restore a conversation

The default browser origin is `http://localhost:8080`. Every public POST must send the exact configured `PUBLIC_BASE_URL` as its `Origin`, including scheme and port. Missing, duplicate or mismatched origins return `403 origin_not_allowed`. The request body must be one UTF-8 JSON object containing only `artwork_id`, with a maximum size of 16 KiB.

Create a conversation and save its anonymous visitor cookie:

```bash
curl -fsS -c /tmp/musebridge-cookies.txt \
  -H 'Origin: http://localhost:8080' \
  -H 'Content-Type: application/json' \
  --data-raw '{"artwork_id":"aic-28560"}' \
  http://127.0.0.1:8000/api/conversations
```

The response contains `conversation` and `created: true`. Repeat the request using the same cookie to restore that artwork's conversation:

```bash
curl -fsS -b /tmp/musebridge-cookies.txt -c /tmp/musebridge-cookies.txt \
  -H 'Origin: http://localhost:8080' \
  -H 'Content-Type: application/json' \
  --data-raw '{"artwork_id":"aic-28560"}' \
  http://127.0.0.1:8000/api/conversations
```

A restore returns `created: false` and identical conversation metadata. Replace `CONVERSATION_ID` below with the returned UUID to read history. A GET does not require an Origin header and does not refresh the cookie.

```bash
curl -fsS -b /tmp/musebridge-cookies.txt \
  http://127.0.0.1:8000/api/conversations/CONVERSATION_ID/messages
```

The visitor token appears only in the host-only `HttpOnly; SameSite=Lax` cookie, with a 24-hour `Max-Age`. HTTPS configuration adds `Secure`. Creation/restoration refreshes the cookie but never extends a conversation's fixed 24-hour lifetime. The two artworks have separate conversations. Concurrent creation with an established cookie restores one conversation for the same artwork and dataset version.

Missing, foreign, unknown, expired or pre-restart identity/history returns the same `404 conversation_not_found`. Malformed UUID text returns `400 invalid_request`; uppercase canonical UUID text is accepted. State lives in one Go process and is lost on restart. Expired records are removed when the store is accessed. See [the conversation contract](../docs/api-contract.md#22-create-or-restore-a-conversation) for the wire format.

## Submit a question

Start the Python service as described below and keep Go running. Replace `CONVERSATION_ID` with the conversation returned above, and send the same cookie:

```bash
curl -fsS -b /tmp/musebridge-cookies.txt \
  -H 'Origin: http://localhost:8080' \
  -H 'Content-Type: application/json' \
  --data-raw '{"client_message_id":"0dd2a1f3-8e81-4266-926a-cbe04584ee84","question":"What material is this artwork made of?","answer_language":"en"}' \
  http://127.0.0.1:8000/api/conversations/CONVERSATION_ID/messages
```

The response contains one `turn`, including the original trimmed question, contiguous sequence, pinned version, requested language, fixed answer and current evidence citations. Use a new UUID for each new question. Repeating the original UUID and normalized payload returns the exact stored turn without another retrieval or generation call. Changing the question or language under that UUID returns `409 idempotency_conflict`.

The fixture registry accepts the reviewed material, brushwork, nervous-energy and known-gap questions in [contract section 6](../docs/api-contract.md#6-query-preparation-and-generation-boundary). Both `en` and `zh-CN` answers are available. Matching applies NFKC, Latin case normalization and whitespace/punctuation rules; that matching normalization never changes the stored question or its idempotency identity. With a completed turn in this artwork's conversation, the two declared material/brushwork follow-ups are rewritten to standalone retrieval queries. Other wording is passed through unchanged.

After successful evidence validation:

- Known gaps and empty evidence produce `insufficient`. A supported fixture also requires its exact chunk IDs; related evidence alone does not satisfy it.
- Supported fixtures with the required evidence produce `answered`. Citation IDs resolve only to this question's validated evidence; the adapter cannot invent source records or URLs.
- Other questions with evidence produce `unsupported_question`. Every completed status has `mode: fixture`; the two fallbacks have empty citation arrays.

A conversation permits one processing request at a time. An active duplicate returns `409 request_in_progress`, and a different UUID returns `409 conversation_busy`. A technical failure appends no turn and preserves a failed `pending_request` with its original question, language, UUID and safe error. Retry that UUID after resolving the fault. Accepting a new UUID supersedes the old failed key, which subsequently returns `409 request_superseded`. History is an atomic snapshot and never presents the same request as both completed and pending.

Each accepted attempt has an independent eight-second budget, with three seconds for retrieval and two seconds for the fixture adapter. Browser disconnection does not cancel an accepted attempt. Expiry is checked again before commit, and an attempt token prevents a late callback from committing after failure or retry. Timeout/failure injection uses replaceable dependencies in Go tests; it is not a public runtime control.

Retrieval errors map to `502 retrieval_invalid_response`, `503 retrieval_unavailable` or `504 retrieval_timeout`. Generation errors have corresponding `generation_*` codes; the total budget maps to `504 request_timeout`. Technical failures remain non-2xx errors and are never saved as insufficient-evidence answers.

## Query retrieval through the Go client

Start the existing Python service from the repository root:

```bash
python3 -m knowledge.http_service --host 127.0.0.1
```

In another terminal, run the Go diagnostic from `api/`:

```bash
go run ./cmd/retrieve \
  --artwork-id aic-28560 \
  --query 'What material is this artwork made of?'
```

The command loads the Go client's local seed, generates a request ID, and sends one real `POST /retrieve` to `RETRIEVAL_BASE_URL`. It prints the validated evidence as JSON. For this example, the result contains `aic-28560-medium`. A valid query with no matching topic succeeds with `evidence: []`; it is not a service error.

The diagnostic does not create a conversation, choose an answer status or generate an answer. It accepts `--artwork-id` and `--query`, with no positional arguments. A successful command exits 0; invalid arguments, configuration, data or a retrieval failure exit 2. Retrieval failures produce a safe JSON diagnostic on stderr, with the request ID and error classification. Startup diagnostics may include local paths. These are CLI diagnostics, not new public HTTP routes. `go run` can additionally print its own exit-status message; use a built executable when scripting exit codes and stderr:

```bash
go build -o /tmp/musebridge-retrieve ./cmd/retrieve
/tmp/musebridge-retrieve --artwork-id aic-11723 --query 'brushwork'
```

## Retrieval client behavior

`internal/retrieval.New(baseURL, dataset)` constructs a reusable client. Call `Retrieve(ctx, Request{ArtworkID, Query, RequestID})` with a prepared question and the Go API's trusted request ID. The client derives the dataset version from the loaded release and always sends `top_k: 2`. Call `CloseIdleConnections` when finished with a short-lived client.

- Requests have a known byte length and use UTF-8 JSON. They send no visitor cookie, chat history, answer language, evaluation ID or expected answer. The private service address is reached directly, without an environment HTTP proxy.
- Each attempt makes one request with a three-second deadline covering connection, headers and body reads. A shorter parent deadline wins. The client does not retry or follow redirects.
- Responses are capped at 64 KiB. The client validates the content type, echoed request ID, JSON integrity, exact required field names/types, version, result limit and unique evidence IDs. Additive response metadata is ignored.
- Every evidence record must match the requested artwork and the Go API's complete trusted chunk/source projection, including excerpt, URL, locator, language, license, attribution and normalization. An invalid record rejects the entire response; no partial result is returned.
- Returned evidence preserves the retrieval service's order and contains no shared mutable catalog state. The result does not make an evidence-sufficiency judgment.

`retrieval.Error` exposes `Code`, `Message`, `Retryable` and the public `HTTPStatus`. It never copies an upstream response body or network diagnostic into its safe message.

| Condition | Client result | Public mapping |
| --- | --- | --- |
| Valid evidence, including an empty array | Successful `Result` | Fixture logic decides the completed answer status. |
| Connection failure or valid retrieval error with status `500`/`503` | `retrieval_unavailable`, retryable | 503 |
| Retrieval service's request deadline or a transport timeout | `retrieval_timeout`, retryable | 504 |
| Invalid evidence, framing/content, redirect or valid retrieval error with status `400`/`404`/`409` | `retrieval_invalid_response`, not retryable | 502 |
| Invalid local query/request ID or unknown artwork | Rejected before contacting the retrieval service | 400 or 404 |
| Parent context cancelled or expired | Original context error, with no result | The question orchestrator owns cancellation and its total-attempt timeout. |

The message endpoint returns these safe classifications and retains the failed pending request for recovery.

## Configuration

The process reads environment variables; it does not automatically load a dotenv file. All defaults are for native local development. To load the supplied example into the current shell:

```bash
set -a
source .env.example
set +a
go run ./cmd/server
```

| Variable | Default | Meaning |
| --- | --- | --- |
| `HOST` | `127.0.0.1` | Listen interface; use `0.0.0.0` for a future container deployment. |
| `PORT` | `8000` | Listen port, from 1 to 65535. |
| `PUBLIC_BASE_URL` | `http://localhost:8080` | Exact frontend origin, with no path or trailing slash. Set the actual reachable LAN origin during browser integration. |
| `COOKIE_SECURE` | Inferred from the public URL scheme | Controls the visitor cookie. Must be `false` for the local HTTP profile and `true` for HTTPS. |
| `DATASET_VERSION` | `aic-seed-v0.1` | Exact version required in the seed and its artwork records. |
| `SEED_PATH` | `../knowledge/data/aic-seed-v0.1.json` | Shared seed file. |
| `RETRIEVAL_BASE_URL` | `http://127.0.0.1:8001` | Private origin used by question submission and the retrieval diagnostic. Artwork and conversation reads make no retrieval calls. |
| `GENERATION_MODE` | `fixture` | Reviewed in-process test answers; the only supported local mode. |

Do not set `GOROOT` or `GOPATH` to the repository. Go modules locate this project through `go.mod`. Host-specific environment files belong outside version control.

## Validation and diagnostics

The seed loader checks exact field names, required/null fields, JSON types, duplicate properties, UTF-8 and surrogate pairs, stable IDs, per-record versions, source relationships, UTC fetch times, field-specific licenses, locators, attribution, normalization notes and topic assignments. This loader implements the current two-work release. A larger collection requires a reviewed contract and loader update.

Structural validation does not authenticate a passage. The startup log records the SHA-256 of the actual loaded file so Jinqing Zhang and Jason Zichen He can compare release identity. The current released SHA-256 is `3703fd4c9fcefb8e9fba20f23a5cb70e612c62ef7cb3f594a15d319ebc18d6ac`.

HTTP replies are UTF-8 JSON with `Cache-Control: no-store` and a server-generated `X-Request-ID`. Error bodies include the same request ID, a stable error code, safe English message and retry flag. Unknown routes and unsupported methods use JSON errors; method errors include the route's `Allow: GET`, `Allow: POST` or `Allow: GET, POST`. HEAD requests omit the body of their applicable method error. Query parameters are rejected on implemented routes.

Structured request logs include the request ID, result code, known artwork ID, dataset version and elapsed time. They omit request URLs, question text, client-supplied IDs and cookies. Startup errors can include local configuration paths for the developer; public error responses do not expose them.

Question-attempt logs additionally record retrieval/generation durations and validated evidence chunk IDs. A completed replay has its own trace event and skips both dependencies. Source excerpts, answers and visitor payloads are excluded from routine logs.

The health routes are private service endpoints. During later web integration, proxy only the intended `/api` routes and keep health endpoints off the public proxy.

## Checks

From `api/`:

```bash
go test -race ./...
go vet ./...
```

Tests cover the published seed, invalid data and provenance, configuration profiles, both artwork cards, common errors, readiness without the retrieval service, request IDs, log privacy and concurrent artwork isolation. HTTP handler tests run in process. Retrieval client tests start temporary loopback servers and need local socket access; they do not need Python, a model or an external network.

Conversation checks cover browser cookie attributes, exact origins, strict request bodies, ownership, separate artwork/release scopes, concurrent creation, uppercase UUID lookup, fixed expiry and process restart. A controlled clock verifies the exact 24-hour boundary without waiting. Errors and history reads never establish or refresh visitor identity.

Message checks cover the reviewed bilingual fixture set, NFKC input, missing required chunks, exact provenance, isolated adapter inputs, scoped follow-ups, bounded recent context, concurrent submissions, completed replay, supersession and recovery after dependency failure. Controlled deadlines verify retrieval, generation and total-attempt errors. Late results cannot append a turn, and an accepted attempt can complete after browser cancellation.

The simulated servers return valid evidence, empty results, individual corrupted fields, foreign records, duplicates, invalid JSON/Unicode, wrong versions, error statuses, oversized or truncated bodies, redirects, and delays before headers or during body reads. Tests also check one-attempt behavior, parent cancellation and safe error text. Test-only deadlines are shorter; production calls retain their three-second limit.

To test against a separately running real Python retrieval service, start Python as shown above and run from `api/`:

```bash
RETRIEVAL_BASE_URL=http://127.0.0.1:8001 \
  go test -race -tags=integration ./internal/retrieval ./internal/httpapi \
  -run 'TestPython(Service|Answer)Integration' -count=1 -v
```

The opt-in tests skip when `RETRIEVAL_BASE_URL` is absent. The retrieval check covers the six frozen questions, brushwork isolation, English wording and no-hit behavior. The public-answer check adds reviewed statuses/citations, replay, both languages, scoped follow-ups, fullwidth input and a fresh conversation without context. Neither test starts or stops the configured Python service.

The source layout follows the request path:

- `cmd/server`: startup, listener and graceful shutdown.
- `cmd/retrieve`: direct Go-to-Python retrieval diagnostic.
- `internal/config`: environment defaults and validation.
- `internal/conversation`: process-local identity, conversation recovery, request reservations, replay, expiry and atomic history.
- `internal/chat`: bounded question orchestration, dependency errors and safe attempt traces.
- `internal/fixture`: reviewed bilingual answers, limited query preparation and output/citation validation.
- `internal/catalog`: immutable seed loading, artwork lookup and trusted evidence projection.
- `internal/jsonwire`: shared strict JSON integrity checks.
- `internal/httpapi`: implemented HTTP routes and shared response formatting.
- `internal/retrieval`: bounded HTTP client, typed errors, fault simulations and opt-in real-service checks.
