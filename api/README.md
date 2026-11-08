# Go application API

This is the first implementation slice of B's application backend. It loads the same immutable seed as C and serves artwork cards independently of retrieval availability.

Implemented routes:

| Method and path | Behavior |
| --- | --- |
| `GET /api/artworks/{slug}` | Return the local artwork card, or `404 artwork_not_found`. |
| `GET /healthz` | Private process liveness and the loaded dataset version. |
| `GET /readyz` | Private local readiness; it does not depend on C being reachable. |

Conversation creation, visitor cookies, POST origin checks, message submission, retrieval calls, fixture generation and replay are subsequent slices. Their public routes are not implemented yet. The full behavior remains defined in [the API contract](../docs/api-contract.md); the published contract is broader than this initial service.

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
| `RETRIEVAL_BASE_URL` | `http://127.0.0.1:8001` | Validated configuration for later B-to-C integration. No calls are made in this slice. |
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

Tests cover the published seed, invalid data and provenance, configuration profiles, both artwork cards, common errors, readiness without C, request IDs, log privacy and concurrent artwork isolation. HTTP handler tests run in process without opening network ports. Validate a separately started executable with the curl commands above.

The source layout follows the request path:

- `cmd/server`: startup, listener and graceful shutdown.
- `internal/config`: environment defaults and validation.
- `internal/catalog`: immutable seed loading and artwork lookup.
- `internal/httpapi`: implemented HTTP routes and shared response formatting.
