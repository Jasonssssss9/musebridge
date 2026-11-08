# Artwork data and keyword retrieval

This package implements C's seed loader, deterministic keyword retriever and private HTTP service for `aic-seed-v0.1`. It uses the Python standard library and serves evidence from the local seed without fetching museum content at runtime.

Use Python 3.10 or later. Development checks run on Python 3.12 in WSL. Run the commands below from the repository root.

## Run a query

```bash
python3 -m knowledge \
  --artwork-id aic-28560 \
  --query '这件作品用了什么材料？'
```

The command returns JSON containing the loaded `dataset_version` and the matching `evidence` records. Each record includes the original excerpt, artwork and chunk IDs, source URL, locator, language, attribution and license.

Compare the two works with the same question:

```bash
python3 -m knowledge \
  --artwork-id aic-28560 \
  --query '馆方如何描述这件作品的笔触？' \
  --top-k 1

python3 -m knowledge \
  --artwork-id aic-11723 \
  --query '馆方如何描述这件作品的笔触？' \
  --top-k 1
```

The first call returns `aic-28560-description-01`; the second returns `aic-11723-description-01`. A valid query with no matching topic returns `"evidence": []` and exits successfully.

The default seed path is resolved relative to the package, not the current directory. Use `--seed PATH` to choose a file and `--dataset-version VERSION` to pin the version that file must declare. `--top-k` defaults to 2 and accepts integers from 1 to 5. A successful query writes JSON to stdout and exits 0. Data or retrieval failures write a diagnostic JSON object to stderr and exit 2; malformed CLI options use argparse's usual diagnostic and exit 2. These are local CLI diagnostics, not HTTP responses.

## Run the private retrieval service

From the repository root, start C on loopback for local testing.

```bash
python3 -m knowledge.http_service --host 127.0.0.1
```

C loads and validates the seed before binding. An invalid configuration, unreadable seed, invalid seed or version mismatch exits with a nonzero status. Check the diagnostic on stderr before trying to connect.

| Environment variable | CLI override | Default |
| --- | --- | --- |
| `HOST` | `--host` | `0.0.0.0` |
| `PORT` | `--port` | `8001` |
| `SEED_PATH` | `--seed` | `data/aic-seed-v0.1.json` relative to the `knowledge` package |
| `DATASET_VERSION` | `--dataset-version` | `aic-seed-v0.1` |

CLI flags take precedence over environment variables. `HOST=0.0.0.0` lets other containers reach C on the private service network. Use the loopback command above when testing a native Python process on the same host.

The local transport requires one `Content-Length` header and an uncompressed UTF-8 JSON body of at most 8 KiB. Chunked transfer is not supported in this demo. Curl's `--data-raw` and Go requests created from a byte buffer supply a known body length. Each response closes its connection. A five-second socket timeout bounds incomplete request reads; B still enforces its own three-second retrieval deadline.

A single printable ASCII `X-Request-ID` of at most 128 characters is echoed. An absent, blank, malformed or duplicated request ID is replaced with a generated one. No visitor cookie or browser Origin header is required on C's private endpoints.

The service uses Python's standard-library HTTP server for the private local demo. Keep it on the internal service network; choose the hosted HTTP runtime during the later deployment review.

### Smoke-test the service

In a second terminal, check liveness and readiness. The healthy response identifies the loaded dataset version.

```bash
curl -fsS http://127.0.0.1:8001/healthz
curl -fsS http://127.0.0.1:8001/readyz
```

Send a complete retrieval request. `-i` shows the status and response headers, including the echoed request ID.

```bash
curl -sS -i http://127.0.0.1:8001/retrieve \
  -H 'Content-Type: application/json' \
  -H 'X-Request-ID: musebridge-local-001' \
  --data-raw '{"artwork_id":"aic-28560","query":"What material is this artwork made of?","top_k":2,"dataset_version":"aic-seed-v0.1"}'
```

The response is HTTP 200 with `dataset_version: "aic-seed-v0.1"` and the `aic-28560-medium` evidence record. For a valid query with no topic match, C also returns HTTP 200, with `evidence: []`.

An incorrect version returns HTTP 409 with `error.code: "dataset_version_mismatch"`. Keep `-i` to see the status and JSON error body.

```bash
curl -sS -i http://127.0.0.1:8001/retrieve \
  -H 'Content-Type: application/json' \
  -H 'X-Request-ID: musebridge-local-002' \
  --data-raw '{"artwork_id":"aic-28560","query":"material","top_k":2,"dataset_version":"wrong-version"}'
```

