"""Select reviewed evidence for explicit requests in one artwork scope."""

import unicodedata

from .dataset import Dataset
from .errors import RetrievalError
from .query import parse_requests


# These are capabilities of the reviewed seed topics. For example, material
# covers the medium and support families, but says nothing about fiber,
# dimensions, pigment identities, or an artist's choice of materials.
_FACT_TOPICS = {
    "medium": "material",
    "paint_medium": "material",
    "painting_support": "material",
    "brushwork_appearance": "brushwork",
    "color_appearance": "color",
    "line_recession": "perspective",
    "visual_effect": "energy",
    "background_objects": "background",
}


def retrieve(
    dataset: Dataset,
    *,
    artwork_id: str,
    query: str,
    dataset_version: str,
    top_k: int = 2,
) -> dict:
    """Return ranked evidence without the evaluation-only request trace."""
    result, _ = retrieve_with_trace(
        dataset, artwork_id=artwork_id, query=query,
        dataset_version=dataset_version, top_k=top_k,
    )
    return result


def retrieve_with_trace(
    dataset: Dataset,
    *,
    artwork_id: str,
    query: str,
    dataset_version: str,
    top_k: int = 2,
) -> tuple[dict, dict]:
    """Return evidence and its request bindings over the normalized query."""
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

    normalized_query, requests = parse_requests(query, artwork_id)
    scoped_chunks = {
        chunk["chunk_id"]: chunk
        for chunk in dataset.chunks if chunk["artwork_id"] == artwork_id
    }
    ranking_topics = {chunk_id: set() for chunk_id in scoped_chunks}
    trace_requests = []
    for request in requests:
        topic = _FACT_TOPICS.get(request.facet)
        chunk_ids = []
        if request.artwork_id != artwork_id:
            status = "out_of_scope"
        elif request.facet == "unknown":
            status = "unknown_intent"
        else:
            chunk_ids = [key for key, chunk in scoped_chunks.items() if topic in chunk["topics"]]
            status = "supported" if chunk_ids else "insufficient_evidence"
            for chunk_id in chunk_ids:
                ranking_topics[chunk_id].add(topic)
        trace_requests.append({
            "start": request.start,
            "end": request.end,
            "artwork_id": request.artwork_id,
            "facet": request.facet,
            "status": status,
            "chunk_ids": chunk_ids,
        })

    selected = sorted(
        (key for key, topics in ranking_topics.items() if topics),
        key=lambda key: (-len(ranking_topics[key]), key),
    )[:top_k]
    for request in trace_requests:
        candidates = request["chunk_ids"]
        request["chunk_ids"] = [key for key in selected if key in candidates]

    evidence = []
    for chunk_id in selected:
        chunk = scoped_chunks[chunk_id]
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
    result = {"dataset_version": dataset.dataset_version, "evidence": evidence}
    return result, {"normalized_query": normalized_query, "requests": trace_requests}
