"""Verify the scoped v3 boundary with canonical Irises records and a scripted model."""
import json
import os
from copy import deepcopy
from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock, patch

import httpx
from jsonschema import Draft202012Validator

from agent.providers.grammar import grammar_schema
from agent.runtime.context_v2 import build_plan_input
from agent.runtime.validation_v2 import validate_plan
from agent.runtime.vincent_search import render_query
from agent.runtime.vincent_plan import (canonical_decision, direct_writer_call, model_call,
                                        needs_direct_writer, with_direct_writing)

from knowledge.vincent import VINCENT_SCOPE
from agent.contracts.codec import ContractCodecV3
from agent.contracts.validation import check_run_result
from agent.runtime.errors import WorkflowError
from agent.runtime.validation import ContractViolation
from agent.service import ModelIdentity, WorkflowExecutor
from agent.testing.v2 import FixtureProfile, ScriptedModel, TestBudget, fixture_prompt_sizer, make_run
from agent.tools.evidence import EvidenceManifest
from agent.workflows.museum_qa_v3 import MuseumQAWorkflowV3, create_vincent_workflow, vincent_manifest, vincent_retrieval


def plan(intent, action, **fields):
    return {"intent": intent, "next": {"action": action, **fields},
            "language": {"detected_language": "en", "explicit_instruction": None, "translation": None}}


def settled(answer):
    return {"sequence": 1, "question": "What does the source say?", "answer_text": answer, "answer_status": "answered"}


