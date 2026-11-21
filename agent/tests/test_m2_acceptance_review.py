"""Read-only label preflight uses annotations and never grants human sign-off."""
import json
from pathlib import Path
import tempfile
import unittest

from agent.testing.review_acceptance import audit


class AcceptanceReviewTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.suite = self.root/"acceptance/visitor-heldout/v0.1"
        self.suite.mkdir(parents=True)
        self.single = {"status": "draft", "scenarios": [{"id": "H-test", "artwork_id": "aic-28560",
            "expected": {"citations": {"required": ["aic-28560-medium"]}}, "variants": [{"language": "en"}]}]}
        self.multi = {"status": "draft", "scripts": [{"id": "S-test", "steps": []}]}
        values = [
            ("es", "en", None, "en", "en", "es"),
            ("und", None, {"type": "turn", "language": "ko"}, "ko", "en", "es"),
            ("und", None, {"type": "translate", "language": "zh-CN"}, "zh-CN", "en", "es"),
            ("und", None, {"type": "setting", "language": "auto"}, "es", "auto", "es"),
        ]
        for n, (detected, override, instruction, answer, preference, automatic) in enumerate(values, 1):
            self.multi["scripts"][0]["steps"].append({"kind": "message", "visitor_turn": n, "artwork_id": "aic-28560",
                "language": detected, "language_override": override, "instruction": instruction,
                "expect": {"answer_language": answer, "preference_after": preference, "auto_language_after": automatic}})

    def write(self):
        (self.suite/"single-turn.json").write_text(json.dumps(self.single))
        (self.suite/"multi-turn.json").write_text(json.dumps(self.multi))

    def test_turn_translation_and_persistent_settings_preserve_contract_state(self):
        self.write()
        result = audit(self.root)
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["annotated_transitions_checked"], 4)

    def test_foreign_source_and_bad_language_transition_are_reported_without_text(self):
        self.single["scenarios"][0]["expected"]["citations"]["required"] = ["aic-11723-medium"]
        self.multi["scripts"][0]["steps"][1]["expect"]["preference_after"] = "ko"
        self.write()
        result = audit(self.root)
        self.assertEqual({issue["check"] for issue in result["issues"]}, {"citation_scope", "preference_after"})
        self.assertFalse(result["question_text_exposed"])

    def test_frozen_status_does_not_fabricate_semantic_or_human_approval(self):
        self.single["status"] = self.multi["status"] = "frozen"
        self.write()
        result = audit(self.root)
        self.assertFalse(result["independent_evaluation_ready"])
        self.assertEqual(result["manual_source_meaning_review"], "pending")
        self.assertEqual(set(result["language_reviewer_signoffs"].values()), {"pending"})


if __name__ == "__main__":
    unittest.main()