See [the retrieval contract](../docs/api-contract.md#4-b--c-retrieval-contract) for validation, status codes and the exact evidence fields.

### Run C in Docker

Build from the repository root. The image uses the [official Python 3.12 slim base](https://hub.docker.com/_/python), the service package and the released seed. The service runs as UID/GID 10001. Its container health check calls `/readyz` on the configured port.

```bash
docker build -f knowledge/Dockerfile -t musebridge-retrieval:local .
docker run --rm --read-only \
  --publish 127.0.0.1:8001:8001 \
  musebridge-retrieval:local
```

Run the same curl commands from the Docker host. The published address is loopback for local integration. To test with the same on-disk seed that B will read, mount the file read-only at C's default seed path.

```bash
docker run --rm --read-only \
  --publish 127.0.0.1:8001:8001 \
  --mount "type=bind,source=$PWD/knowledge/data/aic-seed-v0.1.json,target=/app/knowledge/data/aic-seed-v0.1.json,readonly" \
  musebridge-retrieval:local
```

Stop the first container before starting the second on the same host port. For a different container port, set `PORT` in the container environment and update the publish mapping so the health check uses the same port as the service.

### B integration handoff

- For B running on the same host as the standalone C process or the Docker loopback port, set `RETRIEVAL_BASE_URL=http://127.0.0.1:8001`. In the planned Compose deployment, B calls `http://retrieval:8001` on the private network. The browser calls B's public API.
- Send `POST /retrieve` with `artwork_id`, `query`, `dataset_version` and `top_k: 2`. Forward B's `X-Request-ID`, allow up to three seconds for the call, and validate every returned evidence record against the same immutable seed mounted by B.
- Distinguish a successful empty list from a technical failure. C only returns ranked evidence. B decides the fixture answer status and owns the public error mapping.

## Call the Python API

```python
from knowledge import load_dataset, retrieve

dataset = load_dataset("knowledge/data/aic-seed-v0.1.json")

result = retrieve(
    dataset,
    artwork_id="aic-28560",
    query="What material is this artwork made of?",
    dataset_version="aic-seed-v0.1",
    top_k=2,
)
print(result["evidence"][0]["chunk_id"])
```

Load once and reuse the returned `Dataset`. Its artwork/source mappings, chunk records and topic collections are immutable. Each retrieval call returns new response dictionaries, so changing a response cannot alter later results or the loaded evidence.

`load_dataset` raises `DatasetValidationError` for unreadable files or invalid data. It validates exact fields, types, release cardinalities and IDs, duplicate JSON properties, source relationships, version consistency, UTC timestamps, field-specific licenses, source URLs and published topic assignments. It preserves the excerpt text.

The loader implements the current two-artwork schema. A new version pin alone does not add support for a larger collection or different IDs; update the contract, loader and tests together for that change. Structural validation does not prove a passage's authenticity or prevent an edited passage from retaining an old version string. Compare the release checksum when identifying the published artifact.

`retrieve` raises `RetrievalError` with one of these `code` values:

| Code | Meaning |
| --- | --- |
| `invalid_request` | Invalid argument type, blank/oversized query, forbidden control character, unpaired surrogate or invalid limit. |
| `artwork_not_found` | The artwork is absent from the loaded dataset. |
| `dataset_version_mismatch` | The requested release differs from the loaded release. |

The function requires explicit artwork and version arguments. It does not accept chat history, expected answers or evaluation case IDs. Unknown Python keyword arguments raise `TypeError`; the HTTP service handles JSON request validation and maps retrieval errors to the private API contract.

## How ranking works

The implementation follows [API contract section 4.3](../docs/api-contract.md#43-deterministic-keyword-baseline):

1. Validate the query and scope.
2. Normalize the query with Unicode NFKC, lowercase and collapsed whitespace.
3. Identify topics using the fixed English/Chinese alias dictionary. English aliases use word boundaries; Chinese aliases use substring matching.
4. Consider only chunks assigned to the selected artwork.
5. Score each chunk by the number of distinct matching topics. Repeated words or multiple aliases for the same topic do not increase the score.
6. Exclude zero scores, sort by descending score and then ascending chunk ID, and return up to `top_k` records.

The query normalization never rewrites stored museum excerpts. No hit in the current artwork causes an empty result, with no fallback to the other work. Source records are joined from the loaded seed; retrieval never fetches a URL.

The retriever returns evidence only. B owns the final `answered`, `insufficient` and `unsupported_question` decisions. An empty `expected_chunk_ids` in an insufficient evaluation case means that no seed evidence sufficiently answers the question; it is not a general assertion that retrieval must return zero related chunks.

## Run the checks

```bash
python3 -m unittest discover -s knowledge/tests -v
```

The suite covers seed loading and malformed data, all six frozen cases, both variants of the same-query artwork check, `top_k: 1` rankings, English and Chinese aliases, normalization, word boundaries, stable ties, repeated queries, output isolation, request errors and the CLI. The frozen evaluation inputs come from [the evaluation JSON](../eval/aic-seed-v0.1.tests.json); runtime retrieval does not import that file.

The suite also starts real loopback HTTP servers to verify request framing, body limits, status codes, request IDs, liveness/readiness, concurrent artwork isolation and safe errors. Subprocess checks verify startup failure and configuration precedence. Use the HTTP smoke tests above to check a separately started service.

## Released data

- [Seed JSON](data/aic-seed-v0.1.json): two artworks, two sources and four reviewed chunks.
- Seed SHA-256: `3703fd4c9fcefb8e9fba20f23a5cb70e612c62ef7cb3f594a15d319ebc18d6ac`.
- Evaluation SHA-256: `447e8b2df061553dd42732e9c6659f6d1bacabdee52553129550bdd9405d446f`.

Verify the files from the repository root:

```bash
sha256sum knowledge/data/aic-seed-v0.1.json eval/aic-seed-v0.1.tests.json
```
