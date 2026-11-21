"""Controlled M2 startup isolation and cleanup; no real model or GPU calls."""
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from agent.providers.profile import OllamaProfile
from agent.testing.integration_app import create_app


class IntegrationAppTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env = patch.dict(os.environ, {
            "AGENT_INTEGRATION_MODE": "controlled-ollama",
            "AGENT_TOKENIZER_PATH": "/synthetic/tokenizer.json",
            "AGENT_SERVICE_TOKEN": "SYNTHETIC-TOKEN-" + "x"*43,
        })
        env.start()
        self.addCleanup(env.stop)
        self.model = SimpleNamespace(profile=OllamaProfile(), sizer=lambda messages, schema: 100,
            verified=True, verify=AsyncMock(), warm=AsyncMock(), check_identity=AsyncMock(), aclose=AsyncMock())
        model = patch("agent.testing.integration_app.OllamaModel", return_value=self.model)
        sizer = patch("agent.testing.integration_app.QwenPromptSizer")
        model.start()
        sizer.start()
        self.addCleanup(model.stop)
        self.addCleanup(sizer.stop)

    def test_factory_requires_explicit_mode_token_and_existing_asset_path(self):
        for key in ("AGENT_INTEGRATION_MODE", "AGENT_TOKENIZER_PATH", "AGENT_SERVICE_TOKEN"):
            with self.subTest(key=key), patch.dict(os.environ, {key: ""}):
                with self.assertRaises(ValueError):
                    create_app()

    async def test_controlled_lifespan_uses_real_workflow_without_capability_attestation(self):
        with patch("agent.providers.bootstrap.read_record", side_effect=AssertionError("production gate invoked")):
            app = create_app()
            async with app.router.lifespan_context(app):
                self.assertTrue(app.state.runtime.is_ready())
                self.assertEqual(app.state.integration_mode, "controlled-ollama-not-quality-approved")
                self.model.verify.assert_awaited_once()
                self.model.warm.assert_awaited_once()
                self.model.verified = False
                self.assertFalse(app.state.runtime.is_ready())
        self.model.aclose.assert_awaited_once()

    async def test_identity_or_warm_failure_keeps_private_service_unready(self):
        for operation in ("verify", "warm"):
            with self.subTest(operation=operation):
                self.model.verified = True
                self.model.verify.reset_mock(side_effect=True)
                self.model.warm.reset_mock(side_effect=True)
                getattr(self.model, operation).side_effect = RuntimeError("PRIVATE startup detail")
                app = create_app()
                async with app.router.lifespan_context(app):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as client:
                        reply = await client.get("/readyz")
                        self.assertEqual(reply.status_code, 503)
                        self.assertNotIn("PRIVATE", reply.text)
                    self.assertFalse(app.state.runtime.is_ready())

    async def test_normal_factory_does_not_select_integration_mode(self):
        from agent.app import create_app as production_app
        with patch.dict(os.environ, {"AGENT_PROVIDER": "disabled"}):
            app = production_app()
            async with app.router.lifespan_context(app):
                self.assertFalse(app.state.runtime.is_ready())
                self.assertFalse(hasattr(app.state, "integration_mode"))
                self.model.verify.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
