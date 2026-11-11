# Getting started

This guide covers the currently runnable local components: the shared two-artwork dataset, Python evidence retrieval, the Go artwork/conversation/message APIs, reviewed fixture answers, and a mobile frontend with mock and live modes. The local answer path uses actual museum evidence and labeled fixed responses.

## Start the full local stack with Compose

Install Docker with Compose v2 and run these commands from the repository root.

```bash
docker compose up --build -d
docker compose ps
```

Open [The Bedroom](http://localhost:8080/a/aic-28560), [Woman at Her Toilette](http://localhost:8080/a/aic-11723), or the [QR cards](http://localhost:8080/qr). Compose serves the production web build in live mode. The browser uses relative `/api/` requests. The proxy forwards them to `api:8000`, and Go retrieves evidence from `http://retrieval:8001`. Both services read the same read-only `aic-seed-v0.1` file. Only web port `8080` is published.

For a phone on the same network, replace the example IP with the address the phone can reach and start the stack with that exact origin. Open the same origin in the desktop browser when generating the QR cards. The local HTTP profile uses `COOKIE_SECURE=false` and `GENERATION_MODE=fixture`.

```bash
PUBLIC_BASE_URL=http://192.168.1.20:8080 docker compose up --build -d
```

Open the LAN URL on the phone before scanning a code. See the [phone walkthrough](../web/PHONE-TEST.md) for the two artwork scans, citations, refresh and recovery.

### Check readiness and recovery

```bash
docker compose exec -T api wget -qO- http://127.0.0.1:8000/readyz
docker compose exec -T retrieval python3 -c 'from urllib.request import urlopen; print(urlopen("http://127.0.0.1:8001/readyz").read().decode())'
sha256sum knowledge/data/aic-seed-v0.1.json
```

Both private readiness replies show `aic-seed-v0.1`. The Go startup log records the loaded seed checksum. For a live recovery test, stop retrieval.

```bash
docker compose stop retrieval
```

Submit a supported question. The artwork stays available and the question shows a retryable failure. Start retrieval again and check its readiness. Use **Try again** on the existing question and confirm that one answer appears.

```bash
docker compose start retrieval
docker compose logs api retrieval
```

Run `docker compose down` to stop the stack. Go keeps history in memory for the local demo, so restarting Go clears that history.

## Native prerequisites

- Python 3.10 or later.
- Go 1.27 or later.
- Node.js 20 or later and npm when running the optional frontend.
- A local checkout of this repository.

Python uses its standard library. Go uses its standard library and the pinned `golang.org/x/text` package for Unicode normalization; the first build downloads that module. The backend does not require Node.js, a model, API key or database. The frontend uses Node.js to run Vite and build React/TypeScript assets.

Start each terminal in the repository root unless a command explicitly changes directories. Both services use the immutable `knowledge/data/aic-seed-v0.1.json` release.

## Start the Python retrieval service

In the first terminal:

```bash
python3 -m knowledge.http_service --host 127.0.0.1
```

The service listens on `http://127.0.0.1:8001`. It loads and validates the dataset before serving requests. See the [retrieval guide](../knowledge/README.md) for configuration, direct queries, source provenance and Docker instructions.

## Start the Go application API

In a second terminal:

```bash
cd api
go run ./cmd/server
```

The API listens on `http://127.0.0.1:8000`. It reads the local dataset and keeps artwork cards available when the retrieval service is stopped. From another terminal, check the implemented routes:

```bash
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:8000/readyz
curl -fsS http://127.0.0.1:8000/api/artworks/aic-28560
curl -fsS http://127.0.0.1:8000/api/artworks/aic-11723
```

The health endpoints are private development and operations routes. The Compose proxy keeps them outside the public visitor API.

The [Go backend guide](../api/README.md#create-and-restore-a-conversation) includes cookie-aware commands for creating/restoring an artwork conversation and reading history. These routes do not require Python. Its [question example](../api/README.md#submit-a-question) runs real retrieval, selects a reviewed fixture answer, validates citations and saves the completed turn. Question submission requires Python to be available at `RETRIEVAL_BASE_URL`. A conversation expires 24 hours after creation; restarting Go clears all anonymous visitor and conversation state.

## Run the optional web interface

In another terminal:

```bash
cd web
npm ci
npm run dev
```

The default frontend uses a mock API and can demonstrate the full interface independently. With Go and Python running, use `npm run dev:live` to load artwork, restore conversations and submit questions through the real backend. Answers are still labeled fixed test responses. For phone access, set Go's `PUBLIC_BASE_URL` to the exact LAN origin used in the browser. See the [web guide](../web/README.md) for QR cards and mock scenarios. Browser and physical-phone acceptance remain separate from the automated backend checks.

## Run a Go-to-Python retrieval query

With the Python service running, use a third terminal:

```bash
cd api
go run ./cmd/retrieve \
  --artwork-id aic-28560 \
  --query 'What material is this artwork made of?'
```

The command sends a real request to the Python service, validates its evidence against the Go client's local dataset, and prints the evidence JSON. It does not require the Go artwork HTTP server to be running. It retrieves passages and source details; it does not generate an answer.

The default `RETRIEVAL_BASE_URL` is `http://127.0.0.1:8001`. Configuration examples, diagnostics, failure handling and real-service integration checks are documented in the [Go backend guide](../api/README.md).

## Run automated checks

From the repository root:

```bash
python3 -m unittest discover -s knowledge/tests -v
```

From `api/`:

```bash
go test -race ./...
go vet ./...
```

From `web/`, run the frontend checks.

```bash
npm ci
npm test
npm run build
```

HTTP tests start temporary loopback servers and require local socket access. The normal test suites do not need a separately running retrieval service. Opt-in Go integration tests use a real Python service; see the Go backend guide for the command.

To run the broader retrieval diagnostics from the repository root:

```bash
python3 -m eval.retrieval_eval --top-k 2
```

This evaluation measures evidence retrieval, not generated answers. See the [evaluation guide](../eval/README.md) for the baseline results and their limits.

## Stop the services

Use Ctrl+C in each service terminal. Runtime configuration is read from environment variables. The Go service's default seed path is relative to `api/`; use an absolute `SEED_PATH` when starting it from another directory.
