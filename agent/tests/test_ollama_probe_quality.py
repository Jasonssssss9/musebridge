"""Behavioral checks for private probe reporting and evidence review."""
import hashlib
import json
from pathlib import Path
import tempfile
from time import monotonic
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from unittest.mock import patch

import httpx

from agent.providers.capabilities import CHECKS, make_record
from agent.providers.profile import OllamaProfile
from agent.runtime.errors import WorkflowError
from agent.testing.probe_quality import expected, five_pair_capacity, grade_case, review_slots, visitor_cases
from agent.testing.v2 import medium_plan
from agent.testing.verify_ollama import ProbeModel, apply_review, ensure_probe_identity, save
from agent.tests.test_ollama_provider import Sizer, response
from agent.tools.evidence import EvidenceManifest


class ProbeQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = list(visitor_cases(EvidenceManifest.load()))

    def test_evidence_is_independently_observed_even_if_refinement_fails(self):
        case = self.cases[0]
        decision = medium_plan(case.run)
        model = [{"operation": "plan", "completion": {"data": decision}}]
        tool = [{"candidates": [{"chunk_id": case.evidence[0]}]}]
        metrics, passed = grade_case(expected(case), None, model, tool)
        self.assertFalse(passed)
        self.assertTrue(metrics["retrieved_evidence"])
        self.assertIsNone(metrics["status"])
        self.assertEqual(review_slots(model, None)["query_meaning"], "pending")
        for unobserved in ([], [{"error_code": "retrieval_timeout"}]):
            self.assertIsNone(grade_case(expected(case), None, model, unobserved)[0]["retrieved_evidence"])
        self.assertFalse(grade_case(expected(case), None, model, [{"candidates": []}])[0]["retrieved_evidence"])

    def test_false_language_provenance_survives_format_but_fails_probe(self):
        case = self.cases[0]
        plan = medium_plan(case.run)
        plan["language_decision"]["explicit_instruction"] = {"preference": "en", "purpose": "turn_response",
            "persistence": "turn", "original_fragment": case.run["question"]}
        metrics, _ = grade_case(expected(case), None, [{"operation":"plan","completion":{"data":plan}}], [])
        self.assertTrue(metrics["language_decision"])
        self.assertFalse(metrics["language_instruction"])

    def test_explicit_language_setting_and_skipped_lookup_labels_follow_the_contract(self):
        case = next(c for c in self.cases if c.name == "holdout-support")
        plan = medium_plan(case.run, language="es")
        instruction = {"preference": "es", "purpose": "conversation_setting", "persistence": "conversation",
                       "original_fragment": "Answer in Spanish."}
        plan["language_decision"]["explicit_instruction"] = instruction
        attempts = [{"operation": "plan", "completion": {"data": plan}}]
        self.assertTrue(grade_case(expected(case), None, attempts, [])[0]["language_instruction"])
        instruction.update(purpose="turn_response", persistence="turn")
        self.assertFalse(grade_case(expected(case), None, attempts, [])[0]["language_instruction"])
        greeting = next(c for c in self.cases if c.name == "direct-es")
        plan.update(action="direct_response")
        self.assertEqual(grade_case(expected(greeting), None, attempts, [])[0]["retrieved_evidence"], "not_applicable")

    def test_long_prompt_keeps_five_actual_history_ids_independently_of_answer(self):
        case = next(c for c in self.cases if c.name == "five-pair-sizing")
        history = case.run["context"]["recent_turns"]
        attempt = {"operation":"plan", "retained_turn_ids":[t["turn_id"] for t in history],
                   "server_prompt_tokens":1000, "estimated_prompt_tokens":1050, "outcome":"failed"}
        self.assertTrue(five_pair_capacity([attempt], history, OllamaProfile()))
        attempt["retained_turn_ids"].pop()
        self.assertFalse(five_pair_capacity([attempt], history, OllamaProfile()))

    def test_digest_and_semantic_review_gate_the_capability_record(self):
        marker = {"valid": False, "probe_run_id": "this-observation"}
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path, review, cap = (directory / name for name in ("report", "review", "capability"))
            checks = dict.fromkeys(CHECKS, True)
            provider = SimpleNamespace(verified=True, metadata_fingerprint="test-model", profile=OllamaProfile())
            fields = make_record(provider, checks)
            del fields["checks"]
            report = {"report_version":"ollama-workflow-probes-v2","run_id":marker["probe_run_id"],
                      "review_state":"pending","final_diagnostic_review":{"grounding":"pending"}, "machine_passed":True,"model_attempts":[], "checks":checks,
                      "metadata_fingerprint":provider.metadata_fingerprint,"attestation_fields":fields,
                      "cases":[{"name":"direct","suite":"development","machine_passed":True,
                          "machine_checks":{"route":True},"semantic_review":review_slots([], {"answer":"example"})}]}
            save(path, report)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            save(cap, marker)
            dimensions = {"planning_meaning":"not_applicable","query_meaning":"not_applicable",
                          "answer_language":"pass","memory":"pass","grounding":"fail"}
            judgments = {"report_sha256":digest,"cases":{"direct":dimensions},"final_diagnostic":{"grounding":"pass"}}
            save(review, {**judgments,"report_sha256":"other"})
            with self.assertRaises(ValueError):
                apply_review(path,review,cap)
            save(review, judgments)
            with patch("builtins.print"):
                self.assertFalse(apply_review(path,review,cap))
            self.assertEqual(json.loads(cap.read_bytes()), marker)
            save(path, report)
            dimensions["grounding"] = "pass"
            save(review, judgments)
            save(cap, {**marker,"probe_run_id":"a-newer-run"})
            with self.assertRaises(ValueError): apply_review(path,review,cap)
            save(cap, marker)
            with patch("builtins.print"):
                self.assertTrue(apply_review(path,review,cap))
            self.assertEqual(json.loads(cap.read_bytes()), {**fields, "checks":checks})


