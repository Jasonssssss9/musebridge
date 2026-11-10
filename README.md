# MuseBridge

MuseBridge is a museum guide accessed through artwork QR codes and a mobile web page. The planned application combines artwork-scoped retrieval with cited answers.

The current implementation contains the two-artwork source release, C's Python keyword retriever, B's Go artwork service, and B's validating HTTP retrieval client with a direct diagnostic command. Conversation and question-answering routes are still planned. See [the retrieval guide](knowledge/README.md) for C's configuration, Docker and integration examples, and [the Go API guide](api/README.md) for B's current scope, client checks and startup instructions.

From the repository root, using Python 3.10 or later:

```bash
python3 -m unittest discover -s knowledge/tests -v
python3 -m knowledge --artwork-id aic-28560 --query 'What material is this artwork made of?'
python3 -m knowledge.http_service --host 127.0.0.1
```

These commands need only the Python standard library.

Start the Go API in a separate terminal, using Go 1.27 or later:

```bash
cd api
go run ./cmd/server
```

The API listens on `http://127.0.0.1:8000` and serves both artwork cards from the shared seed even when Python is stopped. See the Go API guide for configuration and checks.

With Python running, exercise B's actual retrieval client from `api/`:

```bash
go run ./cmd/retrieve --artwork-id aic-28560 --query 'What material is this artwork made of?'
```

The command validates returned evidence against B's copy of the seed and prints the evidence JSON. It does not yet generate an answer.

- [System design](docs/design.md)
- [API and data contract](docs/api-contract.md)
- [Implementation plan](docs/plan.md)
- [Retrieval evaluation and recorded baselines](eval/README.md)
