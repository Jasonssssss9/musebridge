"""M1 bounded LangGraph: plan -> real retrieval -> assess -> validate.

M1 intentionally disables refinement. M2 adds that explicit bounded branch
after the provider and shared runtime are integrated and evaluated.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from time import monotonic
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langchain_core.globals import get_debug, get_verbose
from langchain_core.tracers.context import tracing_v2_callback_var
from langsmith import tracing_context

from agent.prompts import PROMPT_VERSION, build_messages
from agent.runtime.context import build_answer_input, build_plan_input, fit_history
from agent.runtime.errors import WorkflowError
from agent.runtime.validation import (
    ContractValidator, ContractViolation, TRACE_ID, assemble_answer,
    validate_plan,
)
from agent.tools.evidence import EvidenceManifest
from agent.workflows.ports import EvidenceSearch, ModelProfile, RunBudget, StructuredModel

WORKFLOW_ID = "museum_qa_v1"
SCHEMA_VERSION = "agent-schema-v1"
RETRIEVAL_STRATEGY = "legacy-facet-v0.1"


class State(TypedDict, total=False):
    run: dict
    retained_run: dict
    plan: dict
    decision: dict
    evidence: list[dict]
    executed_queries: list[dict]
    complete_scope: bool
    model_calls: int
    retrieval_calls: int
    phase_timings_ms: dict[str, int]
    answer: dict
    request_results: list[dict]


@dataclass(frozen=True)
class WorkflowOutcome:
    """Terminal C result; B supplies resolved model identity and HTTP envelope."""

    scope: dict
    answer: dict
    request_results: list[dict]
    model_calls: int
    retrieval_calls: int
    refinement_rounds: int
    diagnostics: dict

    def result_fields(self) -> dict:
        """The C-owned fields of RunResult, detached from graph state."""
        return deepcopy({"scope": self.scope, "answer": self.answer,
                         "request_results": self.request_results})

    def execution_fields(self) -> dict:
        """B adds profile/model identity and actual total duration."""
        return {"workflow_id": WORKFLOW_ID, "prompt_version": PROMPT_VERSION,
                "schema_version": SCHEMA_VERSION,
                "retrieval_strategy_version": RETRIEVAL_STRATEGY,
                "model_calls": self.model_calls, "retrieval_calls": self.retrieval_calls,
                "refinement_rounds": self.refinement_rounds}


class MuseumQAWorkflow:
    """Reusable immutable dependencies; run state and graph closures are per call."""

    def __init__(self, *, model: StructuredModel, retrieval: EvidenceSearch,
                 manifest: EvidenceManifest, profile: ModelProfile, prompt_sizer):
        self.model = model
        self.retrieval = retrieval
        self.manifest = manifest
        self.prompt_sizer = prompt_sizer
        self.profile = profile
        self.validator = ContractValidator()

    async def run(self, request: dict, *, request_id: str,
                  budget: RunBudget) -> WorkflowOutcome:
        # Copy before the first await; caller mutations cannot change scope/history.
        run = deepcopy(request)
        self._validate_input(run, request_id)
        budget.check_active()
        if get_debug() or get_verbose():
            raise WorkflowError("service_not_ready", "Raw workflow logging must be disabled.",
                                phase="admission", retryable=True)

        async def call_model(state: State, operation: str, payload: dict,
                             schema_name: str) -> tuple[dict, dict, int]:
            budget.check_active()
            output_tokens = (self.profile.plan_output_tokens if operation == "plan"
                             else self.profile.answer_output_tokens)
            schema = self.validator.schema(schema_name)
            payload = fit_history(
                operation, payload, schema, prompt_sizer=self.prompt_sizer,
                context_limit=self.profile.context_tokens, output_tokens=output_tokens, safety_tokens=512,
            )
            self.validator.validate("PlanInput" if operation == "plan" else "FinalInput", payload)
            phase = "planning" if operation == "plan" else "assessment"
            budget.check_active()
            cap = 10.0 if operation == "plan" else 15.0
            remaining = budget.remaining_seconds()
            if remaining <= 0:
                raise WorkflowError("request_timeout", "The run deadline was exceeded.",
                                    phase=phase, retryable=True)
            started = monotonic()
            stage_deadline = started + min(cap, remaining)
            try:
                completion = await self.model.complete_structured(
                    operation=operation, messages=build_messages(operation, payload, schema),
                    output_schema=deepcopy(schema), profile=self.profile,
                    deadline=stage_deadline, trace_context={"request_id": request_id},
                )
            except WorkflowError:
                budget.check_active()
                raise
            budget.check_active()
            # B actively enforces the deadline; C also rejects late results.
            if monotonic() > stage_deadline:
                raise WorkflowError("generation_timeout", "The model stage exceeded its deadline.",
                                    phase=phase, retryable=True)
            try:
                decision = deepcopy(completion.data)
                self.validator.validate(schema_name, decision)
            except (ContractViolation, AttributeError, RecursionError) as exc:
                raise WorkflowError(
                    "generation_invalid_response", "The provider returned an invalid decision.",
                    phase=phase,
                ) from exc
            if operation == "plan":
                validate_plan(state["run"], decision, self.validator)
            else:
                assemble_answer(state["plan"], decision, state["evidence"], self.validator)
            budget.check_active()
            if monotonic() > stage_deadline:
                raise WorkflowError("generation_timeout", "The model stage exceeded its deadline.",
                                    phase=phase, retryable=True)
            elapsed = int((monotonic() - started) * 1000)
            return decision, payload, elapsed

        async def prepare(state: State) -> dict:
            budget.check_active()
            # Reject inconsistent history before invoking the model.
            build_plan_input(state["run"])
            return {"retained_run": deepcopy(state["run"]), "evidence": [],
                    "executed_queries": [], "complete_scope": False, "model_calls": 0,
                    "retrieval_calls": 0, "phase_timings_ms": {}}

        async def plan_node(state: State) -> dict:
            decision, payload, elapsed = await call_model(
                state, "plan", build_plan_input(state["retained_run"]), "Plan",
            )
            retained = deepcopy(state["retained_run"])
            retained["context"]["recent_turns"] = payload["recent_turns"]
            return {"plan": decision, "retained_run": retained, "model_calls": 1,
                    "phase_timings_ms": {"planning": elapsed}}

        async def retrieve_node(state: State) -> dict:
            started = monotonic()
            scope = state["run"]["scope"]
            evidence = []
            executed = []
            for query in state["plan"]["queries"]:
                budget.check_active()
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
                    # A total run expiry wins over a child tool timeout.
                    budget.check_active()
                    raise
                budget.check_active()
                self._validate_tool_result(tool_input, reply)
                evidence = self.manifest.merge(
                    scope["artwork_id"], scope["dataset_version"], evidence, reply["candidates"],
                )
                executed.append(deepcopy(query))
            timings = dict(state["phase_timings_ms"])
            timings["retrieval"] = int((monotonic() - started) * 1000)
            return {
                "evidence": evidence, "executed_queries": executed,
                "retrieval_calls": len(executed),
                "complete_scope": self.manifest.complete_scope(
                    scope["artwork_id"], scope["dataset_version"], evidence),
                "phase_timings_ms": timings,
            }

        async def assess_node(state: State) -> dict:
            payload = build_answer_input(
                state["retained_run"], state["plan"]["requests"],
                state["executed_queries"], state["evidence"],
                complete_scope=state["complete_scope"], allow_refinement=False,
            )
            decision, fitted, elapsed = await call_model(
                state, "assess", payload, "FinalDecision",
            )
            timings = dict(state["phase_timings_ms"])
            timings["assessment"] = elapsed
            retained = deepcopy(state["retained_run"])
            retained["context"]["recent_turns"] = fitted["recent_turns"]
            return {"decision": decision, "model_calls": 2,
                    "retained_run": retained, "phase_timings_ms": timings}

        async def finalize(state: State) -> dict:
            budget.check_active()
            answer, results = assemble_answer(
                state["plan"], state.get("decision", state["plan"]),
                state["evidence"], self.validator,
            )
            budget.check_active()
            return {"answer": answer, "request_results": results}

        graph = StateGraph(State)
        graph.add_node("prepare", prepare)
        graph.add_node("plan", plan_node)
        graph.add_node("retrieve", retrieve_node)
        graph.add_node("assess", assess_node)
        graph.add_node("finalize", finalize)
        graph.add_edge(START, "prepare")
        graph.add_edge("prepare", "plan")
        graph.add_conditional_edges(
            "plan", lambda state: state["plan"]["action"],
            {"retrieve": "retrieve", "clarify": "finalize", "out_of_scope": "finalize"},
        )
        graph.add_edge("retrieve", "assess")
        graph.add_edge("assess", "finalize")
        graph.add_edge("finalize", END)
        # Ambient LangChain tracing must not export visitor/history payloads.
        # ContextVars isolate concurrent runs and are restored on cancellation.
        trace_token = tracing_v2_callback_var.set(None)
        try:
            with tracing_context(enabled=False, parent=False):
                result = await graph.compile().ainvoke(
                    {"run": run}, config={"recursion_limit": 8, "callbacks": []},
                )
        finally:
            tracing_v2_callback_var.reset(trace_token)
        budget.check_active()
        return WorkflowOutcome(
            scope=deepcopy(run["scope"]), answer=deepcopy(result["answer"]),
            request_results=deepcopy(result["request_results"]),
            model_calls=result["model_calls"], retrieval_calls=result["retrieval_calls"],
            refinement_rounds=0,
            diagnostics={
                "implementation_stage": "C-M1",
                "complete_scope": result["complete_scope"],
                "executed_queries": deepcopy(result["executed_queries"]),
                "retained_history_turns": len(result["retained_run"]["context"]["recent_turns"]),
                "phase_timings_ms": deepcopy(result["phase_timings_ms"]),
            },
        )

    def _validate_input(self, run: dict, request_id: str) -> None:
        try:
            self.validator.validate("RunRequest", run)
            if run["question"] != run["question"].strip():
                raise ContractViolation("The original question must arrive trimmed.")
            if type(request_id) is not str or TRACE_ID.fullmatch(request_id) is None:
                raise ContractViolation("Invalid trace ID.")
        except ContractViolation as exc:
            raise WorkflowError("invalid_request", "The workflow input is invalid.",
                                phase="admission") from exc
        if run["execution"]["profile_id"] != self.profile.profile_id:
            raise WorkflowError("agent_contract_mismatch", "The model profile does not match the run.",
                                phase="admission")
        scope = run["scope"]
        try:
            artwork = self.manifest.artwork(scope["artwork_id"], scope["dataset_version"])
        except WorkflowError as exc:
            raise WorkflowError("agent_scope_mismatch", "The authorized release or artwork is unavailable.",
                                phase="admission") from exc
        if (run["context"]["artwork_title"] != artwork["title"] or
                run["context"]["artist"] != artwork["artist"]):
            raise WorkflowError("agent_scope_mismatch", "Artwork metadata does not match the release.",
                                phase="context")

    def _validate_tool_result(self, tool_input: dict, reply: dict) -> None:
        try:
            self.validator.validate("ToolResult", reply)
            if any(reply[key] != tool_input[key] for key in (
                "tool_version", "query_id", "artwork_id", "dataset_version"
            )) or reply["retrieval_strategy_version"] != RETRIEVAL_STRATEGY:
                raise ContractViolation("Tool result identity mismatch.")
        except ContractViolation as exc:
            raise WorkflowError(
                "retrieval_invalid_response", "The evidence tool returned an invalid result.",
                phase="retrieval",
            ) from exc
