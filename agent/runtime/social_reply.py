"""Bind social speech acts to source-free replies and visitor-attributed memory."""
from copy import deepcopy

from agent.runtime.language_binding import ordinary_language
from agent.runtime.memory_notes import append_notes
from agent.runtime.validation import ContractViolation


# The model classifies the visitor's communicative act. These lines make no
# claims about the object, the artist, the collection or other visitors.
_REPLIES = {
    "share_impression": {
        "en": "What catches your eye when you look at it that way?",
        "es": "Al mirarla así, ¿qué te llama la atención?",
        "zh-CN": "带着这种感受再看一眼，什么地方最吸引你？",
        "ko": "그런 느낌으로 다시 보니 어떤 부분에 눈길이 가나요?",
    },
    "give_thanks": {"en": "You’re welcome!", "es": "¡De nada!", "zh-CN": "不客气！", "ko": "천만에요!"},
    "greet": {"en": "Hello! What would you like to explore?", "es": "¡Hola! ¿Qué te gustaría explorar?",
              "zh-CN": "你好！你想聊些什么？", "ko": "안녕하세요! 무엇을 살펴보고 싶으세요?"},
    "say_goodbye": {"en": "Enjoy the rest of your visit!", "es": "¡Que disfrutes del resto de la visita!",
                    "zh-CN": "祝你接下来的参观愉快！", "ko": "남은 관람도 즐겁게 하세요!"},
    "acknowledge": {"en": "Of course.", "es": "Claro.", "zh-CN": "好的。", "ko": "네, 알겠습니다."},
}
_TASK = "request_discussion_or_instruction"
_POLICY = (
    "For every clear conversation request except translation, supply one conversation_acts entry in order. "
    "Classify what the visitor did: share_impression (including praise, moods and personal readings), "
    "give_thanks, greet, say_goodbye or acknowledge. These acts get a localized reply from the guide. "
    "Use request_discussion_or_instruction only for an actual question or request, a correction or a setting. "
    "An observation alone does not request an explanation. General art questions use general_art_knowledge. "
    "For social acts put a brief acknowledgement in the result. Keep the visitor’s words attributed to them. "
    "In a direct response, use conversation_acts: [] for a translation or when there is no clear conversation part."
)


def social_policy(schema):
    return _POLICY if "ConversationActs" in schema.get("$defs", {}) else ""


def with_social_acts(schema, decision):
    wire = deepcopy(schema)
    defs = wire["$defs"]
    defs["ConversationActs"] = {
        "type": "array", "maxItems": 4,
        "items": {"type": "object", "properties": {
            "request_id": {"$ref": "#/$defs/RequestID"},
            "act": {"type": "string", "enum": [*_REPLIES, _TASK]}},
            "required": ["request_id", "act"], "additionalProperties": False},
    }
    shape = defs[decision]
    fields = {}
    for name, value in shape["properties"].items():
        if name == "results":
            fields["conversation_acts"] = {"$ref": "#/$defs/ConversationActs"}
        fields[name] = value
    shape["properties"] = fields
    shape["required"].append("conversation_acts")
    return wire


