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
    if compact_schema:
        guide = json.dumps(_schema_guide(output_schema), ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        policies.append("Output structure. The server enforces the full schema.\n" + guide)
    else:
        schema_json = json.dumps(output_schema, ensure_ascii=False, allow_nan=False)
        policies.append("Required JSON output schema:\n" + schema_json)
    system = "\n\n".join(policies)
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    return [{"role": "system", "content": system}, {"role": "user", "content": data}]
