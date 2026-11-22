"""A general definition remains independent of the active artwork and its sources."""
import unittest
from copy import deepcopy

from agent.runtime.context_v2 import build_plan_input
from agent.runtime.general_reply import general_plan, general_schema
from agent.runtime.validation import ContractViolation, StrictValidator
from agent.runtime.validation_v2 import ContractValidatorV2, assemble_answer, validate_plan
from agent.testing.v2 import language_decision, make_run
from agent.tools.evidence import EvidenceManifest


class GeneralReplyTests(unittest.TestCase):
    def setUp(self):
        manifest = EvidenceManifest.load()
        self.run = make_run(question="¿Qué significa claroscuro?", manifest=manifest)
        self.payload = build_plan_input(self.run, manifest)
        self.validator = ContractValidatorV2()
        defs = self.validator.schema("Plan")["$defs"]
        self.wire = {**general_schema(defs), "$defs": defs}
        self.reply = {"visitor_task": "define_general_art_term", "language_decision": language_decision("es"),
                      "original_term": "claroscuro", "english_concept": "chiaroscuro",
                      "definition": "El claroscuro utiliza el contraste entre luz y sombra para crear volumen."}

    def test_compact_definition_is_offered_only_for_vocabulary_requests(self):
        from agent.runtime.followup_plan import planning_schema
        from agent.runtime.validation_v2 import ContractValidatorV2
        for question, expected in (("What does impasto mean?", True), ("¿Qué significa veladura?", True),
                                   ("‘罩染’是什么意思？", True), ("임파스토는 무슨 뜻인가요?", True),
                                   ("Who painted it?", False), ("请用中文回答这一次：刚才提到的是哪位画家？", False),
                                   ("Which painter did you mention?", False)):
            with self.subTest(question=question):
                wire = planning_schema(ContractValidatorV2().schema("Plan"), {"original_question": question})
                self.assertEqual("GeneralConceptPlan" in wire["$defs"], expected)
                self.assertIn("OtherVisitorPlan", wire["$defs"])

    def test_valid_definition_binds_scope_language_support_and_visitor_attribution(self):
        for code, definition in (("es", self.reply["definition"]), ("zh-CN", "明暗法利用光影对比来表现体积。"),
                                 ("ko", "명암법은 빛과 어둠의 대비로 입체감을 나타내는 기법입니다."),
                                 ("en", "Chiaroscuro uses contrasts of light and shadow to suggest volume.")):
            with self.subTest(language=code):
                run = {**self.run, "language_override": code}
                payload = {**self.payload, "language_override": code}
                language = {**self.reply["language_decision"], "answer_language": code}
                reply = {**self.reply, "definition": definition, "language_decision": language}
                StrictValidator(self.wire).validate(reply)
                plan = general_plan(reply, payload)
                validate_plan(run, plan, self.validator, plan_input=payload)
                answer, parts, memory = assemble_answer(run, plan, plan, [], payload["catalog_facts"], self.validator)
                self.assertEqual(parts[0]["support"], "not_applicable")
                self.assertEqual((answer["text"], answer["citations"]), (definition, []))
                self.assertIn(run["question"], memory["continuity_summary"])
                self.assertNotIn(definition, memory["continuity_summary"])

    def test_explicit_work_reference_and_translation_cannot_become_an_uncited_definition(self):
        for name in self.payload["artwork_context"].values():
            with self.subTest(name=name), self.assertRaises(ContractViolation):
                general_plan({**self.reply, "definition": "Example: " + name + "."}, self.payload)
        reply = deepcopy(self.reply)
        reply["language_decision"]["translation"] = {"target_language": "es"}
        with self.assertRaises(ContractViolation):
            general_plan(reply, self.payload)

    def test_term_must_come_from_this_message_even_when_it_is_available_in_history(self):
        payload = {**self.payload, "original_question": "Which artist did we mention?",
                   "recent_turns": [{"answer_text": "We discussed chiaroscuro."}]}
        reply = {**self.reply, "original_term": "chiaroscuro", "language_decision": language_decision("en")}
        with self.assertRaises(ContractViolation):
            general_plan(reply, payload)
        payload["original_question"] = "What does chiaroscuro mean?"
        plan = general_plan({**reply, "english_concept": "a prior topic"}, payload)
        self.assertIn("chiaroscuro", plan["requests"][0]["intent"])
        self.assertNotIn("prior topic", str(plan["memory_update"]))

    def test_requested_term_can_coincide_with_a_title(self):
        self.payload["artwork_context"]["artwork_title"] = "Chiaroscuro"
        self.reply["definition"] = "Chiaroscuro is the contrast of light and shadow."
        self.assertEqual(general_plan(self.reply, self.payload)["results"][0]["text"], self.reply["definition"])
