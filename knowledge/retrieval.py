"""Select reviewed evidence for explicit requests in one artwork scope."""

import unicodedata

from .dataset import Dataset
from .errors import RetrievalError
from .query import parse_requests, scoped_vector_query


# These are capabilities of the reviewed seed topics. For example, material
# covers the medium and support families, but says nothing about fiber,
# dimensions, pigment identities, or an artist's choice of materials.
_FACT_TOPICS = {
    "description_overview": "description",
    "medium": "material",
    "paint_medium": "material",
    "painting_support": "material",
    "brushwork_appearance": "brushwork",
    "color_appearance": "color",
    "line_recession": "perspective",
    "visual_effect": "energy",
    "background_objects": "background",
}


def source_topics(topics, source_locator):
    """A reviewed museum description also supports a request for a general introduction."""
    if source_locator.partition(",")[0] == "data.description":
        return frozenset(topics) | {"description"}
    return frozenset(topics)


def requested_topics(query: str, artwork_id: str, artist: str | None) -> frozenset[str]:
    """Identify released topic categories in a query without accessing any records."""
    _, requests = parse_requests(query, artwork_id, active_artist=artist)
    return frozenset(_FACT_TOPICS[item.facet] for item in requests
                     if item.artwork_id == artwork_id and item.facet in _FACT_TOPICS)


# Candidate relevance only. Qwen determines what the returned passage actually supports.
DENSE_MIN_COSINE = 0.30
RRF_OFFSET = 60


def fuse_rankings(lexical, dense):
    """Fuse ordinal ranks without comparing topic counts with cosine scores."""
    lexical_positions = {key: rank for rank, key in enumerate(lexical, 1)}
    dense_positions = {key: rank for rank, key in enumerate(dense, 1)}

    def order(key):
        score = 0.0
        for positions in (lexical_positions, dense_positions):
            if key in positions:
                score += 1 / (RRF_OFFSET + positions[key])
        return (-score, lexical_positions.get(key, float("inf")), dense_positions.get(key, float("inf")), key)

    return sorted(lexical_positions.keys() | dense_positions.keys(), key=order)


def validate_request(dataset, *, artwork_id, query, dataset_version, top_k):
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


def retrieve(
    dataset: Dataset,
    *,
    artwork_id: str,
    query: str,
    dataset_version: str,
    top_k: int = 2,
    vector_index=None,
) -> dict:
    """Return ranked evidence without the evaluation-only request trace."""
    result, _ = retrieve_with_trace(
        dataset, artwork_id=artwork_id, query=query,
        dataset_version=dataset_version, top_k=top_k, vector_index=vector_index,
    )
    return result


def retrieve_with_trace(
    dataset: Dataset,
    *,
    artwork_id: str,
    query: str,
    dataset_version: str,
    top_k: int = 2,
    vector_index=None,
) -> tuple[dict, dict]:
    """Return evidence and its request bindings over the normalized query."""
    validate_request(dataset, artwork_id=artwork_id, query=query, dataset_version=dataset_version, top_k=top_k)

    scoped_chunks = {
        chunk["chunk_id"]: chunk
        for chunk in dataset.chunks if chunk["artwork_id"] == artwork_id
    }
    if dataset.dataset_version.startswith("getty-"):
        from .passage_ranking import rank
        selected, lexical, dense_hits = rank(dataset, artwork_id, query, vector_index)
        normalized_query, trace_requests = query, []
    else:
        selected, lexical, dense_hits, normalized_query, trace_requests = _rank_legacy(
            dataset, artwork_id, query, vector_index, scoped_chunks,
        )
    selected = selected[:top_k]
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
                **({"source_kind": source["source_kind"]} if "source_kind" in source else {}),
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
    trace = {"normalized_query": normalized_query, "requests": trace_requests}
    if vector_index is not None:
        trace["lexical_ranking"] = lexical
        trace["dense_ranking"] = [{"chunk_id": key, "cosine": score} for key, score in dense_hits]
    return result, trace



def _rank_legacy(dataset, artwork_id, query, vector_index, scoped_chunks):
    normalized_query, requests = parse_requests(query, artwork_id, active_artist=dataset.artworks[artwork_id]["artist"])
    available_topics = {key: source_topics(chunk["topics"], chunk["source_locator"])
                        for key, chunk in scoped_chunks.items()}
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
            chunk_ids = [key for key, topics in available_topics.items() if topic in topics]
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

    lexical = sorted(
        (key for key, topics in ranking_topics.items() if topics),
        key=lambda key: (-len(ranking_topics[key]), key),
    )
    selected = lexical
    dense_hits = []
    if vector_index is not None:
        # Some explicit facets (e.g. pigment identity) fall outside the reviewed seed.
        # An unknown paraphrase has no such exclusion and can be recovered by vectors.
        gaps, searchable = set(), set()
        for request in requests:
            if request.artwork_id == artwork_id:
                span = (request.start, request.end)
                if request.facet == "unknown" or request.facet in _FACT_TOPICS:
                    searchable.add(span)
                else:
                    gaps.add(span)
        search_text = scoped_vector_query(normalized_query, requests, dataset.artworks[artwork_id],
                                          excluded_spans=gaps - searchable)
        if search_text:
            dense_hits = vector_index.rank(artwork_id, search_text)
            eligible = [key for key, score in dense_hits if key in scoped_chunks and score >= DENSE_MIN_COSINE]
            selected = fuse_rankings(lexical, eligible)
    return selected, lexical, dense_hits, normalized_query, trace_requests
