"""Compose C's workflow with B's lifecycle, shared inference gate and identity."""
from dataclasses import dataclass

from agent.contracts.codec import ContractCodec, PROFILE_ID
from agent.runtime.budgets import InferenceGate
from agent.runtime.context_v2 import validate_run_context
from agent.runtime.runs import RunRegistry
from agent.workflows.museum_qa_v2 import MuseumQAWorkflowV2


@dataclass(frozen=True)
class ModelIdentity:
    model_name: str
    model_digest: str
    model_provider: str = "ollama"
    profile_id: str = PROFILE_ID

    def fields(self) -> dict:
        return {"profile_id": self.profile_id, "model_provider": self.model_provider,
                "model_name": self.model_name, "model_digest": self.model_digest}


class WorkflowExecutor:
    def __init__(self, workflow, identity: ModelIdentity):
        self.workflow, self.identity = workflow, identity

    async def __call__(self, request: dict, *, request_id: str, budget) -> dict:
        outcome = await self.workflow.run(request, request_id=request_id, budget=budget)
        result = outcome.result_fields()
        result["execution"] = {**outcome.execution_fields(), **self.identity.fields(), "duration_ms": 0}
        return result


def create_workflow_runtime(*, model, profile, prompt_sizer, manifest, retrieval,
                            identity: ModelIdentity, readiness) -> RunRegistry:
    """Explicit injection only. Readiness must verify the phase-4 capability record.

    No production handler imports testing fakes or fabricates provider identity.
    A caller can inject synthetic dependencies in a controlled test environment.
    """
    gate = InferenceGate()
    workflow = MuseumQAWorkflowV2(model=gate.wrap(model), profile=profile,
                                   prompt_sizer=prompt_sizer, manifest=manifest, retrieval=retrieval)
    codec = ContractCodec()
    codec.validate("ExecutionRecord", {
        "workflow_id": "museum_qa_v2", "prompt_version": "museum-prompts-v2",
        "schema_version": "agent-schema-v2", "retrieval_strategy_version": workflow.retrieval_strategy_version,
        **identity.fields(), "model_calls": 1, "retrieval_calls": 0,
        "refinement_rounds": 0, "duration_ms": 0,
    })
    if profile.profile_id != identity.profile_id:
        raise ValueError("The provider identity and workflow profile must agree.")
    runtime = RunRegistry(WorkflowExecutor(workflow, identity),
                          validate_input=lambda run: validate_run_context(run, manifest),
                          ready=readiness)
    runtime.retrieval_strategy_version = workflow.retrieval_strategy_version
    runtime.inference_gate = gate
    return runtime
