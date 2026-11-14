"""Controlled v2 fixtures only; not a production model or conversation store.

The scripted provider checks supplied output schemas but performs no inference.
The commit simulator builds a next input snapshot for multi-turn workflow tests;
it does not implement B's locking, admission, replay, expiry or atomic storage.
"""
from __future__ import annotations

import asyncio
import inspect
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from time import monotonic
from uuid import UUID, uuid4, uuid5

from jsonschema import Draft202012Validator, FormatChecker, validators
from jsonschema.exceptions import ValidationError

from agent.runtime.errors import WorkflowError
from agent.runtime.validation import ContractViolation, strict_json
from agent.testing.fakes import (
    FixtureCompletion, RecordedModelCall, TestBudget, fixture_prompt_sizer,
)
from agent.tools.catalog import canonical_catalog
from agent.tools.evidence import EvidenceManifest

MOCK_MODE = "mock-model-real-retrieval"
_ROOT = Path(__file__).resolve().parents[2]
_LANGUAGES = frozenset(("en", "es", "zh-CN", "ko"))
_StrictValidator = validators.extend(
    Draft202012Validator,
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine(
        "integer", lambda checker, value: isinstance(value, int) and not isinstance(value, bool)
    ),
)


@dataclass(frozen=True)
class FixtureProfile:
    """Synthetic sizing policy, with no inference route or model identity."""
    profile_id: str = "local-ollama-v2"
    context_tokens: int = 16384
    plan_output_tokens: int = 2048
    answer_output_tokens: int = 2048


@lru_cache(maxsize=1)
def _schema():
    return json.loads((_ROOT / "docs/contracts/agent-v2.schema.json").read_text(encoding="utf-8"))


def _validate(name, payload):
    schema = {"$ref": "#/$defs/" + name, "$defs": _schema()["$defs"]}
    _StrictValidator(schema, format_checker=FormatChecker()).validate(payload)


@lru_cache(maxsize=1)
def _examples():
    return json.loads((_ROOT / "docs/contracts/agent-v2.examples.json").read_text(encoding="utf-8"))


def example(name: str, section: str = "schema_cases") -> dict:
    """Return a detached shared fixture payload; never inject its gold labels."""
    for case in _examples()[section]:
        if case["name"] == name:
            return deepcopy(case["payload"])
    raise KeyError(name)


class ScriptedModel:
    """Emit explicit decisions and validate the actual supplied v2 schema.

    This intentionally does not infer visitor intent or read expected outcomes
    from the fixture file. Callbacks receive only the model's recorded inputs.
    """
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
        if isinstance(response, (Exception, asyncio.CancelledError)):
            raise response
        if callable(response):
            response = response(call)
            if inspect.isawaitable(response):
                response = await response
        try:
            data = strict_json(response) if isinstance(response, str) else deepcopy(response)
            # Validate the supplied reachable schema, not the old v1 registry.
            _StrictValidator(output_schema, format_checker=FormatChecker()).validate(data)
        except (ContractViolation, ValidationError, ValueError, TypeError) as exc:
            raise WorkflowError(
                "generation_invalid_response", "The scripted completion violates its schema.",
                phase="planning" if operation == "plan" else "assessment",
            ) from exc
        return FixtureCompletion(data)


def initial_state(*, artwork_id="aic-28560", dataset_version="aic-seed-v0.1",
                  conversation_id="22222222-2222-4222-8222-222222222222") -> dict:
    state = example("initial_conversation_state")
    state["conversation_id"] = conversation_id
    state["scope"] = {"artwork_id": artwork_id, "dataset_version": dataset_version}
    return state


def make_run(*, artwork_id="aic-28560", question="What is it made of?",
             language_override=None, history=None, conversation_state=None,
             manifest=None) -> dict:
    """Create a scoped synthetic run; invalid test mutations remain explicit."""
    manifest = manifest or EvidenceManifest.load()
    artwork = manifest.artwork(artwork_id, manifest.dataset_version)
    turns = deepcopy(history or [])
    state = deepcopy(conversation_state) if conversation_state is not None else initial_state(
        artwork_id=artwork_id, dataset_version=manifest.dataset_version,
    )
    if conversation_state is None and turns:
        state["revision"] = turns[-1]["sequence"]
        state["last_answer_language"] = turns[-1]["answer_language"]
    return {
        "contract_version": "agent-run-v2",
        "run_id": str(uuid4()),
        "conversation_id": state["conversation_id"],
        "client_message_id": str(uuid4()),
        "question": question,
        "language_override": language_override,
        "scope": {"artwork_id": artwork_id, "dataset_version": manifest.dataset_version},
        "context": {
            "artwork_title": artwork["title"], "artist": artwork["artist"],
            "catalog_facts": canonical_catalog(manifest, artwork_id, manifest.dataset_version),
            "recent_turns": turns,
        },
        "conversation_state": state,
        "execution": {
            "workflow_id": "museum_qa_v2", "profile_id": "local-ollama-v2",
            "deadline_at": (datetime.now(timezone.utc) + timedelta(seconds=55))
                .isoformat().replace("+00:00", "Z"),
        },
    }


