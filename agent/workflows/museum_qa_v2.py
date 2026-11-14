"""Bounded v2 conversation workflow with injected model and real evidence ports.

The Agent owns one run, not a conversation store or a model server. A successful
outcome proposes memory at the immutable base revision; Go decides whether it
may commit. The v1 entry point remains available for existing integrations.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from time import monotonic
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.errors import NodeCancelledError
from langchain_core.globals import get_debug, get_verbose
from langchain_core.tracers.context import tracing_v2_callback_var
from langsmith import tracing_context

from agent.prompts.v2 import PROMPT_VERSION, build_messages
from agent.runtime.context_v2 import (
    build_answer_input, build_plan_input, fit_history, validate_run_context,
)
from agent.runtime.errors import WorkflowError
from agent.runtime.validation import ContractViolation, TRACE_ID
from agent.runtime.validation_v2 import (
    ContractValidatorV2, assemble_answer, resolve_language, resolve_translation,
    validate_plan, validate_refinement,
)
from agent.tools.evidence import EvidenceManifest
from agent.workflows.ports import EvidenceSearch, ModelProfile, RunBudget, StructuredModel

WORKFLOW_ID = "museum_qa_v2"
SCHEMA_VERSION = "agent-schema-v2"
RETRIEVAL_STRATEGY = "legacy-facet-v0.1"


class State(TypedDict, total=False):
    run: dict
    retained_run: dict
    plan: dict
    decision: dict
    translation: dict | None
    answer_language: str
    evidence: list[dict]
    catalog: list[dict]
    executed_queries: list[dict]
    complete_scope: bool
    model_calls: int
    retrieval_calls: int
    refinement_rounds: int
    phase_timings_ms: dict[str, int]
    answer: dict
    request_results: list[dict]
    memory_update: dict


@dataclass(frozen=True)
class WorkflowOutcomeV2:
    """Detached C-owned result fields; B supplies transport/model execution identity."""

    scope: dict
    answer_language: str
    language_decision: dict
    answer: dict
    requests: list[dict]
    request_results: list[dict]
    evidence_snapshot: list[dict]
    catalog_snapshot: list[dict]
    reused_chunk_ids: list[str]
    executed_queries: list[dict]
    conversation_update: dict
    model_calls: int
    retrieval_calls: int
    refinement_rounds: int
    diagnostics: dict

    def result_fields(self) -> dict:
        """Return the RunResult fields owned by C, excluding B's execution record."""
        names = (
            "scope", "answer_language", "language_decision", "answer", "requests",
            "request_results", "evidence_snapshot", "catalog_snapshot", "reused_chunk_ids",
            "executed_queries", "conversation_update",
        )
        return deepcopy({name: getattr(self, name) for name in names})

    def execution_fields(self) -> dict:
        """B adds verified profile/model identity and measured total duration."""
        return {
            "workflow_id": WORKFLOW_ID, "prompt_version": PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION, "retrieval_strategy_version": RETRIEVAL_STRATEGY,
            "model_calls": self.model_calls, "retrieval_calls": self.retrieval_calls,
            "refinement_rounds": self.refinement_rounds,
        }


