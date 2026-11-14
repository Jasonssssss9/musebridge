"""V2 conversation integration using scripted decisions and real HTTP retrieval.

These tests prove execution and contracts, not real-model interpretation quality.
"""
import asyncio
import json
import threading
import unittest
from copy import deepcopy
from time import monotonic
from unittest.mock import patch

from knowledge.http_service import create_server

from agent.runtime.errors import WorkflowError
from agent.runtime.validation_v2 import ContractValidatorV2
from agent.testing.v2 import (
    FixtureProfile, ScriptedModel, TestBudget, example, fixture_prompt_sizer,
    language_decision, make_run, medium_answer, medium_plan, memory_update, simulate_commit,
)
from agent.tools.evidence import EvidenceManifest
from agent.tools.retrieval import RetrievalTool
from agent.workflows.museum_qa_v2 import MuseumQAWorkflowV2


class RecordingTool:
    def __init__(self, delegate=None, *, reply=None, error=None):
        self.delegate, self.reply, self.error = delegate, reply, error
        self.calls = []

    async def search(self, payload, **kwargs):
        self.calls.append(deepcopy(payload))
        if self.error:
            raise self.error
        if self.delegate:
            return await self.delegate.search(payload, **kwargs)
        return deepcopy(self.reply)


def direct(run, *, language="en", detected=None, text="Thank you for sharing your impression.", memory=None):
    return {
        "action": "direct_response", "language_decision": language_decision(language, detected_language=detected),
        "requests": [{"id": "r1", "original_fragment": run["question"],
                      "intent": "Respond to the visitor's current conversational message.",
                      "kind": "conversation", "resolution": "clear"}],
        "results": [{"request_id": "r1", "support": "not_applicable", "text": text, "citation_ids": []}],
        "memory_update": memory or memory_update(),
    }


def cached_run(manifest, *, question="Explain the brushwork.", chunks=None, history=None):
    history = history or [{
        "turn_id": "44444444-4444-4444-8444-444444444444", "sequence": 1,
        "question": "What is it made of?", "answer_language": "en", "answer_status": "answered",
        "answer_mode": "llm", "answer_text": "The museum lists oil on canvas.",
    }]
    run = make_run(question=question, history=history, manifest=manifest)
    run["conversation_state"]["verified_evidence"] = [
        dict(manifest.records[key]) for key in (chunks or ["aic-28560-medium"])
    ]
    return run