def bind_social_acts(decision, requests, language, translation, payload):
    """Convert a terminal model decision into ordinary public result and state fields."""
    acts = decision.get("conversation_acts", [])
    expected = [r["id"] for r in requests if r["kind"] == "conversation" and r["resolution"] == "clear"]
    if translation is not None:
        expected = []
    if (acts and translation is None and decision.get("action") == "direct_response" and requests
            and all(r["kind"] == "catalog_fact" and r["resolution"] == "clear" for r in requests)):
        # Social wording cannot apply to a catalog fact. Discard only these
        # auxiliary annotations when the entire set of ordered results is
        # source-bound. The ordinary canonical and scope validators still run.
        request_ids = [r["id"] for r in requests]
        annotated = [a["request_id"] for a in acts]
        parts = decision.get("results", [])
        catalog_ids = {record["fact_id"] for record in payload["catalog_facts"]}
        if (len(set(annotated)) == len(annotated) and set(annotated) <= set(request_ids)
                and all(set(a) == {"request_id", "act"} and a["act"] in (*_REPLIES, _TASK) for a in acts)
                and [p["request_id"] for p in parts] == request_ids
                and all(p["support"] == "supported" and p["citation_ids"]
                        and set(p["citation_ids"]) <= catalog_ids for p in parts)):
            acts = []
    if [a["request_id"] for a in acts] != expected:
        raise ContractViolation("Classify each clear conversational part exactly once, in order.")
    if not acts:
        if "conversation_acts" in decision:
            decision = {k: v for k, v in decision.items() if k != "conversation_acts"}
        return decision
    canonical = {k: deepcopy(v) for k, v in decision.items() if k != "conversation_acts"}
    if (canonical.get("action") == "direct_response" and len(requests) == len(acts) == 1
            and acts[0]["act"] in {"give_thanks", "greet", "say_goodbye", "acknowledge"}
            and translation is None and canonical["language_decision"]["explicit_instruction"] is None):
        canonical["language_decision"] = ordinary_language(payload, "und")
        language = canonical["language_decision"]["answer_language"]
    by_request = {r["request_id"]: r for r in canonical["results"]}
    social = {}
    for entry in acts:
        key, act = entry["request_id"], entry["act"]
        if act == _TASK:
            continue
        part = by_request.get(key)
        if act not in _REPLIES or part is None or part["support"] != "not_applicable" or part["citation_ids"]:
            raise ContractViolation("A social act requires an uncited conversation result.")
        part["text"] = _REPLIES[act][language]
        social[key] = act
    if social:
        previous = payload["conversation_context"]
        # A gesture never clears a pending question or introduces a work fact.
        # Keep a literal attribution when an impression or another ask changes the topic.
        neutral_only = len(social) == len(requests) and "share_impression" not in social.values()
        notes = []
        if not neutral_only:
            for r in requests:
                fragment = " ".join(r["original_fragment"].splitlines())
                label = "Visitor impression" if social.get(r["id"]) == "share_impression" else "Visitor quotation"
                notes.append(f"{label}: “{fragment}”")
            # The whole current question is at most 500 characters; overlapping
            # request fragments can repeat it, so use it once if the notes overflow.
            if len("\n".join(notes)) > 1000:
                notes = ["Visitor quotation: “" + " ".join(payload["original_question"].splitlines()) + "”"]
        memory = canonical["memory_update"]
        memory["continuity_summary"] = (append_notes(previous["continuity_summary"], notes)
                                        if notes else previous["continuity_summary"])
        if notes:
            # The next turn may refer to this impression or the accompanying question.
            memory["active_topic"] = notes[-1] if len(notes[-1]) <= 300 else "The visitor’s latest observation or request"
        else:
            memory["active_topic"] = previous["active_topic"]
        if len(social) == len(requests):
            memory["pending_update"] = {"action": "keep"}
    return canonical


_SOCIAL_INTENTS = {
    "share_impression": "The visitor shares a personal impression of the artwork.",
    "greet": "The visitor greets the guide.",
    "give_thanks": "The visitor thanks the guide.",
}


def social_schema(definitions):
    return {"type": "object", "properties": {
        "visitor_task": {"type": "string", "const": "respond_to_personal_impression_greeting_or_thanks"},
        "act": {"type": "string", "enum": list(_SOCIAL_INTENTS)},
        "language_decision": {"$ref": "#/$defs/LanguageDecision"}},
        "required": ["visitor_task", "act", "language_decision"], "additionalProperties": False}


def social_plan(data, payload):
    """Complete a single social gesture without asking the model to draft artwork prose."""
    language, act = data["language_decision"], data["act"]
    if language["translation"] is not None or act not in _SOCIAL_INTENTS:
        raise ContractViolation("Only a single personal impression, greeting or thanks uses this task.")
    request = {"id": "r1", "original_fragment": payload["original_question"], "intent": _SOCIAL_INTENTS[act],
               "kind": "conversation", "resolution": "clear"}
    state = payload["conversation_context"]
    plan = {"action": "direct_response", "language_decision": deepcopy(language), "requests": [request],
            "conversation_acts": [{"request_id": "r1", "act": act}],
            "results": [{"request_id": "r1", "support": "not_applicable", "text": "", "citation_ids": []}],
            "memory_update": {"active_topic": state["active_topic"], "continuity_summary": state["continuity_summary"],
                              "pending_update": {"action": "keep"}}}
    return bind_social_acts(plan, [request], language["answer_language"], None, payload)
