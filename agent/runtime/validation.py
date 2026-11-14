"""Schema and cross-field checks for C-owned workflow semantics.

Transport/admission, run retention and provider protocols belong to B.
These checks cannot prove semantic entailment or planning faithfulness.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from copy import deepcopy
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, validators

from agent.runtime.errors import WorkflowError

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "docs/contracts/agent-v1.schema.json"
BAD_TEXT = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ud800-\udfff]")
TRACE_ID = re.compile(r"[\x21-\x7e]{1,128}\Z")


class ContractViolation(ValueError):
    """A payload-free contract error safe to map to a boundary-specific code."""


_strict_types = Draft202012Validator.TYPE_CHECKER.redefine(
    "integer", lambda _checker, value: type(value) is int
)
StrictValidator = validators.extend(Draft202012Validator, type_checker=_strict_types)


def _json_value(value: Any, depth: int = 0) -> None:
    if depth > 64:
        raise ContractViolation("JSON nesting exceeds the validation limit.")
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str or BAD_TEXT.search(key):
                raise ContractViolation("Invalid JSON object key.")
            _json_value(item, depth + 1)
    elif type(value) is list:
        for item in value:
            _json_value(item, depth + 1)
    elif type(value) is str:
        if BAD_TEXT.search(value):
            raise ContractViolation("Invalid Unicode text.")
    elif value is None or type(value) in (bool, int):
        return
    elif type(value) is float and math.isfinite(value):
        return
    else:
        raise ContractViolation("Invalid JSON value.")


def strict_json(raw: str | bytes, *, max_bytes: int = 256 * 1024) -> Any:
    """Decode one JSON value without repairs, duplicate keys or nonfinite numbers."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ContractViolation("Duplicate JSON object key.")
            result[key] = value
        return result

    def constant(_value):
        raise ContractViolation("Nonfinite JSON number.")

    try:
        if type(raw) is bytes:
            if len(raw) > max_bytes:
                raise ContractViolation("JSON body exceeds its limit.")
            raw = raw.decode("utf-8", errors="strict")
        elif type(raw) is str:
            if len(raw.encode("utf-8", errors="strict")) > max_bytes:
                raise ContractViolation("JSON body exceeds its limit.")
        else:
            raise ContractViolation("Expected raw JSON text.")
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
        _json_value(value)
        return value
    except ContractViolation:
        raise
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ContractViolation("Malformed JSON body.") from exc


class ContractValidator:
    """Read the checked-in schema rather than maintain a second wire model."""

    def __init__(self, path: Path = SCHEMA_PATH):
        self._document = strict_json(path.read_bytes())
        Draft202012Validator.check_schema(self._document)
        self._validators = {}

    def schema(self, name: str) -> dict:
        if name not in self._document["$defs"]:
            raise KeyError(name)
        # Keep only definitions reachable from this root. The provider receives
        # a self-contained schema, without unrelated transport contracts.
        definitions = {}
        pending = [name]
        while pending:
            current = pending.pop()
            if current in definitions:
                continue
            definition = self._document["$defs"][current]
            definitions[current] = deepcopy(definition)

            def references(value):
                if isinstance(value, dict):
                    ref = value.get("$ref")
                    if ref:
                        if not ref.startswith("#/$defs/"):
                            raise ValueError("Only local schema references are supported.")
                        pending.append(ref.removeprefix("#/$defs/"))
                    for child in value.values():
                        references(child)
                elif isinstance(value, list):
                    for child in value:
                        references(child)

            references(definition)
        return {"$schema": self._document["$schema"],
                "$ref": f"#/$defs/{name}", "$defs": definitions}

    def validate(self, name: str, data: Any) -> None:
        _json_value(data)
        if name not in self._validators:
            self._validators[name] = StrictValidator(
                self.schema(name), format_checker=FormatChecker()
            )
        if next(self._validators[name].iter_errors(data), None) is not None:
            raise ContractViolation(f"{name} does not satisfy the contract.")


