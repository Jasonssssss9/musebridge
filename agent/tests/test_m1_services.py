"""M1 service harness checks with real loopback HTTP; these do not originate in Go."""
import asyncio
import json
import os
from pathlib import Path
import secrets
import stat
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx

from agent.contracts.codec import ContractCodec
from agent.contracts.validation import check_run_result
from agent.testing.m1_services import ARTWORKS, MODE, MODEL_NAME, QUESTION, running_services
from agent.testing.v2 import make_run

TOKEN = "SYNTHETIC-M1-SERVICE-TEST-" + "x" * 43
ROOT = Path(__file__).resolve().parents[2]


def events(services, name):
    return [item for item in services.audit.snapshot() if item["event"] == name]


def headers(trace):
    return {"Authorization": "Bearer " + TOKEN, "X-Request-ID": trace}


class M1ServicesTests(unittest.IsolatedAsyncioTestCase):
    async def assert_closed(self, *origins):
        async with httpx.AsyncClient(trust_env=False, timeout=1) as client:
            for origin in origins:
                with self.assertRaises(httpx.ConnectError):
                    await client.get(origin + "/healthz")

    async def test_both_artworks_use_real_lookup_and_retained_replay(self):
        # Production environment selection cannot enable Ollama in this harness.
        with patch.dict(os.environ, {"AGENT_PROVIDER": "ollama", "OLLAMA_BASE_URL": "http://127.0.0.1:1"}):
            async with running_services(service_token=TOKEN) as services:
                codec = ContractCodec()
                async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
                    for artwork in ARTWORKS:
                        trace = "m1-python-" + artwork
                        run = make_run(artwork_id=artwork, question=QUESTION)
                        response = await client.post(services.agent_url + "/internal/v2/agent/runs",
                                                     json=run, headers=headers(trace))
                        self.assertEqual(response.status_code, 200, response.text)
                        self.assertEqual(response.headers["X-Request-ID"], trace)
                        snapshot = codec.decode_run_snapshot(response.content)
                        self.assertEqual(snapshot["run_id"], run["run_id"])
                        self.assertEqual(snapshot["state"], "completed")
                        result = snapshot["result"]
                        check_run_result(run, result)
                        self.assertEqual(result["answer"]["status"], "answered")
                        self.assertEqual(result["answer"]["citations"][0]["chunk_id"], artwork + "-medium")
                        self.assertEqual([record["chunk_id"] for record in result["evidence_snapshot"]],
                                         [artwork + "-medium"])
                        self.assertEqual(result["execution"]["model_name"], MODEL_NAME)
                        self.assertEqual((result["execution"]["model_calls"], result["execution"]["retrieval_calls"]), (2, 1))
                        replay = await client.post(services.agent_url + "/internal/v2/agent/runs",
                                                   json=run, headers=headers(trace + "-replay"))
                        self.assertEqual(replay.json(), snapshot)
                lookups = events(services, "retrieval_http_response")
                self.assertEqual(len(lookups), 2)
                self.assertEqual(len(events(services, "model_call")), 4)
                for lookup, artwork in zip(lookups, ARTWORKS):
                    self.assertEqual(lookup["request_id"], "m1-python-" + artwork)
                    self.assertEqual(lookup["artwork_id"], artwork)
                    self.assertEqual(lookup["dataset_version"], "aic-seed-v0.1")
                    self.assertEqual(lookup["original_evidence_ids"], [artwork + "-medium"])
                    self.assertEqual(lookup["evidence_ids"], lookup["original_evidence_ids"])
                self.assertEqual(len(events(services, "retrieval_accepted")), 2)
        self.assertFalse(services.retrieval_thread.is_alive())
        self.assertTrue(services.serving.done())
        self.assertFalse(services.runtime.is_ready())
        await self.assert_closed(services.agent_url, services.retrieval_url)

    async def test_foreign_http_evidence_is_rejected_before_assessment(self):
        async with running_services(service_token=TOKEN, fault="foreign-evidence") as services:
            async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
                for artwork in ARTWORKS:
                    run = make_run(artwork_id=artwork, question=QUESTION)
                    response = await client.post(services.agent_url + "/internal/v2/agent/runs",
                        json=run, headers=headers("m1-foreign-" + artwork))
                    self.assertEqual(response.status_code, 502, response.text)
                    self.assertEqual(response.json()["error"]["code"], "retrieval_invalid_response")
                    self.assertEqual(response.json()["error"]["phase"], "retrieval")
                    retained = await client.get(services.agent_url + "/internal/v2/agent/runs/" + run["run_id"],
                                                headers=headers("m1-foreign-status"))
                    snapshot = ContractCodec().decode_run_snapshot(retained.content)
                    self.assertEqual(snapshot["state"], "failed")
                    self.assertIsNone(snapshot["result"])
            for lookup, artwork in zip(events(services, "retrieval_http_response"), ARTWORKS):
                other = next(item for item in ARTWORKS if item != artwork)
                self.assertEqual(lookup["original_evidence_ids"], [artwork + "-medium"])
                self.assertEqual(lookup["evidence_ids"], [other + "-medium"])
                self.assertEqual(lookup["injection_boundary"], "retrieval HTTP response serialization")
            self.assertEqual(len(events(services, "retrieval_rejected")), 2)
            self.assertEqual(events(services, "retrieval_accepted"), [])
            self.assertEqual([item["operation"] for item in events(services, "model_call")], ["plan", "plan"])

    async def test_retrieval_outage_cannot_return_a_scripted_success(self):
        async with running_services(service_token=TOKEN) as services:
            await asyncio.to_thread(services.retrieval_server.shutdown)
            await asyncio.to_thread(services.retrieval_thread.join, 5)
            services.retrieval_server.server_close()
            async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
                response = await client.post(services.agent_url + "/internal/v2/agent/runs",
                    json=make_run(question=QUESTION), headers=headers("m1-retrieval-offline"))
            self.assertEqual(response.status_code, 503, response.text)
            self.assertEqual(response.json()["error"]["code"], "retrieval_unavailable")
            self.assertEqual(events(services, "retrieval_http_response"), [])
            self.assertEqual(events(services, "retrieval_accepted"), [])
            self.assertEqual(len(events(services, "model_call")), 1)

    async def test_authentication_and_unsupported_questions_do_not_skip_to_answers(self):
        async with running_services(service_token=TOKEN) as services:
            async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
                response = await client.post(services.agent_url + "/internal/v2/agent/runs",
                    json=make_run(question=QUESTION), headers={"X-Request-ID": "m1-no-auth"})
                self.assertEqual(response.status_code, 401)
                self.assertEqual(events(services, "model_call"), [])
                response = await client.post(services.agent_url + "/internal/v2/agent/runs",
                    json=make_run(question="Who painted this?"), headers=headers("m1-unsupported"))
                self.assertEqual(response.status_code, 502, response.text)
                self.assertEqual(response.json()["error"]["code"], "generation_invalid_response")
            self.assertEqual(events(services, "retrieval_http_response"), [])

    async def test_concurrent_runs_do_not_share_a_model_response_queue(self):
        async with running_services(service_token=TOKEN) as services:
            async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
                responses = await asyncio.gather(*(
                    client.post(services.agent_url + "/internal/v2/agent/runs",
                        json=make_run(artwork_id=artwork, question=QUESTION),
                        headers=headers("m1-concurrent-" + artwork)) for artwork in ARTWORKS))
            for artwork, response in zip(ARTWORKS, responses):
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["result"]["answer"]["citations"][0]["chunk_id"],
                                 artwork + "-medium")

    async def test_cancellation_closes_both_servers(self):
        entered = asyncio.Event()
        active = []

        async def owner():
            async with running_services(service_token=TOKEN) as services:
                active.append(services)
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(owner())
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
            services = active[0]
            self.assertFalse(services.retrieval_thread.is_alive())
            self.assertTrue(services.serving.done())
            self.assertEqual(services.audit.snapshot()[-1]["event"], "services_stopped")
            await self.assert_closed(services.agent_url, services.retrieval_url)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_cli_publishes_endpoints_without_tokens_and_stops_both_services(self):
        with TemporaryDirectory(prefix="musebridge-m1-harness-test-") as parent:
            state = Path(parent) / "services"
            token = secrets.token_urlsafe(32)
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-B", "-m", "agent.testing.m1_services", "--state-dir", str(state),
                cwd=ROOT, env={**os.environ, "AGENT_SERVICE_TOKEN": token},
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                ready = await asyncio.wait_for(process.stdout.readline(), 10)
                info = json.loads(ready)
                self.assertEqual(info["mode"], MODE)
                self.assertEqual(info["fault"], "none")
                self.assertEqual(json.loads((state / "connection.json").read_text()), info)
                self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o700)
                for file in ("connection.json", "audit.jsonl"):
                    self.assertEqual(stat.S_IMODE((state / file).stat().st_mode), 0o600)
                async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
                    response = await client.post(info["agent_url"] + "/internal/v2/agent/runs",
                        json=make_run(question=QUESTION),
                        headers={"Authorization": "Bearer " + token, "X-Request-ID": "m1-cli-smoke"})
                self.assertEqual(response.status_code, 200, response.text)
                process.terminate()
                output, errors = await asyncio.wait_for(process.communicate(), 10)
                self.assertEqual(process.returncode, 0, errors.decode())
                self.assertFalse((state / "connection.json").exists())
                audit = (state / "audit.jsonl").read_text()
                self.assertNotIn(token, (ready + output + errors).decode() + audit)
                self.assertEqual(json.loads(audit.splitlines()[-1])["event"], "services_stopped")
                self.assertIn("m1-cli-smoke", audit)
                await self.assert_closed(info["agent_url"], info["retrieval_url"])
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.communicate()

    async def test_cli_invalid_secret_fails_safely_and_cleans_up_startup(self):
        with TemporaryDirectory(prefix="musebridge-m1-harness-test-") as parent:
            state = Path(parent) / "services"
            token = "PRIVATE-invalid-short-token"
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-B", "-m", "agent.testing.m1_services", "--state-dir", str(state),
                cwd=ROOT, env={**os.environ, "AGENT_SERVICE_TOKEN": token},
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                output, errors = await asyncio.wait_for(process.communicate(), 10)
                self.assertEqual(process.returncode, 1)
                self.assertNotIn(token, (output + errors).decode())
                self.assertEqual(json.loads(errors)["event"], "startup_or_runtime_failed")
                self.assertFalse((state / "connection.json").exists())
                self.assertEqual(json.loads((state / "audit.jsonl").read_text().splitlines()[-1])["event"],
                                 "services_stopped")
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.communicate()


if __name__ == "__main__":
    unittest.main()
