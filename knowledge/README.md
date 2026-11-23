# Artwork evidence and hybrid retrieval

MuseBridge serves four reviewed passages from two Art Institute of Chicago artworks in `aic-seed-v0.1`. The Agent supplies an English retrieval query and a trusted artwork ID. The service returns original museum excerpts with stable IDs and provenance. Qwen uses those excerpts to decide what the sources support and to write the visitor's reply.

## Run hybrid retrieval

Use Python 3.12 in WSL. From the repository root, activate the project virtual environment and install the retrieval dependencies once.

```bash
source .venv/bin/activate
python -m pip install -c agent/requirements.txt -r knowledge/requirements.txt
```

In the retrieval terminal, set the API key without adding it to shell history.

```bash
read -rsp 'OpenAI API key: ' OPENAI_API_KEY
echo
export OPENAI_API_KEY
```

The first hybrid run builds a persistent index from the seed. Repeated runs reuse a matching index. Each hybrid search that requests semantic candidates sends the query to OpenAI. Allow outbound HTTPS from the retrieval process to `api.openai.com`. The browser, Go API, Qwen and Agent use their existing interfaces and do not need the embedding key.

Run the same English question for both works. Each lookup should rank the description for the requested artwork first. Inspect its ID, excerpt and source URL.

```bash
python -m knowledge --artwork-id aic-28560 \
  --query 'How does the museum describe the marks left by the brush?' --top-k 1
python -m knowledge --artwork-id aic-11723 \
  --query 'How does the museum describe the marks left by the brush?' --top-k 1
```

For an offline comparison, run the lexical strategy explicitly. It uses the seed and rules alone.

```bash
python -m knowledge --mode lexical --artwork-id aic-28560 \
  --query 'What material is this artwork made of?' --top-k 1
```

### Ranking

1. Validate the dataset version, artwork ID, query and limit. Candidate selection stays within the active artwork.
2. The lexical branch maps phrases to reviewed facets such as medium or brushwork. The semantic branch uses LangChain's `CharacterTextSplitter` with a 500-character target within each evidence record. All four seed excerpts fit individually. OpenAI `text-embedding-3-small` encodes these passages at index time and the English query at lookup time. For an unparsed keyword fragment, the semantic branch omits redundant current-work and artist labels when they accompany a search subject. The lexical branch still sees the original text. A span that asks exclusively for an explicitly classified detail outside the reviewed seed, such as pigment identity, is excluded from semantic search. Unrecognized requests remain eligible, including an open part of a compound question.
3. Semantic retrieval ranks passages by cosine similarity. Multiple windows of one evidence record count once. A starting cosine cutoff of 0.30 filters weak candidates. Both ranked lists contribute to reciprocal rank fusion with a rank constant of 60. Ties favor the lexical ranking, then the vector ranking and chunk ID.
4. Return up to `top_k` distinct canonical evidence records from the released seed. The Agent evaluates coverage. For example, a passage describing blue paint can be relevant to a pigment question even though it gives no pigment identity.

The cutoff is provisional. Validate it against paraphrases and unanswerable questions using live embeddings. An empty successful result describes this lookup only. A failed embedding call is a retrieval error.

## Run the private HTTP service

```bash
python -m knowledge.http_service --host 127.0.0.1 --port 8001
```

| Setting | Purpose |
| --- | --- |
| `MUSEBRIDGE_RETRIEVAL_MODE` | `hybrid` by default for CLI and HTTP startup. Use `lexical` for offline fixtures. Both commands accept `--mode`. |
| `OPENAI_API_KEY` | Retrieval-process credential for OpenAI embeddings in hybrid mode. |
| `MUSEBRIDGE_VECTOR_INDEX` | Persistent index path. The native default is `~/.cache/musebridge/embeddings-v1.json`. Its parent must be writable on initial build or when the index needs rebuilding. |
| `HOST`, `PORT` | HTTP bind address and port. Defaults are `0.0.0.0` and `8001`. Use loopback for native development. |
| `SEED_PATH`, `DATASET_VERSION` | HTTP service's source file and expected release. Both command entry points accept explicit `--seed` and `--dataset-version` flags. |

The local transport requires one `Content-Length` header and an uncompressed UTF-8 JSON body of at most 8 KiB. Chunked transfer is not supported in this demo. Curl's `--data-raw` and Go requests created from a byte buffer supply a known body length. Each response closes its connection. A five-second socket timeout bounds incomplete request reads. The retrieval caller enforces a separate three-second tool deadline.

A single printable ASCII `X-Request-ID` of at most 128 characters is echoed. An absent, blank, malformed or duplicated request ID is replaced with a generated one. No visitor cookie or browser Origin header is required on the retrieval service's private endpoints.

From a second terminal, run:

```bash
curl -fsS http://127.0.0.1:8001/readyz
curl -isS http://127.0.0.1:8001/retrieve \
  -H 'Content-Type: application/json' \
  -H 'X-Request-ID: musebridge-brushwork-001' \
  --data-raw '{"artwork_id":"aic-28560","query":"How did the artist lay down the paint?","top_k":1,"dataset_version":"aic-seed-v0.1"}'
```