class ProviderObservationTests(unittest.IsolatedAsyncioTestCase):
    async def test_next_case_rechecks_identity_after_transport_failure_without_retrying_chat(self):
        model = SimpleNamespace(verified=False, metadata_fingerprint="same", verify=AsyncMock())
        report = {"metadata_fingerprint":"same"}
        await ensure_probe_identity(model, report, "next-case")
        model.verify.assert_awaited_once()
        self.assertTrue(report["metadata_readmissions"][0]["verified"])
        model.metadata_fingerprint = "changed"
        with self.assertRaises(WorkflowError):
            await ensure_probe_identity(model, report, "third-case")
        self.assertFalse(model.verified)
        self.assertEqual(report["metadata_readmissions"][1]["error_code"], "service_not_ready")

    async def test_rejected_inner_schema_records_attempt_actual_usage_and_raw_response(self):
        profile = OllamaProfile()
        def handle(request):
            if request.url.path == "/api/version":
                return response({"version":profile.server_version})
            if request.url.path == "/api/tags":
                return response({"models":[{"name":profile.model_name,"digest":profile.model_digest}]})
            if request.url.path == "/api/chat":
                return response({"model":profile.model_name,"done":True,"done_reason":"stop",
                                 "message":{"role":"assistant","content":'{"ok":false}'},
                                 "prompt_eval_count":80,"eval_count":4})
            self.fail("Unexpected endpoint")
        report = {"model_attempts":[]}
        model = ProbeModel(report, base_url="http://test.invalid", sizer=Sizer(), transport=httpx.MockTransport(handle))
        self.addAsyncCleanup(model.aclose)
        model.verified = True
        with self.assertRaises(WorkflowError):
            await model.complete_structured(operation="plan", messages=[{"role":"system","content":"Return JSON"},
                {"role":"user","content":"Testing"}], output_schema={"type":"object","required":["ok"],
                    "properties":{"ok":{"const":True}}}, profile=profile, deadline=monotonic()+2,
                trace_context={"request_id":"probe-schema"})
        [attempt] = report["model_attempts"]
        self.assertEqual((attempt["outcome"],attempt["schema_result"],attempt["chat_attempted"]), ("failed","invalid",True))
        self.assertEqual((attempt["server_prompt_tokens"],attempt["raw_content"]), (80, '{"ok":false}'))
