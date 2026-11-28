"""Boundaries of the Vincent acceptance measurements and replayed conversation state."""
from copy import deepcopy
from hashlib import sha256
import json
import unittest

from agent.contracts.codec import v3_validator
from agent.testing.v2 import language_decision, memory_update, simulate_commit
from agent.testing.vincent_hybrid_eval import (CASES, GALLERY, VERSION, case_suite, counts, decision_action,
                                               evidence_checks, schema_receipt, visitor_run, workflow_checks)
from agent.tools.evidence import EvidenceManifest
from agent.workflows.museum_qa_v3 import vincent_manifest
from knowledge.vincent import VINCENT_SCOPE


class VincentHybridEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        gallery = EvidenceManifest.load(GALLERY, expected_version=VERSION,
                                        expected_sha256=GALLERY.with_suffix(".sha256").read_text().strip())
        cls.manifest = vincent_manifest(gallery)

    def test_suite_matches_frozen_input_and_canonical_sources(self):
        data = CASES.read_bytes()
        self.assertEqual(sha256(data).hexdigest(), "3e912d96ac580af706ac47607115943437917e881c690c8f660928c58194e111")
        cases = json.loads(data)["conversations"]
        questions = []
        for case in cases:
            for turn in case["turns"]:
                questions.append(turn["question"])
                self.assertTrue(set(turn["required_ids"]) <= self.manifest.records.keys())
                self.assertEqual(bool(turn["required_ids"]), turn["expected"] == "supported")
        self.assertEqual(len(questions), 13)
        self.assertEqual(len(set(questions)), len(questions))

    def test_new_confirmation_keeps_independent_identity(self):
        suite, identity = case_suite(CASES.with_name("vincent_visitor_confirmation_cases.json"))
        self.assertEqual(identity["sha256"], "0d16b099a017a32229d3de92153db7ec2a7a92acb8822a0e814d941e9ea7d478")
        self.assertEqual(sum(len(c["turns"]) for c in suite), 13)
        original, original_id = case_suite(CASES)
        self.assertNotEqual(original_id, identity)
        original_questions = {t["question"] for c in original for t in c["turns"]}
        for case in suite:
            for turn in case["turns"]:
                self.assertNotIn(turn["question"], original_questions)
                self.assertTrue(set(turn["required_ids"]) <= self.manifest.records.keys())

    def test_trace_handles_compact_and_full_decisions(self):
        a = schema_receipt({"$ref": "#/$defs/Plan"})
        b = schema_receipt({"title": "VincentPlan", "properties": {"next": {}}})
        self.assertEqual(a["schema_name"], "Plan")
        self.assertEqual(b["schema_properties"], ["next"])
        self.assertNotEqual(a["schema_sha256"], b["schema_sha256"])
        self.assertEqual(decision_action({"action": "retrieve"}), "retrieve")
        self.assertEqual(decision_action({"next": {"action": "retrieve"}}), "retrieve")
        self.assertIsNone(decision_action({"answer": "Spoken text."}))

    def test_gaps_do_not_require_empty_retrieval_and_errors_are_unscored(self):
        unrelated = evidence_checks("unrelated", ["some-related-art"], [])
        self.assertTrue(unrelated["unrelated_false_return"])
        self.assertFalse(evidence_checks("unrelated", [], [])["unrelated_false_return"])
        self.assertEqual(evidence_checks("gap", ["relevant-but-incomplete"], []), {})
        some = evidence_checks("supported", ["a"], ["a", "b"])
        self.assertEqual(some, {"any_required": True, "all_required": False})
        rows = [{"checks": some}, {"checks": {}}, {"error": "retrieval_unavailable"}]
        self.assertEqual(counts(rows, "all_required"), {"positive": 0, "observed": 1})

    def test_wire_scores_and_semantic_review_remain_distinct(self):
        spec = {"expected": "supported", "required_ids": ["a"]}
        result = {"answer_language": "en", "answer": {"status": "answered", "text": "Requires human review.",
                                                       "citations": [{"chunk_id": "a"}]}}
        self.assertTrue(all(workflow_checks(spec, result).values()))
        self.assertNotIn("semantic_review", workflow_checks(spec, result))
        self.assertFalse(workflow_checks({"expected": "gap"}, result)["status"])
        self.assertFalse(workflow_checks({"expected": "social"}, result)["uncited"])

    def test_actual_answer_memory_and_cited_evidence_continue_only_the_same_chat(self):
        run = visitor_run(self.manifest, "Which color?", None, [])
        v3_validator().validate("RunRequest", run)
        cid = "vincent-772-garden-colors"
        record = dict(self.manifest.records[cid])
        memory = memory_update(topic="Letter", summary="The letter describes violet irises.")
        answer = {"status": "answered", "text": "I wrote of violet irises.", "citations": [record]}
        result = {"scope": run["scope"], "answer_language": "en", "language_decision": language_decision(),
                  "conversation_update": {"base_revision": 0, "memory": memory}, "answer": answer}
        state, pair = simulate_commit(run, result, validate=v3_validator().validate)
        newer = visitor_run(self.manifest, "And later?", state, [pair])
        v3_validator().validate("RunRequest", newer)
        self.assertEqual(newer["conversation_state"]["continuity_summary"], memory["continuity_summary"])
        self.assertEqual(newer["conversation_state"]["verified_evidence"], [record])
        self.assertEqual(newer["context"]["recent_turns"][0]["answer_text"], answer["text"])
        self.assertEqual(newer["scope"], dict(VINCENT_SCOPE))
        self.assertEqual(newer["conversation_id"], run["conversation_id"])
        self.assertEqual(newer["conversation_state"]["revision"], 1)
        fresh = visitor_run(self.manifest, "Fresh question", None, [])
        self.assertNotEqual(fresh["conversation_id"], run["conversation_id"])
        self.assertEqual(fresh["conversation_state"]["verified_evidence"], [])
        self.assertEqual(run["conversation_state"]["revision"], 0)
        # A failed first attempt leaves no committed history, but the next question belongs to the same visit.
        after_failure = visitor_run(self.manifest, "What came next?", None, [],
                                    conversation_id=run["conversation_id"])
        self.assertEqual(after_failure["conversation_id"], run["conversation_id"])
        self.assertEqual(after_failure["conversation_state"]["revision"], 0)
        self.assertEqual(after_failure["context"]["recent_turns"], [])
        self.assertNotEqual(after_failure["client_message_id"], run["client_message_id"])

        wrong = deepcopy(result)
        wrong["scope"]["conversation_mode"] = "guide"
        with self.assertRaises(AssertionError):
            simulate_commit(run, wrong, validate=v3_validator().validate)
