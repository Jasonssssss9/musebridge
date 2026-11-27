"""HTTP checks for parallel v2/v3 admission, readback and authenticated readiness."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest

import httpx

from agent.app import create_app
from agent.contracts.codec import ContractCodecV3
from agent.contracts.validation import check_run_result
from agent.runtime.runs import RunRegistry
from agent.testing.service import controlled_registry, service_run
from agent.tools.evidence import EvidenceManifest
from agent.workflows.museum_qa_v3 import vincent_manifest


class VincentHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_versioned_create_restore_and_cross_version_id(self):
        root = Path(__file__).resolve().parents[2]
        samples = {c["name"]: c["payload"] for c in json.loads(
            (root / "docs/contracts/vincent-examples.json").read_bytes())["schema_cases"]}
        run = samples["run_initial_vincent"]
        run["execution"]["deadline_at"] = (datetime.now(timezone.utc) + timedelta(seconds=50)).isoformat(
            timespec="milliseconds").replace("+00:00", "Z")
        result = samples["vincent_grounded_run_result"]
        gallery = root / "knowledge/data/getty-gallery-v2.json"
        manifest = vincent_manifest(EvidenceManifest.load(gallery, expected_version="getty-gallery-v2",
                                      expected_sha256=gallery.with_suffix(".sha256").read_text().strip()))
        codec = ContractCodecV3()
        calls = []

        async def execute(request, **_):
            calls.append(request["run_id"])
            return deepcopy(result)

        older = controlled_registry()
        newer = RunRegistry(execute, validate_input=lambda req: codec.validate("RunRequest", req),
                            ready=lambda: True, codec=codec, private_version="agent-run-v3",
                            workflow_id="museum_qa_v3", shared_with=older,
                            check_result=lambda req, res: check_run_result(req, res, validator=codec.validator,
                                                                        manifest=manifest))
        newer.dataset_version = manifest.dataset_version
        newer.retrieval_strategy_version = "openai-hybrid-v1"
        token = "s" * 48
        app = create_app(older, v3_runtime=newer, service_token=token)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as client:
            prefix = "/internal/v3/agent"
            self.assertEqual((await client.get(prefix + "/readyz")).status_code, 401)
            client.headers.update({"Authorization": "Bearer " + token, "X-Request-ID": "vincent-http"})
            ready = await client.get(prefix + "/readyz")
            self.assertEqual(ready.status_code, 200)
            self.assertEqual(ready.json()["contract_version"], "agent-run-v3")
            self.assertEqual(ready.headers["X-Retrieval-Strategy"], "openai-hybrid-v1")
            receipt = await client.post(prefix + "/runs", json=run)
            self.assertEqual(receipt.status_code, 200, receipt.text)
            self.assertEqual(receipt.json()["result"]["scope"], run["scope"])
            saved = await client.get(prefix + "/runs/" + run["run_id"])
            self.assertEqual(saved.json(), receipt.json())
            replay = await client.post(prefix + "/runs", json=run)
            self.assertEqual(replay.json(), saved.json())
            self.assertEqual(calls, [run["run_id"]])
            hidden = await client.get("/internal/v2/agent/runs/" + run["run_id"])
            self.assertEqual(hidden.status_code, 404)
            other = service_run()
            other["run_id"] = run["run_id"]
            conflict = await client.post("/internal/v2/agent/runs", json=other)
            self.assertEqual(conflict.status_code, 409)
            self.assertEqual(conflict.json()["error"]["code"], "agent_run_conflict")