def writing():
    return {"topic": "The conversation", "remember": "The visitor is discussing the artwork."}


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
        answer = {"action": "answer", "parts": {"r1": {
            "grounds": [{"source": eid, "spans": ["p1", "l1"], "finding": "Vincent called the irises violet in his letter to Theo."}],
            "reply": "I described them as violet in a letter to Theo."}},
            "topic": "The color in the letter", "remember": "Vincent described the irises as violet in a letter to Theo."}
        model = ScriptedModel(plan(query, "search", perspective="artist_letter", query=query), answer)
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
                                                        "query": query + " Consult the artist's letters.", "top_k": 2})])
        self.assertEqual([c.operation for c in model.calls], ["plan", "assess"])
        plan_input, assess = [json.loads(c.messages[1]["content"]) for c in model.calls]
        self.assertEqual(plan_input["scope"], dict(VINCENT_SCOPE))
        self.assertEqual(list(plan_input)[-1], "original_question")
        system = model.calls[0].messages[0]["content"]
        self.assertIn("repeat_verified", system)
        self.assertNotIn("With no available source, set citations to", system)
        branches = model.calls[0].output_schema["$defs"]["VincentPlan"]["properties"]["next"]["oneOf"]
        actions = [b["properties"]["action"]["const"] for b in branches]
        self.assertNotIn("repeat_verified", actions)
        self.assertIn("search", actions)
        self.assertEqual(set(plan_input["available_source_kinds"]), {"artist_letter", "museum_record", "guide_note"})
        self.assertEqual(assess["scope"], plan_input["scope"])
        self.assertIn(assess["sources"][0]["passage"]["p1"], passage["excerpt"])
        self.assertIn(assess["sources"][0]["locator"]["l1"], passage["source_locator"])
        self.assertEqual(result["answer"]["citations"], [passage])
        self.assertEqual(result["answer"]["text"], answer["parts"]["r1"]["reply"])
        self.assertEqual(result["conversation_update"]["memory"]["continuity_summary"], answer["remember"])
        self.assertEqual(result["conversation_update"]["memory"]["active_topic"], answer["topic"])
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
        searches = [{"parts": [i], "perspective": kind, "query": query} for i, (kind, query) in
                    enumerate(zip(("artist_letter", "museum_record"), queries), 1)]
        hits = {render_query(searches[0]): [records[8], records[9]], render_query(searches[1]): [records[7], records[10]]}

        def send(request):
            result = [dict(r) for r in hits[json.loads(request.content)["query"]]]
            return httpx.Response(200, json={"dataset_version": "getty-gallery-v2", "evidence": result},
                                  headers={"X-Request-ID": "full-cache", "X-Retrieval-Strategy": "openai-hybrid-v1"})

        model = ScriptedModel(plan("Compare letter and museum perspectives.", "search_multiple",
                                   requests=[{"fragment": text, "intent": query, "kind": "artwork_fact"}
                                             for text, query in zip((first, second), queries)],
                                   queries=searches),
                              {"action": "answer", "parts": {"r1": {"grounds": [{"source": records[8]["chunk_id"], "spans": ["p1"], "finding": "First perspective."}],
                                                                    "reply": "One answer."},
                                                            "r2": {"grounds": [{"source": records[10]["chunk_id"], "spans": ["p1"], "finding": "Second perspective."}],
                                                                    "reply": "Another answer."}},
                               "topic": "Two perspectives", "remember": "The visitor compared the sources."})
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
        self.assertEqual([card["ref"] for card in answer_payload["sources"]], [r["chunk_id"] for r in expected])
        for card, record in zip(answer_payload["sources"], expected):
            self.assertTrue(all(span in record["excerpt"] for span in card["passage"].values()))
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
        first, second = "What do the records say?", "And the other one?"
        run = self.run_request(first + " " + second)
        payload = build_plan_input(run, self.manifest)
        schema, _ = model_call(payload)
        grammar = Draft202012Validator(grammar_schema(schema))

        def accepted(raw, writing_result=None):
            grammar.validate(raw)
            if needs_direct_writer(raw):
                writer_schema, _ = direct_writer_call(raw, payload)
                Draft202012Validator(writer_schema).validate(writing_result)
                raw = with_direct_writing(raw, writing_result)
            self.codec.validator.validate("Plan", canonical_decision(raw, payload))
            validate_plan(run, canonical_decision(raw, payload), self.codec.validator, plan_input=payload)
            return canonical_decision(raw, payload)

        prompt = "What do the records say about the artwork?"
        raw = plan(prompt, "search", perspective="any_source", query=prompt)
        single = accepted(raw)
        self.assertEqual(single["requests"], [{"id": "r1", "original_fragment": run["question"], "intent": prompt,
                                               "kind": "artwork_fact", "resolution": "clear"}])
        self.assertEqual(single["queries"], [{"id": "q1", "query": prompt, "request_ids": ["r1"]}])
        self.assertFalse(grammar.is_valid(plan(prompt, "use_passages")))
        self.assertFalse(grammar.is_valid(plan(prompt, "search", perspective="any_source", query=prompt, resolution="needs_clarification")))

        parts = [{"fragment": first, "intent": prompt, "kind": "artwork_fact"},
                 {"fragment": second, "intent": "Identify which other artwork is meant.", "kind": "clarify_artwork_fact"}]
        mixed = plan("Find the record and identify the other work.", "search_multiple",
                     requests=parts, queries=[{"parts": [1], "perspective": "any_source", "query": prompt}])
        self.assertEqual(accepted(mixed)["requests"][1]["resolution"], "needs_clarification")
        unbound = deepcopy(mixed)
        unbound["next"]["queries"][0]["parts"] = [1, 2]
        with self.assertRaises(WorkflowError):
            validate_plan(run, canonical_decision(unbound, payload), self.codec.validator, plan_input=payload)

        social = plan("Acknowledge the visitor.", "acknowledge")
        note = {"parts": {"r1": {"clause": "Thank you", "ending": "."}}, **writing()}
        self.assertEqual(accepted(social, note)["results"][0]["citation_ids"], [])
        self.assertFalse(grammar.is_valid(plan("Acknowledge the visitor.", "acknowledge", reply="Unverified.")))
        catalog = plan("Give the painting's title.", "give_title")
        title = payload["catalog_facts"][0]["value"]
        response = {"parts": {"r1": {"source_value": title, "reply": f"It is called {title}."}}, **writing()}
        self.assertEqual(accepted(catalog, response)["results"][0]["citation_ids"], [payload["catalog_facts"][0]["fact_id"]])
        response["parts"]["r1"]["reply"] = "A fact that is not provided."
        with self.assertRaises(ContractViolation):
            accepted(catalog, response)
        response["parts"]["r1"] = {"source_value": None, "reply": "That detail is not in the title."}
        self.assertEqual(accepted(catalog, response)["results"][0]["support"], "insufficient")
        self.assertFalse(grammar.is_valid(plan("Give the dimensions.", "catalog_fact")))
        clarification = plan("Disambiguate the referent.", "clarify", reply="Which part caught your eye?",
                             pending_source="current_message", writing=writing())
        self.assertEqual(accepted(clarification)["memory_update"]["pending_update"], {
            "action": "set", "source": "current_message", "request_id": None})
        repeat = "Would you repeat the finding?"
        payload = build_plan_input(self.run_request(repeat), self.manifest)
        record = dict(next(iter(self.manifest.records.values())))
        payload["cached_evidence"] = [record]
        proof = {"passage": record["chunk_id"], "source_quote": record["excerpt"], "answer_quote": record["excerpt"],
                 "establishes": "Repeat the previously reported finding."}
        reuse = plan(prompt, "repeat_verified", visitor_request=repeat, support=[proof])
        # A verified passage alone never permits reuse.
        self.assertFalse(Draft202012Validator(model_call(payload)[0]).is_valid(reuse))
        payload["recent_turns"] = [settled(record["excerpt"])]
        self.assertTrue(Draft202012Validator(model_call(payload)[0]).is_valid(reuse))
        self.assertEqual(canonical_decision(reuse, payload)["action"], "answer_from_context")
        for key in ("answer_quote", "source_quote"):
            other = deepcopy(reuse)
            other["next"]["support"][0][key] = "A sentence never recorded."
            with self.assertRaises(ContractViolation):
                canonical_decision(other, payload)
        other = deepcopy(reuse)
        other["next"]["visitor_request"] = "I never asked that."
        with self.assertRaises(ContractViolation):
            canonical_decision(other, payload)
        # Searching is available with a partial cache even on a related question.
        self.assertTrue(Draft202012Validator(model_call(payload)[0]).is_valid(raw))
        payload["complete_scope"] = True
        complete = Draft202012Validator(model_call(payload)[0])
        self.assertTrue(complete.is_valid(plan(prompt, "review_complete_scope")))
        self.assertFalse(complete.is_valid(raw))
        self.assertNotIn("VincentPlan", self.codec.validator.schema("Plan")["$defs"])

    def test_model_selects_followup_intent_and_query_from_history(self):
        question = "Did you write to anyone about that?"
        run = self.run_request(question)
        earlier = "We were discussing a detail in the background."
        run["context"]["recent_turns"] = [{
            "sequence": 1, "turn_id": "11111111-1111-4111-8111-111111111111",
            "question": "What caught your eye?", "answer_text": earlier, "answer_mode": "llm",
            "answer_language": "en", "answer_status": "answered"}]
        payload = build_plan_input(run, self.manifest)
        schema, messages = model_call(payload)
        model_input = json.loads(messages[1]["content"])
        self.assertEqual(model_input["recent_turns"][0]["answer_text"], earlier)
        self.assertEqual(list(model_input)[-1], "original_question")
        intent = "Find letters discussing the background of Irises."
        query = "Did Vincent discuss the background of Irises in a letter?"
        selected = plan(intent, "search", perspective="artist_letter", query=query)
        Draft202012Validator(schema).validate(selected)
        public = canonical_decision(selected, payload)
        self.assertEqual(public["requests"][0]["intent"], intent)
        self.assertEqual(public["queries"][0]["query"], query + " Consult the artist's letters.")
        self.assertEqual(public["requests"][0]["original_fragment"], question)

    def test_compound_cache_witnesses_bind_each_fact_and_direct_writer_sees_only_selected_values(self):
        first, second = "Repeat what you just said about its maker.", "Repeat the title too."
        run = self.run_request(first + " " + second)
        payload = build_plan_input(run, self.manifest)
        payload["cached_evidence"] = [dict(r) for r in list(self.manifest.records.values())[:2]]
        parts = [{"fragment": fragment, "intent": fragment, "kind": "artwork_fact"} for fragment in (first, second)]
        payload["recent_turns"] = [settled(" ".join(item["excerpt"] for item in payload["cached_evidence"]))]
        support = [{"part": i, "witness": {"passage": item["chunk_id"], "source_quote": item["excerpt"],
                                          "answer_quote": item["excerpt"], "establishes": parts[i-1]["intent"]}}
                   for i, item in enumerate(payload["cached_evidence"], 1)]
        model_plan = plan("Repeat both answers.", "repeat_verified_multiple", visitor_request=run["question"],
                          requests=parts, support=support)
        schema, _ = model_call(payload)
        Draft202012Validator(schema).validate(model_plan)
        self.assertEqual(canonical_decision(model_plan, payload)["action"], "answer_from_context")
        model_plan["next"]["support"] = support[:1]
        with self.assertRaises(ContractViolation):
            canonical_decision(model_plan, payload)
        separate = plan("Give the artist and the title.", "reply_multiple",
                        requests=[{**parts[0], "kind": "name_artist"}, {**parts[1], "kind": "give_title"}])
        Draft202012Validator(schema).validate(separate)
        response_schema, messages = direct_writer_call(separate, payload)
        data = json.loads(messages[1]["content"])
        self.assertEqual(set(data["requests"]["r1"]["catalog"]), {"artist"})
        self.assertEqual(set(data["requests"]["r2"]["catalog"]), {"title"})
        self.assertNotIn("cached_evidence", data)
        names = [data["requests"]["r1"]["catalog"]["artist"], data["requests"]["r2"]["catalog"]["title"]]
        words = {"parts": {"r1": {"source_value": names[0], "reply": "The artist is " + names[0] + "."},
                           "r2": {"source_value": names[1], "reply": "The title is " + names[1] + "."}}, **writing()}
        Draft202012Validator(response_schema).validate(words)
        assembled = canonical_decision(with_direct_writing(separate, words), payload)
        validate_plan(run, assembled, self.codec.validator, plan_input=payload)
        facts = {f["field"]: f["fact_id"] for f in payload["catalog_facts"]}
        self.assertEqual([r["citation_ids"] for r in assembled["results"]], [[facts["artist"]], [facts["title"]]])

    def test_source_perspective_is_model_selected_and_independent_of_question_text(self):
        run = self.run_request("What did the source actually say?")
        payload = build_plan_input(run, self.manifest)
        schema, _ = model_call(payload)
        question = "How was the surface described?"
        outcomes = {"any_source": question, "guide_note": question + " Use the MuseBridge guide notes.",
                    "artist_letter": question + " Consult the artist's letters.",
                    "museum_record": question + " Consult the museum record."}
        for perspective, expected in outcomes.items():
            with self.subTest(perspective=perspective):
                raw = plan("Find the description.", "search", perspective=perspective, query=question)
                Draft202012Validator(schema).validate(raw)
                self.assertEqual(canonical_decision(raw, payload)["queries"][0]["query"], expected)
        raw = plan("Find the description.", "search", query=question)
        self.assertFalse(Draft202012Validator(schema).is_valid(raw))
        writer_schema, _ = direct_writer_call(plan("Acknowledge the visitor.", "acknowledge"), payload)
        model_reply = {"parts": {"r1": {"clause": "I'm pleased it feels that way", "ending": "."}}, **writing()}
        Draft202012Validator(writer_schema).validate(model_reply)
        self.assertEqual(with_direct_writing(plan("Acknowledge the visitor.", "acknowledge"),
                                             model_reply)["next"]["reply"], "I'm pleased it feels that way.")
        model_reply["parts"]["r1"].pop("ending")
        self.assertFalse(Draft202012Validator(writer_schema).is_valid(model_reply))
        scoped = render_query({"query": question, "perspective": "artist_letter"})
        self.assertEqual(render_query({"query": scoped, "perspective": "artist_letter"}), scoped)
        repeated = scoped + " Consult the artist's letters."
        self.assertEqual(render_query({"query": repeated, "perspective": "artist_letter"}), scoped)
        self.assertEqual(render_query({"query": scoped, "perspective": "museum_record"}), outcomes["museum_record"])

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
        decision = plan("Acknowledge the language request.", "acknowledge")
        decision["language"]["explicit_instruction"] = {
            "original_fragment": "Please speak Korean", "preference": "ko", "purpose": "turn_response", "persistence": "turn"}
        model = ScriptedModel(decision, {"parts": {"r1": {"clause": "I can speak with you in English", "ending": "."}}, **writing()})
        workflow = create_vincent_workflow(model=model, profile=FixtureProfile(), prompt_sizer=fixture_prompt_sizer,
                                           manifest=self.manifest,
                                           retrieval=vincent_retrieval(self.manifest, base_url="http://knowledge.test"))
        result = await workflow.run(run, request_id="role-lang", budget=TestBudget())
        self.assertEqual((result.answer_language, result.retrieval_calls, result.model_calls), ("en", 0, 2))
        writer_input = json.loads(model.calls[1].messages[1]["content"])
        self.assertNotIn("cached_evidence", writer_input)
        self.assertNotIn("catalog", writer_input["requests"]["r1"])
        self.assertEqual(result.language_decision, {**decision["language"], "answer_language": "en"})
