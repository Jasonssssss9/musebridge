"""Trusted Irises corpus and its combined gallery and artist-letter search."""

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
from types import MappingProxyType

from .dataset import Dataset
from .errors import DatasetValidationError, RetrievalError
from .getty import load_getty
from .passage_ranking import rank
from .retrieval import evidence_record, validate_request
from .strategy import VERSIONS

DATA = Path(__file__).resolve().parent / "data"
ARTWORK = "getty-103jnh"
GALLERY = "getty-gallery-v2"
PACK = "vincent-irises-v1"
VINCENT_SCOPE = MappingProxyType(dict(artwork_id=ARTWORK, dataset_version=GALLERY,
                                     conversation_mode="vincent", reference_pack_version=PACK))


def _release(path, pin):
    raw = Path(path).read_bytes()
    digest = sha256(raw).hexdigest()
    if digest != pin.read_text(encoding="ascii").strip():
        raise DatasetValidationError(f"Vincent evidence digest mismatch: {Path(path).name}.")
    return json.loads(raw), digest


@dataclass(frozen=True)
class VincentCorpus:
    dataset: Dataset
    evidence: dict
    release_hashes: dict


def load_vincent_corpus(scope, *, gallery_path=None):
    """Load the independently pinned releases without credentials or a vector client."""
    if scope != VINCENT_SCOPE:
        raise DatasetValidationError("The supplied scope does not identify the Vincent Irises release.")
    gallery_file = DATA / f"{GALLERY}.json"
    pack_file = DATA / "reference-packs" / f"{PACK}.json"
    gallery, gallery_hash = _release(gallery_path or gallery_file, gallery_file.with_suffix(".sha256"))
    pack, pack_hash = _release(pack_file, pack_file.with_suffix(".sha256"))
    if (gallery["dataset_version"] != GALLERY or pack["dataset_version"] != GALLERY
            or pack["artwork_id"] != ARTWORK or pack["reference_pack_version"] != PACK):
        raise DatasetValidationError("The Vincent release metadata does not match its configured scope.")
    base = load_getty(gallery, GALLERY)
    chunks = [c for c in base.chunks if c["artwork_id"] == ARTWORK]
    sources = {c["source_id"]: base.sources[c["source_id"]] for c in chunks}
    evidence = {c["chunk_id"]: evidence_record(c, sources[c["source_id"]]) for c in chunks}
    reserved = set(base.sources) | {c["chunk_id"] for c in base.chunks}
    reserved.update(f"{work}-catalog-{field}" for work in base.artworks
                    for field in ("title", "artist", "date_display"))
    for record in pack["records"]:
        chunk_id, source_id = record["chunk_id"], record["source_id"]
        if record["artwork_id"] != ARTWORK or reserved.intersection((chunk_id, source_id)) or chunk_id == source_id:
            raise DatasetValidationError("Invalid or conflicting Vincent record identity.")
        reserved.update((chunk_id, source_id))
        evidence[chunk_id] = record
        sources[source_id] = record
        # The source heading distinguishes personal correspondence from modern research.
        # Search context is not part of the canonical quotation returned to the visitor.
        kind = record["source_kind"].replace("_", " ")
        title = record["source_title"]
        chunks.append({**record, "topics": (kind, title),
                       "index_text": f"{kind}. {title}. {record['excerpt']}"})
    # Keep all work titles for the existing cross-work query check, but index only Irises.
    dataset = Dataset(GALLERY, base.artworks, MappingProxyType(sources), tuple(chunks))
    hashes = {"gallery": {"version": GALLERY, "sha256": gallery_hash},
              "reference_pack": {"version": PACK, "sha256": pack_hash}}
    return VincentCorpus(dataset, evidence, hashes)


class VincentHybridRetriever:
    def __init__(self, corpus, index):
        if index is None:
            raise ValueError("Vincent retrieval requires a vector index.")
        self.dataset = corpus.dataset
        self.evidence = corpus.evidence
        self.release_hashes = corpus.release_hashes
        self.index = index

    @classmethod
    def open(cls, scope, *, cache_path=None, embed=None, gallery_path=None):
        corpus = load_vincent_corpus(scope, gallery_path=gallery_path)
        if embed is None and not os.environ.get("OPENAI_API_KEY", "").strip():
            raise RetrievalError("retrieval_unavailable", "Vincent hybrid retrieval requires OPENAI_API_KEY.")
        from .vector_index import open_index

        path = cache_path or os.environ.get("MUSEBRIDGE_VINCENT_VECTOR_INDEX")
        path = Path(path or f"~/.cache/musebridge/embeddings-{GALLERY}-{PACK}.json").expanduser()
        gallery_cache = Path(os.environ.get("MUSEBRIDGE_VECTOR_INDEX") or
                             f"~/.cache/musebridge/embeddings-{GALLERY}.json").expanduser()
        if path.resolve() == gallery_cache.resolve():
            raise DatasetValidationError("Guide and Vincent vector caches must have distinct paths.")
        return cls(corpus, open_index(corpus.dataset, path, embed=embed))

    def search(self, query, *, top_k=2, artwork_id=ARTWORK, dataset_version=GALLERY):
        validate_request(self.dataset, artwork_id=artwork_id, dataset_version=dataset_version, query=query, top_k=top_k)
        if artwork_id != ARTWORK:
            raise RetrievalError("artwork_not_found", "The requested artwork is outside the Vincent scope.")
        if top_k > 2:
            raise RetrievalError("invalid_request", "Vincent top_k must be 1 or 2.")
        selected, lexical, dense = rank(self.dataset, ARTWORK, query, self.index, artist_conversation=True)
        result = {"dataset_version": GALLERY, "evidence": [dict(self.evidence[key]) for key in selected[:top_k]]}
        trace = {"strategy": VERSIONS["hybrid"], "reference_pack_version": PACK,
                 "lexical_ranking": lexical, "dense_ranking": [{"chunk_id": key, "cosine": score} for key, score in dense]}
        return result, trace
