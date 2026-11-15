"""Pure snapshot checks for transport consumers; no admission or persistence.

The Agent's existing validators remain the source of model-decision semantics.
These helpers bind terminal results to immutable run inputs. They cannot prove
fresh-tool provenance, actual execution counts or factual entailment.
"""
from copy import deepcopy
import unicodedata
from urllib.parse import urlsplit

from agent.runtime.context_v2 import validate_run_context
from agent.runtime.validation import ContractViolation
from agent.runtime.validation_v2 import (
    ContractValidatorV2, assemble_answer, resolve_language,
)

_VALIDATOR = ContractValidatorV2()


def _check(condition: bool) -> None:
    if not condition:
        raise ContractViolation("Snapshot relationships violate the contract.")


def check_run_request(run: dict, manifest) -> dict:
    """Reuse C's canonical input validation; deadline admission is separate."""
    return validate_run_context(run, manifest, validator=_VALIDATOR)


def _projection(definition: str, value: dict) -> dict:
    properties = _VALIDATOR.schema(definition)["$defs"][definition]["properties"]
    return {key: value[key] for key in properties if key in value}


def _source_url(value: str) -> bool:
    try:
        url = urlsplit(value)
        return (url.scheme == "https" and url.hostname in {"api.artic.edu", "www.artic.edu"}
                and url.port in (None, 443) and url.username is None
                and url.password is None and not url.fragment)
    except ValueError:
        return False


def check_run_result(run: dict, result: dict) -> dict:
    """Validate a result against supplied snapshots without advancing state.

    Call check_run_request at admission. Source hash/catalog reconstruction and
    current-revision/deadline guards belong to their respective owners.
    """
    _VALIDATOR.validate("RunRequest", run)
    _VALIDATOR.validate("RunResult", result)
    state = run["conversation_state"]
    _check(result["scope"] == run["scope"])
    _check(result["conversation_update"]["base_revision"] == state["revision"])
    _check(result["catalog_snapshot"] == run["context"]["catalog_facts"])
    language = resolve_language(run, result["language_decision"])
    _check(result["answer_language"] == language["answer_language"])
    execution = result["execution"]
    queries = result["executed_queries"]
    _check(execution["retrieval_calls"] == len(queries))
    _check((execution["refinement_rounds"] == 1) == (execution["model_calls"] == 3))
    _check(not queries or execution["model_calls"] >= 2)
    requests = {item["id"]: item for item in result["requests"]}
    _check(list(requests) == [f"r{i}" for i in range(1, len(result["requests"]) + 1)])
    seen = set()
    for index, query in enumerate(queries, 1):
        text = " ".join(unicodedata.normalize("NFKC", query["query"]).casefold().split())
        _check(query["id"] == f"q{index}" and text not in seen
               and query["query"] == query["query"].strip())
        seen.add(text)
        _check(all(identifier in requests and requests[identifier]["kind"] == "artwork_fact"
                   and requests[identifier]["resolution"] == "clear"
                   for identifier in query["request_ids"]))
    evidence = result["evidence_snapshot"]
    by_id = {}
    licenses = {"CC0-1.0": "https://creativecommons.org/publicdomain/zero/1.0/",
                "CC-BY-4.0": "https://creativecommons.org/licenses/by/4.0/"}
    for record in evidence:
        _check(record["chunk_id"] not in by_id and record["artwork_id"] == run["scope"]["artwork_id"]
               and "kind" not in record and "fact_id" not in record
               and _source_url(record["source_url"])
               and licenses[record["license"]] == record["license_url"]
               and record["attribution"] == "Art Institute of Chicago")
        by_id[record["chunk_id"]] = _projection("Evidence", record)
    cached = state["verified_evidence"]
    _check(result["reused_chunk_ids"] == [record["chunk_id"] for record in cached])
    for record in cached:
        _check(by_id.get(record["chunk_id"]) == _projection("Evidence", record))

    # Reconstruct only a validation envelope, not a claim about the model's
    # original route. The C validator checks parts, language, sources and pending.
    language_decision = result["language_decision"]
    memory = result["conversation_update"]["memory"]
    parts = result["request_results"]
    if not result["requests"]:
        action = "clarify" if result["answer"]["status"] == "clarification_needed" else "out_of_scope"
        plan = {"action": action, "language_decision": language_decision,
                "text": result["answer"]["text"], "memory_update": memory}
        decision = plan
    elif parts and not any(item["kind"] == "artwork_fact" and item["resolution"] == "clear"
                           for item in result["requests"]):
        plan = {"action": "direct_response", "language_decision": language_decision,
                "requests": result["requests"], "results": parts, "memory_update": memory}
        decision = plan
    else:
        plan = {"action": "retrieve" if queries else "answer_from_context",
                "language_decision": language_decision, "requests": result["requests"]}
        if queries:
            plan["queries"] = queries[:2]
        decision = ({"action": "finish", "results": parts, "memory_update": memory} if parts else
                    {"action": "clarify", "text": result["answer"]["text"], "memory_update": memory})
    answer, expected_parts, expected_memory = assemble_answer(
        run, plan, decision, evidence, result["catalog_snapshot"], _VALIDATOR)
    _check(_projection_answer(answer) == _projection_answer(result["answer"]))
    _check(expected_parts == parts and expected_memory == memory)
    return deepcopy(result)


def _projection_answer(answer: dict) -> dict:
    return {"status": answer["status"], "text": answer["text"],
            "citations": [_projection("CatalogFact" if item.get("kind") == "catalog" else "Evidence", item)
                          for item in answer["citations"]]}
