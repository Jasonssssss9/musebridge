"""Versioned English policy and JSON data messages for structured operations."""

import json
from pathlib import Path
from typing import Any

PROMPT_VERSION = "museum-prompts-v1"
_PROMPT_DIRECTORY = Path(__file__).resolve().parent
_OPERATION_POLICIES = {
    "plan": ("interpret_and_plan.md",),
    "assess": ("assess_and_respond.md",),
    "final": ("assess_and_respond.md", "final_response.md"),
}


def build_messages(
    operation: str, payload: dict[str, Any], output_schema: dict[str, Any]
) -> list[dict[str, str]]:
    """Render static instructions and a detached JSON data message.

    Callers validate payload and export a self-contained schema first.
    Neither runtime data nor source excerpts are interpolated into policy.
    The provider also receives the same schema as a structured-output argument.
    """
    if operation not in _OPERATION_POLICIES:
        raise ValueError("Unsupported model operation.")
    files = ("core_policy.md", *_OPERATION_POLICIES[operation])
    policies = [(_PROMPT_DIRECTORY / name).read_text(encoding="utf-8").strip()
                for name in files]
    schema_json = json.dumps(output_schema, ensure_ascii=False, allow_nan=False)
    system = "\n\n".join([*policies, "Required JSON output schema:\n" + schema_json])
    user = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
