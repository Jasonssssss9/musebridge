"""Actual HTTP -> FastAPI -> C v2 graph -> HTTP retrieval, scripted model only."""
import asyncio
from copy import deepcopy
import socket
import threading
import unittest

import httpx
import uvicorn

from knowledge.http_service import create_server
from agent.app import create_app
from agent.contracts.codec import ContractCodec
from agent.service import ModelIdentity, create_workflow_runtime
from agent.testing.v2 import (
    FixtureProfile, ScriptedModel, fixture_prompt_sizer, make_run, medium_answer, medium_plan,
)
from agent.tools.evidence import EvidenceManifest
from agent.tools.retrieval import RetrievalTool

TOKEN = "synthetic-local-integration-token-" + "x" * 43


class RecordingRetrieval:
    def __init__(self, delegate):
        self.delegate, self.calls = delegate, []

    async def search(self, request, **options):
        self.calls.append(deepcopy(request))
        return await self.delegate.search(request, **options)


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manifest = EvidenceManifest.load()
        self.retrieval_server = create_server("127.0.0.1", 0)
        self.thread = threading.Thread(target=self.retrieval_server.serve_forever,
                                       kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.retrieval_server.server_close)
        self.addCleanup(self.thread.join, 5)
        self.addCleanup(self.retrieval_server.shutdown)
        self.tool = RecordingRetrieval(RetrievalTool(self.manifest,
            base_url=f"http://127.0.0.1:{self.retrieval_server.server_address[1]}"))
        self.model = ScriptedModel()
        self.runtime = create_workflow_runtime(
            model=self.model, profile=FixtureProfile(), prompt_sizer=fixture_prompt_sizer,
            manifest=self.manifest, retrieval=self.tool,
            identity=ModelIdentity("SYNTHETIC-TEST-ONLY", "SYNTHETIC-NOT-A-REAL-MODEL-DIGEST"),
            readiness=lambda: True,
        )
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        self.addCleanup(sock.close)
        self.server = uvicorn.Server(uvicorn.Config(
            create_app(self.runtime, service_token=TOKEN), log_config=None,
            access_log=False, lifespan="on", http="h11", ws="none", timeout_graceful_shutdown=1))
        self.serving = asyncio.create_task(self.server.serve(sockets=[sock]))

        async def stop():
            self.server.should_exit = True
            await asyncio.wait_for(self.serving, 5)

        self.addAsyncCleanup(stop)
        async with asyncio.timeout(5):
            while not self.server.started:
                if self.serving.done():
                    self.serving.result()
                await asyncio.sleep(0.01)
        self.client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{sock.getsockname()[1]}",
            headers={"Authorization": "Bearer " + TOKEN, "X-Request-ID": "synthetic-chain"}, timeout=5)
        self.addAsyncCleanup(self.client.aclose)

    async def test_real_http_chain_and_retained_replay_for_both_artworks(self):
        for artwork in ("aic-28560", "aic-11723"):
            run = make_run(artwork_id=artwork, manifest=self.manifest)
            proposed = medium_answer(run, text="The collection lists the medium as oil paint on canvas.")
            proposed["memory_update"]["continuity_summary"] = "The visitor learned that the listed medium is oil on canvas."
            self.model.responses.extend((medium_plan(run), proposed))
            response = await self.client.post("/internal/v2/agent/runs", json=run)
            self.assertEqual(response.status_code, 200, response.text)
            completed = ContractCodec().decode_run_snapshot(response.content)
            result = completed["result"]
            self.assertEqual(result["answer"]["text"], proposed["results"][0]["text"])
            self.assertEqual(result["request_results"], proposed["results"])
            self.assertEqual(result["conversation_update"]["memory"], proposed["memory_update"])
            self.assertEqual(result["answer"]["citations"][0]["chunk_id"], artwork + "-medium")
            self.assertEqual(result["execution"]["model_name"], "SYNTHETIC-TEST-ONLY")
            self.assertEqual((result["execution"]["model_calls"], result["execution"]["retrieval_calls"]), (2, 1))
            self.assertEqual(self.tool.calls[-1]["artwork_id"], artwork)
            self.assertEqual((await self.client.get("/internal/v2/agent/runs/" + run["run_id"])).json(), completed)
            self.assertEqual((await self.client.post("/internal/v2/agent/runs", json=run)).json(), completed)
        self.assertEqual(len(self.model.calls), 4)
        self.assertEqual(len(self.tool.calls), 2)

    async def test_dropped_http_response_can_be_reconciled_without_second_model_run(self):
        entered, release = asyncio.Event(), asyncio.Event()
        run = make_run(manifest=self.manifest)

        async def paused_plan(_call):
            entered.set()
            await release.wait()
            return medium_plan(run)

        self.model.responses.extend((paused_plan, medium_answer(run)))
        post = asyncio.create_task(self.client.post("/internal/v2/agent/runs", json=run))
        await asyncio.wait_for(entered.wait(), 3)
        post.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await post
        path = "/internal/v2/agent/runs/" + run["run_id"]
        response = await self.client.get(path)
        self.assertEqual(response.json()["state"], "processing")
        release.set()
        async with asyncio.timeout(3):
            while (response := await self.client.get(path)).json()["state"] == "processing":
                await asyncio.sleep(0.01)
        self.assertEqual(response.json()["state"], "completed", response.text)
        self.assertEqual(len(self.model.calls), 2)
        self.assertEqual(len(self.tool.calls), 1)


if __name__ == "__main__":
    unittest.main()
