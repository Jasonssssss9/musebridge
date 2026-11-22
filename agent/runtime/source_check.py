"""Bind artwork-fact answers to statements from the accepted evidence snapshot."""
from copy import deepcopy

from jsonschema import ValidationError

from agent.prompts.v2.readings import source_statements, statement_sentence, uses_source_checks
from agent.runtime.spatial_support import missing_spatial_detail
from agent.runtime.social_reply import bind_social_acts, with_social_acts
from agent.runtime.validation import ContractViolation, StrictValidator


def _checked_requests(payload):
    return [r["id"] for r in payload.get("requests", [])
            if r["kind"] == "artwork_fact" and r["resolution"] == "clear"]


def _object(fields):
    return {"type": "object", "properties": fields, "required": list(fields),
            "additionalProperties": False}


def assessment_schema(schema, payload):
    """Express source selection inside the normal assessment/final model call."""
    if (schema.get("$ref") not in ("#/$defs/AssessmentDecision", "#/$defs/FinalDecision")
            or payload.get("translation") is not None):
        return schema
    conversational = any(r["kind"] == "conversation" and r["resolution"] == "clear" for r in payload["requests"])
    if conversational:
        schema = with_social_acts(schema, "FinishDecision")
    checked = _checked_requests(payload)
    if not checked:
        return schema
    statements = source_statements(payload["evidence"], payload["answer_language"], payload["catalog_facts"])
    if not statements:
        return schema

    check = _object({
        "request_id": {"type": "string", "enum": checked},
        "requested_detail": {"type": "string", "minLength": 1, "maxLength": 300},
        "evidence_relation": {"type": "string", "enum": ["direct_answer", "related_only", "not_addressed"]},
        "statement_ids": {"type": "array", "items": {"type": "string", "enum": list(statements)},
                          "maxItems": len(statements), "uniqueItems": True},
    })
    fields = {
        "action": {"type": "string", "const": "finish"},
        "source_checks": {"type": "array", "minItems": len(checked), "maxItems": len(checked),
                          "items": check},
    }
    other = len(payload["requests"]) - len(checked)
    if other:
        fields["other_results"] = {"type": "array", "minItems": other, "maxItems": other,
                                   "items": {"$ref": "#/$defs/RequestResult"}}
    if conversational:
        fields["conversation_acts"] = {"$ref": "#/$defs/ConversationActs"}
    fields["memory_update"] = {"$ref": "#/$defs/MemoryUpdate"}
    wire = deepcopy(schema)
    wire["$defs"]["FinishDecision"] = _object(fields)
    return wire


def expand_assessment(decision, payload, schema):
    """Build each public result and citation from the model's scoped selection."""
    if not isinstance(decision, dict) or decision.get("action") != "finish":
        return decision
    if not uses_source_checks(schema):
        if "ConversationActs" in schema.get("$defs", {}):
            return bind_social_acts(decision, payload["requests"], payload["answer_language"],
                                    payload["translation"], payload)
        return decision
    try:
        StrictValidator(schema).validate(decision)
    except ValidationError as error:
        raise ContractViolation("The source assessment does not satisfy its model contract.") from error
    checks = decision["source_checks"]
    checked = _checked_requests(payload)
    if [c["request_id"] for c in checks] != checked:
        raise ContractViolation("Source assessments must cover the clear artwork requests in order.")
    language = payload["answer_language"]
    statements = source_statements(payload["evidence"], language, payload["catalog_facts"])
    other_ids = [r["id"] for r in payload["requests"] if r["id"] not in checked]
    other = decision.get("other_results", [])
    if [part["request_id"] for part in other] != other_ids:
        raise ContractViolation("Other results must cover the remaining requests in order.")
    parts = {part["request_id"]: part for part in other}
    intents = {r["id"]: r["intent"] for r in payload["requests"]}
    for check in checks:
        request_id = check["request_id"]
        if check["evidence_relation"] == "direct_answer":
            if not check["statement_ids"]:
                raise ContractViolation("A direct answer requires a source statement.")
            rows = [statements[key] for key in check["statement_ids"]]
            english = [statements[key]["english"] for key in check["statement_ids"] if key.startswith("s")]
            if missing_spatial_detail(intents[request_id], english):
                rows = []
        else:
            rows = []
        if rows:
            ids = list(dict.fromkeys(row["citation_id"] for row in rows))
            text = " ".join(statement_sentence(row["wording"], language) for row in rows)
            parts[request_id] = {"request_id": request_id, "support": "supported",
                                 "text": text, "citation_ids": ids}
        else:
            # Related wording can justify the comparison but cannot justify a
            # supported result. The final renderer localizes the scoped gap.
            parts[request_id] = {"request_id": request_id, "support": "insufficient",
                                 "text": "Unable to verify this detail here.", "citation_ids": []}
    result = {"action": "finish", "results": [parts[r["id"]] for r in payload["requests"]],
              "memory_update": decision["memory_update"]}
    if "ConversationActs" in schema["$defs"]:
        result["conversation_acts"] = decision["conversation_acts"]
        return bind_social_acts(result, payload["requests"], language, payload["translation"], payload)
    return result
