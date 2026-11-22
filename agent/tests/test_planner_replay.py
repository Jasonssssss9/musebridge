"""Offline regressions for scoring frozen PLAN inputs."""
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agent.runtime.context_v2 import build_plan_input
from agent.runtime.errors import WorkflowError
from agent.runtime.validation_v2 import ContractValidatorV2
from agent.testing.planner_comparison import plan_check
from agent.testing.replay_plans import replay_turn, summary
from agent.testing.v2 import example, make_run
from agent.tools.evidence import EvidenceManifest


class PlannerReplayTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.run_input = make_run(question="This painting is beautiful.")
        self.payload = build_plan_input(self.run_input, EvidenceManifest.load())
        self.plan = example("direct_appreciation")
        self.plan["conversation_acts"] = [{"request_id": "r1", "act": "share_impression"}]
        wire = {"visitor_task": "chat_translate_read_catalog_or_handle_mixed_requests", "plan": self.plan}
        self.attempt = {"operation": "plan", "schema_result": "valid", "model_input": self.payload,
                        "completion": {"data": wire}}
        self.validator = ContractValidatorV2()

    def test_quote_drift_fails_a_schema_valid_plan(self):
        self.plan["requests"][0]["original_fragment"] = "This painting looks beautiful."
        check = plan_check(self.run_input, [self.attempt], self.validator)
        self.assertFalse(check["valid"])
        self.assertEqual(check["schema_result"], "valid")
        self.assertIn("source fragment", check["reason"])

    def test_stage_deadline_ends_before_terminal_assembly(self):
        clock = "agent.testing.planner_comparison.monotonic"
        for times in ([11], [9, 11]):
            with self.subTest(times=times), patch(clock, side_effect=times):
                check = plan_check(self.run_input, [self.attempt], self.validator, deadline=10)
                self.assertIsNone(check["valid"])
                self.assertEqual(check["error_code"], "generation_timeout")
        # Terminal assembly still validates its answer after the two stage clock checks.
        with patch(clock, side_effect=[9, 9]):
            self.assertTrue(plan_check(self.run_input, [self.attempt], self.validator, deadline=10)["valid"])

    async def test_failed_admission_is_unobserved_and_does_not_count_as_a_regression(self):
        turn = {"request_id": "example", "number": 2, "input": self.run_input,
                "plan_check": {"valid": True, "schema_result": "valid"}}
        failure = WorkflowError("service_not_ready", "The provider is unavailable.", phase="admission")
        with patch("agent.testing.replay_plans.ensure_probe_identity", AsyncMock(side_effect=failure)):
            row = await replay_turn(None, self.validator, {"model_attempts": []},
                                    {"id": "case", "language": "en"}, turn, self.payload)
        self.assertEqual(row["error"]["code"], "service_not_ready")
        self.assertIsNone(row["candidate"]["valid"])
        counts = summary([row])["follow_up"]
        self.assertEqual(counts["candidate_unobserved"], 1)
        self.assertEqual(counts["regressions"], [])
        schema_failure = {"operation": "plan", "schema_result": "invalid"}
        self.assertFalse(plan_check(self.run_input, [schema_failure], self.validator)["valid"])
