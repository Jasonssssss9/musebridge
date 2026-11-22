"""Model-facing fact selection must still yield a valid, literal canonical plan."""
from copy import deepcopy
import unittest

from jsonschema import ValidationError

from agent.runtime.followup_plan import expand_plan, planning_schema, source_sentences
from agent.runtime.validation import ContractViolation, StrictValidator
from agent.runtime.validation_v2 import ContractValidatorV2, validate_plan
from agent.runtime.errors import WorkflowError
from agent.testing.v2 import language_decision, make_run, medium_plan


def facts(*sources, mode="search"):
    questions = ["What does the label say about the brushwork?",
                 "Which tool did the artist use to paint The Bedroom?"]
    return {"visitor_task": "check_artwork_facts", "language_decision": language_decision("ko"),
            "evidence_action": mode, "facts": [
                {"source": source, "english_search_question": questions[i]} for i, source in enumerate(sources)]}


class FollowupPlanTests(unittest.TestCase):
    def setUp(self):
        self.validator = ContractValidatorV2()
        self.schema = self.validator.schema("Plan")
        self.question = "그 붓질은 무엇을 떠올리게 하나요? 화가가 실제로 깃털을 도구로 쓴 건지도 궁금해요."
        self.payload = {"original_question": self.question, "recent_turns": [{"answer_text": "Earlier."}],
                        "cached_evidence": [], "complete_scope": False}

    def test_followup_schema_requires_a_tag_and_preserves_standard_plans(self):
        snapshot = deepcopy(self.schema)
        wire = planning_schema(self.schema, self.payload)
        self.assertEqual(self.schema, snapshot)
        self.assertEqual(wire["$ref"], "#/$defs/Plan")
        self.assertEqual(wire["$defs"]["StandardPlan"], snapshot["$defs"]["Plan"])
        for ref in wire["$defs"]["Plan"]["oneOf"]:
            branch = wire["$defs"][ref["$ref"].split("/")[-1]]
            self.assertEqual(next(iter(branch["properties"])), "visitor_task")
        for name in snapshot["$defs"].keys() - {"Plan", "DirectResponsePlan"}:
            self.assertEqual(wire["$defs"][name], snapshot["$defs"][name])
        public = medium_plan(make_run())
        wrapped = {"visitor_task": "chat_translate_read_catalog_or_handle_mixed_requests", "plan": public}
        StrictValidator(wire).validate(wrapped)
        self.assertIs(expand_plan(wrapped, self.payload), public)
        with self.assertRaises(ValidationError):
            StrictValidator(wire).validate(public)
        with self.assertRaises(ContractViolation):
            expand_plan(public, self.payload)
        decision = facts({"sentence": 1}, {"sentence": 2})
        StrictValidator(wire).validate(decision)
        with self.assertRaises(ValidationError):
            StrictValidator(self.schema).validate(decision)
        for changed in ({"recent_turns": []}, {"original_question": " "}):
            payload = {**self.payload, **changed}
            wire = planning_schema(self.schema, payload)
            if payload["original_question"].strip():
                StrictValidator(wire).validate(wrapped)
                StrictValidator(wire).validate(decision)
                self.assertEqual(expand_plan(decision, payload)["action"], "retrieve")
            else:
                StrictValidator(wire).validate(public)
                with self.assertRaises(ValidationError):
                    StrictValidator(wire).validate(decision)
                with self.assertRaises(ContractViolation):
                    expand_plan(decision, payload)
        other = self.validator.schema("AssessmentDecision")
        self.assertIs(planning_schema(other, self.payload), other)
        self.assertEqual(planning_schema({}, self.payload), {})

    def test_sentence_ids_resolve_to_literal_slices_in_request_order(self):
        decision = facts({"sentence": 1}, {"sentence": 2})
        canonical = expand_plan(decision, self.payload)
        run = make_run(question=self.question)
        validate_plan(run, canonical, self.validator, plan_input=self.payload)
        self.assertEqual(canonical["action"], "retrieve")
        self.assertEqual([r["original_fragment"] for r in canonical["requests"]],
                         ["그 붓질은 무엇을 떠올리게 하나요?", "화가가 실제로 깃털을 도구로 쓴 건지도 궁금해요."])
        self.assertEqual([r["kind"] for r in canonical["requests"]], ["artwork_fact"] * 2)
        for i, (r, q) in enumerate(zip(canonical["requests"], canonical["queries"]), 1):
            self.assertEqual((r["id"], q["id"], q["request_ids"]), (f"r{i}", f"q{i}", [f"r{i}"]))
            self.assertEqual(r["intent"], q["query"])
            self.assertEqual(r["resolution"], "clear")
        self.assertEqual(source_sentences('¿Mide 2.5 cm?  “看这儿！”为什么？'), ['¿Mide 2.5 cm?', '“看这儿！”', '为什么？'])
        self.assertEqual(source_sentences("What  material。是哪种  工具"), ["What  material。", "是哪种  工具"])

    def test_bound_sources_must_be_distinct_exact_and_unambiguous(self):
        payload = {**self.payload, "original_question": "Was it blue or was it green?"}
        valid = facts({"excerpt": "blue"}, {"excerpt": "green"})
        self.assertEqual([r["original_fragment"] for r in expand_plan(valid, payload)["requests"]],
                         ["blue", "green"])
        for sources in (({"sentence": 1}, {"sentence": 1}), ({"excerpt": "blue"}, {"sentence": 1}),
                        ({"sentence": 1}, {"excerpt": "green"}), ({"excerpt": "it"},),
                        ({"excerpt": "green"}, {"excerpt": "blue"}), ({"sentence": 2},),
                        ({"excerpt": "blue"}, {"excerpt": "blue"}), ({"excerpt": "blue-green"},)):
            with self.subTest(sources=sources), self.assertRaises(ContractViolation):
                expand_plan(facts(*sources), payload)
        with self.assertRaises(ContractViolation):
            expand_plan(facts({"excerpt": "화가 가 실제로"}), self.payload)

    def test_reuse_cache_does_not_bypass_evidence_checks(self):
        plan = expand_plan(facts({"sentence": 1}, mode="reuse_cache"), self.payload)
        self.assertEqual(plan["action"], "answer_from_context")
        self.assertNotIn("queries", plan)
        with self.assertRaises(WorkflowError):
            validate_plan(make_run(question=self.question), plan, self.validator, plan_input=self.payload)

    def test_listed_medium_uses_scoped_record_and_ui_language(self):
        payload = {**self.payload, "artwork_context": {"artwork_title": "Example"},
                   "conversation_context": {"language_preference": "auto", "auto_language": "en"},
                   "language_override": "es"}
        wire = {"visitor_task": "look_up_listed_medium", "detected_language": "ko"}
        for history in ([], self.payload["recent_turns"]):
            current = {**payload, "recent_turns": history}
            StrictValidator(planning_schema(self.schema, current)).validate(wire)
            self.assertEqual(expand_plan(wire, current)["action"], "retrieve")
        plan = expand_plan(wire, payload)
        self.assertEqual(plan["action"], "retrieve")
        self.assertEqual(plan["queries"][0]["query"], "What material is Example made of?")
        self.assertEqual(plan["requests"][0]["kind"], "artwork_fact")
        self.assertEqual(plan["requests"][0]["original_fragment"], self.question)
        self.assertEqual(plan["language_decision"],
                         {"detected_language": "ko", "answer_language": "es",
                          "explicit_instruction": None, "translation": None})
        for locator, expected in (("data.medium_display", "answer_from_context"),
                                  ("data.description", "retrieve")):
            reused = expand_plan(wire, {**payload, "cached_evidence": [{"source_locator": locator}],
                                        "language_override": None})
            self.assertEqual(reused["action"], expected)
            self.assertEqual(reused["language_decision"]["answer_language"], "ko")
        with self.assertRaises(ContractViolation):
            expand_plan({**wire, "source": {"excerpt": "invented"}}, payload)

    def test_unavailable_translation_and_pointing_bind_whole_message_clarifications(self):
        for history in ([], self.payload["recent_turns"]):
            payload = {**self.payload, "conversation_context": {
                "language_preference": "es", "auto_language": "en",
                "active_topic": "Earlier topic", "continuity_summary": "Earlier note."},
                "language_override": None, "recent_turns": history}
            for task, expected in (({"visitor_task": "ask_for_translation_source", "target_language": "ko"}, "ko"),
                                   ({"visitor_task": "ask_which_detail", "detected_language": "en"}, "es")):
                StrictValidator(planning_schema(self.schema, payload)).validate(task)
                plan = expand_plan(task, payload)
                StrictValidator(self.schema).validate(plan)
                self.assertEqual(plan["action"], "clarify")
                self.assertEqual(plan["language_decision"]["answer_language"], expected)
                self.assertEqual(plan["memory_update"]["continuity_summary"], "Earlier note.")
                self.assertEqual(plan["memory_update"]["pending_update"],
                                 {"action": "set", "source": "current_message", "request_id": None})
                if expected == "ko":
                    self.assertEqual(plan["language_decision"]["translation"],
                                     {"target_language": "ko", "source": None, "original_fragment": self.question})

    def test_invalid_provider_data_is_rejected_before_conversion(self):
        valid = facts({"sentence": 1})
        malformed = [{"visitor_task": "chat_translate_read_catalog_or_handle_mixed_requests", "plan": [], "extra": True},
                     {"visitor_task": "chat_translate_read_catalog_or_handle_mixed_requests", "plan": {}, "ignored": True},
                     {"visitor_task": "chat_translate_read_catalog_or_handle_mixed_requests"}, {"visitor_task": "unknown"},
                     {"visitor_task": "chat_translate_read_catalog_or_handle_mixed_requests", "plan": "guess"}, [],
                     {**valid, "extra": "ignored"}, {**valid, "evidence_action": "guess"},
                     {**valid, "facts": []}, {**valid, "facts": valid["facts"] * 3},
                     {**valid, "language_decision": {"answer_language": "ko"}}]
        for bad_fact in ({"source": {"sentence": True}}, {"source": {"sentence": "1"}},
                         {"source": {"sentence": 1, "excerpt": "actual"}}, {"extra": True},
                         {"english_search_question": " "}, {"english_search_question": "x" * 1001}):
            malformed.append({**valid, "facts": [{**valid["facts"][0], **bad_fact}]})
        for i, decision in enumerate(malformed):
            with self.subTest(i=i), self.assertRaises(ContractViolation):
                expand_plan(decision, self.payload)