def language_decision(language="en", *, detected_language=None) -> dict:
    return {"detected_language": detected_language or language,
            "explicit_instruction": None, "translation": None, "answer_language": language}


def memory_update(*, topic=None, summary="", pending=None) -> dict:
    return {"active_topic": topic, "continuity_summary": summary,
            "pending_update": deepcopy(pending) if pending is not None else {"action": "keep"}}


def medium_plan(run, *, language="en") -> dict:
    query = f"What material is {run['context']['artwork_title']} made of?"
    return {
        "action": "retrieve", "language_decision": language_decision(language),
        "requests": [{"id": "r1", "original_fragment": run["question"],
                      "intent": query, "kind": "artwork_fact", "resolution": "clear"}],
        "queries": [{"id": "q1", "request_ids": ["r1"], "query": query}],
    }


def medium_answer(run, *, text="The museum lists the medium as oil on canvas.") -> dict:
    return {
        "action": "finish",
        "results": [{"request_id": "r1", "support": "supported", "text": text,
                     "citation_ids": [run["scope"]["artwork_id"] + "-medium"]}],
        "memory_update": memory_update(topic="The artwork's material",
                                        summary="The visitor asked about the artwork's material."),
    }


def simulate_commit(run: dict, result: dict) -> tuple[dict, dict]:
    """Derive one successful next snapshot for tests, without saving anything.

    The caller supplies an already validated workflow result. This independent
    fixture calculation is not a replacement for Go's production commit logic.
    It intentionally has no expiry, retry registry, locking or HTTP handlers.
    """
    state = deepcopy(run["conversation_state"])
    assert result["scope"] == run["scope"] == state["scope"]
    assert result["conversation_update"]["base_revision"] == state["revision"]
    sequence = state["revision"] + 1
    assert sequence <= 2147483647
    decision = result["language_decision"]
    instruction = decision["explicit_instruction"]
    candidate = run["language_override"] if run["language_override"] is not None else state["language_preference"]
    effective = instruction["preference"] if instruction else candidate
    translation = decision["translation"]
    answer_language = translation["target_language"] if translation else (
        effective if effective != "auto" else (
            decision["detected_language"] if decision["detected_language"] in _LANGUAGES
            else state["auto_language"]
        )
    )
    assert result["answer_language"] == decision["answer_language"] == answer_language
    state["language_preference"] = (
        instruction["preference"] if instruction and instruction["purpose"] == "conversation_setting"
        else candidate
    )
    state["last_answer_language"] = answer_language
    if decision["detected_language"] in _LANGUAGES:
        state["auto_language"] = decision["detected_language"]
    memory = result["conversation_update"]["memory"]
    state["active_topic"] = memory["active_topic"]
    state["continuity_summary"] = memory["continuity_summary"]
    pending = memory["pending_update"]
    if pending["action"] == "clear":
        state["pending_clarification"] = None
    elif pending["action"] == "set":
        old = state["pending_clarification"]
        inherited = pending["source"] == "pending_clarification"
        assert not inherited or old is not None
        request_id = pending["request_id"]
        request = next((r for r in result["requests"] if r["id"] == request_id), None)
        part = next((r for r in result["request_results"] if r["request_id"] == request_id), None)
        if request_id is not None:
            assert request is not None and part is not None
            assert request["resolution"] == "needs_clarification"
            assert part["support"] == "clarification_needed" and not part["citation_ids"]
        state["pending_clarification"] = {
            "original_question": old["original_question"] if inherited else run["question"],
            "original_fragment": old["original_fragment"] if inherited else (
                request["original_fragment"] if request else None),
            "unresolved_intent": request["intent"] if request else (
                old["unresolved_intent"] if inherited else None),
            "question": part["text"] if part else result["answer"]["text"],
            "created_sequence": sequence,
        }
    cache = []
    seen = set()
    for record in [*result["answer"]["citations"], *state["verified_evidence"]]:
        if "chunk_id" not in record or record["chunk_id"] in seen:
            continue
        seen.add(record["chunk_id"])
        cache.append(deepcopy(record))
    state["verified_evidence"] = cache[:8]
    state["revision"] = sequence
    turn = {
        "turn_id": str(uuid5(UUID(state["conversation_id"]), f"fixture-turn-{sequence}")),
        "sequence": sequence, "question": run["question"],
        "answer_language": answer_language,
        "answer_status": result["answer"]["status"], "answer_mode": "llm",
        "answer_text": result["answer"]["text"],
    }
    _validate("ConversationState", state)
    _validate("HistoryTurn", turn)
    return state, turn
