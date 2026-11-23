"""Per-turn answer grammar and canonical validation share the frozen request order."""

import json
import unittest
from copy import deepcopy
from time import monotonic
from unittest.mock import Mock

from jsonschema import Draft202012Validator

from agent.providers.grammar import grammar_schema
from agent.runtime.context_v2 import fit_model_call
from agent.runtime.validation_v2 import ContractValidatorV2
from agent.testing.v2 import (
    FixtureProfile, ScriptedModel, TestBudget, fixture_prompt_sizer, make_run,
    medium_answer, medium_plan, memory_update,
)
from agent.tools.evidence import EvidenceManifest
from agent.workflows.museum_qa_v2 import MuseumQAWorkflowV2


class FrozenAnswerSchemaTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = ContractValidatorV2()

    def test_full_and_ollama_schemas_accept_exactly_one_result_per_planned_id_in_order(self):
        for operation, stage in (("assess", "AssessmentDecision"), ("final", "FinalDecision")):
            base = self.validator.schema(stage)
            original = deepcopy(base)
            for count in range(1, 5):
                with self.subTest(operation=operation, count=count):
                    ids = [f"r{position}" for position in range(1, count + 1)]
                    payload = {"requests": [{"id": request_id} for request_id in ids], "recent_turns": []}
                    _, schema, _ = fit_model_call(operation, payload, base, prompt_sizer=fixture_prompt_sizer,
                                                  context_limit=16384, output_tokens=2048)
                    valid = [{"request_id": rid, "text": "Two sentences are fine. This detail remains open.",
                              "support": "insufficient", "citation_ids": []} for rid in ids]
                    valid[-1].update(text="The museum lists oil on canvas.", support="supported",
                                     citation_ids=["aic-28560-medium"])
                    for shape in (schema, grammar_schema(schema)):
                        validator = Draft202012Validator(shape)
                        reply = {"action": "finish", "results": valid, "memory_update": memory_update()}
                        self.assertTrue(validator.is_valid(reply))
                        bad_id = dict(valid[-1], request_id="r1" if count > 1 else "r2")
                        bad_ids = [valid + [valid[-1]], valid[:-1], valid[:-1] + [bad_id]]
                        if count > 1:
                            bad_ids.append(list(reversed(valid)))
                        for invalid in bad_ids:
                            self.assertFalse(validator.is_valid(dict(reply, results=invalid)))
            self.assertEqual(base, original)

    async def test_deadline_downgrade_sends_the_same_single_request_constraint(self):
        manifest = EvidenceManifest.load()
        history = [{"turn_id": "44444444-4444-4444-8444-444444444444", "sequence": 1,
                    "question": "What is the medium?", "answer_language": "en", "answer_status": "answered",
                    "answer_mode": "llm", "answer_text": "The museum lists oil on canvas."}]
        run = make_run(manifest=manifest, history=history)
        run["conversation_state"]["verified_evidence"] = [dict(manifest.records["aic-28560-medium"])]
        plan = medium_plan(run)
        plan["action"] = "answer_from_context"
        del plan["queries"]
        answer = medium_answer(run)
        model = ScriptedModel(plan, answer)
        budget = TestBudget()

        def size(messages, schema):
            if schema["$ref"].endswith("/AssessmentDecision"):
                budget.deadline = monotonic() + 30
            return fixture_prompt_sizer(messages, schema)

        workflow = MuseumQAWorkflowV2(model=model, manifest=manifest, retrieval=Mock(spec=[]),
                                      profile=FixtureProfile(), prompt_sizer=size)
        outcome = await workflow.run(run, request_id="frozen-results-time-limit", budget=budget)
        self.assertEqual(outcome.answer["text"], answer["results"][0]["text"])
        call = model.calls[-1]
        self.assertEqual(call.output_schema["$ref"], "#/$defs/FinalDecision")
        self.assertFalse(json.loads(call.messages[1]["content"])["allow_refinement"])
        validator = Draft202012Validator(grammar_schema(call.output_schema))
        self.assertFalse(validator.is_valid(dict(answer, results=answer["results"] * 2)))
