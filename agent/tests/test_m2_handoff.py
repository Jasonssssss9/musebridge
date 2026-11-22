"""Development records cannot fabricate release or human-review approval."""
import unittest
from unittest.mock import patch

from agent.testing.m2_handoff import summarize
from agent.testing.m2_smoke import development_passed, exercise, journeys, warm_latencies


class HandoffTests(unittest.TestCase):
    def test_machine_success_never_grants_formal_or_independent_acceptance(self):
        runtime = {"mode": "controlled-real-ollama-development", "cases": [
            {"name": artwork, "turn": 1, "artwork_id": artwork, "machine_pass": True,
             "response": {"request_id": artwork, "http_status": 200}} for artwork in ("aic-28560", "aic-11723")]}
        result = summarize(runtime, {"cases": [{"pass": True}]}, {"issues": []})
        self.assertEqual(result["gates"]["both_artworks_real_public_path"], "passed")
        self.assertFalse(result["m2_complete"])
        self.assertFalse(result["formal_activation_allowed"])
        self.assertEqual(result["gates"]["human_language_and_semantic_reviews"], "pending")

    def test_blocked_and_recovery_failures_remain_visible_in_the_handoff(self):
        row = {"name": "dev", "turn": 1, "artwork_id": "aic-28560",
               "response": {"request_id": "trace", "http_status": 200}, "machine_pass": True,
               "replay_pass": True, "history_pass": True, "runtime_counts_match": True,
               "replay_no_inference": True}
        report = {"mode": "controlled", "cases": [row], "credential_scan": "passed"}
        self.assertTrue(development_passed(report))
        for field in ("replay_pass", "history_pass", "runtime_counts_match", "replay_no_inference"):
            with self.subTest(field=field):
                self.assertFalse(development_passed({**report, "cases": [{**row, field: False}]}))
        report["cases"].append({"name": "dev", "turn": 2, "blocked": "previous turn"})
        result = summarize(report, {"cases": [{"http_pass": True, "source_visible": False}]}, {})
        self.assertEqual(result["gates"]["automated_browser"], "partial")
        self.assertEqual(result["gates"]["development_expectations"], "failures_recorded")
        self.assertEqual(result["scheduled_development_trials"], 2)
        self.assertEqual(result["blocked_development_trials"], 1)
        self.assertNotEqual(result["gates"]["heldout_label_preflight"], "passed")

    def test_latency_uses_observed_public_request_times_and_does_not_impute_failures(self):
        rows = [{"execution": {"model_calls": 1, "refinement_rounds": 0},
                 "response": {"http_status": 200, "duration_ms": duration}} for duration in range(1, 21)]
        rows.extend([{"response": {"http_status": 502, "duration_ms": 8000}}, {"blocked": "previous turn"}])
        rows.append({"execution": {"model_calls": 2, "refinement_rounds": 0},
                     "response": {"http_status": 200, "duration_ms": 5312}})
        values = warm_latencies(rows)
        self.assertEqual(values["direct"], {"completed_turns": 20, "p95_ms": 19})
        self.assertEqual(values["retrieval_or_reuse"]["p95_ms"], 5312)
        self.assertEqual(values["refined"], {"completed_turns": 0, "p95_ms": None})

    def test_development_scenarios_count_related_language_and_artwork_variants_once(self):
        cases = list(journeys())
        ids = {case["scenario"] for case in cases}
        self.assertGreaterEqual(len(ids), 24)
        self.assertEqual(len({case["name"] for case in cases}), len(cases))
        self.assertEqual(sum(c["scenario"] == "museum-medium" for c in cases), 8)
        for case in cases:
            for turn in case["turns"]:
                self.assertTrue(all(chunk.startswith(case["artwork"]+"-") for chunk in turn["sources"]))

    def test_failed_first_turn_blocks_followup_without_a_new_inference(self):
        conversation = {"contract_version": "agent-demo-v0.3", "conversation_id": "11111111-1111-4111-8111-111111111111",
            "artwork_id": "aic-28560", "dataset_version": "aic-seed-v0.1", "created_at": "2026-11-20T00:00:00Z",
            "expires_at": "2026-11-21T00:00:00Z", "revision": 0, "language_preference": "auto", "auto_language": "en", "last_answer_language": None}
        responses = [
            {"http_status": 200, "body": {"conversation": conversation, "created": True}},
            {"http_status": 503, "request_id": "test-failure", "body": {"error": {"code": "generation_unavailable", "retryable": True}}},
            {"http_status": 200, "body": {"conversation": conversation, "turns": [], "pending_request": None}},
        ]
        journey = {"name": "synthetic-followup", "artwork": "aic-28560", "turns": [
            {"question": "First", "language": "en"}, {"question": "Follow-up", "language": "en"},
            {"question": "Last", "language": "en"}]}
        with patch("agent.testing.m2_smoke.journeys", return_value=[journey]), patch("agent.testing.m2_smoke.call", side_effect=responses) as call:
            rows = exercise("http://127.0.0.1:8080")
        self.assertEqual(call.call_count, 3)
        self.assertEqual(len(rows), 3)
        self.assertTrue(rows[0]["failure_atomicity_pass"])
        self.assertEqual([row["turn"] for row in rows if "blocked" in row], [2, 3])


if __name__ == "__main__":
    unittest.main()
