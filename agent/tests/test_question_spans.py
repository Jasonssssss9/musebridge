"""A shared sentence index can identify two facts only with unambiguous literal boundaries."""
import unittest

from jsonschema import ValidationError

from agent.runtime.followup_plan import expand_plan, planning_schema
from agent.runtime.validation import ContractViolation, StrictValidator
from agent.runtime.validation_v2 import ContractValidatorV2
from agent.testing.v2 import language_decision


def decision(language, queries, sources=None):
    return {"visitor_task": "check_artwork_facts", "language_decision": language_decision(language),
            "evidence_action": "search",
            "facts": [{"source": source, "english_search_question": query}
                      for source, query in zip(sources or [{"sentence": 1}, {"sentence": 1}], queries)]}


class CoordinatedQuestionTests(unittest.TestCase):
    queries = ["What material is the painting made of?", "How many hours did the artist spend on it?"]

    def test_literally_separates_two_independent_questions_in_visitor_order(self):
        examples = {
            "en": ("What is the painting made of", ", and ", "how many hours did the artist spend painting it?"),
            "es": ("¿De qué está hecha la obra", ", y ", "cuántas horas tardó la artista en pintarla?"),
            "zh-CN": ("这件作品是用什么材料做的", "，另外", "多少小时才画完？"),
            "ko": ("이 작품은 무엇으로 만들었고", ", 그리고 ", "몇 시간 동안 그렸나요?"),
        }
        for language, (left, join, right) in examples.items():
            with self.subTest(language=language):
                question = "  " + left + join + right + "  "
                out = expand_plan(decision(language, self.queries), {"original_question": question})
                self.assertEqual([r["original_fragment"] for r in out["requests"]], [left, right])
                self.assertEqual([q["query"] for q in out["queries"]], self.queries)
                self.assertEqual([q["request_ids"] for q in out["queries"]], [["r1"], ["r2"]])

    def test_second_question_can_ask_for_yes_or_no(self):
        question = "What colors does the label describe, and does it name the exact blue pigment?"
        queries = ["What colors does the label describe?", "Does it identify the exact blue pigment?"]
        plan = expand_plan(decision("en", queries), {"original_question": question})
        self.assertEqual([r["original_fragment"] for r in plan["requests"]],
                         ["What colors does the label describe", "does it name the exact blue pigment?"])

    def test_preserves_rejection_of_unresolved_pointer_collisions(self):
        question = "What is it made of, and how many days did it take?"
        cases = [(question, list(reversed(self.queries))), (question, [self.queries[0]] * 2),
                 ("What color is it, and what is this area?", self.queries),
                 ("What is the color or material, and how many days did it take?", self.queries),
                 ("What are the color and texture, and how many days did it take?", self.queries),
                 ("What does the guide say about how many days it took?", self.queries),
                 ("What is its medium? What is it made of, and how many days did it take?", self.queries)]
        for original, queries in cases:
            with self.subTest(original=original, queries=queries), self.assertRaises(ContractViolation):
                expand_plan(decision("en", queries), {"original_question": original})
        for sources in ([{"excerpt": "how many days"}, {"excerpt": "made of"}],
                        [{"excerpt": "made of"}, {"excerpt": "how many years"}],
                        [{"sentence": 1}, {"excerpt": "how many days"}]):
            with self.subTest(sources=sources), self.assertRaises(ContractViolation):
                expand_plan(decision("en", self.queries, sources), {"original_question": question})


class CurrentSentenceSelectionTests(unittest.TestCase):
    def setUp(self):
        self.question = "I saw blue elsewhere. Does blue appear here, and what is the texture?"
        self.payload = {"original_question": self.question,
                        "recent_turns": [{"answer_text": "Colors glow on the blue chair in the background."}]}
        self.queries = ["Does the label describe blue?", "How does the label describe the texture?"]
        self.wire = StrictValidator(planning_schema(ContractValidatorV2().schema("Plan"), self.payload))

    def test_live_multisentence_schema_requires_an_id_and_binds_exactly_within_it(self):
        decision_data = decision("en", self.queries, [{"sentence": 2, "excerpt": "blue"},
                                                       {"sentence": 2, "excerpt": "what is the texture?"}])
        self.wire.validate(decision_data)
        out = expand_plan(decision_data, self.payload)
        self.assertEqual([r["original_fragment"] for r in out["requests"]], ["blue", "what is the texture?"])
        ordinary = decision("en", self.queries, [{"sentence": 1}, {"sentence": 2}])
        self.wire.validate(ordinary)
        self.assertEqual(expand_plan(ordinary, self.payload)["requests"][1]["original_fragment"],
                         "Does blue appear here, and what is the texture?")
        old = decision("en", self.queries, [{"excerpt": "appear here"}, {"excerpt": "texture"}])
        with self.assertRaises(ValidationError):
            self.wire.validate(old)
        self.assertEqual(expand_plan(old, self.payload)["requests"][0]["original_fragment"], "appear here")

    def test_verbatim_full_sentence_matches_the_numbered_selection(self):
        for lang, text in (("en", "What material is this painting made of?"), ("zh-CN", "这幅画用的是什么材料？")):
            for prefix, number in (("", 1), ("Look here. ", 2)):
                payload = {**self.payload, "original_question": prefix + text}
                selected = {"sentence": number, "excerpt": text}
                with self.subTest(lang=lang, number=number):
                    actual = expand_plan(decision(lang, self.queries[:1], [selected]), payload)
                    ordinary = expand_plan(decision(lang, self.queries[:1], [{"sentence": number}]), payload)
                    self.assertEqual(actual, ordinary)
                    with self.assertRaises(ContractViolation):
                        expand_plan(decision(lang, self.queries, [selected, selected]), payload)

    def test_rejects_excerpts_from_another_turn_or_sentence_and_ambiguous_matches(self):
        for snippet in ("the blue chair", "elsewhere", "elsewhere. Does", "Does blue appear here, and what is the texture!"):
            wrong = decision("en", self.queries[:1], [{"sentence": 2, "excerpt": snippet}])
            with self.subTest(snippet=snippet), self.assertRaises(ContractViolation):
                expand_plan(wrong, self.payload)
        repeated = {**self.payload, "original_question": "I see. Does blue ever appear beside blue?"}
        with self.assertRaises(ContractViolation):
            expand_plan(decision("en", self.queries[:1], [{"sentence": 2, "excerpt": "blue"}]), repeated)
        with self.assertRaises(ValidationError):
            self.wire.validate(decision("en", self.queries[:1], [{"sentence": 3}]))
