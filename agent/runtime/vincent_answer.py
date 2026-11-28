"""A small, source-bound model exchange for the Vincent assessment and final nodes.

The model writes every spoken line, its sources and the next memory. The adapter
validates its selected persistent source IDs and expands the v3 decision fields.
"""
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from agent.runtime.errors import WorkflowError
from agent.runtime.source_spans import literal_spans as _spans
from agent.runtime.vincent_search import query_fields, render_query

PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "vincent" / "answer.md"
SOURCE_VOICES = {
    "artist_letter": "Distinguish the letter writer from anyone the writer quotes.",
    "museum_record": "Museum account or named speaker quoted by the museum.",
    "guide_note": "MuseBridge guide's present-day suggestion for the visitor.",
}


def _object(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _text(limit):
    return {"type": "string", "minLength": 1, "maxLength": limit, "pattern": r"\S"}


def _catalog_records(payload):
    if any(r["kind"] == "catalog_fact" for r in payload["requests"]):
        return payload["catalog_facts"]
    return []


def _request_ids(choices):
    return {"type": "array", "items": {"type": "string", "enum": choices},
            "minItems": 1, "maxItems": len(choices), "uniqueItems": True}


def _cards(payload, fresh_ids=None):
    cards = {}
    for record in payload["evidence"]:
        ref = record["chunk_id"]
        card = {"ref": ref, "kind": record["source_kind"], "source": record["source_title"],
                "voice": SOURCE_VOICES[record["source_kind"]], "locator": _spans(record["source_locator"], "l"),
                "credit": record["attribution"], "passage": _spans(record["excerpt"], "p")}
        if fresh_ids is not None:
            card["availability"] = "current search" if ref in fresh_ids else "verified earlier citation"
        cards[ref] = card
    for fact in _catalog_records(payload):
        ref = fact["fact_id"]
        cards[ref] = {"ref": ref, "kind": "catalog_fact", "voice": "Neutral catalog metadata.",
                      "field": fact["field"], "value": {"v1": fact["value"]}}
    return cards


def _finding_schema(card):
    choices = list(card.get("passage", {})) + list(card.get("locator", {})) + list(card.get("value", {}))
    spans = {"type": "array", "items": {"type": "string", "enum": choices}, "minItems": 1,
             "maxItems": len(choices), "uniqueItems": True}
    return _object({"source": {"const": card["ref"]}, "spans": spans, "finding": _text(300)})


def _part_schema(request, sources, catalog):
    reply = {"reply": _text(2000)}
    if request["resolution"] != "clear" or request["kind"] not in ("artwork_fact", "catalog_fact"):
        return _object(reply)
    gap = _object({"unresolved": _text(300), **reply})
    eligible = sources if request["kind"] == "artwork_fact" else catalog
    if not eligible:
        return gap
    choices = [_finding_schema(card) for card in eligible]
    finding = choices[0] if len(choices) == 1 else {"oneOf": choices}
    grounded = _object({"grounds": {"type": "array", "items": finding, "minItems": 1, "maxItems": len(eligible),
                                    "uniqueItems": True}, **reply})
    return {"oneOf": [grounded, gap]}


def _schema(payload, cards):
    sources = [card for card in cards.values() if card["kind"] != "catalog_fact"]
    catalog = [card for card in cards.values() if card["kind"] == "catalog_fact"]
    requests = payload["requests"]
    parts = {request["id"]: _part_schema(request, sources, catalog) for request in requests}
    memory = {"topic": _text(300), "remember": _text(1000)}
    local_clarification = any(r["resolution"] == "needs_clarification" for r in requests)
    prior_pending = payload["conversation_context"]["pending_clarification"] is not None
    answer = {"action": {"const": "answer"}, "parts": _object(parts), **memory}
    if prior_pending:
        choices = ["current_message", "pending_clarification"] if local_clarification else ["keep", "clear"]
        answer["pending"] = {"enum": choices}
    branches = [_object(answer)]

    # Clear intent is settled in planning. Exact translation can still need a source/length clarification.
    if payload.get("translation"):
        ask = {"action": {"const": "ask"}, "question": _text(2000), **memory}
        if prior_pending:
            ask["pending"] = {"enum": ["current_message", "pending_clarification"]}
        branches.append(_object(ask))
    factual = [r["id"] for r in requests if r["resolution"] == "clear" and r["kind"] == "artwork_fact"]
    if factual and payload["allow_refinement"]:
        search = _object({"for": _request_ids(factual), **query_fields()})
        branches.append(_object({"action": {"const": "search_again"},
                                 "reason": {"enum": ["no_candidates", "poor_candidate_relevance", "uncovered_request"]},
                                 "searches": {"type": "array", "items": search, "minItems": 1, "maxItems": 2}}))
    return branches[0] if len(branches) == 1 else {"oneOf": branches}


def model_call(payload, *, fresh_ids=None):
    """Render only model-relevant content; keep the canonical full input for validation."""
    history = payload["recent_turns"]
    context = payload["conversation_context"]
    cards = _cards(payload, fresh_ids)
    data = {"scope": payload["scope"], "artwork": payload["artwork_context"],
            "recent_turns": [{"visitor": t["question"], "reply": t["answer_text"]} for t in history]}
    # A complete transcript already carries the conversation. An older summary can
    # supply a missing referent if the retained five-turn window starts later.
    if (not history or history[0]["sequence"] > 1) and context["continuity_summary"]:
        data["earlier_conversation"] = context["continuity_summary"]
    if context["pending_clarification"]:
        data["open_question"] = context["pending_clarification"]["question"]
    if payload.get("translation"):
        data["translation"] = payload["translation"]
    data["requests"] = [{"id": r["id"], "visitor_words": r["original_fragment"], "intent": r["intent"],
                         "kind": r["kind"], "resolution": r["resolution"]} for r in payload["requests"]]
    data["sources"] = list(cards.values())
    if payload["allow_refinement"]:
        data["searches_already_tried"] = [q["query"] for q in payload["executed_queries"]]
    data["visitor_question"] = payload["original_question"]
    return _schema(payload, cards), [{"role": "system", "content": PROMPT.read_text(encoding="utf-8")},
                              {"role": "user", "content": json.dumps(data, ensure_ascii=False)}]


def canonical_decision(raw, payload):
    """Resolve references and identifiers. Never generate, splice or repair a claim."""
    if not Draft202012Validator(_schema(payload, _cards(payload))).is_valid(raw):
        raise WorkflowError("generation_invalid_response", "The Vincent answer has invalid fields or sources.",
                            phase="assessment")
    action = raw["action"]
    if action == "search_again":
        next_index = len(payload["executed_queries"]) + 1
        queries = [{"id": f"q{number}", "request_ids": item["for"], "query": render_query(item)}
                   for number, item in enumerate(raw["searches"], next_index)]
        return {"action": "refine", "reason_code": raw["reason"],
                "request_ids": list(dict.fromkeys(rid for q in queries for rid in q["request_ids"])), "queries": queries}

    pending = {"action": raw.get("pending", "clear")}
    if action == "ask" or any(r["resolution"] == "needs_clarification" for r in payload["requests"]):
        linked = None if action == "ask" else next(r["id"] for r in payload["requests"]
                                                   if r["resolution"] == "needs_clarification")
        pending = {"action": "set", "source": raw.get("pending", "current_message"), "request_id": linked}
    memory = {"active_topic": raw["topic"], "continuity_summary": raw["remember"], "pending_update": pending}
    if action == "ask":
        return {"action": "clarify", "text": raw["question"], "memory_update": memory}

    results = []
    for request in payload["requests"]:
        part = raw["parts"][request["id"]]
        cited = list(dict.fromkeys(item["source"] for item in part.get("grounds", ())))
        if request["resolution"] == "needs_clarification":
            support = "clarification_needed"
        elif request["kind"] in ("artwork_fact", "catalog_fact"):
            support = "supported" if cited else "insufficient"
        else:
            support = "out_of_scope" if request["kind"] == "outside_scope" else "not_applicable"
        results.append({"request_id": request["id"], "support": support,
                        "text": part["reply"], "citation_ids": cited})
    return {"action": "finish", "results": results, "memory_update": memory}
