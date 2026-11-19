"""Model-only shorthand for one or two clear artwork facts in a follow-up."""
from copy import deepcopy
from functools import lru_cache
import re

from jsonschema import FormatChecker, ValidationError

from agent.runtime.validation import ContractViolation, StrictValidator
from agent.runtime.validation_v2 import ContractValidatorV2


# Keep the terminal punctuation and any closing quotation mark on the source.
# A point between digits belongs to the number (for example, 2.5).
_SENTENCE_END = re.compile(r"""(?:[!?。！？｡…]|(?<!\d)[.．]|[.．](?!\d))+["'”’»）)\]」』]*""")


def source_sentences(question: str) -> list[str]:
    """Return ordered literal slices of the current message, numbered from one."""
    start = 0
    sentences = []
    for stop in [*(match.end() for match in _SENTENCE_END.finditer(question)), len(question)]:
        sentence = question[start:stop].strip()
        if sentence:
            sentences.append(sentence)
        start = stop
    return sentences


def _object(properties):
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


def _branch(definitions, sentence_limit):
    source = {"oneOf": [
        _object({"sentence": {"type": "integer", "minimum": 1, "maximum": sentence_limit}}),
        _object({"excerpt": definitions["InterpretedRequest"]["properties"]["original_fragment"]}),
    ]}
    return _object({
        "visitor_task": {"type": "string", "const": "check_artwork_facts"},
        "language_decision": {"$ref": "#/$defs/LanguageDecision"},
        "evidence_action": {"type": "string", "enum": ["search", "reuse_cache"]},
        "facts": {"type": "array", "minItems": 1, "maxItems": 2, "items": _object({
            "source": source, "english_search_question": definitions["SearchQuery"]["properties"]["query"],
        })},
    })


def planning_schema(schema: dict, payload: dict) -> dict:
    if (schema.get("$ref") != "#/$defs/Plan" or not payload.get("recent_turns")
            or not payload.get("original_question", "").strip()):
        return schema
    result = deepcopy(schema)
    definitions = result["$defs"]
    fact_plan = _branch(definitions, len(source_sentences(payload["original_question"])))
    if payload.get("complete_scope") or not payload.get("cached_evidence"):
        route = "reuse_cache" if payload.get("complete_scope") else "search"
        fact_plan["properties"]["evidence_action"] = {"type": "string", "const": route}
    definitions["FollowupFactPlan"] = fact_plan
    definitions["StandardPlan"] = definitions["Plan"]
    definitions["OtherVisitorPlan"] = _object({
        "visitor_task": {"type": "string", "const": "other_request"},
        "plan": {"$ref": "#/$defs/StandardPlan"},
    })
    definitions["Plan"] = {"oneOf": [
        {"$ref": "#/$defs/FollowupFactPlan"}, {"$ref": "#/$defs/OtherVisitorPlan"},
    ]}
    return result


@lru_cache(maxsize=1)
def _fact_validator():
    definitions = ContractValidatorV2().schema("Plan")["$defs"]
    # The input contract caps the whole visitor message at 500 characters. The
    # actual sentence count is checked against the current message at binding.
    schema = {**_branch(definitions, 500), "$defs": definitions}
    return StrictValidator(schema, format_checker=FormatChecker())


def _bind_source(source, question, spans):
    if "sentence" in source:
        index = source["sentence"] - 1
        if index >= len(spans):
            raise ContractViolation("A selected source sentence is unavailable.")
        return spans[index]
    excerpt = source["excerpt"]
    start = question.find(excerpt)
    if start < 0 or question.find(excerpt, start + 1) >= 0:
        raise ContractViolation("A source excerpt must identify one exact passage in the current message.")
    return start, start + len(excerpt)


def expand_plan(data: dict, payload: dict) -> dict:
    """Decode the model-only follow-up choice before canonical plan validation."""
    question = payload.get("original_question", "")
    if not payload.get("recent_turns") or not question.strip():
        if isinstance(data, dict) and "visitor_task" in data:
            raise ContractViolation("The follow-up decision requires a retained exchange.")
        return data
    if type(data) is not dict:
        raise ContractViolation("The follow-up decision must choose a visitor task.")
    if data.get("visitor_task") == "other_request":
        if set(data) != {"visitor_task", "plan"} or type(data["plan"]) is not dict:
            raise ContractViolation("The standard follow-up decision requires exactly one plan.")
        return data["plan"]
    if data.get("visitor_task") != "check_artwork_facts":
        raise ContractViolation("The follow-up decision must choose a known visitor task.")
    try:
        _fact_validator().validate(data)
    except ValidationError as error:
        raise ContractViolation("The factual follow-up decision has an invalid shape.") from error

    spans = []
    cursor = 0
    for sentence in source_sentences(question):
        start = question.index(sentence, cursor)
        cursor = start + len(sentence)
        spans.append((start, cursor))

    requests, queries, bindings = [], [], []
    for index, fact in enumerate(data["facts"], 1):
        start, end = _bind_source(fact["source"], question, spans)
        fragment = question[start:end]
        if bindings:
            previous_start, previous_end = bindings[0]
            if start <= previous_start or fragment == question[previous_start:previous_end]:
                raise ContractViolation("Select distinct passages in visitor-message order.")
            # A shared sentence needs a shorter excerpt for each independent fact.
            for left, right in spans:
                if start < right and left < min(previous_end, end):
                    both_shorter = all(left <= a and b <= right and b - a < right - left
                                       for a, b in ((previous_start, previous_end), (start, end)))
                    if not both_shorter:
                        raise ContractViolation("Select a distinct excerpt for each fact in the same sentence.")
        bindings.append((start, end))
        query = fact["english_search_question"]
        if query != query.strip():
            raise ContractViolation("A factual question must have no surrounding whitespace.")
        request_id = f"r{index}"
        requests.append({"id": request_id, "original_fragment": fragment, "intent": query,
                         "kind": "artwork_fact", "resolution": "clear"})
        queries.append({"id": f"q{index}", "request_ids": [request_id], "query": query})

    plan = {"language_decision": deepcopy(data["language_decision"]), "requests": requests}
    if data["evidence_action"] == "search":
        return {"action": "retrieve", **plan, "queries": queries}
    return {"action": "answer_from_context", **plan}
