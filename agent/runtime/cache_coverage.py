"""Check whether a factual rewrite asks for a source category missing from the cache."""
from agent.runtime.validation import normalized_query
from knowledge.dataset import CHUNK_TOPICS, DATASET_VERSION
from knowledge.retrieval import requested_topics, source_topics


def needs_scoped_lookup(queries, payload):
    """Use released topic metadata only to veto reuse of an incomplete evidence set."""
    if payload.get("complete_scope") or not payload.get("cached_evidence") or not payload.get("catalog_facts"):
        return False
    catalog = payload["catalog_facts"][0]
    if catalog["dataset_version"] != DATASET_VERSION:
        return False
    if len({normalized_query(item["query"]) for item in queries}) != len(queries):
        return False

    cached_topics = set()
    for record in payload["cached_evidence"]:
        cached_topics.update(source_topics(CHUNK_TOPICS[record["chunk_id"]], record["source_locator"]))
    artwork_id = catalog["artwork_id"]
    artist = payload["artwork_context"]["artist"]
    return any(requested_topics(item["query"], artwork_id, artist) - cached_topics for item in queries)
