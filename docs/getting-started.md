# Getting started

This guide covers the currently runnable local components: the shared two-artwork dataset, the Python keyword retriever, the Go artwork API, and the validating Go retrieval client. Conversation endpoints, answer generation and a mobile frontend are not implemented yet.

## Prerequisites

- Python 3.10 or later.
- Go 1.27 or later.
- A local checkout of this repository.

The native commands use only the Python and Go standard libraries. They do not require a model, API key or database. Docker is optional for the standalone retrieval service; full-stack Compose startup is planned.

Start each terminal in the repository root unless a command explicitly changes directories. Both services use the immutable `knowledge/data/aic-seed-v0.1.json` release.

## Start the Python retrieval service

In the first terminal:

```bash
python3 -m knowledge.http_service --host 127.0.0.1
```

The service listens on `http://127.0.0.1:8001`. It loads and validates the dataset before serving requests. See the [retrieval guide](../knowledge/README.md) for configuration, direct queries, source provenance and Docker instructions.

## Start the Go artwork API

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

The health endpoints are private development/operations routes. Future frontend proxy configuration must keep them outside the public visitor API.

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

HTTP tests start temporary loopback servers and require local socket access. The normal test suites do not need a separately running retrieval service. Opt-in Go integration tests use a real Python service; see the Go backend guide for the command.

To run the broader retrieval diagnostics from the repository root:

```bash
python3 -m eval.retrieval_eval --top-k 2
```

This evaluation measures evidence retrieval, not generated answers. See the [evaluation guide](../eval/README.md) for the baseline results and their limits.

## Stop the services

Use Ctrl+C in each service terminal. Runtime configuration is read from environment variables. The Go service's default seed path is relative to `api/`; use an absolute `SEED_PATH` when starting it from another directory.