def model_decision(raw: str, name: str, validator: ContractValidator, *, phase: str) -> dict:
    try:
        value = strict_json(raw)
        validator.validate(name, value)
        return value
    except ContractViolation as exc:
        raise WorkflowError(
            "generation_invalid_response", "The model returned an invalid decision.",
            phase=phase,
        ) from exc


def normalized_query(query: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", query).casefold().split())


def validate_plan(run: dict, plan: dict, validator: ContractValidator) -> None:
    """Enforce links and scope; semantic query quality remains an evaluation task."""
    try:
        validator.validate("Plan", plan)
        if plan["action"] != "retrieve":
            return
        requests = plan["requests"]
        if [item["id"] for item in requests] != [
            f"r{i}" for i in range(1, len(requests) + 1)
        ]:
            raise ContractViolation("Request IDs must follow the plan order.")
        if any(item["original_fragment"] not in run["question"] for item in requests):
            raise ContractViolation("A fragment is absent from the original question.")
        current_ids = {item["id"] for item in requests if item["scope"] == "current_artwork"}
        linked = set()
        texts = set()
        for index, query in enumerate(plan["queries"], 1):
            if query["id"] != f"q{index}" or query["query"] != query["query"].strip():
                raise ContractViolation("Invalid initial query ID or surrounding whitespace.")
            links = set(query["request_ids"])
            if not links <= current_ids:
                raise ContractViolation("A query links an unauthorized request.")
            normalized = normalized_query(query["query"])
            if normalized in texts:
                raise ContractViolation("Repeated normalized query.")
            texts.add(normalized)
            linked.update(links)
        if not current_ids or linked != current_ids:
            raise ContractViolation("Queries must cover all current-artwork requests.")
    except ContractViolation as exc:
        raise WorkflowError(
            "generation_invalid_response", "The model plan has invalid request or query links.",
            phase="planning",
        ) from exc


def assemble_answer(plan: dict, decision: dict, evidence: list[dict],
                    validator: ContractValidator) -> tuple[dict, list[dict]]:
    """Assemble only validated parts and resolve citations from accepted evidence."""
    try:
        if plan["action"] in ("clarify", "out_of_scope"):
            validator.validate("Plan", plan)
            answer = {
                "status": "clarification_needed" if plan["action"] == "clarify" else "out_of_scope",
                "text": plan["text"], "citations": [],
            }
            results = []
        else:
            validator.validate("FinalDecision", decision)
            if decision["action"] == "clarify":
                answer = {"status": "clarification_needed", "text": decision["text"], "citations": []}
                results = []
            else:
                results = decision["results"]
                if [part["request_id"] for part in results] != [
                    request["id"] for request in plan["requests"]
                ]:
                    raise ContractViolation("Results must cover every request in order.")
                by_id = {record["chunk_id"]: record for record in evidence}
                if len(by_id) != len(evidence):
                    raise ContractViolation("Duplicate accepted evidence ID.")
                citation_ids = []
                for request, part in zip(plan["requests"], results):
                    outside = request["scope"] == "outside_scope"
                    if outside != (part["support"] == "out_of_scope"):
                        raise ContractViolation("Result support conflicts with request scope.")
                    if any(chunk_id not in by_id for chunk_id in part["citation_chunk_ids"]):
                        raise ContractViolation("A citation was not retrieved in this run.")
                    for chunk_id in part["citation_chunk_ids"]:
                        if chunk_id not in citation_ids:
                            citation_ids.append(chunk_id)
                supports = [part["support"] for part in results]
                if all(support == "supported" for support in supports):
                    status = "answered"
                elif "supported" in supports:
                    status = "partially_answered"
                elif "insufficient" in supports:
                    status = "insufficient"
                else:
                    status = "out_of_scope"
                answer = {
                    "status": status,
                    "text": "\n\n".join(part["text"] for part in results),
                    "citations": [deepcopy(by_id[chunk_id]) for chunk_id in citation_ids],
                }
        validator.validate("AgentAnswer", answer)
        return deepcopy(answer), deepcopy(results)
    except ContractViolation as exc:
        raise WorkflowError(
            "generation_invalid_response", "The model answer has invalid support or citations.",
            phase="validation",
        ) from exc
