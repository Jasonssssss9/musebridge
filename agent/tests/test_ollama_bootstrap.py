"""Opt-in startup and readiness fail closed; no real model is contacted."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from agent.app import create_app
from agent.providers.bootstrap import start_ollama_runtime
from agent.providers.capabilities import CHECKS, make_record
from agent.providers.profile import OllamaProfile
from agent.testing.verify_ollama import verify
import json
from types import SimpleNamespace


class BootstrapTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.environment = patch.dict(os.environ, {"AGENT_TOKENIZER_PATH": "/synthetic/tokenizer.json",
            "AGENT_CAPABILITY_RECORD": "/synthetic/record.json", "AGENT_PROVIDER": "ollama"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.model = type("SyntheticModel", (), {})()
        self.model.profile = OllamaProfile()
        self.model.sizer = lambda messages, schema: 100
        self.model.verified = True
        self.model.metadata_fingerprint = "synthetic-test-only"
        self.model.verify = AsyncMock()
        self.model.warm = AsyncMock()
        self.model.check_identity = AsyncMock()
        self.model.aclose = AsyncMock()
        self.sizer_patch = patch("agent.providers.bootstrap.QwenPromptSizer", return_value=self.model.sizer)
        self.model_patch = patch("agent.providers.bootstrap.OllamaModel", return_value=self.model)
        self.sizer_patch.start(); self.model_patch.start()
        self.addCleanup(self.sizer_patch.stop); self.addCleanup(self.model_patch.stop)

    async def test_absent_or_failed_capability_attestation_never_enables_runs(self):
        with patch("agent.providers.bootstrap.read_record", return_value=None):
            runtime, close = await start_ollama_runtime()
        self.assertFalse(runtime.is_ready())
        self.model.warm.assert_not_awaited()
        await close()
        self.model.aclose.assert_awaited_once()

    async def test_provider_outage_preserves_live_unready_service_without_raw_error(self):
        self.model.verify.side_effect = RuntimeError("PRIVATE PROVIDER CONFIGURATION")
        runtime, close = await start_ollama_runtime()
        self.assertFalse(runtime.is_ready())
        self.assertEqual(runtime.startup_error, "agent_internal_error")
        await close()

    async def test_running_service_observes_capability_renewal_and_revocation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "record.json"
            record = make_record(self.model, dict.fromkeys(CHECKS, True))
            path.write_text(json.dumps(record))
            with patch.dict(os.environ, {"AGENT_CAPABILITY_RECORD": str(path)}):
                runtime, close = await start_ollama_runtime()
                self.addAsyncCleanup(close)
                self.assertTrue(runtime.is_ready())
                self.model.warm.assert_awaited_once()

                for invalid in ({"valid": False}, {**record, "created_at": "2000-01-01T00:00:00+00:00"}):
                    replacement = path.with_suffix(".new")
                    replacement.write_text(json.dumps(invalid))
                    replacement.replace(path)
                    self.assertFalse(runtime.is_ready())
                    path.write_text(json.dumps(record))
                    self.assertTrue(runtime.is_ready())

                path.unlink()
                self.assertFalse(runtime.is_ready())
                path.write_text(json.dumps(record))
                self.assertTrue(runtime.is_ready())
                self.model.verified = False
                self.assertFalse(runtime.is_ready())

    async def test_attestation_and_readiness_identify_the_configured_retrieval_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capability.json"
            with patch.dict(os.environ, {"AGENT_CAPABILITY_RECORD": str(path), "MUSEBRIDGE_RETRIEVAL_MODE": "lexical"}):
                path.write_text(json.dumps(make_record(self.model, dict.fromkeys(CHECKS, True))))
                with patch.dict(os.environ, {"MUSEBRIDGE_RETRIEVAL_MODE": "hybrid"}):
                    runtime, close = await start_ollama_runtime()
                    self.assertFalse(runtime.is_ready())
                    await close()
                    path.write_text(json.dumps(make_record(self.model, dict.fromkeys(CHECKS, True))))
                    runtime, close = await start_ollama_runtime()
                    self.addAsyncCleanup(close)
                    app = create_app(runtime=runtime, service_token="x"*43)
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as client:
                        response = await client.get("/readyz")
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers["X-Retrieval-Strategy"], "openai-hybrid-v1")

    async def test_factory_lifespan_uses_selected_real_provider_shell(self):
        token = "SYNTHETIC-TEST-TOKEN-" + "x"*43
        with patch("agent.providers.bootstrap.read_record", return_value=None):
            app = create_app(service_token=token)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as client:
                    self.assertEqual((await client.get("/healthz")).status_code, 200)
                    response = await client.get("/readyz")
                    self.assertEqual(response.status_code, 503)
                    self.assertEqual(response.json()["error"]["code"], "service_not_ready")
        self.model.aclose.assert_awaited_once()

    async def test_explicit_mode_rejects_missing_paths_without_auto_downloading(self):
        with patch.dict(os.environ, {"AGENT_TOKENIZER_PATH": ""}):
            with self.assertRaises(ValueError):
                await start_ollama_runtime()

    async def test_failed_repeat_invalidates_previous_capability_attestation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"previous.json"
            path.write_text('{"previously_passed":true}')
            options = SimpleNamespace(base_url="http://synthetic-test.invalid", report=str(Path(directory)/"report.json"),
                                      capability_record=str(path), tokenizer="missing-tokenizer")
            with patch("agent.testing.verify_ollama.QwenPromptSizer", side_effect=ValueError("Synthetic asset error")):
                self.assertFalse(await verify(options))
            self.assertIs(json.loads(path.read_text())["valid"], False)
            report = json.loads(Path(options.report).read_text())
            self.assertEqual(report["fatal_error"], "ValueError")
            self.assertFalse(report["passed"])


if __name__ == "__main__":
    unittest.main()