class MuseumQAWorkflowV2:
    """Reusable dependencies with isolated per-run graph state and no hidden retries."""

    def __init__(self, *, model: StructuredModel, retrieval: EvidenceSearch,
                 manifest: EvidenceManifest, profile: ModelProfile, prompt_sizer):
        self.model = model
        self.retrieval = retrieval
        self.manifest = manifest
        self.profile = profile
        self.prompt_sizer = prompt_sizer
        self.validator = ContractValidatorV2()

    async def run(self, request: dict, *, request_id: str,
                  budget: RunBudget) -> WorkflowOutcomeV2:
        # Detach before the first await; caller mutations cannot rebind the run.
        run = deepcopy(request)
        budget.check_active()
        run = self._validate_input(run, request_id)
        if get_debug() or get_verbose():
            raise WorkflowError("service_not_ready", "Raw workflow logging must be disabled.",
                                phase="admission", retryable=True)

        async def call_model(state: State, operation: str, payload: dict,
                             schema_name: str) -> tuple[dict, dict, int]:
            budget.check_active()
            output_tokens = (self.profile.plan_output_tokens if operation == "plan"
                             else self.profile.answer_output_tokens)
            schema = self.validator.schema(schema_name)
            fitted = fit_history(
                operation, payload, schema, prompt_sizer=self.prompt_sizer,
                context_limit=self.profile.context_tokens, output_tokens=output_tokens,
                safety_tokens=512,
            )
            # Sizing can consume time. Do not advertise refinement if its full
            # assessment + retrieval + final allowance no longer remains.
            if operation == "assess" and fitted["allow_refinement"] and budget.remaining_seconds() < 38:
                fitted["allow_refinement"] = False
                schema_name = "FinalDecision"
                schema = self.validator.schema(schema_name)
                fitted = fit_history(
                    operation, fitted, schema, prompt_sizer=self.prompt_sizer,
                    context_limit=self.profile.context_tokens, output_tokens=output_tokens,
                    safety_tokens=512,
                )
            input_name = ("PlanInput" if operation == "plan" else
                          "AnswerInput" if fitted["allow_refinement"] else "FinalInput")
            try:
                self.validator.validate(input_name, fitted)
            except ContractViolation as exc:
                raise WorkflowError("agent_internal_error", "The prepared model context is invalid.",
                                    phase="context") from exc
            phase = {"plan": "planning", "assess": "assessment", "final": "final_generation"}[operation]
            budget.check_active()
            remaining = budget.remaining_seconds()
            if remaining <= 0:
                raise WorkflowError("request_timeout", "The run deadline was exceeded.",
                                    phase=phase, retryable=True)
            started = monotonic()
            deadline = started + min(10.0 if operation == "plan" else 15.0, remaining)
            try:
                completion = await self.model.complete_structured(
                    operation=operation, messages=build_messages(operation, fitted, schema),
                    output_schema=deepcopy(schema), profile=self.profile, deadline=deadline,
                    trace_context={"request_id": request_id},
                )
            except WorkflowError:
                budget.check_active()
                raise
            budget.check_active()
            if monotonic() > deadline:
                raise WorkflowError("generation_timeout", "The model stage exceeded its deadline.",
                                    phase=phase, retryable=True)
            try:
                decision = deepcopy(completion.data)
                self.validator.validate(schema_name, decision)
            except (ContractViolation, AttributeError, RecursionError) as exc:
                raise WorkflowError("generation_invalid_response", "The provider returned an invalid decision.",
                                    phase=phase) from exc
            if operation == "plan":
                validate_plan(run, decision, self.validator, plan_input=fitted,
                              complete_scope=state["complete_scope"])
            elif decision["action"] == "refine":
                validate_refinement(
                    state["plan"], decision, state["executed_queries"], self.validator,
                    complete_scope=state["complete_scope"],
                    allow_refinement=fitted["allow_refinement"],
                )
            else:
                assemble_answer(run, state["plan"], decision, state["evidence"],
                                state["catalog"], self.validator)
            budget.check_active()
            if monotonic() > deadline:
                raise WorkflowError("generation_timeout", "The model stage exceeded its deadline.",
                                    phase=phase, retryable=True)
            return decision, fitted, int((monotonic() - started) * 1000)

        async def prepare(state: State) -> dict:
            budget.check_active()
            evidence = deepcopy(run["conversation_state"]["verified_evidence"])
            scope = run["scope"]
            return {
                "retained_run": deepcopy(run), "catalog": deepcopy(run["context"]["catalog_facts"]),
                "evidence": evidence, "executed_queries": [], "model_calls": 0,
                "retrieval_calls": 0, "refinement_rounds": 0, "phase_timings_ms": {},
                "complete_scope": self.manifest.complete_scope(
                    scope["artwork_id"], scope["dataset_version"], evidence),
            }

        async def plan_node(state: State) -> dict:
            decision, payload, elapsed = await call_model(
                state, "plan", build_plan_input(run, self.manifest), "Plan",
            )
            language = resolve_language(run, decision["language_decision"])
            translation = resolve_translation(
                run, decision["language_decision"], recent_turns=payload["recent_turns"],
            )
            if decision["action"] in {"direct_response", "clarify", "out_of_scope"}:
                assemble_answer(run, decision, decision, state["evidence"], state["catalog"], self.validator)
            retained = deepcopy(run)
            retained["context"]["recent_turns"] = payload["recent_turns"]
            return {
                "plan": decision, "translation": translation,
                "answer_language": language["answer_language"], "retained_run": retained,
                "model_calls": 1, "phase_timings_ms": {"planning": elapsed},
            }

        async def lookups(state: State, queries: list[dict], *, refinement: bool) -> dict:
            started = monotonic()
            scope = run["scope"]
            evidence = deepcopy(state["evidence"])
            executed = deepcopy(state["executed_queries"])
            if refinement:
                budget.check_active()
                required = len(queries) * 3 + 15 + 2
                if budget.remaining_seconds() < required:
                    raise WorkflowError("request_timeout", "Insufficient time remains for bounded refinement.",
                                        phase="assessment", retryable=True)
            for query in queries:
                budget.check_active()
                # All facts in the release are already available. Remaining
                # planned lookups would be redundant; record only executed ones.
                if self.manifest.complete_scope(scope["artwork_id"], scope["dataset_version"], evidence):
                    break
                if len(executed) >= 4:
                    raise WorkflowError("generation_invalid_response", "The lookup limit was exceeded.",
                                        phase="validation")
                tool_input = {
                    "tool_version": "evidence-tool-v1", "query_id": query["id"],
                    "request_ids": list(query["request_ids"]), "query": query["query"],
                    **scope, "top_k": 2,
                }
                remaining = budget.remaining_seconds()
                if remaining <= 0:
                    raise WorkflowError("request_timeout", "The run deadline was exceeded.",
                                        phase="retrieval", retryable=True)
                try:
                    reply = await self.retrieval.search(
                        tool_input, request_id=request_id, remaining_seconds=remaining,
                    )
                except WorkflowError:
                    budget.check_active()
                    raise
                budget.check_active()
                self._validate_tool_result(tool_input, reply)
                evidence = self.manifest.merge(
                    scope["artwork_id"], scope["dataset_version"], evidence, reply["candidates"],
                )
                executed.append(deepcopy(query))
            timings = dict(state["phase_timings_ms"])
            timings["refinement_retrieval" if refinement else "retrieval"] = int((monotonic() - started) * 1000)
            return {
                "evidence": evidence, "executed_queries": executed, "retrieval_calls": len(executed),
                "refinement_rounds": 1 if refinement else 0, "phase_timings_ms": timings,
                "complete_scope": self.manifest.complete_scope(
                    scope["artwork_id"], scope["dataset_version"], evidence),
            }

        async def retrieve_node(state: State) -> dict:
            return await lookups(state, state["plan"]["queries"], refinement=False)

        async def assess_node(state: State) -> dict:
            allow = not state["complete_scope"] and budget.remaining_seconds() >= 38
            payload = build_answer_input(
                state["retained_run"], state["plan"]["requests"], state["executed_queries"],
                state["evidence"], answer_language=state["answer_language"],
                complete_scope=state["complete_scope"], allow_refinement=allow,
                translation=state["translation"],
            )
            decision, fitted, elapsed = await call_model(
                state, "assess", payload, "AssessmentDecision" if allow else "FinalDecision",
            )
            retained = deepcopy(state["retained_run"])
            retained["context"]["recent_turns"] = fitted["recent_turns"]
            return {
                "decision": decision, "retained_run": retained, "model_calls": 2,
                "phase_timings_ms": {**state["phase_timings_ms"], "assessment": elapsed},
            }

        async def refine_node(state: State) -> dict:
            return await lookups(state, state["decision"]["queries"], refinement=True)

        async def final_node(state: State) -> dict:
            payload = build_answer_input(
                state["retained_run"], state["plan"]["requests"], state["executed_queries"],
                state["evidence"], answer_language=state["answer_language"],
                complete_scope=state["complete_scope"], allow_refinement=False,
                translation=state["translation"],
            )
            decision, fitted, elapsed = await call_model(state, "final", payload, "FinalDecision")
            retained = deepcopy(state["retained_run"])
            retained["context"]["recent_turns"] = fitted["recent_turns"]
            return {
                "decision": decision, "retained_run": retained, "model_calls": 3,
                "phase_timings_ms": {**state["phase_timings_ms"], "final_generation": elapsed},
            }

        async def finalize(state: State) -> dict:
            budget.check_active()
            answer, results, memory = assemble_answer(
                run, state["plan"], state.get("decision", state["plan"]), state["evidence"],
                state["catalog"], self.validator,
            )
            budget.check_active()
            return {"answer": answer, "request_results": results, "memory_update": memory}

        graph = StateGraph(State)
        for name, function in (
            ("prepare", prepare), ("plan", plan_node), ("retrieve", retrieve_node),
            ("assess", assess_node), ("refine", refine_node), ("final", final_node), ("finalize", finalize),
        ):
            graph.add_node(name, function)
        graph.add_edge(START, "prepare")
        graph.add_edge("prepare", "plan")
        graph.add_conditional_edges("plan", lambda state: state["plan"]["action"], {
            "direct_response": "finalize", "clarify": "finalize", "out_of_scope": "finalize",
            "retrieve": "retrieve", "answer_from_context": "assess",
        })
        graph.add_edge("retrieve", "assess")
        graph.add_conditional_edges("assess", lambda state: state["decision"]["action"], {
            "finish": "finalize", "clarify": "finalize", "refine": "refine",
        })
        graph.add_edge("refine", "final")
        graph.add_edge("final", "finalize")
        graph.add_edge("finalize", END)
        token = tracing_v2_callback_var.set(None)
        try:
            with tracing_context(enabled=False, parent=False):
                result = await graph.compile().ainvoke(
                    {"run": run}, config={"recursion_limit": 12, "callbacks": []},
                )
        except NodeCancelledError as exc:
            # LangGraph wraps a node's own cancellation; preserve the provider
            # cancellation contract at the workflow boundary.
            raise asyncio.CancelledError() from exc
        finally:
            tracing_v2_callback_var.reset(token)
        budget.check_active()
        return WorkflowOutcomeV2(
            scope=deepcopy(run["scope"]), answer_language=result["answer_language"],
            language_decision=deepcopy(result["plan"]["language_decision"]),
            answer=deepcopy(result["answer"]), requests=deepcopy(result["plan"].get("requests", [])),
            request_results=deepcopy(result["request_results"]), evidence_snapshot=deepcopy(result["evidence"]),
            catalog_snapshot=deepcopy(result["catalog"]),
            reused_chunk_ids=[item["chunk_id"] for item in run["conversation_state"]["verified_evidence"]],
            executed_queries=deepcopy(result["executed_queries"]),
            conversation_update={"base_revision": run["conversation_state"]["revision"],
                                 "memory": deepcopy(result["memory_update"])},
            model_calls=result["model_calls"], retrieval_calls=result["retrieval_calls"],
            refinement_rounds=result["refinement_rounds"],
            diagnostics={
                "implementation_stage": "C-v2", "complete_scope": result["complete_scope"],
                "retained_history_turns": len(result["retained_run"]["context"]["recent_turns"]),
                "phase_timings_ms": deepcopy(result["phase_timings_ms"]),
            },
        )

    def _validate_input(self, run: dict, request_id: str) -> dict:
        try:
            if type(request_id) is not str or TRACE_ID.fullmatch(request_id) is None:
                raise ContractViolation("Invalid trace ID.")
        except ContractViolation as exc:
            raise WorkflowError("invalid_request", "The workflow input is invalid.", phase="admission") from exc
        run = validate_run_context(run, self.manifest, validator=self.validator)
        if run["execution"]["profile_id"] != self.profile.profile_id:
            raise WorkflowError("agent_contract_mismatch", "The model profile does not match the run.",
                                phase="admission")
        return run

    def _validate_tool_result(self, tool_input: dict, reply: dict) -> None:
        try:
            self.validator.validate("ToolResult", reply)
            if (any(reply[key] != tool_input[key] for key in (
                    "tool_version", "query_id", "artwork_id", "dataset_version"))
                    or reply["retrieval_strategy_version"] != RETRIEVAL_STRATEGY):
                raise ContractViolation("Tool result identity mismatch.")
        except ContractViolation as exc:
            raise WorkflowError("retrieval_invalid_response", "The evidence tool returned an invalid result.",
                                phase="retrieval") from exc
