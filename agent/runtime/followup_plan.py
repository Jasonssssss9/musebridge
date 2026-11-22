"""Bind selected visitor tasks to scoped catalog, evidence and conversation state."""
from copy import deepcopy
from functools import lru_cache
import re

from jsonschema import FormatChecker, ValidationError

from agent.runtime.language_binding import bind_setting_scope, ordinary_language
from agent.runtime.social_reply import bind_social_acts, social_plan, social_schema, with_social_acts
from agent.runtime.catalog_reply import catalog_field_for_query, catalog_plan
from agent.runtime.general_reply import general_plan, general_schema, offers_definition
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


def _source_choice(definitions, sentence_limit):
    excerpt = definitions["InterpretedRequest"]["properties"]["original_fragment"]
    numbered = _object({"sentence": {"type": "integer", "minimum": 1, "maximum": sentence_limit or 500}})
    numbered["properties"]["excerpt"] = excerpt
    if sentence_limit is not None and sentence_limit > 1:
        return numbered
    # The generic replay validator also reads older decisions with bare excerpts.
    return {"oneOf": [numbered, _object({"excerpt": excerpt})]}


def _medium_branch(definitions):
    return _object({
        "visitor_task": {"type": "string", "const": "look_up_listed_medium"},
        "detected_language": definitions["LanguageDecision"]["properties"]["detected_language"],
    })


def _branch(definitions, sentence_limit):
    source = _source_choice(definitions, sentence_limit)
    return _object({
        "visitor_task": {"type": "string", "const": "check_artwork_facts"},
        "language_decision": {"$ref": "#/$defs/LanguageDecision"},
        "evidence_action": {"type": "string", "enum": ["search", "reuse_cache"]},
        "facts": {"type": "array", "minItems": 1, "maxItems": 2, "items": _object({
            "source": source, "english_search_question": definitions["SearchQuery"]["properties"]["query"],
        })},
    })


def _single_tasks(definitions):
    detected = definitions["LanguageDecision"]["properties"]["detected_language"]
    return {
        "GeneralConceptPlan": general_schema(definitions),
        "PersonalResponsePlan": social_schema(definitions),
        "MissingTranslationPlan": _object({
            "visitor_task": {"const": "ask_for_translation_source", "type": "string"},
            "target_language": {"$ref": "#/$defs/Language"},
        }),
        "MissingSubjectPlan": _object({
            "visitor_task": {"const": "ask_which_detail", "type": "string"},
            "detected_language": detected,
        }),
        "CatalogFieldPlan": _object({
            "visitor_task": {"const": "read_catalog_field", "type": "string"},
            "fields": {"type": "array", "minItems": 1, "maxItems": 3, "uniqueItems": True,
                       "items": {"type": "string", "enum": ["artist", "title", "date_display"]}},
            "language_decision": {"$ref": "#/$defs/LanguageDecision"},
        }),
    }


def planning_schema(schema: dict, payload: dict) -> dict:
    if schema.get("$ref") != "#/$defs/Plan":
        return schema
    result = with_social_acts(schema, "DirectResponsePlan")
    if not payload.get("original_question", "").strip():
        return result
    definitions = result["$defs"]
    extra = _single_tasks(definitions)
    if not offers_definition(payload["original_question"]):
        extra.pop("GeneralConceptPlan")
    extra["ListedMediumPlan"] = _medium_branch(definitions)
    definitions.update(extra)
    shortcuts = [{"$ref": "#/$defs/" + name} for name in extra]
    sentence_count = len(source_sentences(payload["original_question"]))
    fact_plan = _branch(definitions, sentence_count)
    if payload.get("complete_scope") or not payload.get("cached_evidence"):
        route = "reuse_cache" if payload.get("complete_scope") else "search"
        fact_plan["properties"]["evidence_action"] = {"type": "string", "const": route}
    definitions["FollowupFactPlan"] = fact_plan
    definitions["StandardPlan"] = definitions["Plan"]
    definitions["OtherVisitorPlan"] = _object({
        "visitor_task": {"type": "string", "const": "chat_translate_read_catalog_or_handle_mixed_requests"},
        "plan": {"$ref": "#/$defs/StandardPlan"},
    })
    definitions["Plan"] = {"oneOf": [*shortcuts,
        {"$ref": "#/$defs/FollowupFactPlan"},
        {"$ref": "#/$defs/OtherVisitorPlan"},
    ]}
    return result


@lru_cache(maxsize=1)
def _fact_validator():
    definitions = ContractValidatorV2().schema("Plan")["$defs"]
    # The input contract caps the whole visitor message at 500 characters. The
    # actual sentence count is checked against the current message at binding.
    schema = {"oneOf": [_branch(definitions, None), _medium_branch(definitions),
                        *_single_tasks(definitions).values()], "$defs": definitions}
    return StrictValidator(schema, format_checker=FormatChecker())


def _bind_source(source, question, spans):
    left, right = 0, len(question)
    if "sentence" in source:
        index = source["sentence"] - 1
        if index >= len(spans):
            raise ContractViolation("A selected source sentence is unavailable.")
        left, right = spans[index]
        if "excerpt" not in source:
            return left, right
    excerpt = source["excerpt"]
    start = question.find(excerpt, left, right)
    if start < 0 or question.find(excerpt, start + 1, right) >= 0:
        raise ContractViolation("The excerpt must identify one exact passage in the selected visitor text.")
    return start, start + len(excerpt)


