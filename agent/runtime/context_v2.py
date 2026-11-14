"""V2 input snapshots and five-pair context fitting for the Agent workflow.

The application owns committed history and state. Validate its complete snapshot
once at admission; subsequent operation projections may contain fewer historical
pairs after fitting. This module never commits or mutates conversation state.
"""

from copy import deepcopy
from pathlib import Path
from typing import Any

from agent.prompts.v2 import build_messages
from agent.runtime.context import PromptSizer
from agent.runtime.errors import WorkflowError
from agent.runtime.validation import ContractValidator, ContractViolation
from agent.tools.catalog import validate_catalog
from agent.tools.evidence import EvidenceManifest

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "docs/contracts/agent-v2.schema.json"
_CONVERSATION_FIELDS = (
    "language_preference", "auto_language", "last_answer_language", "active_topic",
    "continuity_summary", "pending_clarification",
)


def _invalid_context() -> WorkflowError:
    return WorkflowError(
        "invalid_request", "The supplied conversation context is invalid.", phase="context",
    )


def _invalid_cache() -> WorkflowError:
    return WorkflowError(
        "retrieval_invalid_response", "The cached evidence does not match the configured release.",
        phase="context",
    )


def validate_cache(manifest: EvidenceManifest, scope: dict, records: object) -> list[dict]:
    """Reject an entire corrupt cache; compatible metadata is not model context."""
    try:
        if type(records) is list and any(
            type(record) is dict and ("kind" in record or "fact_id" in record)
            for record in records
        ):
            raise _invalid_cache()
        return manifest.validate_candidates(
            scope["artwork_id"], scope["dataset_version"], records, limit=8,
        )
    except (WorkflowError, KeyError, TypeError) as exc:
        raise _invalid_cache() from exc


def validate_run_context(
    run: dict[str, Any], manifest: EvidenceManifest, *, validator: ContractValidator | None = None
) -> dict[str, Any]:
    """Validate the full original request and return a canonical detached copy.

    Admission deadlines, ownership and history authenticity remain application
    responsibilities. This checks declared identities, exact history selection,
    state relationships and canonical source integrity before planning.
    """
    validator = validator or ContractValidator(_SCHEMA_PATH)
    try:
        validator.validate("RunRequest", run)
    except ContractViolation as exc:
        # Malformed cached records are source-integrity failures, rather than
        # successful empty context. Other malformed input remains invalid_request.
        if (type(run) is dict and type(run.get("scope")) is dict
                and type(run.get("conversation_state")) is dict
                and "verified_evidence" in run["conversation_state"]):
            validate_cache(manifest, run["scope"], run["conversation_state"]["verified_evidence"])
        raise _invalid_context() from exc

    scope = run["scope"]
    try:
        artwork = manifest.artwork(scope["artwork_id"], scope["dataset_version"])
    except WorkflowError as exc:
        raise WorkflowError(
            "agent_scope_mismatch", "The requested artwork or release is not configured.",
            phase="context",
        ) from exc
    context, state = run["context"], run["conversation_state"]
    if (context["artwork_title"] != artwork["title"]
            or context["artist"] != artwork["artist"]):
        raise WorkflowError(
            "agent_scope_mismatch", "The artwork metadata does not match the configured release.",
            phase="context",
        )
    catalog = validate_catalog(
        manifest, scope["artwork_id"], scope["dataset_version"], context["catalog_facts"],
    )
    if (run["question"] != run["question"].strip() or not run["question"]
            or state["conversation_id"] != run["conversation_id"] or state["scope"] != scope):
        raise _invalid_context()
    revision = state["revision"]
    history = context["recent_turns"]
    expected_sequences = list(range(max(1, revision - 4), revision + 1))
    if ([turn["sequence"] for turn in history] != expected_sequences
            or len({turn["turn_id"] for turn in history}) != len(history)
            or (history and history[-1]["answer_language"] != state["last_answer_language"])):
        raise _invalid_context()
    pending = state["pending_clarification"]
    if pending is not None:
        fragment = pending["original_fragment"]
        if (pending["created_sequence"] > revision
                or (fragment is not None and fragment not in pending["original_question"])):
            raise _invalid_context()
    cache = validate_cache(manifest, scope, state["verified_evidence"])
    accepted = deepcopy(run)
    accepted["context"]["catalog_facts"] = catalog
    accepted["conversation_state"]["verified_evidence"] = cache
    return accepted


def _base_context(run: dict[str, Any]) -> dict[str, Any]:
    """Project a validated run, allowing an already fitted recent-turn window."""
    state, context = run["conversation_state"], run["context"]
    return deepcopy({
        "original_question": run["question"],
        "artwork_context": {"artwork_title": context["artwork_title"], "artist": context["artist"]},
        "catalog_facts": context["catalog_facts"],
        "recent_turns": context["recent_turns"],
        "conversation_context": {key: state[key] for key in _CONVERSATION_FIELDS},
    })


def build_plan_input(run: dict[str, Any], manifest: EvidenceManifest) -> dict[str, Any]:
    """Expose only planning fields; application/run identities stay out of prompts."""
    payload = _base_context(run)
    scope = run["scope"]
    cache = deepcopy(run["conversation_state"]["verified_evidence"])
    payload.update({
        "language_override": run["language_override"],
        "cached_evidence": cache,
        "complete_scope": manifest.complete_scope(scope["artwork_id"], scope["dataset_version"], cache),
        "query_policy": {"search_language": "en", "max_requests": 4,
                         "max_queries": 2, "scope_policy": "current_artwork_only"},
    })
    return payload


def build_answer_input(
    run: dict[str, Any], requests: list[dict], executed_queries: list[dict], evidence: list[dict],
    *, answer_language: str, complete_scope: bool, allow_refinement: bool,
    translation: dict | None = None,
) -> dict[str, Any]:
    """Project accepted evidence and an independently pinned translation source."""
    if (type(complete_scope) is not bool or type(allow_refinement) is not bool
            or (complete_scope and allow_refinement)):
        raise _invalid_context()
    payload = _base_context(run)
    payload.update(deepcopy({
        "answer_language": answer_language, "requests": requests,
        "executed_queries": executed_queries, "evidence": evidence,
        "complete_scope": complete_scope, "allow_refinement": allow_refinement,
        "translation": translation,
    }))
    return payload


def fit_history(
    operation: str, payload: dict[str, Any], output_schema: dict[str, Any], *,
    prompt_sizer: PromptSizer, context_limit: int, output_tokens: int, safety_tokens: int = 512,
) -> dict[str, Any]:
    """Fit whole history pairs using the verified injected provider sizer.

    Catalog, semantic memory, current message, plan, accepted evidence and exact
    selected translation text are required inputs. Only recent_turns may shrink.
    Model/template token accounting and output reserves are supplied by B.
    """
    if (type(context_limit) is not int or context_limit <= 0
            or type(output_tokens) is not int or output_tokens <= 0
            or type(safety_tokens) is not int or safety_tokens < 0):
        raise ValueError("Context sizing configuration must use positive integer limits.")
    fitted = deepcopy(payload)
    while True:
        messages = build_messages(operation, fitted, output_schema)
        count = prompt_sizer(messages, deepcopy(output_schema))
        if type(count) is not int or count < 0:
            raise ValueError("The prompt sizer must return a nonnegative integer token count.")
        if count + output_tokens + safety_tokens <= context_limit:
            return fitted
        if not fitted["recent_turns"]:
            raise WorkflowError(
                "agent_context_limit", "The required inputs exceed the model context limit.",
                phase="context",
            )
        fitted["recent_turns"].pop(0)
