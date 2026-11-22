"""Bind neutral dialogue and narrowly unqualified language commands to session state."""
import re

from agent.runtime.validation_v2 import LANGUAGES


def ordinary_language(payload, detected):
    state = payload["conversation_context"]
    preference = payload["language_override"] or state["language_preference"]
    automatic = detected if detected in LANGUAGES else state["auto_language"]
    return {"detected_language": detected, "explicit_instruction": None, "translation": None,
            "answer_language": automatic if preference == "auto" else preference}


# Only complete, positive imperatives are eligible. Unknown or qualified wording
# remains the model's responsibility. Targets must agree with the literal words.
_NAMES = {
    "en": ("english", "inglés", "(?:英语|英文)", "영어"),
    "es": ("spanish", "español", "(?:西班牙语|西班牙文)", "스페인어"),
    "zh-CN": ("(?:simplified chinese|chinese)", "(?:chino simplificado|chino)", "(?:简体中文|中文|汉语)", "중국어"),
    "ko": ("korean", "coreano", "(?:韩语|韩文)", "한국어"),
}
_COMMANDS = (
    r"(?:please[, ]+)?(?:(?:answer|reply|respond|speak) in|use|switch to) {name}(?:,? please)?",
    r"(?:por favor[, ]+)?(?:(?:responde|responda|contesta|conteste|habla|hable) en|usa|use) {name}(?:,? por favor)?",
    r"(?:请你?|麻烦你)?(?:用|以){name}(?:来)?(?:回答|回复|作答)(?:我)?",
    r"{name}(?:으)?로\s*(?:답변|대답)(?:해\s*(?:줘요?|주세요|주십시오)|하세요)",
)
# A second sentence can qualify the directive. Prefer leaving a genuine one-off
# alone whenever the surrounding request introduces a time, quotation or condition.
_SCOPE_CUE = re.compile(
    r"\b(?:this time|this one|this (?:reply|response|turn|question)|just|only|once|next|temporar\w*|"
    r"for now|one[- ]off|single|moment|reply|response|turn|if|unless|say|quote|translat\w*|"
    r"solo|sólo|solamente|únicamente|ahora|esta vez|momento|respuesta|turno|"
    r"est[ae] (?:respuesta|turno|pregunta)|próxim\w*|siguiente|temporal\w*|si|tradu\w*)\b|"
    r"这次|本次|这回|本轮|这一|临时|暂|仅|只|下一|翻译|"
    r"이번|지금|다음|한\s*번|만|잠깐|임시|번역|인용|[“”‘’«»「」『』\"'\[\]():：;,，；]", re.IGNORECASE,
)


def bind_setting_scope(plan, payload, sentences):
    if not isinstance(plan, dict):
        return plan
    language = plan.get("language_decision", {})
    instruction = language.get("explicit_instruction")
    if (not instruction or language.get("translation") is not None
            or (instruction.get("purpose"), instruction.get("persistence")) != ("turn_response", "turn")
            or instruction.get("preference") not in _NAMES or not sentences):
        return plan
    # A previous turn can give an otherwise standalone command a temporary scope.
    # Never override the model's interpretation in that situation.
    state = payload.get("conversation_context", {})
    if payload.get("recent_turns") or state.get("pending_clarification") or state.get("last_answer_language"):
        return plan
    question = payload["original_question"]
    fragment = instruction.get("original_fragment", "")
    command = sentences[-1].rstrip(".!。！").strip()
    if not fragment or fragment not in question or fragment.rstrip(".!。！").strip() != command:
        return plan
    if len(sentences) > 1:
        first = sentences[0]
        requests = plan.get("requests", [])
        if (len(sentences) != 2 or not first.endswith(("?", "？")) or _SCOPE_CUE.search(first)
                or not requests or any(r["kind"] not in {"artwork_fact", "catalog_fact"}
                                       or r["resolution"] != "clear" or r["original_fragment"] not in first
                                       for r in requests)):
            return plan
    names = _NAMES[instruction["preference"]]
    if not any(re.fullmatch(pattern.format(name=name), command, re.IGNORECASE)
               for pattern, name in zip(_COMMANDS, names)):
        return plan
    persistent = {**instruction, "purpose": "conversation_setting", "persistence": "conversation"}
    return {**plan, "language_decision": {**language, "explicit_instruction": persistent}}
