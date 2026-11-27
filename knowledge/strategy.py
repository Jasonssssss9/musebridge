"""Construct the retrieval strategy once at each process boundary."""

import os
from pathlib import Path

from .dataset import DATASET_VERSION
from .errors import RetrievalError


MODES = ("lexical", "hybrid")
VERSIONS = {"lexical": "legacy-facet-v0.1", "hybrid": "openai-hybrid-v1"}


def configured_index(dataset, mode):
    if mode == "lexical":
        return None
    if mode != "hybrid":
        raise ValueError("Retrieval mode must be hybrid or lexical.")
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        raise RetrievalError("retrieval_unavailable", "Hybrid retrieval requires OPENAI_API_KEY.")
    try:
        from .vector_index import open_index
    except ImportError as exc:
        raise RetrievalError("retrieval_unavailable", "Install knowledge/requirements.txt for hybrid retrieval.") from exc
    name = "embeddings-v1" if dataset.dataset_version == DATASET_VERSION else f"embeddings-{dataset.dataset_version}"
    path = Path(os.environ.get("MUSEBRIDGE_VECTOR_INDEX") or f"~/.cache/musebridge/{name}.json")
    return open_index(dataset, path.expanduser())
