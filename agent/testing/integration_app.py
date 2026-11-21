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
from agent.tools.retrieval import RetrievalTool


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
        runtime = app.state.runtime
        try:
            try:
                model = OllamaModel(base_url=os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
                                    sizer=QwenPromptSizer(tokenizer))
                await model.verify(deadline=monotonic()+60)
                await model.warm(deadline=monotonic()+120)
                manifest = EvidenceManifest.load()
                runtime = create_workflow_runtime(model=model, profile=model.profile,
                    prompt_sizer=model.sizer, manifest=manifest,
                    retrieval=RetrievalTool(manifest,
                        base_url=os.environ.get("RETRIEVAL_BASE_URL", "http://127.0.0.1:8001")),
                    identity=ModelIdentity(model.profile.model_name, model.profile.model_digest),
                    readiness=lambda: model.verified)

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

    app.router.lifespan_context = lifespan
    return app
