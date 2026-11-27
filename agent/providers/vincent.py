"""Optional Vincent runtime sharing the guide's model, admission limits and private origin."""
import os
from pathlib import Path

import httpx

from agent.service import ModelIdentity, create_workflow_runtime_v3
from agent.workflows.museum_qa_v3 import vincent_manifest, vincent_retrieval


async def refresh_vincent(runtime):
    """Read the loaded index identity without embedding a probe or generating a turn."""
    if not hasattr(runtime, "v3_runtime"):
        return
    role = runtime.v3_runtime
    try:
        async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
            response = await client.get(role.knowledge_origin + "/readyz/vincent")
        role.index_ready = (
            response.status_code == 200
            and response.headers.get("X-Retrieval-Strategy") == role.retrieval_strategy_version
            and response.json() == {"status": "ok", "dataset_version": role.dataset_version,
                                    "reference_pack_version": "vincent-irises-v1"}
        )
    except (httpx.HTTPError, ValueError):
        role.index_ready = False


async def attach_vincent(runtime, model, manifest, guide_tool):
    if os.environ.get("MUSEBRIDGE_ENABLE_VINCENT") != "1":
        return
    try:
        await _attach(runtime, model, manifest, guide_tool)
    except (OSError, ValueError) as error:
        # A missing or incompatible role pack disables only the optional version.
        runtime.v3_startup_error = type(error).__name__


async def _attach(runtime, model, manifest, guide_tool):
    root = Path(__file__).resolve().parents[2]
    role_files = (root / "agent/prompts/vincent/dialogue.md", root / "agent/prompts/vincent/plan.md",
                  root / "docs/contracts/agent-v3.patch.json",
                  root / "knowledge/data/reference-packs/vincent-irises-v1.json")
    snapshots = tuple(path.read_bytes() for path in role_files)
    origin = os.environ.get("RETRIEVAL_BASE_URL", "http://127.0.0.1:8001").rstrip("/")
    historical = vincent_manifest(manifest)

    def ready():
        return (runtime.is_ready() and role.index_ready
                and all(path.read_bytes() == snapshot for path, snapshot in zip(role_files, snapshots)))

    role = create_workflow_runtime_v3(
        model=model, profile=model.profile, prompt_sizer=model.sizer, manifest=manifest, retrieval=guide_tool,
        role_manifest=historical, role_retrieval=vincent_retrieval(historical, base_url=origin),
        identity=ModelIdentity(model.profile.model_name, model.profile.model_digest),
        readiness=ready, gate=runtime.inference_gate, shared_with=runtime,
    )
    role.knowledge_origin = origin
    role.index_ready = False
    runtime.v3_runtime = role
    await refresh_vincent(runtime)
