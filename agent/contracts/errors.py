"""Safe public mappings shared with Go and the versioned interface contract."""
from dataclasses import dataclass
import json
from pathlib import Path

from agent.runtime.validation import ContractViolation
from agent.runtime.validation_v2 import ContractValidatorV2

_PATH = Path(__file__).resolve().parents[2] / "docs/contracts/agent-v2.errors.json"
_POLICY = json.loads(_PATH.read_text())
_VALIDATOR = ContractValidatorV2()


@dataclass(frozen=True)
class ContractFailure(Exception):
    status: int
    code: str
    retryable: bool = False
    reconcile: bool = False

    def public_error(self) -> dict:
        return {"code": self.code, "message": _POLICY["messages"][self.code],
                "retryable": self.retryable}


def map_agent_failure(status: int, error: dict) -> ContractFailure:
    """Return fixed safe prose; an in-progress run requires reconciliation first."""
    invalid = ContractFailure(502, "agent_invalid_response")
    try:
        _VALIDATOR.validate("AgentError", error)
    except ContractViolation:
        return invalid
    policy = _POLICY["policies"].get(error["code"])
    if policy is None or type(status) is not int or status != policy[0] or error["retryable"] != policy[3]:
        return invalid
    return ContractFailure(policy[1], policy[2], policy[3],
                           reconcile=error["code"] == "agent_run_in_progress")
