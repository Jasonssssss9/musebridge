"""Private HTTP contracts with controlled execution, without real inference."""
import asyncio
from copy import deepcopy
import json
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx

from agent.app import create_app
from agent.contracts.codec import ContractCodec, PRIVATE_BODY_LIMIT
from agent.runtime.service_errors import failure
from agent.testing.service import ControlledExecutor, controlled_registry, service_run

TOKEN = "synthetic-test-token-" + "x" * 43
ROUTE = "/internal/v2/agent/runs"
HEADERS = {"Authorization": "Bearer " + TOKEN, "X-Request-ID": "service-http-test"}
CODEC = ContractCodec()


class HTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.executor = ControlledExecutor()
        self.runtime = controlled_registry(self.executor)
        self.app = create_app(self.runtime, service_token=TOKEN)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                       base_url="http://agent", headers=HEADERS)
        self.addAsyncCleanup(self.runtime.close, 0.1)
        self.addAsyncCleanup(self.client.aclose)

    def assert_error(self, response, status, code, run_id=None):
        self.assertEqual(response.status_code, status, response.text)
        value = CODEC.decode("ErrorEnvelope", response.content)
        self.assertEqual(value["error"]["code"], code)
        self.assertEqual(value["run_id"], run_id)
        self.assertEqual(response.headers["X-Request-ID"], value["request_id"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertNotIn("set-cookie", response.headers)
        self.assertNotIn("access-control-allow-origin", response.headers)

    async def test_completed_post_and_retained_get_have_v2_shapes(self):
        run = service_run()
        response = await self.client.post(ROUTE, json=run)
        self.assertEqual(response.status_code, 200, response.text)
        completed = CODEC.decode("RunSnapshot", response.content)
        self.assertEqual(completed["state"], "completed")
        self.assertEqual(response.headers["X-Request-ID"], HEADERS["X-Request-ID"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        response = await self.client.get(ROUTE + "/" + run["run_id"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), completed)
        raw = json.dumps(run, sort_keys=True, indent=2)
        response = await self.client.post(ROUTE, content=raw, headers={"Content-Type": "application/json", "X-Request-ID": "different-trace"})
        self.assertEqual(response.json(), completed)
        self.assertEqual(len(self.executor.calls), 1)

    async def test_failed_post_is_non_success_and_failed_get_is_successful_snapshot(self):
        self.executor.error = failure("generation_timeout", "planning")
        run = service_run()
        for _ in range(2):
            self.assert_error(await self.client.post(ROUTE, json=run), 504, "generation_timeout", run["run_id"])
        response = await self.client.get(ROUTE + "/" + run["run_id"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(CODEC.decode("RunSnapshot", response.content)["state"], "failed")
        self.assertEqual(len(self.executor.calls), 1)

    async def test_processing_snapshot_and_duplicate_conflict(self):
        self.executor.release.clear()
        run = service_run()
        task = asyncio.create_task(self.client.post(ROUTE, json=run))
        await asyncio.wait_for(self.executor.started.wait(), 2)
        response = await self.client.get(ROUTE + "/" + run["run_id"])
        self.assertEqual(CODEC.decode("RunSnapshot", response.content)["state"], "processing")
        self.assert_error(await self.client.post(ROUTE, json=run), 409, "agent_run_in_progress", run["run_id"])
        changed = deepcopy(run)
        changed["language_override"] = "es"
        self.assert_error(await self.client.post(ROUTE, json=changed), 409, "agent_run_conflict", run["run_id"])
        self.executor.release.set()
        self.assertEqual((await task).status_code, 200)

    async def test_default_factory_is_live_but_unready_without_provider(self):
        app = create_app(service_token=TOKEN)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as client:
                response = await client.get("/healthz")
                self.assertEqual(response.json(), {"status": "ok"})
                self.assertEqual(response.status_code, 200)
                self.assert_error(await client.get("/readyz"), 503, "service_not_ready")
                run = service_run()
                self.assert_error(await client.post(ROUTE, json=run, headers=HEADERS), 503, "service_not_ready", run["run_id"])

    async def test_injected_readiness_shape_does_not_call_model_on_probe(self):
        for _ in range(2):
            response = await self.client.get("/readyz")
            self.assertEqual(response.status_code, 200)
            CODEC.decode("ReadinessResponse", response.content)
        self.assertEqual(self.executor.calls, [])
        self.runtime.ready = lambda: False
        self.assert_error(await self.client.get("/readyz"), 503, "service_not_ready")

    async def test_authentication_precedes_run_lookup_and_invalid_trace(self):
        for authorization in ([], [("Authorization", "Bearer wrong")],
                              [("Authorization", "Bearer " + TOKEN)] * 2):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://agent") as client:
                response = await client.get(ROUTE + "/" + str(uuid4()), headers=authorization)
            self.assert_error(response, 401, "agent_unauthorized")
            self.assertNotIn(TOKEN, response.text)
        self.assertEqual(self.executor.calls, [])

    async def test_missing_invalid_and_duplicate_trace_get_safe_generated_trace(self):
        traces = ([], [("X-Request-ID", "")], [("X-Request-ID", "two words")],
                  [("X-Request-ID", "x" * 129)], [("X-Request-ID", "one"), ("X-Request-ID", "two")])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://agent") as client:
            for trace in traces:
                response = await client.get(ROUTE + "/" + str(uuid4()), headers=[("Authorization", "Bearer " + TOKEN), *trace])
                self.assert_error(response, 400, "invalid_request")
                self.assertTrue(response.headers["X-Request-ID"].startswith("req-"))

    async def test_unknown_routes_docs_redirects_and_methods(self):
        for path in ("/docs", "/redoc", "/openapi.json", ROUTE + "/", "/unknown"):
            self.assert_error(await self.client.get(path), 404, "route_not_found")
        response = await self.client.put(ROUTE)
        self.assert_error(response, 405, "method_not_allowed")
        self.assertIn("POST", response.headers["Allow"])
        self.assertNotIn("location", response.headers)
        for method in ("get", "post"):
            self.assert_error(await getattr(self.client, method)(ROUTE + "?unknown=value"), 400, "invalid_request")

    async def test_uuid_validation_and_unknown_run(self):
        for run_id in ("not-a-uuid", "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"):
            self.assert_error(await self.client.get(ROUTE + "/" + run_id), 400, "invalid_request")
        unknown = str(uuid4())
        self.assert_error(await self.client.get(ROUTE + "/" + unknown), 404, "agent_run_not_found", unknown)

    async def test_strict_media_type_and_compression(self):
        run = service_run()
        for content_type in ("text/plain", "application/json; charset=latin-1", "application/json; other=x",
                             "application/json; char set=utf-8", "application/json; charset=utf-8; charset=utf-8"):
            self.assert_error(await self.client.post(ROUTE, content=json.dumps(run), headers={"Content-Type": content_type}),
                              415, "unsupported_media_type")
        self.assert_error(await self.client.post(ROUTE, json=run, headers={"Content-Encoding": "gzip"}), 415, "unsupported_media_type")
        self.assert_error(await self.client.post(ROUTE, content=b"{}"), 415, "unsupported_media_type")
        response = await self.client.post(ROUTE, content=json.dumps(run), headers={"Content-Type": 'Application/JSON; charset = "UTF-8"'})
        self.assertEqual(response.status_code, 200, response.text)

    async def test_duplicate_content_headers_and_invalid_declared_lengths(self):
        raw = json.dumps(service_run()).encode()
        cases = [([("Content-Type", "application/json")] * 2, "unsupported_media_type", 415),
                 ([("Content-Type", "application/json"), ("Content-Length", "2"), ("Content-Length", "2")], "invalid_request", 400),
                 ([("Content-Type", "application/json"), ("Content-Length", "invalid")], "invalid_request", 400),
                 ([("Content-Type", "application/json"), ("Content-Length", "2")], "invalid_request", 400),
                 ([("Content-Type", "application/json"), ("Content-Length", str(PRIVATE_BODY_LIMIT + 1))], "payload_too_large", 413)]
        for headers, code, status in cases:
            self.assert_error(await self.client.post(ROUTE, content=raw, headers=headers), status, code)

    async def test_chunked_payload_is_bounded_independently_of_content_length(self):
        async def chunks():
            yield b" " * PRIVATE_BODY_LIMIT
            yield b"x"
        self.assert_error(await self.client.post(ROUTE, content=chunks(), headers={"Content-Type": "application/json"}), 413, "payload_too_large")

    async def test_malformed_duplicate_or_nonfinite_json_is_rejected(self):
        for raw in (b'{"run_id":"one","run_id":"two"}', b'{"value":NaN}', b"{}{}", b"\xff", b"[1]",
                    b'{"value":"\\ud800"}', b"[" * 65 + b"0" + b"]" * 65):
            self.assert_error(await self.client.post(ROUTE, content=raw, headers={"Content-Type": "application/json"}), 400, "invalid_request")
        self.assertEqual(self.executor.calls, [])

    async def test_unknown_fields_types_and_untrimmed_question_are_rejected(self):
        for key, value in (("question", " padded "), ("question", True), ("extra", "unknown")):
            run = service_run()
            run[key] = value
            self.assert_error(await self.client.post(ROUTE, json=run), 400, "invalid_request")
        for key in ("contract_version", "workflow_id", "profile_id"):
            run = service_run()
            target = run if key == "contract_version" else run["execution"]
            target[key] = "unsupported"
            self.assert_error(await self.client.post(ROUTE, json=run), 409, "agent_contract_mismatch", run["run_id"])

    async def test_unexpected_handler_exception_gets_safe_contract_error(self):
        run_id = str(uuid4())
        with patch.object(self.runtime, "get", AsyncMock(side_effect=RuntimeError("PRIVATE PROVIDER BODY"))):
            self.assert_error(await self.client.get(ROUTE + "/" + run_id), 500, "agent_internal_error", run_id)

    def test_startup_requires_explicit_generated_token_without_echoing_it(self):
        for token in ("", "x" * 32, "bad token" * 8, "秘密" * 32):
            with self.assertRaises(ValueError) as caught:
                create_app(service_token=token)
            if token:
                self.assertNotIn(token, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
