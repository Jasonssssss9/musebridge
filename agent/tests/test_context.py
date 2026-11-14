"""Context projection and sizing tests; no model or tokenizer is configured."""

from copy import deepcopy
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

from agent.runtime.context import build_answer_input, build_plan_input, fit_history
from agent.runtime.errors import WorkflowError

_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA = json.loads((_ROOT / "docs/contracts/agent-v1.schema.json").read_text())
_EXAMPLES = json.loads((_ROOT / "docs/contracts/agent-v1.examples.json").read_text())


def run_request():
    return deepcopy(next(case["payload"] for case in _EXAMPLES["schema_cases"]
                         if case["name"] == "run_request"))


def history_turn(sequence, *, mode="llm", status="answered"):
    return {
        "turn_id": f"00000000-0000-4000-8000-{sequence:012d}",
        "sequence": sequence,
        "question": f"Earlier question {sequence}",
        "answer_language": "en",
        "answer_status": status,
        "answer_mode": mode,
        "answer_text": f"Earlier answer {sequence}",
    }


def validate_input(name, data):
    schema = {"$ref": f"#/$defs/{name}", "$defs": _SCHEMA["$defs"]}
    Draft202012Validator(schema).validate(data)


class ContextTests(unittest.TestCase):
    def test_plan_preserves_original_language_and_projects_only_allowed_fields(self):
        run = run_request()
        run["question"] = "What about its support?"
        run["answer_language"] = "zh-CN"
        run["context"]["recent_turns"] = [history_turn(7), history_turn(9)]
        payload = build_plan_input(run)
        validate_input("PlanInput", payload)
        self.assertEqual(payload["original_question"], run["question"])
        self.assertEqual(payload["answer_language"], "zh-CN")
        self.assertEqual(payload["artwork_context"], {
            "artwork_title": "The Bedroom", "artist": "Vincent van Gogh"})
        self.assertEqual(payload["recent_turns"], run["context"]["recent_turns"])
        self.assertEqual(set(payload), {"original_question", "answer_language",
                                      "artwork_context", "recent_turns", "query_policy"})
        self.assertEqual(payload["query_policy"]["search_language"], "en")
        payload["recent_turns"][0]["answer_text"] = "Changed"
        payload["artwork_context"]["artwork_title"] = "Changed"
        self.assertEqual(run["context"]["recent_turns"][0]["answer_text"], "Earlier answer 7")
        self.assertEqual(run["context"]["artwork_title"], "The Bedroom")

    def test_language_does_not_follow_history_language(self):
        run = run_request()
        run["context"]["recent_turns"] = [history_turn(1)]
        run["context"]["recent_turns"][0]["answer_language"] = "zh-CN"
        self.assertEqual(build_plan_input(run)["answer_language"], "en")

    def test_invalid_history_pairs_order_duplicates_and_bound_are_rejected(self):
        cases = [
            [history_turn(1, mode="fixture", status="partially_answered")],
            [history_turn(1, mode="llm", status="unsupported_question")],
            [history_turn(2), history_turn(1)],
            [history_turn(1), history_turn(1)],
            [history_turn(n) for n in range(1, 5)],
            [dict(history_turn(1), sequence=True)],
        ]
        duplicate_id = [history_turn(1), history_turn(2)]
        duplicate_id[1]["turn_id"] = duplicate_id[0]["turn_id"]
        cases.append(duplicate_id)
        for history in cases:
            with self.subTest(history=history):
                run = run_request()
                run["context"]["recent_turns"] = history
                with self.assertRaises(WorkflowError) as caught:
                    build_plan_input(run)
                self.assertEqual(caught.exception.code, "invalid_request")
                self.assertEqual(caught.exception.phase, "context")

    def test_all_valid_mode_status_pairs_remain_available_for_reference(self):
        pairs = {
            "fixture": ("answered", "insufficient", "unsupported_question"),
            "llm": ("answered", "partially_answered", "insufficient",
                    "clarification_needed", "out_of_scope"),
        }
        for mode, statuses in pairs.items():
            for status in statuses:
                with self.subTest(mode=mode, status=status):
                    run = run_request()
                    run["context"]["recent_turns"] = [history_turn(1, mode=mode, status=status)]
                    payload = build_plan_input(run)
                    validate_input("PlanInput", payload)
                    self.assertEqual(payload["recent_turns"][0]["answer_status"], status)

    def test_original_question_is_rejected_instead_of_silently_normalized(self):
        for question in (" leading whitespace", "trailing whitespace ", " "):
            with self.subTest(question=question):
                run = run_request()
                run["question"] = question
                with self.assertRaises(WorkflowError):
                    build_plan_input(run)

    def test_answer_snapshots_do_not_share_mutable_inputs(self):
        run = run_request()
        plan = deepcopy(next(case["payload"] for case in _EXAMPLES["schema_cases"]
                             if case["name"] == "retrieve_plan"))
        evidence = [{"excerpt": "Source uncertainty may matter."}]
        payload = build_answer_input(run, plan["requests"], plan["queries"], evidence,
                                     complete_scope=False, allow_refinement=False)
        self.assertEqual(payload["original_question"], run["question"])
        self.assertNotIn("query_policy", payload)
        self.assertEqual(payload["evidence"], evidence)
        payload["evidence"][0]["excerpt"] = "Changed"
        payload["requests"][0]["question"] = "Changed"
        payload["executed_queries"][0]["request_ids"].append("r4")
        self.assertEqual(evidence[0]["excerpt"], "Source uncertainty may matter.")
        self.assertNotEqual(plan["requests"][0]["question"], "Changed")
        self.assertEqual(plan["queries"][0]["request_ids"], ["r1"])

    def test_empty_evidence_and_disabled_refinement_are_valid_final_input(self):
        plan = next(case["payload"] for case in _EXAMPLES["schema_cases"]
                    if case["name"] == "retrieve_plan")
        payload = build_answer_input(run_request(), plan["requests"], plan["queries"], [],
                                     complete_scope=False, allow_refinement=False)
        validate_input("FinalInput", payload)
        with self.assertRaises(WorkflowError):
            build_answer_input(run_request(), plan["requests"], plan["queries"], [],
                               complete_scope=True, allow_refinement=True)
        with self.assertRaises(WorkflowError):
            build_answer_input(run_request(), plan["requests"], plan["queries"], [],
                               complete_scope=1, allow_refinement=False)

    def test_fit_drops_oldest_whole_turns_and_preserves_required_data(self):
        run = run_request()
        run["context"]["recent_turns"] = [history_turn(n) for n in range(1, 4)]
        payload = build_plan_input(run)
        original = deepcopy(payload)
        schema = {"type": "object"}
        observed = []

        def fake_sizer(messages, provider_schema):
            observed.append(json.loads(messages[1]["content"]))
            self.assertIn("Required JSON output schema:", messages[0]["content"])
            self.assertEqual(provider_schema, schema)
            return 100 + 100 * len(observed[-1]["recent_turns"])

        fitted = fit_history("plan", payload, schema, prompt_sizer=fake_sizer,
                             context_limit=270, output_tokens=50, safety_tokens=20)
        self.assertEqual([turn["sequence"] for turn in fitted["recent_turns"]], [3])
        self.assertEqual([len(item["recent_turns"]) for item in observed], [3, 2, 1])
        self.assertEqual(payload, original)
        for key in payload.keys() - {"recent_turns"}:
            self.assertEqual(fitted[key], payload[key])

    def test_fit_fails_without_truncating_required_question_or_evidence(self):
        plan = next(case["payload"] for case in _EXAMPLES["schema_cases"]
                    if case["name"] == "retrieve_plan")
        evidence = [{"excerpt": "An important qualifier may not be cut."}]
        payload = build_answer_input(run_request(), plan["requests"], plan["queries"], evidence,
                                     complete_scope=False, allow_refinement=False)
        original = deepcopy(payload)
        with self.assertRaises(WorkflowError) as caught:
            fit_history("assess", payload, {}, prompt_sizer=lambda messages, schema: 101,
                        context_limit=100, output_tokens=1, safety_tokens=0)
        self.assertEqual(caught.exception.code, "agent_context_limit")
        self.assertEqual(caught.exception.phase, "context")
        self.assertEqual(payload, original)

    def test_fit_honors_exact_boundary_and_returns_detached_payload(self):
        payload = build_plan_input(run_request())
        fitted = fit_history("plan", payload, {}, prompt_sizer=lambda messages, schema: 100,
                             context_limit=868, output_tokens=256)
        self.assertEqual(fitted, payload)
        self.assertIsNot(fitted, payload)
        self.assertIsNot(fitted["recent_turns"], payload["recent_turns"])

    def test_fake_sizer_contract_rejects_non_token_counts(self):
        for count in (-1, True, 1.5):
            with self.subTest(count=count), self.assertRaises(ValueError):
                fit_history("plan", build_plan_input(run_request()), {},
                            prompt_sizer=lambda messages, schema: count,
                            context_limit=8192, output_tokens=768)


if __name__ == "__main__":
    unittest.main()
