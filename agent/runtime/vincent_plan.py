"""Model-selected Vincent planning actions and their lossless wire projection."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from agent.contracts.codec import v3_validator
from agent.runtime.validation import ContractViolation
from agent.runtime.vincent_search import query_fields, render_query


_PROMPTS = Path(__file__).resolve().parents[1] / "prompts" / "vincent"
_KINDS = ("artwork_fact", "catalog_fact", "conversation", "general_art_knowledge", "outside_scope")
_CATALOG = {"give_title": "title", "name_artist": "artist", "give_catalog_date": "date_display"}
_TEXT = {"type": "string", "minLength": 1, "maxLength": 2000}
_INTENT = {"type": "string", "minLength": 1, "maxLength": 1000}


def _obj(**properties):
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


def _choice(*values):
    return {"type": "string", "enum": list(values)}


def _array(item, low, high, *, unique=False):
    result = {"type": "array", "items": item, "minItems": low, "maxItems": high}
    if unique:
        result["uniqueItems"] = True
    return result


def _ref(name):
    return {"$ref": "#/$defs/" + name}


def _route(action, **props):
    return _obj(action={"const": action}, **props)


def _last_settled(payload):
    return next((t for t in reversed(payload["recent_turns"]) if t["answer_status"] == "answered"), None)


def _schema(payload):
    # Language instructions/translation are the existing exact-source wire types.
    defs = v3_validator().schema("LanguageDecision")["$defs"]
    language = defs["LanguageDecision"]
    del language["properties"]["answer_language"]
    language["required"].remove("answer_language")
    defs["Writing"] = _obj(
        topic={"oneOf": [{"type": "string", "minLength": 1, "maxLength": 300}, {"type": "null"}]},
        remember={"type": "string", "maxLength": 1000},
    )
    had_pending = payload["conversation_context"]["pending_clarification"] is not None
    pending_source = _choice("current_message", "pending_clarification") if had_pending else {"const": "current_message"}
    # Without an earlier clarification, keep versus clear has identical state effects.
    terminal = {"writing": _ref("Writing")}
    if had_pending:
        terminal["pending"] = _choice("keep", "clear")
    fragment = {"type": "string", "minLength": 1, "maxLength": 500}
    base_part = {"fragment": fragment, "intent": _INTENT}
    types = (*_KINDS, *("clarify_" + kind for kind in _KINDS))
    defs["Part"] = _obj(**base_part, kind=_choice(*types))
    # The compound form preserves independent requests, literal spans and shared queries.
    requests = _array(_ref("Part"), 2, 4)
    linked = _obj(parts=_array({"type": "integer", "minimum": 1, "maximum": 4}, 1, 4, unique=True),
                  **query_fields())
    lookup = []
    if not payload["complete_scope"]:
        lookup += [_route("search", **query_fields()),
                   _route("search_multiple", requests=requests, queries=_array(linked, 1, 2))]
    if payload["cached_evidence"] and _last_settled(payload) is not None:
        witness = _obj(answer_quote=_TEXT, passage=_choice(*(r["chunk_id"] for r in payload["cached_evidence"])),
                       source_quote=_TEXT, establishes=_INTENT)
        lookup += [_route("repeat_verified", visitor_request=fragment, support=_array(witness, 1, 4)),
                   _route("repeat_verified_multiple", visitor_request=fragment, requests=requests,
                          support=_array(_obj(part={"type": "integer", "minimum": 1, "maximum": 4},
                                              witness=witness), 1, 8))]
    if payload["complete_scope"] and payload["cached_evidence"]:
        lookup += [_route("review_complete_scope"), _route("review_complete_scope_multiple", requests=requests)]
    terminal_branches = [
        _route("acknowledge"),
        *[_route(name) for name in _CATALOG],
        _route("general_art_knowledge", reply=_TEXT, **terminal),
        _route("clarify", reply=_TEXT, pending_source=pending_source, writing=_ref("Writing")),
        _route("outside_scope", reply=_TEXT, **terminal),
    ]
    defs["ReplyPart"] = {"oneOf": [
        _obj(**base_part, kind=_choice("acknowledge", "general_art_knowledge", "outside_scope", *_CATALOG)),
        _obj(**base_part, kind=_choice(*("clarify_" + kind for kind in _KINDS)), pending_source=pending_source),
    ]}
    terminal_branches.append(_route("reply_multiple", requests=_array(_ref("ReplyPart"), 2, 4)))
    defs["VincentPlan"] = _obj(intent=_INTENT, next={"oneOf": lookup + terminal_branches},
                              language=_ref("LanguageDecision"))
    return {"$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": "#/$defs/VincentPlan", "$defs": defs}


def model_call(payload):
    """Return the exact planner prompt and model-output schema for this fitted turn."""
    schema = _schema(payload)
    context = payload["conversation_context"]
    data = {
        "scope": payload["scope"],
        "artwork": payload["artwork_context"],
        "catalog": {item["field"]: item["value"] for item in payload["catalog_facts"]},
        "available_source_kinds": payload["available_source_kinds"],
        "complete_scope": payload["complete_scope"],
        "language_override": payload["language_override"],
        "conversation_context": context,
        "recent_turns": [{key: turn[key] for key in ("sequence", "question", "answer_text")}
                         for turn in payload["recent_turns"]],
        "repeatable_answer_sequence": (_last_settled(payload) or {}).get("sequence"),
        "cached_evidence": [{"ref": item["chunk_id"], "source": item["source_title"], "kind": item["source_kind"],
                             "locator": item["source_locator"], "passage": item["excerpt"]}
                            for item in payload["cached_evidence"]],
        "original_question": payload["original_question"],
    }
    policy = "\n\n".join((_PROMPTS / name).read_text(encoding="utf-8").strip()
                         for name in ("dialogue.md", "plan.md"))
    return schema, [{"role": "system", "content": policy},
                    {"role": "user", "content": json.dumps(data, ensure_ascii=False, allow_nan=False)}]


def _request(index, fragment, intent, kind):
    unresolved = kind.startswith("clarify_")
    kind = kind.removeprefix("clarify_")
    if kind in _CATALOG:
        kind = "catalog_fact"
    elif kind == "acknowledge":
        kind = "conversation"
    return {"id": f"r{index}", "original_fragment": fragment, "intent": intent,
            "kind": kind, "resolution": "needs_clarification" if unresolved else "clear"}


def _check_witnesses(decision, requests, payload):
    cache = {record["chunk_id"]: record for record in payload["cached_evidence"]}
    previous = _last_settled(payload)
    repeat_span = decision["visitor_request"]
    if previous is None or not repeat_span.strip() or repeat_span not in payload["original_question"]:
        raise ContractViolation("A repeat must point to the current visitor's request and a settled answer.")
    if decision["action"] == "repeat_verified":
        witnesses = [(1, proof) for proof in decision["support"]]
    else:
        witnesses = [(proof["part"], proof["witness"]) for proof in decision["support"]]
    covered = set()
    for part, proof in witnesses:
        record = cache.get(proof["passage"])
        answer_quote, quote = proof["answer_quote"], proof["source_quote"]
        if not answer_quote.strip() or answer_quote not in previous["answer_text"]:
            raise ContractViolation("The repeated finding must quote the last settled answer.")
        if record is None or not quote.strip() or not any(
                quote in record[field] for field in ("excerpt", "source_locator")):
            raise ContractViolation("A cache witness must quote its selected passage or locator exactly.")
        covered.add(part)
    expected = {i for i, item in enumerate(requests, 1)
                if item["kind"] == "artwork_fact" and item["resolution"] == "clear"}
    if expected != covered:
        raise ContractViolation("Cache witnesses must cover the clear factual requests.")


def needs_direct_writer(raw):
    return raw["next"]["action"] in (*_CATALOG, "acknowledge", "reply_multiple")


def _direct_parts(raw, payload):
    route = raw["next"]
    if route["action"] == "reply_multiple":
        return route["requests"]
    return [{"kind": route["action"], "fragment": payload["original_question"], "intent": raw["intent"]}]


def direct_writer_call(raw, payload):
    """A social turn has only the visitor's words; identity replies see only selected fields."""
    parts = {}
    shape = {}
    for i, part in enumerate(_direct_parts(raw, payload), 1):
        key = f"r{i}"
        item = {k: part[k] for k in ("fragment", "kind", "intent")}
        reply_shape = {"type": "string", "minLength": 1, "maxLength": 400}
        if part["kind"] == "acknowledge":
            # Keep the character ceiling in strict validation, outside Ollama's string grammar.
            clause = {"type": "string", "allOf": [{"minLength": 1, "maxLength": 399, "pattern": r"\S"}]}
            shape[key] = _obj(clause=clause, ending=_choice(".", "!", "?"))
        elif part["kind"] in _CATALOG:
            field = _CATALOG[part["kind"]]
            record = next(f for f in payload["catalog_facts"] if f["field"] == field)
            item["catalog"] = {field: record["value"]}
            shape[key] = _obj(source_value={"oneOf": [{"const": record["value"]}, {"type": "null"}]},
                              reply=reply_shape)
        else:
            shape[key] = _obj(reply=reply_shape)
        parts[key] = item
    properties = {
        "parts": _obj(**shape),
        "topic": {"oneOf": [{"type": "string", "minLength": 1, "maxLength": 300}, {"type": "null"}]},
        "remember": {"type": "string", "maxLength": 1000},
    }
    if payload["conversation_context"]["pending_clarification"] is not None:
        properties["pending"] = _choice("keep", "clear")
    schema = {"$ref": "#/$defs/VincentDirectReply", "$defs": {"VincentDirectReply": _obj(**properties)}}
    data = {"requests": parts, "original_question": payload["original_question"]}
    prior = payload["conversation_context"]["pending_clarification"]
    if prior is not None:
        data["pending_question"] = prior["question"]
    policy = (_PROMPTS / "direct.md").read_text(encoding="utf-8").strip()
    return schema, [{"role": "system", "content": policy},
                    {"role": "user", "content": json.dumps(data, ensure_ascii=False, allow_nan=False)}]


