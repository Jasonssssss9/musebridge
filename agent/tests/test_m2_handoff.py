"""Development records cannot fabricate release or human-review approval."""
import unittest
from unittest.mock import patch

from agent.testing.m2_handoff import summarize
from agent.testing.m2_smoke import exercise


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

    def test_failed_first_turn_blocks_followup_without_a_new_inference(self):
        conversation = {"contract_version": "agent-demo-v0.3", "conversation_id": "11111111-1111-4111-8111-111111111111",
            "artwork_id": "aic-28560", "dataset_version": "aic-seed-v0.1", "created_at": "2026-11-20T00:00:00Z",
            "expires_at": "2026-11-21T00:00:00Z", "revision": 0, "language_preference": "auto", "auto_language": "en", "last_answer_language": None}
        responses = [
            {"http_status": 200, "body": {"conversation": conversation, "created": True}},
            {"http_status": 503, "request_id": "test-failure", "body": {"error": {"code": "generation_unavailable", "retryable": True}}},
            {"http_status": 200, "body": {"conversation": conversation}},
        ]
        journey = {"name": "synthetic-followup", "artwork": "aic-28560", "turns": [
            {"question": "First", "language": "en"}, {"question": "Follow-up", "language": "en"}]}
        with patch("agent.testing.m2_smoke.journeys", return_value=[journey]), patch("agent.testing.m2_smoke.call", side_effect=responses) as call:
            rows = exercise("http://127.0.0.1:8080")
        self.assertEqual(call.call_count, 3)
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0]["failure_atomicity_pass"])
        self.assertIn("blocked", rows[1])


if __name__ == "__main__":
    unittest.main()