class WorkflowV2Tests(unittest.IsolatedAsyncioTestCase):
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

    def workflow(self, model, *, tool=None, sizer=fixture_prompt_sizer):
        tool = tool or RecordingTool(RetrievalTool(self.manifest, base_url=self.base_url))
        return MuseumQAWorkflowV2(model=model, retrieval=tool, manifest=self.manifest,
                                  profile=FixtureProfile(), prompt_sizer=sizer), tool

    async def execute(self, run, *decisions, tool=None, sizer=fixture_prompt_sizer, budget=None):
        model = ScriptedModel(*decisions)
        workflow, tool = self.workflow(model, tool=tool, sizer=sizer)
        outcome = await workflow.run(run, request_id="c-v2-integration", budget=budget or TestBudget())
        return outcome, model, tool

    async def test_both_artworks_and_all_languages_use_real_retrieval(self):
        messages = {
            "en": ("What is it made of?", "The museum lists oil on canvas."),
            "es": ("¿De qué material está hecha?", "El museo indica óleo sobre lienzo."),
            "zh-CN": ("这幅画是什么材质？", "馆方列出的材质是布面油画。"),
            "ko": ("이 작품은 어떤 재료로 그렸나요?", "미술관은 캔버스에 유채라고 설명합니다."),
        }
        for artwork in ("aic-28560", "aic-11723"):
            for language, (question, answer) in messages.items():
                with self.subTest(artwork=artwork, language=language):
                    run = make_run(artwork_id=artwork, question=question, manifest=self.manifest)
                    original = deepcopy(run)
                    out, model, tool = await self.execute(
                        run, medium_plan(run, language=language), medium_answer(run, text=answer))
                    self.assertEqual((out.model_calls, out.retrieval_calls, out.refinement_rounds), (2, 1, 0))
                    self.assertEqual(out.answer_language, language)
                    self.assertEqual(out.answer["citations"][0]["chunk_id"], artwork + "-medium")
                    self.assertEqual(tool.calls[0]["top_k"], 2)
                    self.assertTrue(tool.calls[0]["query"].startswith("What material"))
                    self.assertEqual([c.operation for c in model.calls], ["plan", "assess"])
                    self.assertEqual([c.max_output_tokens for c in model.calls], [2048, 2048])
                    self.assertEqual(run, original)
                    self.assertEqual(out.conversation_update["base_revision"], 0)
                    wire = out.result_fields()
                    # Synthetic identity only checks the full wire shape.
                    wire["execution"] = {**out.execution_fields(), "profile_id": "local-ollama-v2",
                        "model_provider": "ollama", "model_name": "SYNTHETIC-CONTRACT-ONLY",
                        "model_digest": "SYNTHETIC-NOT-A-MODEL-DIGEST", "duration_ms": 1}
                    ContractValidatorV2().validate("RunResult", wire)

    async def test_direct_dialogue_never_retrieves_in_four_languages(self):
        for language, message in (("en", "Beautiful!"), ("es", "¡Qué bonito!"),
                                  ("zh-CN", "真好看！"), ("ko", "정말 아름다워요!")):
            run = make_run(question=message)
            out, model, tool = await self.execute(run, direct(run, language=language, text=message))
            self.assertEqual((out.answer["status"], out.model_calls, out.retrieval_calls), ("conversational", 1, 0))
            self.assertEqual(out.answer["citations"], [])
            self.assertEqual(tool.calls, [])

    async def test_catalog_answers_have_separate_sources_and_no_retrieval_cache(self):
        for artwork in ("aic-28560", "aic-11723"):
            run = make_run(artwork_id=artwork, question="Who made it, and when?")
            plan = direct(run)
            fact = run["context"]["catalog_facts"][1]
            date = run["context"]["catalog_facts"][2]
            plan["requests"][0].update(kind="catalog_fact", intent="Give the catalog creator and displayed date.")
            plan["results"][0].update(support="supported", text=f"{fact['value']}; {date['value']}.",
                                      citation_ids=[fact["fact_id"], date["fact_id"]])
            out, _, tool = await self.execute(run, plan)
            self.assertEqual(out.answer["status"], "answered")
            self.assertTrue(all(c["kind"] == "catalog" for c in out.answer["citations"]))
            self.assertEqual(len(out.catalog_snapshot), 3)
            self.assertEqual(out.evidence_snapshot, [])
            state, _ = simulate_commit(run, out.result_fields())
            self.assertEqual(state["verified_evidence"], [])
            self.assertEqual(tool.calls, [])

    async def test_whole_clarification_and_scope_are_terminal(self):
        for action in ("clarify", "out_of_scope"):
            run = make_run(question="What about that other one?")
            plan = {"action": action, "language_decision": language_decision(),
                    "text": "Please identify the work you mean.",
                    "memory_update": memory_update(pending=(
                        {"action": "set", "source": "current_message", "request_id": None}
                        if action == "clarify" else {"action": "keep"}))}
            out, _, tool = await self.execute(run, plan)
            self.assertEqual(out.requests, [])
            self.assertEqual(out.request_results, [])
            self.assertEqual(out.model_calls, 1)
            self.assertFalse(tool.calls)

    async def test_partial_clarification_survives_thanks_then_resolves(self):
        run = make_run(question="What is it made of, and what is that little object?")
        plan = medium_plan(run)
        plan["requests"][0]["original_fragment"] = "What is it made of"
        plan["requests"].append({"id": "r2", "original_fragment": "what is that little object",
            "intent": "Identify the object the visitor is referring to.", "kind": "artwork_fact",
            "resolution": "needs_clarification"})
        answer = medium_answer(run)
        answer["results"].append({"request_id": "r2", "support": "clarification_needed",
                                 "text": "Which object do you mean?", "citation_ids": []})
        answer["memory_update"]["pending_update"] = {"action": "set", "source": "current_message", "request_id": "r2"}
        out, _, _ = await self.execute(run, plan, answer)
        self.assertEqual(out.answer["status"], "partially_answered")
        state, turn = simulate_commit(run, out.result_fields())
        self.assertEqual(state["pending_clarification"]["question"], "Which object do you mean?")
        self.assertEqual(state["pending_clarification"]["original_fragment"], "what is that little object")
        thanks = make_run(question="Thanks.", history=[turn], conversation_state=state)
        thanks_out, _, tool = await self.execute(thanks, direct(thanks, detected="und"))
        self.assertEqual(tool.calls, [])
        state2, turn2 = simulate_commit(thanks, thanks_out.result_fields())
        self.assertEqual(state2["pending_clarification"], state["pending_clarification"])
        followup = make_run(question="Actually, explain the brushwork instead.", history=[turn, turn2], conversation_state=state2)
        follow_plan, follow_answer = medium_plan(followup), medium_answer(followup)
        follow_plan["requests"][0]["intent"] = "Explain the brushwork of The Bedroom."
        follow_plan["queries"][0]["query"] = "How does the museum describe the brushwork of The Bedroom?"
        follow_answer["results"][0].update(text="The museum describes thick and broken brushwork.",
                                          citation_ids=["aic-28560-description-01"])
        follow_answer["memory_update"]["pending_update"] = {"action": "clear"}
        end, model, tool = await self.execute(followup, follow_plan, follow_answer)
        self.assertEqual(len(tool.calls), 1)
        self.assertEqual(json.loads(model.calls[0].messages[1]["content"])["conversation_context"]["pending_clarification"], state2["pending_clarification"])
        state3, _ = simulate_commit(followup, end.result_fields())
        self.assertIsNone(state3["pending_clarification"])

    async def test_one_off_translation_uses_exact_source_and_keeps_preference(self):
        run = make_run(question="这幅画是什么材质？", language_override="zh-CN")
        first, _, _ = await self.execute(run, medium_plan(run, language="zh-CN"),
                                       medium_answer(run, text="馆方列出的材质是布面油画。"))
        state, turn = simulate_commit(run, first.result_fields())
        translated = make_run(question="Translate your previous answer into Korean for my friend.",
                              history=[turn], conversation_state=state)
        plan = medium_plan(translated)
        plan["action"] = "answer_from_context"
        del plan["queries"]
        plan["language_decision"] = {"detected_language": "und", "explicit_instruction": None,
            "answer_language": "ko", "translation": {"target_language": "ko",
            "original_fragment": translated["question"], "source": {"kind": "history_answer", "sequence": 1}}}
        plan["requests"][0]["intent"] = "Translate the previous source-backed material explanation into Korean."
        out, model, tool = await self.execute(translated, plan,
            medium_answer(translated, text="미술관은 캔버스에 유채라고 설명합니다."))
        self.assertEqual((out.model_calls, out.retrieval_calls), (2, 0))
        self.assertEqual(json.loads(model.calls[1].messages[1]["content"])["translation"]["text"], turn["answer_text"])
        self.assertFalse(tool.calls)
        state2, turn2 = simulate_commit(translated, out.result_fields())
        self.assertEqual(state2["language_preference"], "zh-CN")
        self.assertEqual(state2["last_answer_language"], "ko")
        thanks = make_run(question="OK", history=[turn, turn2], conversation_state=state2)
        last, _, _ = await self.execute(thanks, direct(thanks, language="zh-CN", detected="und", text="不客气。"))
        self.assertEqual(last.answer_language, "zh-CN")

    async def test_five_pair_window_and_summary_continue_for_eight_turns(self):
        state, history = None, []
        for index in range(8):
            run = make_run(question=f"Continue our discussion, turn {index + 1}.",
                           history=history[-5:], conversation_state=state)
            summary = "The visitor prefers simple explanations." if index < 5 else "The visitor now requests more detail."
            plan = direct(run, memory=memory_update(topic="Visitor preferences", summary=summary))
            out, model, _ = await self.execute(run, plan)
            payload = json.loads(model.calls[0].messages[1]["content"])
            self.assertEqual(len(payload["recent_turns"]), min(index, 5))
            if index > 5:
                self.assertEqual(payload["conversation_context"]["continuity_summary"], "The visitor now requests more detail.")
            self.assertNotIn("conversation_id", payload["conversation_context"])
            state, turn = simulate_commit(run, out.result_fields())
            history.append(turn)
        self.assertEqual(state["revision"], 8)

    async def test_complete_cache_reuses_without_lookup_and_catalog_stays_direct(self):
        run = cached_run(self.manifest, question="What is the material?",
                         chunks=["aic-28560-medium", "aic-28560-description-01"])
        plan = medium_plan(run); plan["action"] = "answer_from_context"; del plan["queries"]
        out, model, tool = await self.execute(run, plan, medium_answer(run))
        self.assertEqual(out.retrieval_calls, 0)
        self.assertEqual(out.reused_chunk_ids, ["aic-28560-medium", "aic-28560-description-01"])
        self.assertFalse(json.loads(model.calls[1].messages[1]["content"])["allow_refinement"])
        self.assertFalse(tool.calls)
        with self.assertRaises(WorkflowError):
            await self.execute(run, medium_plan(run))

    async def test_real_empty_lookup_can_refine_once_and_finish(self):
        run = make_run(question="What does the museum say about the brushwork?")
        plan = medium_plan(run)
        plan["requests"][0]["intent"] = "Describe the brushwork of The Bedroom."
        plan["queries"][0]["query"] = "What does the museum say about the facture of The Bedroom?"
        refine = {"action": "refine", "reason_code": "no_candidates", "request_ids": ["r1"],
                  "queries": [{"id": "q2", "request_ids": ["r1"],
                               "query": "How does the museum describe the brushwork of The Bedroom?"}]}
        answer = medium_answer(run)
        answer["results"][0].update(text="The museum describes thick and broken brushwork.",
                                    citation_ids=["aic-28560-description-01"])
        out, model, tool = await self.execute(run, plan, refine, answer)
        self.assertEqual(json.loads(model.calls[1].messages[1]["content"])["evidence"], [])
        self.assertEqual((out.model_calls, out.retrieval_calls, out.refinement_rounds), (3, 2, 1))
        self.assertEqual([c.operation for c in model.calls], ["plan", "assess", "final"])
        self.assertFalse(json.loads(model.calls[2].messages[1]["content"])["allow_refinement"])
        self.assertEqual([c["query_id"] for c in tool.calls], ["q1", "q2"])
        with self.assertRaises(WorkflowError):
            await self.execute(run, plan, refine, refine)

    async def test_cache_path_refinement_starts_at_q1(self):
        run = cached_run(self.manifest)
        plan = medium_plan(run); plan["action"] = "answer_from_context"; del plan["queries"]
        plan["requests"][0]["intent"] = "Describe the brushwork of The Bedroom."
        refine = {"action": "refine", "reason_code": "uncovered_request", "request_ids": ["r1"],
                  "queries": [{"id": "q1", "request_ids": ["r1"], "query": "Describe the brushwork of The Bedroom."}]}
        answer = medium_answer(run)
        answer["results"][0].update(text="The museum describes thick and broken brushwork.", citation_ids=["aic-28560-description-01"])
        out, _, tool = await self.execute(run, plan, refine, answer)
        self.assertEqual((out.model_calls, out.retrieval_calls), (3, 1))
        self.assertEqual(tool.calls[0]["query_id"], "q1")
        self.assertTrue(out.diagnostics["complete_scope"])

    async def test_complete_union_stops_remaining_planned_lookup(self):
        run = make_run(question="Describe the material and brushwork.")
        plan = medium_plan(run)
        plan["queries"][0]["query"] = "What is the material and brushwork of The Bedroom?"
        plan["queries"].append({"id": "q2", "request_ids": ["r1"], "query": "Describe the brushwork of The Bedroom."})
        out, _, tool = await self.execute(run, plan, medium_answer(run))
        self.assertTrue(out.diagnostics["complete_scope"])
        self.assertEqual(len(tool.calls), 1)
        self.assertEqual(len(out.executed_queries), 1)

    async def test_gap_and_dependency_failure_are_different(self):
        run = make_run(question="Which exact pigments were used?")
        plan = medium_plan(run); plan["requests"][0]["intent"] = run["question"]; plan["queries"][0]["query"] = run["question"]
        answer = medium_answer(run)
        answer["results"][0].update(support="insufficient", citation_ids=[],
            text="I cannot confirm the exact pigments from the available sources. We can discuss paint materials generally.")
        out, _, _ = await self.execute(run, plan, answer)
        self.assertEqual(out.answer["status"], "insufficient")
        for code in ("retrieval_unavailable", "retrieval_timeout", "retrieval_invalid_response"):
            tool = RecordingTool(error=WorkflowError(code, "Synthetic failure.", phase="retrieval"))
            with self.assertRaises(WorkflowError) as caught:
                await self.execute(run, plan, tool=tool)
            self.assertEqual(caught.exception.code, code)

    async def test_invalid_input_cache_and_catalog_stop_before_model(self):
        base = cached_run(self.manifest)
        variants = []
        item = deepcopy(base); item["conversation_state"]["verified_evidence"][0]["excerpt"] = "Forged."; variants.append(item)
        item = deepcopy(base); item["conversation_state"]["verified_evidence"] = [dict(self.manifest.records["aic-11723-medium"])]; variants.append(item)
        item = deepcopy(base); item["context"]["catalog_facts"][1]["value"] = "Wrong artist"; variants.append(item)
        item = deepcopy(base); item["context"]["recent_turns"] = []; variants.append(item)
        for run in variants:
            model = ScriptedModel(); workflow, tool = self.workflow(model)
            original = deepcopy(run)
            with self.assertRaises(WorkflowError):
                await workflow.run(run, request_id="test", budget=TestBudget())
            self.assertFalse(model.calls)
            self.assertFalse(tool.calls)
            self.assertEqual(run, original)

    async def test_malformed_cache_uses_integrity_error(self):
        run = cached_run(self.manifest)
        del run["conversation_state"]["verified_evidence"][0]["excerpt"]
        with self.assertRaises(WorkflowError) as caught:
            await self.execute(run)
        self.assertEqual((caught.exception.code, caught.exception.phase), ("retrieval_invalid_response", "context"))

    async def test_invalid_sources_are_rejected_before_assessment(self):
        run = make_run()
        reply = {"tool_version": "evidence-tool-v1", "query_id": "q1", **run["scope"],
                 "retrieval_strategy_version": "legacy-facet-v0.1", "candidates": [dict(self.manifest.records["aic-11723-medium"])]}
        model = ScriptedModel(medium_plan(run)); workflow, _ = self.workflow(model, tool=RecordingTool(reply=reply))
        with self.assertRaises(WorkflowError) as caught:
            await workflow.run(run, request_id="test", budget=TestBudget())
        self.assertEqual(caught.exception.code, "retrieval_invalid_response")
        self.assertEqual(len(model.calls), 1)

    async def test_citation_must_be_accepted_not_merely_in_manifest(self):
        run = make_run(); answer = medium_answer(run)
        answer["results"][0]["citation_ids"] = ["aic-28560-description-01"]
        with self.assertRaises(WorkflowError):
            await self.execute(run, medium_plan(run), answer)

    async def test_translation_source_must_have_survived_planning_fit(self):
        run = example("five_complete_history_turns")
        run["question"] = "Translate the first reply into Korean."
        plan = direct(run, language="ko")
        plan["language_decision"] = {"detected_language": "und", "explicit_instruction": None,
            "answer_language": "ko", "translation": {"target_language": "ko", "original_fragment": run["question"],
            "source": {"kind": "history_answer", "sequence": run["context"]["recent_turns"][0]["sequence"]}}}
        def sizer(messages, schema):
            return 16000 if len(json.loads(messages[1]["content"])["recent_turns"]) > 1 else 1000
        with self.assertRaises(WorkflowError):
            await self.execute(run, plan, sizer=sizer)

    async def test_required_context_overflow_prevents_all_model_calls(self):
        run = make_run(); model = ScriptedModel(); workflow, tool = self.workflow(model, sizer=lambda *_: 20000)
        with self.assertRaises(WorkflowError) as caught:
            await workflow.run(run, request_id="test", budget=TestBudget())
        self.assertEqual(caught.exception.code, "agent_context_limit")
        self.assertFalse(model.calls)
        self.assertFalse(tool.calls)

    async def test_refinement_disabled_when_time_is_low(self):
        run = make_run()
        out, model, _ = await self.execute(run, medium_plan(run), medium_answer(run), budget=TestBudget(30))
        self.assertFalse(json.loads(model.calls[1].messages[1]["content"])["allow_refinement"])
        self.assertEqual(model.calls[1].output_schema["$ref"], "#/$defs/FinalDecision")
        self.assertEqual(out.refinement_rounds, 0)

    async def test_refinement_time_is_rechecked_after_decision(self):
        run = make_run(); budget = TestBudget()
        def late_refinement(call):
            budget.deadline = monotonic() + 20
            return {"action": "refine", "reason_code": "poor_candidate_relevance", "request_ids": ["r1"],
                "queries": [{"id": "q2", "request_ids": ["r1"], "query": "What painting medium is listed?"},
                            {"id": "q3", "request_ids": ["r1"], "query": "What painting support is listed?"}]}
        with self.assertRaises(WorkflowError) as caught:
            await self.execute(run, medium_plan(run), late_refinement, budget=budget)
        self.assertEqual(caught.exception.code, "request_timeout")

    async def test_cancellation_does_not_return_a_memory_proposal(self):
        run = make_run(); original = deepcopy(run)
        with self.assertRaises(asyncio.CancelledError):
            await self.execute(run, medium_plan(run), asyncio.CancelledError())
        self.assertEqual(run, original)

    async def test_parent_cancellation_stops_without_result(self):
        run = make_run()
        entered = asyncio.Event()
        async def waiting_provider(call):
            entered.set()
            await asyncio.Event().wait()
        model = ScriptedModel(waiting_provider)
        workflow, tool = self.workflow(model)
        task = asyncio.create_task(workflow.run(run, request_id="cancel-test", budget=TestBudget()))
        await asyncio.wait_for(entered.wait(), timeout=5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(len(model.calls), 1)
        self.assertFalse(tool.calls)

    async def test_concurrent_runs_keep_scopes_isolated(self):
        def callback(call):
            payload = json.loads(call.messages[1]["content"])
            artwork_id = payload["catalog_facts"][0]["artwork_id"]
            run = make_run(artwork_id=artwork_id, question=payload["original_question"])
            return medium_plan(run) if call.operation == "plan" else medium_answer(run)
        model = ScriptedModel(callback, callback, callback, callback)
        workflow, _ = self.workflow(model)
        runs = [make_run(artwork_id=artwork) for artwork in ("aic-28560", "aic-11723")]
        outcomes = await asyncio.gather(*[workflow.run(run, request_id=f"scope-{i}", budget=TestBudget())
                                          for i, run in enumerate(runs)])
        for run, out in zip(runs, outcomes):
            self.assertEqual(out.answer["citations"][0]["artwork_id"], run["scope"]["artwork_id"])
            self.assertTrue(all(f["artwork_id"] == run["scope"]["artwork_id"] for f in out.catalog_snapshot))

    async def test_raw_logging_cannot_export_context(self):
        run = make_run(); model = ScriptedModel(); workflow, _ = self.workflow(model)
        with patch("agent.workflows.museum_qa_v2.get_debug", return_value=True):
            with self.assertRaises(WorkflowError) as caught:
                await workflow.run(run, request_id="test", budget=TestBudget())
        self.assertEqual(caught.exception.code, "service_not_ready")
        self.assertFalse(model.calls)
