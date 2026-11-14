"""Committed contract cases plus stricter model-output and relationship checks."""
import json
import unittest
from copy import deepcopy
from pathlib import Path

from agent.runtime.errors import WorkflowError
from agent.runtime.validation import (
    ContractValidator, ContractViolation, assemble_answer, model_decision,
    normalized_query, strict_json, validate_plan,
)
from agent.tools.evidence import EvidenceManifest
from agent.testing.fakes import medium_answer, medium_plan, sample_run

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = json.loads((ROOT / "docs/contracts/agent-v1.examples.json").read_text())


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = ContractValidator()
        cls.manifest = EvidenceManifest.load()

    def test_all_35_committed_schema_examples(self):
        self.assertEqual(len(EXAMPLES["schema_cases"]), 35)
        for case in EXAMPLES["schema_cases"]:
            with self.subTest(case=case["name"]):
                if case["valid"]:
                    self.validator.validate(case["definition"], case["payload"])
                else:
                    with self.assertRaises(ContractViolation):
                        self.validator.validate(case["definition"], case["payload"])

    def test_13_committed_m1_runtime_cases(self):
        covered = 0
        for case in EXAMPLES["runtime_cases"]:
            if case["kind"] == "refine":
                continue  # Three refinement cases belong to M2.
            covered += 1
            with self.subTest(case=case["name"]):
                if case["valid"]:
                    self.runtime_case(case)
                else:
                    with self.assertRaises((WorkflowError, ContractViolation)):
                        self.runtime_case(case)
        self.assertEqual(covered, 13)

    def runtime_case(self, case):
        payload = case["payload"]
        if case["kind"] == "plan":
            validate_plan(payload["run"], payload["plan"], self.validator)
        elif case["kind"] == "finish":
            assemble_answer(payload["plan"], payload["decision"],
                            payload["evidence"], self.validator)
        elif case["kind"] == "merge":
            scope = payload["scope"]
            evidence = []
            for response in payload["responses"]:
                self.validator.validate("ToolResult", response)
                self.assertEqual(response["artwork_id"], scope["artwork_id"])
                self.assertEqual(response["dataset_version"], scope["dataset_version"])
                evidence = self.manifest.merge(
                    scope["artwork_id"], scope["dataset_version"], evidence, response["candidates"],
                )
        elif case["kind"] == "completeness":
            scope = payload["scope"]
            actual = self.manifest.complete_scope(
                scope["artwork_id"], scope["dataset_version"], payload["evidence"],
            )
            self.assertEqual(actual, payload["expected_complete"])
        else:
            self.fail("Unhandled runtime fixture kind.")

    def test_strict_json_rejects_wrappers_duplicates_nonfinite_and_invalid_unicode(self):
        samples = [
            '{"a":1,"a":2}', '{"a":{"x":1,"x":2}}', '{"a":NaN}', '{"a":Infinity}',
            '{"a":1e9999}', '["a"] ["b"]', '```json\n{}\n```',
            '{"a":"\\ud800"}', '{"a":"\\u0001"}', b'{"a":"\xff"}',
            '\ufeff{}', {"already": "parsed"}, '{"n":' + '9' * 5000 + '}',
        ]
        for raw in samples:
            with self.subTest(raw=repr(raw)):
                with self.assertRaises(ContractViolation):
                    strict_json(raw)
        self.assertEqual(strict_json('{"question":"这幅画？"}'), {"question": "这幅画？"})
        with self.assertRaises(ContractViolation):
            strict_json("{}", max_bytes=1)

    def test_strict_integer_does_not_accept_bool_or_integral_float(self):
        record = {"workflow_id": "museum_qa_v1", "profile_id": "local-ollama-v1",
                  "prompt_version": "museum-prompts-v1", "schema_version": "agent-schema-v1",
                  "retrieval_strategy_version": "legacy-facet-v0.1", "model_provider": "ollama",
                  "model_name": "synthetic", "model_digest": "synthetic", "model_calls": 1,
                  "retrieval_calls": 0, "refinement_rounds": 0, "duration_ms": 0}
        for value in (True, 1.0, "1"):
            changed = deepcopy(record)
            changed["model_calls"] = value
            with self.assertRaises(ContractViolation):
                self.validator.validate("ExecutionRecord", changed)

    def test_selected_schema_contains_only_reachable_definitions_and_is_detached(self):
        schema = self.validator.schema("Plan")
        self.assertNotIn("RunRequest", schema["$defs"])
        self.assertNotIn("Evidence", schema["$defs"])
        schema["$defs"]["Plan"] = {}
        with self.assertRaises(ContractViolation):
            self.validator.validate("Plan", {"action": "anything"})

    def test_query_normalization_does_not_change_original(self):
        self.assertEqual(normalized_query("ＷＨＡＴ  Is\tIt?"), "what is it?")
        run = sample_run()
        plan = medium_plan(run)
        plan["queries"].append({
            "id": "q2", "request_ids": ["r1"],
            "query": plan["queries"][0]["query"].upper(),
        })
        with self.assertRaises(WorkflowError):
            validate_plan(run, plan, self.validator)
        self.assertEqual(run["question"], "What is it made of?")

    def test_plan_rejects_nonsequential_ids_uncovered_requests_and_extra_fields(self):
        run = sample_run()
        base = medium_plan(run)
        bad = []
        item = deepcopy(base); item["requests"][0]["id"] = "r2"; bad.append(item)
        item = deepcopy(base); item["queries"][0]["id"] = "q2"; bad.append(item)
        item = deepcopy(base); item["requests"].append({
            **item["requests"][0], "id": "r2",
        }); bad.append(item)
        item = deepcopy(base); item["queries"][0]["artwork_id"] = "aic-11723"; bad.append(item)
        item = deepcopy(base); item["queries"][0]["query"] = " untrimmed "; bad.append(item)
        item = deepcopy(base); item["requests"][0]["scope"] = "outside_scope"; bad.append(item)
        for plan in bad:
            with self.subTest(plan=plan):
                with self.assertRaises(WorkflowError):
                    validate_plan(run, plan, self.validator)

    def test_no_ascii_topic_whitelist_in_plan_validation(self):
        run = sample_run()
        plan = medium_plan(run)
        plan["queries"][0]["query"] = "What is Morisot’s color palette?"
        validate_plan(run, plan, self.validator)
        # This only checks shape/links. A semantically wrong named work must
        # be caught in independent planner evaluation, not a keyword rule.

    def test_missing_unknown_repeated_or_wrong_scope_citations_fail(self):
        run = sample_run()
        plan = medium_plan(run)
        evidence = [dict(self.manifest.records["aic-28560-medium"])]
        base = medium_answer(run)
        for ids in ([], ["unknown"], ["aic-11723-medium"],
                    ["aic-28560-medium", "aic-28560-medium"]):
            decision = deepcopy(base)
            decision["results"][0]["citation_chunk_ids"] = ids
            with self.assertRaises(WorkflowError):
                assemble_answer(plan, decision, evidence, self.validator)
        decision = deepcopy(base)
        decision["results"][0]["support"] = "insufficient"
        with self.assertRaises(WorkflowError):
            assemble_answer(plan, decision, evidence, self.validator)

    def test_scope_result_cannot_be_upgraded_with_current_evidence(self):
        run = sample_run()
        plan = medium_plan(run)
        plan["requests"].append({
            "id": "r2", "original_fragment": run["question"],
            "question": "What material is another artwork made of?", "scope": "outside_scope",
        })
        decision = medium_answer(run)
        decision["results"].append({
            **decision["results"][0], "request_id": "r2",
        })
        with self.assertRaises(WorkflowError):
            assemble_answer(plan, decision, [dict(self.manifest.records["aic-28560-medium"])],
                            self.validator)

    def test_status_order_citation_dedup_and_detached_snapshot(self):
        run = sample_run()
        plan = medium_plan(run)
        plan["requests"].append({**plan["requests"][0], "id": "r2"})
        decision = medium_answer(run)
        decision["results"].append({**decision["results"][0], "request_id": "r2", "text": "Second part."})
        evidence = [dict(self.manifest.records["aic-28560-medium"])]
        answer, parts = assemble_answer(plan, decision, evidence, self.validator)
        self.assertEqual(answer["status"], "answered")
        self.assertEqual(answer["text"], decision["results"][0]["text"] + "\n\nSecond part.")
        self.assertEqual(len(answer["citations"]), 1)
        evidence[0]["excerpt"] = "changed"
        decision["results"][0]["text"] = "changed"
        self.assertEqual(answer["citations"][0]["excerpt"], "Oil on canvas")
        self.assertNotEqual(parts[0]["text"], "changed")
        decision["results"].reverse()
        with self.assertRaises(WorkflowError):
            assemble_answer(plan, decision, evidence, self.validator)

    def test_model_error_has_safe_envelope(self):
        with self.assertRaises(WorkflowError) as caught:
            model_decision('{"private visitor text": "bad"}', "Plan", self.validator, phase="planning")
        self.validator.validate("AgentError", caught.exception.as_dict())
        self.assertNotIn("private visitor", str(caught.exception))
