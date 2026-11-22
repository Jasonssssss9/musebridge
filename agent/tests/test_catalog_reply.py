"""Catalog shortcuts must preserve released values, citation scope and source-linked memory."""
from copy import deepcopy
import unittest

from agent.runtime.catalog_reply import catalog_field_for_query, catalog_plan
from agent.runtime.context_v2 import build_plan_input
from agent.runtime.followup_plan import expand_plan, planning_schema
from agent.runtime.grounding import grounded_memory
from agent.runtime.validation import ContractViolation, StrictValidator
from agent.runtime.validation_v2 import ContractValidatorV2, assemble_answer, validate_plan
from agent.testing.v2 import language_decision, make_run
from agent.tools.evidence import EvidenceManifest


class CatalogReplyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = EvidenceManifest.load()
        cls.validator = ContractValidatorV2()

    def test_only_complete_queries_for_the_exact_catalog_subject_are_bound(self):
        self.assertEqual(catalog_field_for_query("Who painted The Reader?", "The Reader"), "artist")
        self.assertEqual(catalog_field_for_query("When was The Reader made?", "The Reader"), "date_display")
        for query in ("Who painted the frame of The Reader?", "Who painted The Reader before 1900?",
                      "Who painted The Bedroom?", "Who commissioned The Reader?",
                      "Who painted The Reader and who bought it?"):
            self.assertIsNone(catalog_field_for_query(query, "The Reader"))

    def test_each_released_field_in_each_language_through_answer_and_memory(self):
        questions = {
            "en": ("What is the title?", "Who is the artist?", "What date is listed?"),
            "es": ("¿Cómo se titula?", "¿Quién es el artista?", "¿Qué fecha figura en la ficha?"),
            "zh-CN": ("这幅作品叫什么名字？", "作者是谁？", "作品标注的年代是什么？"),
            "ko": ("작품 제목이 무엇인가요?", "작가는 누구인가요?", "제작 연도는 언제인가요?"),
        }
        for artwork_id in self.manifest.artworks:
            for language, prompts in questions.items():
                for field, question in zip(("title", "artist", "date_display"), prompts):
                    with self.subTest(artwork_id=artwork_id, language=language, field=field):
                        run = make_run(artwork_id=artwork_id, question=question, manifest=self.manifest)
                        prior = "Visitor asked about art. " * 40
                        run["conversation_state"]["continuity_summary"] = prior
                        payload = build_plan_input(run, self.manifest)
                        snapshot = deepcopy(payload)
                        plan = catalog_plan([field], question, language_decision(language), payload)
                        validate_plan(run, plan, self.validator, plan_input=payload)
                        answer, parts, proposal = assemble_answer(
                            run, plan, plan, [], payload["catalog_facts"], self.validator)
                        memory = grounded_memory(run, plan, parts, proposal)
                        record = next(r for r in payload["catalog_facts"] if r["field"] == field)
                        self.assertEqual(answer["citations"], [record])
                        self.assertIn(record["value"], answer["text"])
                        markers = {"en": ("catalog",), "es": ("obra", "ficha"),
                                   "zh-CN": ("馆藏目录",), "ko": ("등록된",)}
                        self.assertTrue(any(marker in answer["text"] for marker in markers[language]))
                        self.assertIn(record["fact_id"], memory["continuity_summary"])
                        self.assertIn("Source wording:", memory["continuity_summary"])
                        self.assertIn(record["value"], memory["continuity_summary"])
                        self.assertLessEqual(len(memory["continuity_summary"]), 1000)
                        self.assertEqual(plan["requests"][0]["original_fragment"], question)
                        self.assertEqual(memory["pending_update"], {"action": "keep"})
                        self.assertEqual(payload, snapshot)

    def test_model_wire_preserves_two_and_three_catalog_requests_in_visitor_order(self):
        prompts = {
            "en": ("What are its title and artist?", "Who made it, when, and what is its title?"),
            "es": ("¿Cuál es el título y quién es el artista?", "¿Quién la hizo, de qué fecha es y cómo se titula?"),
            "zh-CN": ("这件作品的标题和作者是什么？", "作者是谁，创作年代和标题是什么？"),
            "ko": ("제목과 작가는 무엇인가요?", "작가와 제작 연도, 제목은 무엇인가요?"),
        }
        for artwork_id in self.manifest.artworks:
            for language, pair in prompts.items():
                for fields, question in zip((["title", "artist"], ["artist", "date_display", "title"]), pair):
                    with self.subTest(artwork_id=artwork_id, language=language, fields=fields):
                        run = make_run(artwork_id=artwork_id, question=question, manifest=self.manifest)
                        payload = build_plan_input(run, self.manifest)
                        choice = {"visitor_task": "read_catalog_field", "fields": fields,
                                  "language_decision": language_decision(language)}
                        schema = planning_schema(self.validator.schema("Plan"), payload)
                        StrictValidator(schema).validate(choice)
                        plan = expand_plan(choice, payload)
                        validate_plan(run, plan, self.validator, plan_input=payload)
                        answer, parts, proposal = assemble_answer(
                            run, plan, plan, [], payload["catalog_facts"], self.validator)
                        memory = grounded_memory(run, plan, parts, proposal)
                        self.assertEqual([r["field"] for r in answer["citations"]], fields)
                        self.assertEqual(len(plan["requests"]), len(fields))
                        self.assertEqual(len(parts), len(fields))
                        self.assertEqual(len({r["intent"] for r in plan["requests"]}), len(fields))
                        for request, part, record in zip(plan["requests"], parts, answer["citations"]):
                            self.assertEqual(request["id"], part["request_id"])
                            self.assertEqual(request["original_fragment"], question)
                            self.assertEqual(record["artwork_id"], artwork_id)
                            self.assertEqual(part["citation_ids"], [record["fact_id"]])
                            self.assertIn(record["value"], part["text"])
                            self.assertIn(record["fact_id"], memory["continuity_summary"])
                        self.assertLessEqual(len(memory["continuity_summary"]), 1000)

    def test_translation_and_missing_or_ambiguous_sources_cannot_use_a_catalog_shortcut(self):
        run = make_run(manifest=self.manifest)
        payload = build_plan_input(run, self.manifest)
        question = run["question"]
        decision = language_decision()
        with self.assertRaises(ContractViolation):
            catalog_plan(["artist"], question, {**decision, "translation": {"target_language": "es"}}, payload)
        with self.assertRaises(ContractViolation):
            catalog_plan(["medium"], question, decision, payload)
        with self.assertRaises(ContractViolation):
            catalog_plan(["title"], "A different question", decision, payload)
        for fields in ([], ["title", "title"], ["artist", "medium"]):
            with self.assertRaises(ContractViolation):
                expand_plan({"visitor_task": "read_catalog_field", "fields": fields,
                             "language_decision": decision}, payload)
        for records in ([], payload["catalog_facts"] * 2):
            with self.assertRaises(ContractViolation):
                catalog_plan(["artist"], question, decision, {**payload, "catalog_facts": records})
