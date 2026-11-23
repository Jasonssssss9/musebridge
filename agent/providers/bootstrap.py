"""Opt-in production wiring; no test providers or automatic asset downloads."""
import asyncio
from contextlib import suppress
import os
from time import monotonic

from agent.providers.capabilities import read_record
from agent.providers.ollama import OllamaModel
from agent.providers.sizing import QwenPromptSizer
from agent.runtime.runs import RunRegistry
from agent.runtime.service_errors import safe_failure
from agent.service import ModelIdentity, create_workflow_runtime
from agent.tools.evidence import EvidenceManifest
from agent.tools.retrieval import RetrievalTool, configured_mode


async def start_ollama_runtime():
    tokenizer = os.environ.get("AGENT_TOKENIZER_PATH")
    record_path = os.environ.get("AGENT_CAPABILITY_RECORD")
    if not tokenizer or not record_path:
        raise ValueError("Ollama mode requires AGENT_TOKENIZER_PATH and AGENT_CAPABILITY_RECORD.")
    model = OllamaModel(base_url=os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
                        sizer=QwenPromptSizer(tokenizer))
    registry = RunRegistry()
    watcher = None
    try:
        await model.verify(deadline=monotonic()+60)
        record = read_record(record_path, model)
        if record is not None:
            await model.warm(deadline=monotonic()+120)
            manifest = EvidenceManifest.load()
            def ready():
                if not model.verified:
                    return False
                current = read_record(record_path, model)
                # A renewed attestation must still describe this running implementation.
                return current is not None and current["implementation_fingerprint"] == record["implementation_fingerprint"]

            registry = create_workflow_runtime(model=model, profile=model.profile, prompt_sizer=model.sizer,
                manifest=manifest, retrieval=RetrievalTool(manifest,
                    mode=configured_mode(), base_url=os.environ.get("RETRIEVAL_BASE_URL", "http://127.0.0.1:8001")),
                identity=ModelIdentity(model.profile.model_name, model.profile.model_digest),
                readiness=ready)

            async def refresh():
                while True:
                    await asyncio.sleep(30)
                    try:
                        await model.check_identity(deadline=monotonic()+3)
                        model.verified = True
                    except Exception:
                        model.verified = False

            watcher = asyncio.create_task(refresh(), name="musebridge-model-identity")
        else:
            registry.startup_error = "service_not_ready"
    except asyncio.CancelledError:
        await model.aclose()
        raise
    except Exception as exc:
        model.verified = False
        # Keep the shell live but unready, without logging configuration or
        # provider exception text. Operators can rerun the explicit probe CLI.
        registry.startup_error = safe_failure(exc).code

    async def close():
        if watcher:
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher
        await registry.close()
        await model.aclose()

    return registry, close
