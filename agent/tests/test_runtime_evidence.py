"""Metadata capture is private, correlatable and does not log visitor content."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from agent.runtime.errors import WorkflowError
from agent.testing.runtime_evidence import EvidenceExecution, EvidenceModel, EvidenceRetrieval, RuntimeEvidence


class EvidenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)/"events.jsonl"
        self.evidence = RuntimeEvidence(self.path)
        self.addCleanup(self.evidence.close)

    def events(self):
        return [json.loads(line) for line in self.path.read_text().splitlines()]

    def test_file_is_private_and_reuse_or_repository_paths_are_rejected(self):
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            RuntimeEvidence(self.path)
        with self.assertRaises(ValueError):
            RuntimeEvidence(Path(__file__).resolve().parents[2]/"unsafe-evidence.jsonl")

    async def test_model_counts_http_attempt_even_if_decision_is_later_invalid(self):
        model = EvidenceModel(evidence=self.evidence, base_url="http://model.invalid", sizer=None)
        self.addAsyncCleanup(model.aclose)
        marker = model.operation.set("plan")
        try:
            with patch("agent.providers.ollama.OllamaModel._json", new=AsyncMock(return_value={
                "prompt_eval_count": 100, "eval_count": 50,
                "message": {"content": "PRIVATE raw decision"}})):
                await model._json("POST", "/api/chat", trace="trace-one")
        finally:
            model.operation.reset(marker)
        self.assertEqual([e["event"] for e in self.events()], ["model_http_attempt", "model_http_response"])
        self.assertEqual(self.events()[1]["prompt_eval_count"], 100)
        self.assertNotIn("PRIVATE", self.path.read_text())

    async def test_model_failure_records_safe_phase_and_preserves_exception(self):
        model = EvidenceModel(evidence=self.evidence, base_url="http://model.invalid", sizer=None)
        self.addAsyncCleanup(model.aclose)
        error = WorkflowError("generation_invalid_response", "PRIVATE provider diagnostic", phase="planning")
        with patch("agent.providers.ollama.OllamaModel.complete_structured", new=AsyncMock(side_effect=error)):
            with self.assertRaises(WorkflowError) as caught:
                await model.complete_structured(operation="plan", trace_context={"request_id": "trace-two"})
        self.assertIs(caught.exception, error)
        self.assertEqual(self.events()[0]["code"], "generation_invalid_response")
        self.assertNotIn("PRIVATE", self.path.read_text())

    async def test_retrieval_records_ids_and_timing_without_query_or_excerpt(self):
        delegate = type("Tool", (), {"search": AsyncMock(return_value={"candidates": [{"chunk_id": "aic-28560-medium", "excerpt": "PRIVATE excerpt"}]})})()
        tool = EvidenceRetrieval(delegate, self.evidence)
        result = await tool.search({"artwork_id": "aic-28560", "dataset_version": "aic-seed-v0.1", "query": "PRIVATE question"}, request_id="trace-three")
        self.assertEqual(result["candidates"][0]["chunk_id"], "aic-28560-medium")
        self.assertEqual(self.events()[1]["evidence_ids"], ["aic-28560-medium"])
        self.assertNotIn("PRIVATE", self.path.read_text())

    async def test_workflow_failure_never_records_question_or_semantic_state(self):
        delegate = AsyncMock(side_effect=WorkflowError("retrieval_unavailable", "PRIVATE failure", phase="retrieval"))
        executor = EvidenceExecution(delegate, self.evidence)
        run = {"run_id": "run", "question": "PRIVATE visitor message", "scope": {"artwork_id": "aic-28560", "dataset_version": "aic-seed-v0.1"}, "conversation_state": {"revision": 2, "continuity_summary": "PRIVATE memory"}}
        with self.assertRaises(WorkflowError):
            await executor(run, request_id="trace-four", budget=object())
        self.assertEqual([e["event"] for e in self.events()], ["workflow_started", "workflow_failed"])
        self.assertEqual(self.events()[0]["base_revision"], 2)
        self.assertNotIn("PRIVATE", self.path.read_text())

    async def test_raw_input_requires_explicit_private_development_option(self):
        path = Path(self.directory.name)/"explicit-private.jsonl"
        evidence = RuntimeEvidence(path, include_decisions=True)
        self.addCleanup(evidence.close)
        executor = EvidenceExecution(AsyncMock(return_value={"answer": {"status": "conversational"},
            "answer_language": "en", "execution": {"model_calls": 1}}), evidence)
        run = {"run_id": "run", "question": "PRIVATE development question", "scope": {
            "artwork_id": "aic-28560", "dataset_version": "aic-seed-v0.1"}, "conversation_state": {"revision": 0}}
        await executor(run, request_id="explicit-development", budget=object())
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(rows[1]["event"], "private_run_input")
        self.assertEqual(rows[1]["request"]["question"], "PRIVATE development question")
        verdict = next(row for row in rows if row["event"] == "private_workflow_result")
        self.assertEqual(verdict["result"]["answer"], {"status": "conversational"})
        self.assertEqual(verdict["request_id"], "explicit-development")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    async def test_success_omits_canonical_answer_and_memory_from_default_log(self):
        result = {"answer": {"status": "answered", "text": "PRIVATE answer"},
                  "answer_language": "en", "execution": {"model_calls": 1},
                  "memory_update": {"continuity_summary": "PRIVATE memory"}}
        executor = EvidenceExecution(AsyncMock(return_value=result), self.evidence)
        run = {"run_id": "run", "scope": {"artwork_id": "aic-28560", "dataset_version": "release"},
               "conversation_state": {"revision": 0}}
        self.assertEqual(await executor(run, request_id="default", budget=object()), result)
        self.assertEqual([row["event"] for row in self.events()], ["workflow_started", "workflow_returned"])
        self.assertNotIn("PRIVATE", self.path.read_text())


if __name__ == "__main__":
    unittest.main()
