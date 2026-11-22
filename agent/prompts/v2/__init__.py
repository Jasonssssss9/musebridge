"""Versioned v2 policies and detached JSON data for structured model operations."""
import json
from pathlib import Path
from typing import Any

from agent.runtime.social_reply import social_policy

from .readings import source_statements, translation_notes, uses_source_checks

PROMPT_VERSION = "museum-prompts-v2"
_PROMPT_DIRECTORY = Path(__file__).resolve().parent
_OPERATION_POLICIES = {
    "plan": ("interpret_and_plan.md",),
    "assess": ("assess_and_respond.md",),
    "final": ("assess_and_respond.md", "final_response.md"),
}


def _schema_guide(node):
    """Present structure, choices and required fields without repeating lexical bounds."""
    if isinstance(node, list):
        return [_schema_guide(item) for item in node]
    if not isinstance(node, dict):
        return node
    result = {}
    for key, value in node.items():
        if key in {"pattern", "minLength", "maxLength", "minimum", "maximum",
                   "minItems", "maxItems", "uniqueItems"}:
            continue
        if key in ("properties", "$defs"):
            result[key] = {name: _schema_guide(schema) for name, schema in value.items()}
        elif key == "allOf":
            branches = [branch for branch in _schema_guide(value) if branch]
            if branches:
                result[key] = branches
        else:
            result[key] = _schema_guide(value)
    return result


def build_messages(operation: str, payload: dict[str, Any],
                   output_schema: dict[str, Any], *, compact_schema: bool = False) -> list[dict[str, str]]:
    """Keep static instructions separate from untrusted conversation/source data.

    The caller validates the payload and exports a self-contained output schema.
    User input is serialized, never interpolated into or evaluated as policy.
    Shared memory policy applies to every operation because planning can finish.
    """
    if operation not in _OPERATION_POLICIES:
        raise ValueError("Unsupported model operation.")
    files = ("core_policy.md", "memory_policy.md", *_OPERATION_POLICIES[operation])
    policies = [(_PROMPT_DIRECTORY / name).read_text(encoding="utf-8").strip()
                for name in files]
    if operation == "plan" and "FollowupFactPlan" in output_schema.get("$defs", {}):
        policies[-1] = ("The following reference describes language handling for all requests and "
                        "the full Plan used inside chat_translate_read_catalog_or_handle_mixed_requests. Choose the visitor task first. "
                        "A question about the museum's wording, interpretation or physical details "
                        "belongs to check_artwork_facts, even when the visitor wants it restated.\n"
                        + policies[-1])
    if operation == "plan" and payload.get("language_override") in {"en", "es", "zh-CN", "ko"}:
        policies.append(
            "The UI selection specifies the preferred reply language only. For an acknowledgement "
            "such as 'Thanks for the explanation', use this language_decision: "
            + json.dumps({"detected_language": "und", "explicit_instruction": None,
                          "answer_language": payload["language_override"], "translation": None})
            + ". For a substantive question or observation, detect the language of those words. "
            "An actual visitor instruction or translation request follows the normal language rules."
        )
    if operation != "plan" and "answer_language" in payload:
        notes = translation_notes(payload["evidence"], payload["answer_language"])
        if notes:
            policies.append(
                "Wording guide for the accepted excerpts. For each request, select only the line "
                "that answers it. Use its visitor-language wording in the supported part. Preserve "
                "its English wording if you mention the fact in memory. A different line about "
                "the same artwork answers a different question. All citations and verbatim "
                "quotations still come from the original evidence.\n" + "\n".join(notes)
            )
        gap_example = {
            "en": "I can’t verify who carved it from the information here.",
            "es": "Con la información que tengo aquí no puedo verificar quién lo talló.",
            "zh-CN": "凭这里的信息，我无法确认是谁雕刻的。",
            "ko": "지금 주어진 정보로는 누가 조각했는지 확인할 수 없습니다.",
        }[payload["answer_language"]]
        policies.append(
            f'For a gap, follow this first-person example in the reply language: "{gap_example}" '
            'Use the actual missing detail. Speak about this answer only. '
            'For English memory, use one short source quotation or answered topic for each '
            'supported part. Keep each unresolved visitor question open with its original meaning.'
        )
    if guidance := social_policy(output_schema):
        policies.append(guidance)
    if compact_schema:
        guide = json.dumps(_schema_guide(output_schema), ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        policies.append("Output structure. The server enforces the full schema.\n" + guide)
    else:
        schema_json = json.dumps(output_schema, ensure_ascii=False, allow_nan=False)
        policies.append("Required JSON output schema:\n" + schema_json)
    if operation == "plan" and "FollowupFactPlan" in output_schema.get("$defs", {}):
        policies.append((_PROMPT_DIRECTORY / "followup_planning.md").read_text(encoding="utf-8").strip())
        cached_medium = any(item.get("source_locator") == "data.medium_display"
                            for item in payload.get("cached_evidence", []))
        policies.append("The scoped medium record is " + ("already verified and available to reuse."
                        if cached_medium else "available for lookup.") +
                        " If the visitor asks for the listed material, choose look_up_listed_medium.")
        # Keep the current question next to the generation boundary, in the data message.
        payload = payload.copy()
        if "original_question" in payload:
            from agent.runtime.followup_plan import source_sentences

            question = payload.pop("original_question")
            payload["source_sentences"] = [
                {"sentence": number, "text": sentence}
                for number, sentence in enumerate(source_sentences(question), 1)
            ]
            payload["original_question"] = question
    if operation == "plan" and "MissingTranslationPlan" in output_schema.get("$defs", {}):
        policies.append((_PROMPT_DIRECTORY / "visitor_tasks.md").read_text(encoding="utf-8").strip())
        if "GeneralConceptPlan" not in output_schema["$defs"]:
            policies.append("There is no standalone vocabulary shortcut for this message. An inquiry "
                            "about the identity of a creator already mentioned uses the artist catalog field.")
        if not payload.get("recent_turns"):
            policies.append("No earlier exchange is available. To translate an absent previous "
                            "reply, choose ask_for_translation_source and the requested target language.")
    if operation != "plan" and uses_source_checks(output_schema):
        policies.append((_PROMPT_DIRECTORY / "source_check.md").read_text(encoding="utf-8").strip())
        payload = {**payload, "available_statements":
                   source_statements(payload["evidence"], payload["answer_language"], payload["catalog_facts"])}
    system = "\n\n".join(policies)
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    return [{"role": "system", "content": system}, {"role": "user", "content": data}]
