"""Current-utterance context can qualify a lookup without becoming another request."""
import unittest
from copy import deepcopy

from agent.runtime.followup_plan import expand_plan, planning_schema
from agent.runtime.validation import ContractViolation, StrictValidator
from agent.runtime.validation_v2 import ContractValidatorV2
from agent.testing.v2 import language_decision


class PassageRoleTests(unittest.TestCase):
    def setUp(self):
        self.payload = {"original_question": "I want to sketch it. Who designed it and who donated it?",
                        "recent_turns": [], "cached_evidence": [], "complete_scope": False}
        self.schema = planning_schema(ContractValidatorV2().schema("Plan"), self.payload)
        self.choice = {"visitor_task": "check_artwork_facts", "language_decision": language_decision("en"),
                       "evidence_action": "search", "passages": [
                           {"role": "context_for_the_request"},
                           {"role": "two_independent_evidence_requests", "clauses": [
                               {"original_excerpt": "Who designed it", "english_search_question": "Who designed the sculpture?"},
                               {"original_excerpt": "who donated it", "english_search_question": "Who donated the sculpture?"},
                           ]}]}

    def test_background_cannot_emit_a_query_and_two_current_clauses_bind_in_order(self):
        StrictValidator(self.schema).validate(self.choice)
        plan = expand_plan(self.choice, self.payload)
        self.assertEqual([r["original_fragment"] for r in plan["requests"]], ["Who designed it", "who donated it"])
        self.assertEqual([q["request_ids"] for q in plan["queries"]], [["r1"], ["r2"]])
        self.assertNotIn("sketch", str(plan))
        # The same semantic reading works for an operative first sentence and a trailing preference.
        payload = {**self.payload, "original_question": "¿De qué está hecha? Prefiero una frase corta."}
        choice = {**self.choice, "passages": [
            {"role": "one_evidence_request", "english_search_question": "What material is it made of?"},
            {"role": "context_for_the_request"}]}
        self.assertEqual(expand_plan(choice, payload)["requests"][0]["original_fragment"], "¿De qué está hecha?")

    def test_context_only_wrong_count_excess_requests_and_wrong_source_fail_closed(self):
        context = {"role": "context_for_the_request"}
        fact = {"role": "one_evidence_request", "english_search_question": "Who made the sculpture?"}
        bad_clause = deepcopy(self.choice["passages"][1])
        bad_clause["clauses"][1]["original_excerpt"] = "earlier history"
        for readings in ([context, context], [context, fact, context], [fact, self.choice["passages"][1]],
                         [context, bad_clause]):
            with self.subTest(readings=readings), self.assertRaises(ContractViolation):
                expand_plan({**self.choice, "passages": readings}, self.payload)

    def test_overlap_cannot_turn_the_same_words_into_two_independent_clauses(self):
        decision = deepcopy(self.choice)
        clauses = decision["passages"][1]["clauses"]
        clauses[0]["original_excerpt"] = "Who designed it and who donated"
        with self.assertRaises(ContractViolation):
            expand_plan(decision, self.payload)

    def test_old_model_captures_still_decode_and_public_plan_has_no_role_annotations(self):
        old = {k: v for k, v in self.choice.items() if k != "passages"}
        old["facts"] = [{"source": {"sentence": 2}, "english_search_question": "Who made the sculpture?"}]
        public = expand_plan(old, self.payload)
        self.assertEqual(public["requests"][0]["original_fragment"], "Who designed it and who donated it?")
        self.assertNotIn("passages", public)
