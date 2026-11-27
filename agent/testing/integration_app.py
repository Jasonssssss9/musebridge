"""Explicit controlled M2 real-model wiring; never a production readiness gate."""
import asyncio
from contextlib import asynccontextmanager, suppress
import os
from time import monotonic

from agent.app import create_app as private_app
from agent.providers.ollama import OllamaModel
from agent.providers.sizing import QwenPromptSizer
from agent.runtime.runs import RunRegistry
from agent.runtime.service_errors import safe_failure
from agent.service import ModelIdentity, create_workflow_runtime
from agent.tools.evidence import EvidenceManifest
from agent.tools.retrieval import RetrievalTool, configured_mode
from agent.testing.runtime_evidence import EvidenceExecution, EvidenceModel, EvidenceRetrieval, RuntimeEvidence


def create_app():
    """Run real Qwen for A/B/C integration without attesting semantic quality.

    The normal agent.app factory never imports this module. This separate
    entry point retains real identity, sizing, workflow and result validation;
    only the semantic capability-record requirement is intentionally absent.
    """
    if os.environ.get("AGENT_INTEGRATION_MODE") != "controlled-ollama":
        raise ValueError("Controlled M2 startup requires AGENT_INTEGRATION_MODE=controlled-ollama.")
    tokenizer = os.environ.get("AGENT_TOKENIZER_PATH")
    if not tokenizer:
        raise ValueError("Controlled M2 startup requires AGENT_TOKENIZER_PATH.")
    app = private_app(runtime=RunRegistry())
    app.state.integration_mode = "controlled-ollama-not-quality-approved"

    @asynccontextmanager
    async def lifespan(_app):
        model = None
        watcher = None
        evidence = None
        runtime = app.state.runtime
        try:
            if os.environ.get("AGENT_RUNTIME_EVIDENCE"):
                evidence = RuntimeEvidence(os.environ["AGENT_RUNTIME_EVIDENCE"],
                    include_decisions=os.environ.get("AGENT_PRIVATE_DECISIONS") == "development-only")
            try:
                options = dict(base_url=os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
                               sizer=QwenPromptSizer(tokenizer))
                model = EvidenceModel(evidence=evidence, **options) if evidence else OllamaModel(**options)
                await model.verify(deadline=monotonic()+60)
                await model.warm(deadline=monotonic()+120)
                manifest = EvidenceManifest.from_environment()
                retrieval = RetrievalTool(manifest,
                    mode=configured_mode(default="lexical"), base_url=os.environ.get("RETRIEVAL_BASE_URL", "http://127.0.0.1:8001"))
                if evidence:
                    from agent.providers.capabilities import implementation_fingerprint
                    evidence.record("integration_ready", mode="controlled-ollama-not-quality-approved",
                        profile_id=model.profile.profile_id, profile_fingerprint=model.profile.fingerprint(),
                        model_name=model.profile.model_name, model_digest=model.profile.model_digest,
                        implementation_fingerprint=implementation_fingerprint())
                    retrieval = EvidenceRetrieval(retrieval, evidence)
                runtime = create_workflow_runtime(model=model, profile=model.profile,
                    prompt_sizer=model.sizer, manifest=manifest,
                    retrieval=retrieval,
                    identity=ModelIdentity(model.profile.model_name, model.profile.model_digest),
                    readiness=lambda: model.verified)
                if evidence:
                    runtime.executor = EvidenceExecution(runtime.executor, evidence)

                async def refresh():
                    while True:
                        await asyncio.sleep(30)
                        try:
                            await model.check_identity(deadline=monotonic()+3)
                            model.verified = True
                        except Exception:
                            model.verified = False

                watcher = asyncio.create_task(refresh(), name="m2-controlled-model-identity")
            except Exception as exc:
                if model:
                    model.verified = False
                runtime.startup_error = safe_failure(exc).code
                if evidence:
                    evidence.record("integration_unready", code=runtime.startup_error)
            app.state.runtime = runtime
            yield
        finally:
            if watcher:
                watcher.cancel()
                with suppress(asyncio.CancelledError):
                    await watcher
            await runtime.close()
            if model:
                await model.aclose()
            if evidence:
                evidence.record("integration_stopped")
                evidence.close()

    app.router.lifespan_context = lifespan
    return app
