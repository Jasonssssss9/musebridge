"""Verify the scoped v3 boundary with canonical Irises records and a scripted model."""
import json
import os
from copy import deepcopy
from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock, patch

import httpx
from jsonschema import Draft202012Validator

from agent.providers.grammar import grammar_schema
from agent.runtime.context_v2 import build_plan_input, fit_model_call

from knowledge.vincent import VINCENT_SCOPE
from agent.contracts.codec import ContractCodecV3
from agent.contracts.validation import check_run_result
from agent.runtime.errors import WorkflowError
from agent.runtime.validation import ContractViolation
from agent.service import ModelIdentity, WorkflowExecutor
from agent.testing.v2 import FixtureProfile, ScriptedModel, TestBudget, fixture_prompt_sizer, make_run
from agent.tests.test_workflow_v2 import artwork_plan, direct, finish
from agent.tools.evidence import EvidenceManifest
from agent.workflows.museum_qa_v3 import MuseumQAWorkflowV3, create_vincent_workflow, vincent_manifest, vincent_retrieval


class VincentWorkflowTests(IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(os.environ, {"DATASET_VERSION": "getty-gallery-v2"}, clear=True):
            cls.gallery = EvidenceManifest.from_environment()
        cls.manifest = vincent_manifest(cls.gallery)
        cls.codec = ContractCodecV3()

    def run_request(self, question, *, role="vincent"):
        scope = dict(VINCENT_SCOPE)
        if role == "guide":
            scope.update(conversation_mode="guide", reference_pack_version=None)
        run = make_run(question=question, artwork_id=scope["artwork_id"], manifest=self.gallery)
        run.update(scope=scope, contract_version="agent-run-v3")
        run["execution"]["workflow_id"] = "museum_qa_v3"
        run["conversation_state"].update(state_version="conversation-state-v2", scope=dict(scope),
                                        language_preference="en" if role == "vincent" else "auto")
        return run

    async def test_model_queries_bound_route_and_sees_scoped_letters_before_responding(self):
        question = "What color did you call the irises?"
        run = self.run_request(question)
        query = "How did Vincent describe the color of Irises in his letters?"
        eid = "vincent-772-garden-colors"
        passage = dict(self.manifest.records[eid])
        answer = finish(("I described them as violet in a letter to Theo.", [eid]),
                        summary="Vincent described the irises as violet in a letter to Theo.")
        model = ScriptedModel(artwork_plan((question, query)), answer)
        calls = []

        def send(request):
            body = json.loads(request.content)
            calls.append((request.url.path, body))
            return httpx.Response(200, json={"dataset_version": run["scope"]["dataset_version"], "evidence": [passage]},
                                  headers={"X-Request-ID": "vincent-test", "X-Retrieval-Strategy": "openai-hybrid-v1"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
            tool = vincent_retrieval(self.manifest, base_url="http://knowledge.test", client=client)
            workflow = create_vincent_workflow(model=model, profile=FixtureProfile(), prompt_sizer=fixture_prompt_sizer,
                                               manifest=self.manifest, retrieval=tool)
            result = await WorkflowExecutor(workflow, ModelIdentity("fixture", "a"*64))(
                run, request_id="vincent-test", budget=TestBudget())
        self.assertEqual(calls, [("/retrieve/vincent", {"artwork_id": "getty-103jnh", "dataset_version": "getty-gallery-v2",
                                                        "query": query, "top_k": 2})])
        self.assertEqual([c.operation for c in model.calls], ["plan", "assess"])
        plan, assess = [json.loads(c.messages[1]["content"]) for c in model.calls]
        self.assertEqual(plan["scope"], dict(VINCENT_SCOPE))
        self.assertEqual(list(plan)[-1], "original_question")
        system = model.calls[0].messages[0]["content"]
        self.assertIn("direct_response", system)
        self.assertNotIn("With no available source, set citations to", system)
        decoded_schema = model.calls[0].output_schema["$defs"]
        self.assertNotIn({"$ref": "#/$defs/ContextAnswerPlan"}, decoded_schema["Plan"]["oneOf"])
        for variant in decoded_schema["RequestResult"]["oneOf"]:
            citations = variant["properties"]["citation_ids"]
            if citations["minItems"]:
                self.assertEqual(citations["items"]["enum"], [f["fact_id"] for f in plan["catalog_facts"]])
        self.assertEqual(set(plan["available_source_kinds"]), {"artist_letter", "museum_record", "guide_note"})
        self.assertEqual(assess["scope"], plan["scope"])
        self.assertEqual(assess["evidence"], [passage])
        self.assertEqual(result["answer"]["citations"], [passage])
        self.assertEqual(result["answer"]["text"], answer["results"][0]["text"])
        self.assertEqual(result["conversation_update"]["memory"], answer["memory_update"])
        check_run_result(run, result, validator=self.codec.validator, manifest=self.manifest)
        swapped = deepcopy(result)
        swapped["scope"].update(conversation_mode="guide", reference_pack_version=None)
        with self.assertRaises(ContractViolation):
            check_run_result(run, swapped, validator=self.codec.validator, manifest=self.manifest)
        altered = deepcopy(result)
        altered["answer"]["citations"][0]["excerpt"] = altered["evidence_snapshot"][0]["excerpt"] = "Changed words."
        with self.assertRaises(WorkflowError):
            check_run_result(run, altered, validator=self.codec.validator, manifest=self.manifest)

    async def test_saturated_cache_retains_all_fresh_hits_and_recent_prior_records(self):
        first, second = "What did you write?", "What has the museum found?"
        run = self.run_request(first + " " + second)
        records = list(self.manifest.records.values())
        cache = [dict(record) for record in records[:8]]
        run["conversation_state"].update(verified_evidence=deepcopy(cache), revision=1, last_answer_language="en")
        run["context"]["recent_turns"] = [{
            "turn_id": "11111111-1111-4111-8111-111111111111", "sequence": 1,
            "question": "What can you tell me so far?", "answer_text": "We have been looking at the painting.",
            "answer_language": "en", "answer_mode": "llm", "answer_status": "answered"}]
        original = deepcopy(run)
        queries = ["What do the letters say?", "What do the museum records say?"]
        hits = {queries[0]: [records[8], records[9]], queries[1]: [records[7], records[10]]}

        def send(request):
            result = [dict(r) for r in hits[json.loads(request.content)["query"]]]
            return httpx.Response(200, json={"dataset_version": "getty-gallery-v2", "evidence": result},
                                  headers={"X-Request-ID": "full-cache", "X-Retrieval-Strategy": "openai-hybrid-v1"})

        model = ScriptedModel(artwork_plan((first, queries[0]), (second, queries[1])),
                              finish(("One answer.", [records[8]["chunk_id"]]),
                                     ("Another answer.", [records[10]["chunk_id"]])))
        async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
            tool = vincent_retrieval(self.manifest, base_url="http://knowledge.test", client=client)
            workflow = create_vincent_workflow(model=model, profile=FixtureProfile(), prompt_sizer=fixture_prompt_sizer,
                                               manifest=self.manifest, retrieval=tool)
            result = await WorkflowExecutor(workflow, ModelIdentity("fixture", "a"*64))(
                run, request_id="full-cache", budget=TestBudget())
        self.assertEqual(run, original)
        self.assertEqual(result["reused_chunk_ids"], [r["chunk_id"] for r in cache])
        expected = [dict(r) for r in (*records[:4], records[7], *records[8:11])]
        self.assertEqual(result["evidence_snapshot"], expected)
        answer_payload = json.loads(model.calls[-1].messages[1]["content"])
        self.assertEqual(answer_payload["evidence"], expected)
        check_run_result(run, result, validator=self.codec.validator, manifest=self.manifest)

        # An omitted cached record cannot disappear unless a fresh one took its slot.
        missing = deepcopy(result)
        missing["evidence_snapshot"].pop(0)
        with self.assertRaises(ContractViolation):
            check_run_result(run, missing, validator=self.codec.validator, manifest=self.manifest)
        # Even a displaced record still belongs to the admitted cache identity.
        missing = deepcopy(result)
        missing["reused_chunk_ids"].pop(6)
        with self.assertRaises(ContractViolation):
            check_run_result(run, missing, validator=self.codec.validator, manifest=self.manifest)

    def test_single_request_grammar_and_valid_mixed_plans(self):
        run = self.run_request("What can the sources tell me?")
        payload = build_plan_input(run, self.manifest)
        _, schema, _ = fit_model_call("plan", payload, self.codec.validator.schema("Plan"),
                                      prompt_sizer=fixture_prompt_sizer, context_limit=16384,
                                      output_tokens=2048)
        grammar = Draft202012Validator(grammar_schema(schema))
        factual = artwork_plan((run["question"], "What do sources say about this artwork?"))
        grammar.validate(factual)
        unresolved = deepcopy(factual)
        unresolved["requests"][0]["resolution"] = "needs_clarification"
        self.assertFalse(grammar.is_valid(unresolved))
        mixed = deepcopy(factual)
        mixed["requests"].append({**unresolved["requests"][0], "id": "r2"})
        grammar.validate(mixed)
        social = direct(run)
        grammar.validate(social)
        repeated = deepcopy(social)
        repeated["results"] *= 2
        self.assertFalse(grammar.is_valid(repeated))
        unsupported = deepcopy(social)
        unsupported["results"][0].update(support="supported", citation_ids=[payload["catalog_facts"][0]["fact_id"]])
        self.assertFalse(grammar.is_valid(unsupported))
        catalog = deepcopy(unsupported)
        catalog["requests"][0]["kind"] = "catalog_fact"
        grammar.validate(catalog)
        clarification = {"action": "clarify", "language_decision": social["language_decision"],
                         "text": "Which part caught your eye?", "memory_update": {
                             **social["memory_update"],
                             "pending_update": {"action": "set", "source": "current_message", "request_id": None}}}
        self.codec.validator.validate("Plan", clarification)
        grammar.validate(clarification)
        # Guide schema is independent of the additional actor-specific grammar.
        guide = grammar_schema(self.codec.validator.schema("Plan"))
        self.assertNotIn("oneOf", guide["$defs"]["DirectResponsePlan"])

    def test_lexical_only_tool_cannot_start_vincent_graph(self):
        with self.assertRaisesRegex(ValueError, "require the hybrid"):
            create_vincent_workflow(model=ScriptedModel(), profile=FixtureProfile(),
                                    prompt_sizer=fixture_prompt_sizer, manifest=self.manifest,
                                    retrieval=Mock(retrieval_strategy_version="legacy-facet-v0.1"))

    async def test_guide_cache_and_mode_mismatch_rejected_before_model(self):
        model = ScriptedModel()
        tool = vincent_retrieval(self.manifest, base_url="http://knowledge.test")
        deps = dict(model=model, profile=FixtureProfile(), prompt_sizer=fixture_prompt_sizer, retrieval=tool)
        vincent = create_vincent_workflow(manifest=self.manifest, **deps)
        run = self.run_request("Hello", role="guide")
        with self.assertRaises(WorkflowError):
            await vincent.run(run, request_id="wrong-mode", budget=TestBudget())
        guide = MuseumQAWorkflowV3(conversation_mode="guide", manifest=self.gallery, **deps)
        forged = deepcopy(run)
        forged["scope"]["reference_pack_version"] = "vincent-irises-v1"
        forged["conversation_state"]["scope"] = dict(forged["scope"])
        with self.assertRaises(WorkflowError):
            await guide.run(forged, request_id="guide-claiming-pack", budget=TestBudget())
        run["conversation_state"]["verified_evidence"] = [dict(self.manifest.records["vincent-772-garden-colors"])]
        with self.assertRaises(WorkflowError):
            await guide.run(run, request_id="wrong-cache", budget=TestBudget())
        self.assertEqual(model.calls, [])

    async def test_conversation_needs_no_retrieval_and_keeps_english_with_language_request(self):
        run = self.run_request("Please speak Korean while we talk.")
        decision = direct(run)
        decision["language_decision"]["explicit_instruction"] = {
            "original_fragment": "Please speak Korean", "preference": "ko", "purpose": "turn_response", "persistence": "turn"}
        model = ScriptedModel(decision)
        workflow = create_vincent_workflow(model=model, profile=FixtureProfile(), prompt_sizer=fixture_prompt_sizer,
                                           manifest=self.manifest,
                                           retrieval=vincent_retrieval(self.manifest, base_url="http://knowledge.test"))
        result = await workflow.run(run, request_id="role-lang", budget=TestBudget())
        self.assertEqual((result.answer_language, result.retrieval_calls), ("en", 0))
        self.assertEqual(result.language_decision, decision["language_decision"])
