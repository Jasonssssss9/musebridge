"""Versioned v2 policies and detached JSON data for structured model operations."""
import json
from pathlib import Path
from typing import Any

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
    schema_json = json.dumps(output_schema, ensure_ascii=False, allow_nan=False)
    system = "\n\n".join([*policies, "Required JSON output schema:\n" + schema_json])
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    return [{"role": "system", "content": system}, {"role": "user", "content": data}]
