"""Prepare immutable model inputs and fit history through an injected sizer.

This module owns no tokenizer, provider profile or deployment configuration.
The runtime supplies a validated prompt sizer for its pinned model/template.
"""

from collections.abc import Callable
from copy import deepcopy
from typing import Any

from agent.prompts import build_messages
from agent.runtime.errors import WorkflowError

# The sizer counts rendered messages, schema and provider template overhead.
PromptSizer = Callable[[list[dict[str, str]], dict[str, Any]], int]
_ALLOWED_HISTORY_STATUSES = {
    "fixture": frozenset({"answered", "insufficient", "unsupported_question"}),
    "llm": frozenset({"answered", "partially_answered", "insufficient",
                      "clarification_needed", "out_of_scope"}),
}


def _invalid_context() -> WorkflowError:
    return WorkflowError("invalid_request", "The supplied conversation context is invalid.",
                         phase="context")


def _base_context(run: dict[str, Any]) -> dict[str, Any]:
    """Project an already schema-validated and manifest-checked run request."""
    question = run["question"]
    if not isinstance(question, str) or question != question.strip() or not question:
        raise _invalid_context()
    if run["answer_language"] not in {"en", "zh-CN"}:
        raise _invalid_context()
    context = run["context"]
    history = context["recent_turns"]
    if not isinstance(history, list) or len(history) > 3:
        raise _invalid_context()
    previous_sequence = 0
    seen_ids = set()
    for turn in history:
        sequence = turn["sequence"]
        if (type(sequence) is not int or sequence <= previous_sequence
                or turn["turn_id"] in seen_ids
                or turn["answer_status"] not in _ALLOWED_HISTORY_STATUSES.get(
                    turn["answer_mode"], frozenset())):
            raise _invalid_context()
        previous_sequence = sequence
        seen_ids.add(turn["turn_id"])
    return deepcopy({
        "original_question": question,
        "answer_language": run["answer_language"],
        "artwork_context": {
            "artwork_title": context["artwork_title"],
            "artist": context["artist"],
        },
        "recent_turns": history,
    })


def build_plan_input(run: dict[str, Any]) -> dict[str, Any]:
    """Expose identity/history and fixed query policy, without runtime identities."""
    payload = _base_context(run)
    payload["query_policy"] = {
        "search_language": "en",
        "max_requests": 4,
        "max_queries": 2,
        "scope_policy": "current_artwork_only",
    }
    return payload


def build_answer_input(
    run: dict[str, Any],
    requests: list[dict[str, Any]],
    executed_queries: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    *,
    complete_scope: bool,
    allow_refinement: bool,
) -> dict[str, Any]:
    """Project accepted snapshots; the caller validates AnswerInput/FinalInput."""
    if type(complete_scope) is not bool or type(allow_refinement) is not bool:
        raise _invalid_context()
    if complete_scope and allow_refinement:
        raise _invalid_context()
    payload = _base_context(run)
    payload.update(deepcopy({
        "requests": requests,
        "executed_queries": executed_queries,
        "evidence": evidence,
        "complete_scope": complete_scope,
        "allow_refinement": allow_refinement,
    }))
    return payload


def fit_history(
    operation: str,
    payload: dict[str, Any],
    output_schema: dict[str, Any],
    *,
    prompt_sizer: PromptSizer,
    context_limit: int,
    output_tokens: int,
    safety_tokens: int = 512,
) -> dict[str, Any]:
    """Drop oldest whole history turns until required inputs fit, or fail.

    The sizer is supplied by the runtime, never estimated from character counts
    here. It receives the full rendered messages and the provider output schema.
    Return a detached payload; subtract history lengths to obtain the drop count.
    Required question, plan and evidence are never removed or shortened.
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
