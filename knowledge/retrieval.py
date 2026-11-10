"""Deterministic keyword retrieval for the local museum dataset."""

import re
import unicodedata

from .dataset import Dataset
from .errors import RetrievalError


_TOPIC_ALIASES = {
    "material": (
        "material", "materials", "medium", "made of", "oil", "canvas", "painting support",
        "材料", "材质", "媒介", "油画", "画布", "底材",
    ),
    "brushwork": (
        "brushwork", "brushstroke", "brushstrokes", "brush stroke", "brush strokes",
        "brush-stroke", "brush-strokes", "brush mark", "brush marks", "paint mark", "paint marks",
        "笔触", "画痕", "笔痕",
    ),
    "energy": ("nervous", "energy", "紧张"),
    "color": ("color", "colors", "colour", "colours", "颜色", "色彩"),
    "perspective": ("perspective", "receding lines", "透视"),
    "background": ("background", "wallpaper", "bedspread", "背景", "墙纸", "床罩"),
}

_TOPIC_PATTERNS = {
    topic: re.compile(
        "|".join(
            rf"\b{re.escape(alias)}\b" if alias.isascii() else re.escape(alias)
            for alias in aliases
        )
    )
    for topic, aliases in _TOPIC_ALIASES.items()
}


# Each alternative describes cues that must occur in the same clause. Ambiguous
# words such as "surface", "uneasy" and "behind" are not standalone aliases.
_CLAUSE_SEPARATOR = re.compile(r"[,.;:!?，。；：！？、]+")
_CONTEXTUAL_TOPIC_CUES = {
    "material": (
        (r"\b(?:what|which)\b", r"\bpainted (?:on|onto)(?=\s*(?:$|(?:and|or)\b))"),
        (
            r"\b(?:what|which) (?:(?:kind|type) of )?(?:surface|support)\b",
            r"\b(?:paint(?:ed|ing)?|picture|artwork)\b",
        ),
        (r"\b(?:paint and (?:the )?(?:surface|support)|(?:surface|support) and (?:the )?paint)\b",),
        (r"(?:画|绘)在.{0,16}(?:什么|哪[种类样]).{0,12}上",),
    ),
    "brushwork": (
        (r"\b(?:painter|artist)['’]s marks?\b",),
    ),
    "energy": (
        (
            r"\b(?:room|scene|painting|picture|image|composition) "
            r"(?:(?:can|could|may|might) )?(?:feels?|seems?|looks?) "
            r"(?:(?:so|very|rather|somewhat|slightly|a little|a bit) )?(?:unsettling|uneasy)\b",
        ),
        (r"(?:画面|房间|场景|构图).{0,12}(?:显得|看起来|让人|令人|给人).{0,6}不安",),
    ),
    "background": (
        (
            r"\bbehind (?:her|(?:the )?(?:figure|woman|sitter))\b",
            r"\b(?:floral|flowers?|flower[- ]like|patterns?|shapes?|looks? like|suggest(?:s|ed)?)\b",
        ),
        (r"身后", r"花形|花纹|花卉|花朵|图案|看起来像|看着像|像什么"),
    ),
}
_CONTEXTUAL_TOPIC_PATTERNS = {
    topic: tuple(tuple(re.compile(cue) for cue in rule) for rule in rules)
    for topic, rules in _CONTEXTUAL_TOPIC_CUES.items()
}


def _query_topics(normalized_query: str) -> set[str]:
    topics = {
        topic for topic, pattern in _TOPIC_PATTERNS.items() if pattern.search(normalized_query)
    }
    clauses = _CLAUSE_SEPARATOR.split(normalized_query)
    for topic, alternatives in _CONTEXTUAL_TOPIC_PATTERNS.items():
        if topic in topics:
            continue
        if any(
            all(cue.search(clause) for cue in rule)
            for clause in clauses
            for rule in alternatives
        ):
            topics.add(topic)
    return topics


def retrieve(
    dataset: Dataset,
    *,
    artwork_id: str,
    query: str,
    dataset_version: str,
    top_k: int = 2,
) -> dict:
    """Return fresh evidence records for a query scoped to one artwork."""
    if not isinstance(artwork_id, str) or not artwork_id.strip():
        raise RetrievalError("invalid_request", "artwork_id must be a nonempty string.")
    if not isinstance(dataset_version, str) or not dataset_version.strip():
        raise RetrievalError("invalid_request", "dataset_version must be a nonempty string.")
    if not isinstance(query, str):
        raise RetrievalError("invalid_request", "query must be a string.")
    if any(
        unicodedata.category(char) in {"Cc", "Cs"} and char not in "\t\n\r"
        for char in query
    ):
        raise RetrievalError("invalid_request", "query contains invalid characters.")

    query = query.strip()
    if not 1 <= len(query) <= 1000:
        raise RetrievalError("invalid_request", "query must contain 1 to 1000 Unicode code points.")
    if type(top_k) is not int or not 1 <= top_k <= 5:
        raise RetrievalError("invalid_request", "top_k must be an integer from 1 to 5.")
    if dataset_version != dataset.dataset_version:
        raise RetrievalError("dataset_version_mismatch", "The requested dataset version is not loaded.")
    if artwork_id not in dataset.artworks:
        raise RetrievalError("artwork_not_found", "The requested artwork is not in the dataset.")

    normalized_query = " ".join(unicodedata.normalize("NFKC", query).lower().split())
    topics = _query_topics(normalized_query)
    ranked_chunks = []
    for chunk in dataset.chunks:
        if chunk["artwork_id"] != artwork_id:
            continue
        score = len(topics.intersection(chunk["topics"]))
        if score:
            ranked_chunks.append((score, chunk))
    ranked_chunks.sort(key=lambda item: (-item[0], item[1]["chunk_id"]))

    evidence = []
    for _, chunk in ranked_chunks[:top_k]:
        source = dataset.sources[chunk["source_id"]]
        evidence.append(
            {
                "chunk_id": chunk["chunk_id"],
                "artwork_id": chunk["artwork_id"],
                "source_id": chunk["source_id"],
                "source_title": source["source_title"],
                "source_url": source["source_url"],
                "source_language": source["source_language"],
                "source_locator": chunk["source_locator"],
                "excerpt": chunk["excerpt"],
                "license": chunk["license"],
                "license_url": chunk["license_url"],
                "attribution": chunk["attribution"],
                "normalization": chunk["normalization"],
            }
        )
    return {"dataset_version": dataset.dataset_version, "evidence": evidence}
