# MuseBridge

MuseBridge is a museum guide accessed through artwork QR codes and a mobile web page. The planned application combines artwork-scoped retrieval with cited answers.

The current implementation contains the two-artwork source release and C's Python keyword retriever. C supports direct queries and a private HTTP service for B. See [the retrieval guide](knowledge/README.md) for service configuration, Docker and integration examples.

From the repository root, using Python 3.10 or later:

```bash
python3 -m unittest discover -s knowledge/tests -v
python3 -m knowledge --artwork-id aic-28560 --query 'What material is this artwork made of?'
python3 -m knowledge.http_service --host 127.0.0.1
```

These commands need only the Python standard library.

- [System design](docs/design.md)
- [API and data contract](docs/api-contract.md)
- [Implementation plan](docs/plan.md)
- [Retrieval evaluation and recorded baselines](eval/README.md)
