"""Versioned v2 policies and detached JSON data for structured model operations."""
import json
from pathlib import Path
from typing import Any

from .readings import translation_notes

PROMPT_VERSION = "museum-prompts-v2"
_PROMPT_DIRECTORY = Path(__file__).resolve().parent
_OPERATION_POLICIES = {
    "plan": ("interpret_and_plan.md",),
    "assess": ("assess_and_respond.md",),
    "final": ("assess_and_respond.md", "final_response.md"),
}


def build_messages(operation: str, payload: dict[str, Any],
                   output_schema: dict[str, Any]) -> list[dict[str, str]]:
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
    schema_json = json.dumps(output_schema, ensure_ascii=False, allow_nan=False)
    system = "\n\n".join([*policies, "Required JSON output schema:\n" + schema_json])
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    return [{"role": "system", "content": system}, {"role": "user", "content": data}]