def with_direct_writing(raw, written):
    """Carry model-authored prose verbatim. Bind affirmative identity replies to their actual values."""
    result = deepcopy(raw)
    route = result["next"]
    parts = route["requests"] if route["action"] == "reply_multiple" else [route]
    for i, part in enumerate(parts, 1):
        text = written["parts"][f"r{i}"]
        if "clause" in text:
            clause, ending = text["clause"], text["ending"]
            if clause != clause.strip() or (clause[-1] in ".!?…" and not clause.endswith(ending)):
                raise ContractViolation("The social clause and its selected ending disagree.")
            part["reply"] = clause if clause.endswith(ending) else clause + ending
        else:
            part["reply"] = text["reply"]
        if "source_value" in text:
            value = text["source_value"]
            if value is not None and value.casefold() not in text["reply"].casefold():
                raise ContractViolation("A supported catalog reply must state the cited value.")
            field = _CATALOG[part.get("kind", route["action"])]
            part["fields"] = [field] if value is not None else []
    route["writing"] = {"topic": written["topic"], "remember": written["remember"]}
    if "pending" in written:
        route["pending"] = written["pending"]
    return result


def canonical_decision(raw, payload):
    """Restore deterministic wire IDs and fields implied by model-selected actions."""
    decision = raw["next"]
    action = decision["action"]
    out = {"language_decision": {**raw["language"], "answer_language": "en"}}
    multiple = action.endswith("_multiple")
    if action in ("clarify", "outside_scope"):
        out.update(action="clarify" if action == "clarify" else "out_of_scope", text=decision["reply"])
    else:
        if multiple:
            parts = decision["requests"]
            requests = [_request(i, part["fragment"], part["intent"], part["kind"])
                        for i, part in enumerate(parts, 1)]
        else:
            kind = "artwork_fact" if action in ("search", "repeat_verified", "review_complete_scope") else action
            parts = [decision]
            requests = [_request(1, payload["original_question"], raw["intent"], kind)]
        out["requests"] = requests
        if action in ("search", "search_multiple"):
            if multiple:
                queries = [(render_query(x), [f"r{i}" for i in x["parts"]]) for x in decision["queries"]]
            else:
                queries = [(render_query(decision), ["r1"])]
            return {**out, "action": "retrieve", "queries": [
                {"id": f"q{i}", "query": query, "request_ids": request_ids}
                for i, (query, request_ids) in enumerate(queries, 1)]}
        if action in ("repeat_verified", "repeat_verified_multiple"):
            _check_witnesses(decision, requests, payload)
            return {**out, "action": "answer_from_context"}
        if action in ("review_complete_scope", "review_complete_scope_multiple"):
            return {**out, "action": "answer_from_context"}
        out["action"] = "direct_response"
        by_field = {item["field"]: item["fact_id"] for item in payload["catalog_facts"]}
        results = []
        for req, part in zip(requests, parts):
            ids = [by_field[field] for field in part.get("fields", [])]
            if req["resolution"] == "needs_clarification":
                support = "clarification_needed"
            elif req["kind"] == "catalog_fact":
                support = "supported" if ids else "insufficient"
            elif req["kind"] == "outside_scope":
                support = "out_of_scope"
            else:
                support = "not_applicable"
            results.append({"request_id": req["id"], "support": support, "text": part["reply"], "citation_ids": ids})
        out["results"] = results
    pending = {"action": decision.get("pending", "keep")}
    if action == "clarify":
        pending = {"action": "set", "source": decision["pending_source"], "request_id": None}
    elif action == "reply_multiple":
        for req, part in zip(out["requests"], decision["requests"]):
            if "pending_source" in part:
                pending = {"action": "set", "source": part["pending_source"], "request_id": req["id"]}
    writing = decision["writing"]
    out["memory_update"] = {"active_topic": writing["topic"], "continuity_summary": writing["remember"],
                            "pending_update": pending}
    return out
