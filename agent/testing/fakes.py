"""Controlled substitutes for local C tests only; never a production fallback."""
from __future__ import annotations

import asyncio
import inspect
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import monotonic

from agent.runtime.errors import WorkflowError
from agent.runtime.validation import ContractValidator, ContractViolation, strict_json
from agent.tools.evidence import EvidenceManifest

MOCK_MODE = "mock-model-real-retrieval"


@dataclass(frozen=True)
class FixtureProfile:
    """Synthetic context policy, with no model route or model identity."""
    profile_id: str = "local-ollama-v1"
    context_tokens: int = 8192
    plan_output_tokens: int = 768
    answer_output_tokens: int = 1536


@dataclass(frozen=True)
class RecordedModelCall:
    operation: str
    messages: list[dict[str, str]]
    output_schema: dict
    max_output_tokens: int
    timeout_seconds: float
    request_id: str


@dataclass(frozen=True)
class FixtureCompletion:
    data: dict
    provider: str = "scripted-test-only"
    model_name: str = "none"
    model_digest: str = "none"
    completion_reason: str = "scripted"
    usage: None = None
    duration_ms: int = 0


class TestBudget:
    """Local test clock; B supplies the production deadline/admission system."""

    def __init__(self, seconds: float = 55):
        self.deadline = monotonic() + seconds

    def check_active(self):
        if self.remaining_seconds() <= 0:
            raise WorkflowError("request_timeout", "The run deadline was exceeded.",
                                phase="validation", retryable=True)

    def remaining_seconds(self):
        return max(0.0, self.deadline - monotonic())


class ScriptedModel:
    """Return explicit test decisions, recording the exact inputs received."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def complete_structured(self, *, operation, messages, output_schema,
                                  profile, deadline, trace_context):
        call = RecordedModelCall(
            operation, deepcopy(messages), deepcopy(output_schema),
            profile.plan_output_tokens if operation == "plan" else profile.answer_output_tokens,
            max(0.0, deadline - monotonic()), trace_context["request_id"],
        )
        self.calls.append(deepcopy(call))
        if not self.responses:
            raise AssertionError("The workflow made an unexpected model call.")
        response = self.responses.pop(0)
        await asyncio.sleep(0)
        if isinstance(response, Exception):
            raise response
        if callable(response):
            response = response(call)
            if inspect.isawaitable(response):
                response = await response
        # Exercise strict inner-JSON parsing with scripted data. This is not
        # an implementation of B's Ollama outer transport/readiness checks.
        try:
            data = strict_json(response) if isinstance(response, str) else deepcopy(response)
            name = output_schema["$ref"].split("/")[-1]
            ContractValidator().validate(name, data)
        except ContractViolation as exc:
            raise WorkflowError(
                "generation_invalid_response", "The scripted completion violates its schema.",
                phase="planning" if operation == "plan" else "assessment",
            ) from exc
        return FixtureCompletion(data)


def fixture_prompt_sizer(messages, output_schema):
    """Synthetic count only. It makes no assertion about a real tokenizer."""
    return 1000


def sample_run(*, artwork_id="aic-28560", question="What is it made of?",
               answer_language="en", history=None):
    manifest = EvidenceManifest.load()
    artwork = manifest.artwork(artwork_id, manifest.dataset_version)
    return {
        "contract_version": "agent-run-v1",
        "run_id": "11111111-1111-4111-8111-111111111111",
        "conversation_id": "22222222-2222-4222-8222-222222222222",
        "client_message_id": "33333333-3333-4333-8333-333333333333",
        "question": question, "answer_language": answer_language,
        "scope": {"artwork_id": artwork_id, "dataset_version": manifest.dataset_version},
        "context": {"artwork_title": artwork["title"], "artist": artwork["artist"],
                    "recent_turns": deepcopy(history or [])},
        "execution": {
            "workflow_id": "museum_qa_v1", "profile_id": "local-ollama-v1",
            "deadline_at": (datetime.now(timezone.utc) + timedelta(seconds=55))
                .isoformat().replace("+00:00", "Z"),
        },
    }


def medium_plan(run):
    question = f"What material is {run['context']['artwork_title']} made of?"
    return {
        "action": "retrieve",
        "requests": [{"id": "r1", "original_fragment": run["question"],
                      "question": question, "scope": "current_artwork"}],
        "queries": [{"id": "q1", "request_ids": ["r1"], "query": question}],
    }


def medium_answer(run):
    return {
        "action": "finish",
        "results": [{"request_id": "r1", "support": "supported",
                     "text": "The museum lists the medium as oil on canvas.",
                     "citation_chunk_ids": [run["scope"]["artwork_id"] + "-medium"]}],
    }
