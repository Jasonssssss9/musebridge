# Go application API

This implements B's artwork service and validating retrieval client. Both load the same immutable seed as C. Artwork cards remain available independently of retrieval, and a separate Go diagnostic command can call C before conversation endpoints exist.

Implemented routes:

| Method and path | Behavior |
| --- | --- |
| `GET /api/artworks/{slug}` | Return the local artwork card, or `404 artwork_not_found`. |
| `GET /healthz` | Private process liveness and the loaded dataset version. |
| `GET /readyz` | Private local readiness; it does not depend on C being reachable. |

Conversation creation, visitor cookies, POST origin checks, message submission, fixture generation and replay are subsequent slices. Their public routes are not implemented yet. The HTTP server still exposes only the routes above; retrieval is available through the Go client and diagnostic command below. The full behavior remains defined in [the API contract](../docs/api-contract.md).

## Run locally

Use Go 1.27 or later. This module has no third-party dependencies. From the repository root:

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

## Query C through B's client

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

The command loads B's copy of the seed, generates a request ID, and sends one real `POST /retrieve` to `RETRIEVAL_BASE_URL`. It prints the validated evidence as JSON. For this example, the result contains `aic-28560-medium`. A valid query with no matching topic succeeds with `evidence: []`; it is not a service error.

The diagnostic does not create a conversation, choose an answer status or generate an answer. It accepts `--artwork-id` and `--query`, with no positional arguments. A successful command exits 0; invalid arguments, configuration, data or a retrieval failure exit 2. Retrieval failures produce a safe JSON diagnostic on stderr, with the request ID and error classification. Startup diagnostics may include local paths. These are CLI diagnostics, not new public HTTP routes. `go run` can additionally print its own exit-status message; use a built executable when scripting exit codes and stderr:

```bash
go build -o /tmp/musebridge-retrieve ./cmd/retrieve
/tmp/musebridge-retrieve --artwork-id aic-11723 --query 'brushwork'
```

## Retrieval client behavior

`internal/retrieval.New(baseURL, dataset)` constructs a reusable client. Call `Retrieve(ctx, Request{ArtworkID, Query, RequestID})` with a prepared question and B's trusted request ID. The client derives the dataset version from the loaded release and always sends `top_k: 2`. Call `CloseIdleConnections` when finished with a short-lived client.

- Requests have a known byte length and use UTF-8 JSON. They send no visitor cookie, chat history, answer language, evaluation ID or expected answer. The private service address is reached directly, without an environment HTTP proxy.
- Each attempt makes one request with a three-second deadline covering connection, headers and body reads. A shorter parent deadline wins. The client does not retry or follow redirects.
- Responses are capped at 64 KiB. The client validates the content type, echoed request ID, JSON integrity, exact required field names/types, version, result limit and unique evidence IDs. Additive response metadata is ignored.
- Every evidence record must match the requested artwork and B's complete trusted chunk/source projection, including excerpt, URL, locator, language, license, attribution and normalization. An invalid record rejects the entire response; no partial result is returned.
- Returned evidence preserves C's order and contains no shared mutable catalog state. The result does not make an evidence-sufficiency judgment.

`retrieval.Error` exposes `Code`, `Message`, `Retryable` and the future public `HTTPStatus`. It never copies an upstream response body or network diagnostic into its safe message.

| Condition | Client result | Future public mapping |
| --- | --- | --- |
| Valid evidence, including an empty array | Successful `Result` | Later answer logic decides the answer status. |
| Connection failure or valid C `500`/`503` error | `retrieval_unavailable`, retryable | 503 |
| C's request deadline or a transport timeout | `retrieval_timeout`, retryable | 504 |
| Invalid evidence, framing/content, redirect or valid C `400`/`404`/`409` error | `retrieval_invalid_response`, not retryable | 502 |
| Invalid local query/request ID or unknown artwork | Rejected before contacting C | 400 or 404 |
| Parent context cancelled or expired | Original context error, with no result | The later question orchestrator owns cancellation and its total-attempt timeout. |