def _standard_plan(plan, payload):
    if not isinstance(plan, dict) or plan.get("action") != "direct_response":
        return plan
    decision = plan["language_decision"]
    return bind_social_acts(plan, plan["requests"], decision["answer_language"], decision["translation"], payload)


_CLARIFICATION = {
    "ask_which_detail": {
        "en": "Which detail of the artwork do you mean?",
        "es": "¿A qué detalle de la obra te refieres?",
        "zh-CN": "你指的是作品中的哪个细节？",
        "ko": "작품의 어느 부분을 말씀하시는 건가요?",
    },
    "ask_for_translation_source": {
        "en": "Could you share the passage you would like me to translate?",
        "es": "¿Puedes compartir el texto que quieres que traduzca?",
        "zh-CN": "可以把你想翻译的原文发给我吗？",
        "ko": "번역할 원문을 보내 주시겠어요?",
    },
}


def _clarification(task, data, payload):
    state = payload["conversation_context"]
    if task == "ask_for_translation_source":
        target = data["target_language"]
        language = {"detected_language": "und", "explicit_instruction": None, "answer_language": target,
                    "translation": {"target_language": target, "source": None,
                                    "original_fragment": payload["original_question"]}}
    else:
        language = ordinary_language(payload, data["detected_language"])
    return {"action": "clarify", "language_decision": language,
            "text": _CLARIFICATION[task][language["answer_language"]],
            "memory_update": {"active_topic": state["active_topic"],
                              "continuity_summary": state["continuity_summary"],
                              "pending_update": {"action": "set", "source": "current_message",
                                                 "request_id": None}}}


def expand_plan(data: dict, payload: dict) -> dict:
    plan = _expand_plan(data, payload)
    return bind_setting_scope(plan, payload, source_sentences(payload.get("original_question", "")))


def _expand_plan(data: dict, payload: dict) -> dict:
    """Decode the model-only follow-up choice before canonical plan validation."""
    question = payload.get("original_question", "")
    task = data.get("visitor_task") if isinstance(data, dict) else None
    if question.strip() and task in ("read_catalog_field", "define_general_art_term",
                                     "respond_to_personal_impression_greeting_or_thanks", *_CLARIFICATION):
        try:
            _fact_validator().validate(data)
        except ValidationError as error:
            raise ContractViolation("The selected visitor task has an invalid shape.") from error
        if task == "respond_to_personal_impression_greeting_or_thanks":
            return social_plan(data, payload)
        if task == "define_general_art_term":
            return general_plan(data, payload)
        if task == "read_catalog_field":
            return catalog_plan(data["fields"], question, data["language_decision"], payload)
        return _clarification(task, data, payload)
    if question.strip() and task == "look_up_listed_medium":
        try:
            _fact_validator().validate(data)
        except ValidationError as error:
            raise ContractViolation("The medium selection has an invalid shape.") from error
        # The model selected one published field. Scope, cache availability and
        # UI language are already trusted inputs to the ordinary planning path.
        title = payload["artwork_context"]["artwork_title"]
        query = f"What material is {title} made of?"
        language = ordinary_language(payload, data["detected_language"])
        requests = [{"id": "r1", "original_fragment": question, "intent": query,
                     "kind": "artwork_fact", "resolution": "clear"}]
        plan = {"language_decision": language, "requests": requests}
        if payload["complete_scope"] or any(item["source_locator"] == "data.medium_display"
                                            for item in payload["cached_evidence"]):
            return {"action": "answer_from_context", **plan}
        return {"action": "retrieve", **plan, "queries": [{"id": "q1", "request_ids": ["r1"], "query": query}]}
    if not question.strip() or (not task and not payload.get("recent_turns")):
        if isinstance(data, dict) and "visitor_task" in data:
            raise ContractViolation("The follow-up decision requires a retained exchange.")
        return _standard_plan(data, payload)
    if type(data) is not dict:
        raise ContractViolation("The follow-up decision must choose a visitor task.")
    if data.get("visitor_task") in ("chat_translate_read_catalog_or_handle_mixed_requests", "other_request"):
        # Older private probe reports used the shorter tag. Live decisions must
        # match the model-facing schema before reaching this decoder.
        if set(data) != {"visitor_task", "plan"} or type(data["plan"]) is not dict:
            raise ContractViolation("The standard follow-up decision requires exactly one plan.")
        return _standard_plan(data["plan"], payload)
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

    from agent.runtime.question_spans import coordinated_spans
    coordinated = coordinated_spans(question, spans, data["facts"], data["language_decision"]["detected_language"])
    requests, queries, bindings = [], [], []
    for index, fact in enumerate(data["facts"], 1):
        start, end = coordinated[index - 1] if coordinated else _bind_source(fact["source"], question, spans)
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

    if len(queries) == 1 and payload.get("catalog_facts"):
        # A rewritten question can already be an exact lookup of a published
        # metadata field. Bind it to that record before dispatching to retrieval.
        field = catalog_field_for_query(queries[0]["query"], payload["artwork_context"]["artwork_title"])
        if field:
            return catalog_plan([field], question, data["language_decision"], payload)
    plan = {"language_decision": deepcopy(data["language_decision"]), "requests": requests}
    if data["evidence_action"] == "search":
        return {"action": "retrieve", **plan, "queries": queries}
    return {"action": "answer_from_context", **plan}
