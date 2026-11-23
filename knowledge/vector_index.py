"""OpenAI passage embeddings and exact, artwork-scoped cosine search."""

from dataclasses import dataclass
from hashlib import sha256
from http.client import HTTPSConnection, HTTPException
import json
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Callable

from langchain_text_splitters import CharacterTextSplitter

from .dataset import Dataset
from .errors import RetrievalError

MODEL = "text-embedding-3-small"
DIMENSIONS = 1536
CHUNK_SIZE = 500
EMBED_TIMEOUT_SECONDS = 2.0
Embed = Callable[[list[str]], list[list[float]]]
Passage = tuple[str, str, str]
UnitVector = tuple[float, ...]


def _unavailable(message: str) -> RetrievalError:
    return RetrievalError("retrieval_unavailable", message)


def _openai_embed(texts: list[str]) -> list[list[float]]:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise _unavailable("OpenAI embeddings require a server-side OPENAI_API_KEY.")
    connection = HTTPSConnection("api.openai.com", timeout=EMBED_TIMEOUT_SECONDS)
    body = json.dumps({"model": MODEL, "input": texts, "encoding_format": "float"})
    try:
        connection.request("POST", "/v1/embeddings", body, {
            "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        })
        response = connection.getresponse()
        if response.status != 200:
            raise _unavailable(f"OpenAI embeddings returned HTTP {response.status}.")
        payload = json.load(response)
        rows = payload["data"]
        if payload["model"] != MODEL or not isinstance(rows, list) or len(rows) != len(texts):
            raise ValueError
        rows = sorted(rows, key=lambda row: row["index"])
        for position, row in enumerate(rows):
            if type(row["index"]) is not int or row["index"] != position:
                raise ValueError
        return [row["embedding"] for row in rows]
    except RetrievalError:
        raise
    except (OSError, HTTPException, ValueError, TypeError, KeyError):
        # Raw HTTP bodies and credentials must never be exposed through retrieval.
        raise _unavailable("OpenAI embeddings could not complete the request.") from None
    finally:
        connection.close()


def _unit_vectors(rows: list, count: int) -> list[UnitVector]:
    if not isinstance(rows, list) or len(rows) != count:
        raise ValueError("Unexpected vector count.")
    result = []
    for row in rows:
        if not isinstance(row, list) or len(row) != DIMENSIONS:
            raise ValueError("Unexpected vector dimensions.")
        if any(type(value) not in (float, int) for value in row):
            raise ValueError("Invalid vector element.")
        length = math.hypot(*row)
        if not math.isfinite(length) or not length:
            raise ValueError("Invalid vector norm.")
        result.append(tuple(value / length for value in row))
    return result


def _parts(dataset: Dataset) -> tuple[list[Passage], str]:
    splitter = CharacterTextSplitter(separator=" ", chunk_size=CHUNK_SIZE, chunk_overlap=0)
    character_splitter = CharacterTextSplitter(
        separator="", chunk_size=CHUNK_SIZE, chunk_overlap=0,
    )
    passages = []
    sources = []
    for chunk in dataset.chunks:
        artwork_id, chunk_id = chunk["artwork_id"], chunk["chunk_id"]
        source = dataset.sources[chunk["source_id"]]
        sources.append((chunk_id, source["source_id"], source["source_url"],
                        chunk["source_locator"], chunk["excerpt"]))
        for part in splitter.split_text(chunk["excerpt"]):
            # A single unbroken word can exceed the word splitter's target size.
            pieces = character_splitter.split_text(part) if len(part) > CHUNK_SIZE else [part]
            passages.extend((artwork_id, chunk_id, text) for text in pieces)
    fingerprint = sha256(json.dumps([sources, passages], ensure_ascii=False).encode()).hexdigest()
    return passages, fingerprint


@dataclass
class VectorIndex:
    passages: list[Passage]
    vectors: list[UnitVector]
    embed: Embed

    def rank(self, artwork_id: str, query: str) -> list[tuple[str, float]]:
        candidates = [
            (chunk_id, vector) for (scope, chunk_id, _), vector in zip(self.passages, self.vectors)
            if scope == artwork_id
        ]
        if not candidates:
            return []
        try:
            query_vector, = _unit_vectors(self.embed([query]), 1)
        except RetrievalError:
            raise
        except (OSError, OverflowError, ValueError, TypeError):
            raise _unavailable("The query embedding is unavailable or invalid.") from None
        scores = {}
        for chunk_id, vector in candidates:
            cosine = max(-1.0, min(1.0, sum(a * b for a, b in zip(query_vector, vector))))
            scores[chunk_id] = max(cosine, scores.get(chunk_id, -1.0))
        return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
            temp_path = Path(tmp.name)
            json.dump(data, tmp, separators=(",", ":"), allow_nan=False)
        temp_path.replace(path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def open_index(dataset: Dataset, path: str | Path, *, embed: Embed | None = None) -> VectorIndex:
    """Load matching local embeddings, or build and atomically publish a fresh batch."""
    embed = _openai_embed if embed is None else embed
    path = Path(path)
    passages, fingerprint = _parts(dataset)
    header = {"format": 1, "dataset_version": dataset.dataset_version,
              "model": MODEL, "fingerprint": fingerprint}
    try:
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(cached, dict) and cached.get("header") == header:
                vectors = _unit_vectors(cached.get("vectors"), len(passages))
                return VectorIndex(passages, vectors, embed)
        except (FileNotFoundError, ValueError, OverflowError):
            # Missing, malformed and stale caches are rebuilt from reviewed source text.
            pass
        rows = embed([text for _, _, text in passages])
        vectors = _unit_vectors(rows, len(passages))
        _save(path, {"header": header, "vectors": rows})
        return VectorIndex(passages, vectors, embed)
    except RetrievalError:
        raise
    except (OSError, ValueError, TypeError, OverflowError):
        raise _unavailable("The museum evidence vector index could not be opened or built.") from None
