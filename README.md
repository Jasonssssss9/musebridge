# MuseBridge

MuseBridge is a museum guide accessed through artwork QR codes and a mobile web page. The planned application combines artwork-scoped retrieval with cited answers.

The current implementation contains the two-artwork source release and C's Python data loader, keyword retriever and automated checks. See [the retrieval guide](knowledge/README.md) for commands and examples.

From the repository root, using Python 3.10 or later:

```bash
python3 -m unittest discover -s knowledge/tests -v
python3 -m knowledge --artwork-id aic-28560 --query 'What material is this artwork made of?'
```

These commands need only the Python standard library.

- [System design](docs/design.md)
- [API and data contract](docs/api-contract.md)
- [Implementation plan](docs/plan.md)