The body contains `dataset_version` and `evidence`. Each candidate carries the source language, URL, locator, attribution and license. The hybrid service identifies its strategy with `X-Retrieval-Strategy: openai-hybrid-v1`. The Agent verifies each record against the released manifest and enforces a three-second tool deadline. Missing or unusable credentials prevent live hybrid operation. An upstream failure during lookup returns a technical error. See the [private retrieval contract](../docs/api-contract.md#4-go-api-and-retrieval-service-retrieval-contract) and the [native Agent startup guide](../docs/public-agent-integration.md).

### Run in Docker

Build the default hybrid image. Pass the exported key only to retrieval. The named volume keeps the index writable by UID 10001 and preserves it across container restarts. The published port accepts connections on loopback.

```bash
docker build -f knowledge/Dockerfile -t musebridge-retrieval:local .
docker run --rm --read-only --env OPENAI_API_KEY \
  --mount type=volume,source=musebridge-vectors,target=/var/cache/musebridge \
  --publish 127.0.0.1:8001:8001 musebridge-retrieval:local
```

The repository's `docker compose up --build` starts the fixture web demo. Its retrieval container uses the lexical image target and the private network. Use the native Agent guide for a complete Qwen-backed visitor session.

## Call the Python API

```python
from knowledge import load_dataset, retrieve
from knowledge.strategy import configured_index

dataset = load_dataset("knowledge/data/aic-seed-v0.1.json")
index = configured_index(dataset, "hybrid")

result = retrieve(
    dataset,
    artwork_id="aic-28560",
    query="What material is this artwork made of?",
    dataset_version="aic-seed-v0.1",
    top_k=2,
    vector_index=index,
)
print(result["evidence"][0]["chunk_id"])
```

Load the dataset and index once per process. Pass the index to each query. Omit `vector_index` for the lexical baseline. Its artwork/source mappings, chunk records and topic collections are immutable. Each retrieval call returns new response dictionaries, so changing a response cannot alter later results or the loaded evidence.

`load_dataset` raises `DatasetValidationError` for unreadable files or invalid data. It validates exact fields, types, release cardinalities and IDs, duplicate JSON properties, source relationships, version consistency, UTC timestamps, field-specific licenses, source URLs and published topic assignments. It preserves the excerpt text.

The loader implements the current two-artwork schema. A new version pin alone does not add support for a larger collection or different IDs; update the contract, loader and tests together for that change. Structural validation does not prove a passage's authenticity or prevent an edited passage from retaining an old version string. Compare the release checksum when identifying the published artifact.

`retrieve` raises `RetrievalError` with one of these `code` values:

| Code | Meaning |
| --- | --- |
| `retrieval_unavailable` | Hybrid embeddings or the vector index could not be used. |
| `invalid_request` | Invalid argument type, blank/oversized query, forbidden control character, unpaired surrogate or invalid limit. |
| `artwork_not_found` | The artwork is absent from the loaded dataset. |
| `dataset_version_mismatch` | The requested release differs from the loaded release. |

The function requires explicit artwork and version arguments. It does not accept chat history, expected answers or evaluation case IDs. Unknown Python keyword arguments raise `TypeError`; the HTTP service handles JSON request validation and maps retrieval errors to the private API contract.

## Checks and evaluation

```bash
python -m unittest discover -s knowledge/tests -v
python -m eval.retrieval_eval --mode lexical --top-k 1
python -m eval.retrieval_eval --mode hybrid --top-k 1
```

Unit tests use controlled embedding responses. The last command uses live OpenAI embeddings and requires the key. Compare top-1 correctness, missing evidence, returns on unsupported questions and artwork isolation. Each work currently has only two records, so returning both at top-2 is a weak test of ranking. In a separate end-to-end run, check whether Qwen's answer and memory stay within the cited source. See the [evaluation guide](../eval/README.md) for suite labels and historical lexical results.

### Live embedding check (2026-11-23)

Using `text-embedding-3-small`, the 48-case original suite and 12-case independent suite both returned at least one labeled passage for every answerable case at rank 1. Rank 2 recovered all labeled passages. The final hybrid run returned candidates on 0/20 and 0/6 labeled negative cases, respectively, and had no artwork-scope violations. The lexical baseline achieved the same aggregate results on these previously used suites.

The more informative comparison was a pair of planner fragments: `The Bedroom van Gogh brushwork description` and `Woman at Her Toilette Morisot brushwork description`. Lexical search returned nothing for either. The live Agent HTTP tool ranked the correct description first for both and validated its original excerpt and ID. On five additional English probes, both answerable paraphrases ranked the correct description first, one unsupported paint-thickness query returned nothing, and two unsupported queries about flower models and brush manufacture returned related passages. These are retrieval-level results; answer and memory quality require a run with Qwen.

## Released data

- [Seed JSON](data/aic-seed-v0.1.json): two artworks, two sources and four reviewed chunks.
- Seed SHA-256: `3703fd4c9fcefb8e9fba20f23a5cb70e612c62ef7cb3f594a15d319ebc18d6ac`.
- Evaluation SHA-256: `447e8b2df061553dd42732e9c6659f6d1bacabdee52553129550bdd9405d446f`.

Verify the files from the repository root:

```bash
sha256sum knowledge/data/aic-seed-v0.1.json eval/aic-seed-v0.1.tests.json
```
