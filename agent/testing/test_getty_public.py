"""Opt-in public Go → FastAPI/LangGraph → HTTP retrieval test with a scripted provider.

Run with the Go toolchain on PATH: python -m unittest agent.testing.test_getty_public
"""

import asyncio
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import unittest
from uuid import uuid4

import httpx

from agent.testing.m1_services import running_services
from agent.testing.v2 import ScriptedModel, language_decision, memory_update
from agent.tools.evidence import EvidenceManifest


ROOT = Path(__file__).resolve().parents[2]
VERSION = "getty-pilot-v1"
SEED = ROOT / "knowledge/data" / f"{VERSION}.json"
CONTRACT = "agent-demo-v0.3"


class GettyPublicChainTests(unittest.IsolatedAsyncioTestCase):
    async def test_guide_then_museum_facts_through_public_conversation_history(self):
        manifest = EvidenceManifest.load(SEED, expected_version=VERSION,
                                         expected_sha256=SEED.with_suffix(".sha256").read_text().strip())
        model = ScriptedModel()
        token = secrets.token_urlsafe(32)
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "musebridge-api"
            await asyncio.to_thread(subprocess.run, ["go", "build", "-o", binary, "./cmd/server"],
                                    cwd=ROOT / "api", check=True)
            async with running_services(service_token=token, seed_path=SEED, manifest=manifest, model=model) as services:
                with socket.socket() as sock:
                    sock.bind(("127.0.0.1", 0))
                    port = sock.getsockname()[1]
                origin = f"http://127.0.0.1:{port}"
                env = dict(os.environ, DATASET_VERSION=VERSION, SEED_PATH=str(SEED),
                           AGENT_SERVICE_TOKEN=token, AGENT_BASE_URL=services.agent_url,
                           GENERATION_MODE="agent", HOST="127.0.0.1", PORT=str(port),
                           PUBLIC_BASE_URL=origin, COOKIE_SECURE="false")
                go = await asyncio.create_subprocess_exec(binary, env=env, stdout=subprocess.DEVNULL)
                try:
                    async with httpx.AsyncClient(base_url=origin, headers={"Origin": origin},
                                                 trust_env=False, timeout=10) as public:
                        async with asyncio.timeout(5):
                            while True:
                                try:
                                    response = await public.get("/healthz")
                                    response.raise_for_status()
                                    break
                                except httpx.ConnectError:
                                    await asyncio.sleep(.03)
                        for artwork in ("getty-108hgy", "getty-103rgh", "getty-103rfp"):
                            await self.play(public, manifest, artwork, model)
                    events = [e for e in services.audit.snapshot() if e["event"] == "retrieval_http_response"]
                    self.assertEqual(len(events), 6)
                    self.assertEqual(len(model.calls), 12)
                finally:
                    if go.returncode is None:
                        go.terminate()
                    await go.wait()

    async def play(self, public, manifest, artwork, model):
        fact, looking, _ = manifest.artworks[artwork]["suggested_questions"]
        official = "According to the Getty, " + fact[0].lower() + fact[1:]
        opened = await public.post("/api/conversations", json={"contract_version": CONTRACT, "artwork_id": artwork})
        self.assertEqual(opened.status_code, 200, opened.text)
        conversation = opened.json()["conversation"]
        path = f"/api/conversations/{conversation['conversation_id']}/messages"
        turns = []
        guide_id = artwork + "-guide-01"
        for revision, (question, expected_kind, key) in enumerate((
            (looking, "guide_note", guide_id), (official, "museum_record", artwork + "-description-02")
        )):
            def plan(call):
                payload = json.loads(call.messages[-1]["content"])
                self.assertEqual(payload["original_question"], question)
                self.assertEqual([r["chunk_id"] for r in payload["cached_evidence"]], [guide_id] if revision else [])
                if revision:
                    self.assertEqual(payload["cached_evidence"][0]["source_kind"], "guide_note")
                    self.assertEqual(len(payload["recent_turns"]), 1)
                return {"action": "retrieve", "language_decision": language_decision(),
                        "requests": [{"id": "r1", "original_fragment": question, "intent": question,
                                      "kind": "artwork_fact", "resolution": "clear"}],
                        "queries": [{"id": "q1", "request_ids": ["r1"], "query": question}]}

            def assess(call):
                records = json.loads(call.messages[-1]["content"])["evidence"]
                expected = next(r for r in records if r["chunk_id"] == key)
                self.assertEqual(expected["source_kind"], expected_kind)
                label = "The Getty records" if revision else "MuseBridge suggests"
                text = f"{label}: {expected['excerpt']}"
                return {"action": "finish", "results": [{
                    "request_id": "r1", "support": "supported", "text": text, "citation_ids": [key]}],
                    "memory_update": memory_update(topic="Looking at the artwork", summary=label + " a detail.")}

            model.responses.extend((plan, assess))
            response = await public.post(path, json={"contract_version": CONTRACT, "client_message_id": str(uuid4()),
                                         "question": question, "expected_revision": revision, "language_override": None})
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            self.assertEqual(result["conversation"]["revision"], revision + 1)
            turn = result["turn"]
            citation, = turn["answer"]["citations"]
            self.assertEqual((citation["chunk_id"], citation["source_kind"]), (key, expected_kind))
            self.assertEqual(citation["attribution"], manifest.records[key]["attribution"])
            turns.append(turn)
        restored = (await public.get(path)).json()
        self.assertEqual(restored["turns"], turns)
        self.assertEqual(restored["conversation"]["revision"], 2)
