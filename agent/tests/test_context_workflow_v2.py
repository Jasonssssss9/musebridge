"""End-to-end v2 context snapshots and exact translation retention.

The provider is scripted; the optional evidence lookup uses the real local HTTP
service. These cases test execution integrity, not translation/model quality.
"""

import asyncio
from copy import deepcopy
import json
import threading
import unittest

from knowledge.http_service import create_server

from agent.runtime.errors import WorkflowError
from agent.testing.v2 import (
    FixtureProfile, ScriptedModel, TestBudget, make_run, medium_answer, medium_plan,
)
from agent.tests.test_workflow_v2 import RecordingTool, cached_run
from agent.tools.evidence import EvidenceManifest
from agent.tools.retrieval import RetrievalTool
from agent.workflows.museum_qa_v2 import MuseumQAWorkflowV2


def translation_run(manifest):
    history = [{
        "turn_id": f"00000000-0000-4000-8000-{n:012d}", "sequence": n,
        "question": "What material is it made of?" if n == 4 else f"Conversation turn {n}",
        "answer_language": "en", "answer_status": "answered" if n == 4 else "conversational",
        "answer_mode": "llm", "answer_text": (
            "The museum lists oil on canvas. This does not identify individual pigments."
            if n == 4 else f"A conversational reply for turn {n}."),
    } for n in range(4, 9)]
    run = cached_run(manifest, question="Translate the earlier material answer into Korean.", history=history)
    plan = medium_plan(run)
    plan["action"] = "answer_from_context"
    del plan["queries"]
    plan["language_decision"] = {
        "detected_language": "und", "explicit_instruction": None, "answer_language": "ko",
        "translation": {"target_language": "ko", "original_fragment": run["question"],
                        "source": {"kind": "history_answer", "sequence": 4}},
    }
    plan["requests"][0]["intent"] = "Translate the exact earlier material answer into Korean, preserving its uncertainty."
    answer = medium_answer(run, text="미술관은 캔버스에 유채라고 설명합니다. 개별 안료는 확인되지 않습니다.")
    return run, plan, answer


class ContextWorkflowV2Tests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = EvidenceManifest.load()
        cls.server = create_server("127.0.0.1", 0)
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      kwargs={"poll_interval": 0.01}, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.thread.join, 5)
        cls.addClassCleanup(cls.server.shutdown)

    def workflow(self, model, sizer):
        tool = RecordingTool(RetrievalTool(self.manifest, base_url=self.base_url))
        workflow = MuseumQAWorkflowV2(
            model=model, retrieval=tool, manifest=self.manifest, profile=FixtureProfile(),
            prompt_sizer=sizer,
        )
        return workflow, tool

    async def test_translation_remains_exact_after_its_history_pair_is_dropped(self):
        run, plan, answer = translation_run(self.manifest)
        original = deepcopy(run)
        exact_source = original["context"]["recent_turns"][0]["answer_text"]
        model = ScriptedModel(plan, answer)

        def sizer(messages, schema):
            payload = json.loads(messages[1]["content"])
            if "requests" not in payload:
                return 1000
            return 16000 if len(payload["recent_turns"]) > 1 else 1000

        workflow, tool = self.workflow(model, sizer)
        out = await workflow.run(run, request_id="translation-pinned", budget=TestBudget())
        planned = json.loads(model.calls[0].messages[1]["content"])
        assessed = json.loads(model.calls[1].messages[1]["content"])
        self.assertEqual([turn["sequence"] for turn in planned["recent_turns"]], [4, 5, 6, 7, 8])
        self.assertEqual([turn["sequence"] for turn in assessed["recent_turns"]], [8])
        self.assertEqual(assessed["translation"], {
            "target_language": "ko", "source": {"kind": "history_answer", "sequence": 4},
            "text": exact_source,
        })
        self.assertEqual(out.answer["citations"][0]["chunk_id"], "aic-28560-medium")
        self.assertEqual(out.reused_chunk_ids, ["aic-28560-medium"])
        self.assertEqual(out.diagnostics["retained_history_turns"], 1)
        self.assertEqual(out.model_calls, 2)
        self.assertEqual(tool.calls, [])
        self.assertEqual(run, original)

    async def test_pinned_source_overflow_stops_before_assessment_without_truncation(self):
        run, plan, _ = translation_run(self.manifest)
        original = deepcopy(run)
        exact_source = original["context"]["recent_turns"][0]["answer_text"]
        model = ScriptedModel(plan)
        attempted_inputs = []

        def sizer(messages, schema):
            payload = json.loads(messages[1]["content"])
            if "requests" not in payload:
                return 1000
            attempted_inputs.append(payload)
            return 16000

        workflow, tool = self.workflow(model, sizer)
        with self.assertRaises(WorkflowError) as caught:
            await workflow.run(run, request_id="translation-overflow", budget=TestBudget())
        self.assertEqual((caught.exception.code, caught.exception.phase), ("agent_context_limit", "context"))
        self.assertEqual([len(payload["recent_turns"]) for payload in attempted_inputs], [5, 4, 3, 2, 1, 0])
        self.assertTrue(all(payload["translation"]["text"] == exact_source for payload in attempted_inputs))
        self.assertTrue(all(payload["evidence"][0]["excerpt"] == "Oil on canvas" for payload in attempted_inputs))
        self.assertTrue(all(len(payload["catalog_facts"]) == 3 for payload in attempted_inputs))
        self.assertEqual([call.operation for call in model.calls], ["plan"])
        self.assertEqual(tool.calls, [])
        self.assertEqual(run, original)

    async def test_caller_mutation_during_model_await_cannot_rebind_scope_or_sources(self):
        run = make_run(manifest=self.manifest)
        original = deepcopy(run)
        plan, answer = medium_plan(original), medium_answer(original)

        async def mutate_caller(call):
            await asyncio.sleep(0)
            run["scope"]["artwork_id"] = "aic-11723"
            run["question"] = "A different question after admission."
            run["context"]["catalog_facts"][1]["value"] = "A substituted artist"
            run["conversation_state"]["revision"] = 99
            run["conversation_state"]["scope"]["artwork_id"] = "aic-11723"
            run["conversation_state"]["verified_evidence"] = [
                dict(self.manifest.records["aic-11723-medium"])
            ]
            return plan

        model = ScriptedModel(mutate_caller, answer)
        workflow, tool = self.workflow(model, lambda messages, schema: 1000)
        out = await workflow.run(run, request_id="immutable-admitted-run", budget=TestBudget())
        self.assertNotEqual(run, original)
        self.assertEqual(out.scope, original["scope"])
        self.assertEqual(out.conversation_update["base_revision"], 0)
        self.assertEqual(out.catalog_snapshot, original["context"]["catalog_facts"])
        self.assertEqual(out.reused_chunk_ids, [])
        self.assertEqual(tool.calls[0]["artwork_id"], "aic-28560")
        self.assertEqual(out.answer["citations"][0]["artwork_id"], "aic-28560")
        for call in model.calls:
            payload = json.loads(call.messages[1]["content"])
            self.assertEqual(payload["original_question"], original["question"])
            self.assertEqual(payload["catalog_facts"], original["context"]["catalog_facts"])


if __name__ == "__main__":
    unittest.main()