The existing HTTP server does not yet return these retrieval errors, because message submission is still pending.

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
| `COOKIE_SECURE` | Inferred from the public URL scheme | Must be `false` for the local HTTP profile and `true` for HTTPS. Validated now for later visitor state. |
| `DATASET_VERSION` | `aic-seed-v0.1` | Exact version required in the seed and its artwork records. |
| `SEED_PATH` | `../knowledge/data/aic-seed-v0.1.json` | Shared seed file. |
| `RETRIEVAL_BASE_URL` | `http://127.0.0.1:8001` | C's private origin, used by the retrieval diagnostic/client. The artwork HTTP server makes no retrieval calls. |
| `GENERATION_MODE` | `fixture` | Only supported local mode; the adapter will be implemented later. |

Do not set `GOROOT` or `GOPATH` to the repository. Go modules locate this project through `go.mod`. Host-specific environment files belong outside version control.

## Validation and diagnostics

The seed loader checks exact field names, required/null fields, JSON types, duplicate properties, UTF-8 and surrogate pairs, stable IDs, per-record versions, source relationships, UTC fetch times, field-specific licenses, locators, attribution, normalization notes and topic assignments. This loader implements the current two-work release. A larger collection requires a reviewed contract and loader update.

Structural validation does not authenticate a passage. The startup log records the SHA-256 of the actual loaded file so B and C can compare release identity. The current released SHA-256 is `3703fd4c9fcefb8e9fba20f23a5cb70e612c62ef7cb3f594a15d319ebc18d6ac`.

HTTP replies are UTF-8 JSON with `Cache-Control: no-store` and a server-generated `X-Request-ID`. Error bodies include the same request ID, a stable error code, safe English message and retry flag. Unknown routes and unsupported methods use JSON errors; method errors include `Allow: GET`. HEAD requests omit the body of their applicable method error. Query parameters are rejected on implemented routes.

Structured request logs include the request ID, result code, known artwork ID, dataset version and elapsed time. They omit request URLs, question text, client-supplied IDs and cookies. Startup errors can include local configuration paths for the developer; public error responses do not expose them.

The health routes are private service endpoints. During later web integration, proxy only the intended `/api` routes and keep health endpoints off the public proxy.

## Checks

From `api/`:

```bash
go test -race ./...
go vet ./...
```

Tests cover the published seed, invalid data and provenance, configuration profiles, both artwork cards, common errors, readiness without C, request IDs, log privacy and concurrent artwork isolation. HTTP handler tests run in process. Retrieval client tests start temporary loopback servers and need local socket access; they do not need Python, a model or an external network.

The simulated servers return valid evidence, empty results, individual corrupted fields, foreign records, duplicates, invalid JSON/Unicode, wrong versions, error statuses, oversized or truncated bodies, redirects, and delays before headers or during body reads. Tests also check one-attempt behavior, parent cancellation and safe error text. Test-only deadlines are shorter; production calls retain their three-second limit.

To test against a separately running real C service, start Python as shown above and run from `api/`:

```bash
RETRIEVAL_BASE_URL=http://127.0.0.1:8001 \
  go test -race -tags=integration ./internal/retrieval \
  -run TestPythonServiceIntegration -count=1 -v
```

The opt-in integration test skips when `RETRIEVAL_BASE_URL` is absent. It uses the six frozen questions, both brushwork isolation calls, an English material question and a valid no-hit query. It verifies retrieval and provenance only; expected answer statuses are for the later fixture adapter. It neither starts nor stops the configured Python service.

The source layout follows the request path:

- `cmd/server`: startup, listener and graceful shutdown.
- `cmd/retrieve`: direct B-to-C retrieval diagnostic.
- `internal/config`: environment defaults and validation.
- `internal/catalog`: immutable seed loading, artwork lookup and trusted evidence projection.
- `internal/jsonwire`: shared strict JSON integrity checks.
- `internal/httpapi`: implemented HTTP routes and shared response formatting.
- `internal/retrieval`: bounded HTTP client, typed errors, fault simulations and opt-in real-C checks.
