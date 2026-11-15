"""Private service failures with fixed safe messages and shared HTTP policies."""
import json
from pathlib import Path

from agent.runtime.errors import WorkflowError

_POLICIES = json.loads((Path(__file__).resolve().parents[2] /
                       "docs/contracts/agent-v2.errors.json").read_text())["policies"]
_PHASES = frozenset(("admission", "context", "planning", "retrieval", "assessment",
                     "final_generation", "validation", "capacity"))
_MESSAGES = {
    "invalid_request": "The request is invalid.",
    "payload_too_large": "The request exceeds its size limit.",
    "unsupported_media_type": "Uncompressed UTF-8 JSON is required.",
    "agent_unauthorized": "Service authentication failed.",
    "route_not_found": "The requested route does not exist.",
    "method_not_allowed": "The method is not allowed for this route.",
    "agent_contract_mismatch": "The requested contract, workflow or profile is unsupported.",
    "agent_scope_mismatch": "The requested artwork or release is unavailable.",
    "agent_run_conflict": "The run ID belongs to different input.",
    "agent_run_in_progress": "The run is still processing.",
    "agent_run_not_found": "The run is not retained by this service.",
    "agent_busy": "The Agent service is at its active-run limit.",
    "service_not_ready": "The Agent service is not ready for new runs.",
    "agent_context_limit": "The required inputs exceed the model context limit.",
    "retrieval_invalid_response": "The evidence response is invalid.",
    "retrieval_unavailable": "The evidence service is unavailable.",
    "retrieval_timeout": "The evidence service exceeded its deadline.",
    "generation_invalid_response": "The model response is invalid.",
    "generation_unavailable": "The model service is unavailable.",
    "generation_timeout": "The model stage exceeded its deadline.",
    "request_timeout": "The run exceeded its deadline.",
    "agent_internal_error": "The Agent service could not complete the run.",
}


def failure(code: str, phase: str = "admission") -> WorkflowError:
    if type(code) is not str or code not in _POLICIES:
        code = "agent_internal_error"
    return WorkflowError(code, _MESSAGES[code], phase=phase if type(phase) is str and phase in _PHASES else "validation",
                         retryable=_POLICIES[code][3])


def safe_failure(error: BaseException) -> WorkflowError:
    if isinstance(error, WorkflowError):
        return failure(error.code, error.phase)
    return failure("agent_internal_error", "validation")


def private_status(code: str) -> int:
    return _POLICIES.get(code, _POLICIES["agent_internal_error"])[0]
