"""Strict detached wire decoding using C's canonical v2 schema validator."""
from copy import deepcopy
import json
from functools import lru_cache
from pathlib import Path
from typing import cast

from agent.runtime.validation import ContractValidator, ContractViolation, strict_json
from agent.runtime.validation_v2 import ContractValidatorV2
from . import models
from .errors import ContractFailure

PUBLIC_VERSION = "agent-demo-v0.3"
PRIVATE_VERSION = "agent-run-v2"
WORKFLOW_ID = "museum_qa_v2"
STATE_VERSION = "conversation-state-v1"
PROFILE_ID = "local-ollama-v2"
PUBLIC_BODY_LIMIT = 8 * 1024
PRIVATE_BODY_LIMIT = 256 * 1024


@lru_cache(maxsize=1)
def v3_validator():
    """Apply the shared v3 contract delta once for all private operations."""
    import jsonpatch

    directory = Path(__file__).resolve().parents[2] / "docs" / "contracts"
    base = strict_json((directory / "agent-v2.schema.json").read_bytes())
    patch = strict_json((directory / "agent-v3.patch.json").read_bytes())
    return ContractValidator(document=jsonpatch.apply_patch(base, patch))


class ContractCodec:
    """TypedDicts describe wire data; this codec enforces runtime constraints.

    Schema-open response metadata is retained as inert detached data. Requests,
    model decisions and state retain the closed shapes defined by the schema.
    """

    def __init__(self):
        self.validator = ContractValidatorV2()

    def validate(self, name: str, value):
        self.validator.validate(name, value)
        return deepcopy(value)

    def decode(self, name: str, raw: str | bytes):
        limit = PUBLIC_BODY_LIMIT if name in {
            "PublicCreateConversationRequest", "PublicMessageRequest"
        } else PRIVATE_BODY_LIMIT
        return self.validate(name, strict_json(raw, max_bytes=limit))

    def encode(self, name: str, value) -> bytes:
        detached = self.validate(name, value)
        raw = json.dumps(detached, ensure_ascii=False, allow_nan=False,
                         separators=(",", ":")).encode("utf-8")
        # Enforce the same complete body limit on outgoing messages.
        self.decode(name, raw)
        return raw

    def decode_run_request(self, raw: str | bytes) -> models.RunRequest:
        return cast(models.RunRequest, self.decode("RunRequest", raw))

    def decode_run_snapshot(self, raw: str | bytes) -> models.RunSnapshot:
        return cast(models.RunSnapshot, self.decode("RunSnapshot", raw))

    def decode_public_message(self, raw: str | bytes) -> models.PublicMessageRequest:
        return cast(models.PublicMessageRequest, self._decode_public("PublicMessageRequest", raw))

    def decode_public_create(self, raw: str | bytes) -> models.PublicCreateConversationRequest:
        return cast(models.PublicCreateConversationRequest,
                    self._decode_public("PublicCreateConversationRequest", raw))

    def _decode_public(self, name: str, raw: str | bytes):
        try:
            if type(raw) not in (str, bytes):
                raise ContractViolation("Expected raw JSON.")
            size = len(raw.encode("utf-8")) if isinstance(raw, str) else len(raw)
            if size > PUBLIC_BODY_LIMIT:
                raise ContractFailure(413, "payload_too_large")
            value = strict_json(raw, max_bytes=PUBLIC_BODY_LIMIT)
            if type(value) is not dict or type(value.get("contract_version")) is not str:
                raise ContractViolation("Missing contract version.")
            version = value["contract_version"]
            value["contract_version"] = PUBLIC_VERSION
            if type(value.get("question")) is str:
                value["question"] = value["question"].strip()
            validated = self.validate(name, value)
        except (ContractViolation, UnicodeError, KeyError) as exc:
            raise ContractFailure(400, "invalid_request") from exc
        if version != PUBLIC_VERSION:
            raise ContractFailure(409, "agent_contract_mismatch")
        return validated


class ContractCodecV3(ContractCodec):
    """Codec for the private role-aware Agent routes."""

    def __init__(self):
        self.validator = v3_validator()
