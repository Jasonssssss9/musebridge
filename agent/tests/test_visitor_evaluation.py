"""Independent evaluation integrity with synthetic public-contract examples."""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from acceptance.reviewed_suite import digest, frozen_inputs, suite_directory, validate_reviews, verify
from acceptance.quality_gates import quality_checklist, threshold
from acceptance.evaluation import check_independent_coverage, development_schedule, pending_check, public_checks, schedule, trace_verdict
from acceptance.run_evaluation import RuntimeLog, execute
from acceptance.score_evaluation import check_review, load_runs, summarize, template
from agent.testing.v2 import example, medium_answer, medium_plan


class VisitorEvaluationTests(unittest.TestCase):
    def test_reviewed_artifacts_and_language_signoffs_must_match(self):
        hashes = {"single-turn.json": "a", "multi-turn.json": "b"}
        approvals = {role: {"reviewer": "Test reviewer", "approved": True, "date": "2026-01-02",
                            "reference": "Synthetic test report"}
                     for role in ("visitor_realism", "source_and_state", "en", "es", "ko", "zh-CN")}
        validate_reviews({"approvals": approvals, "sha256": hashes}, hashes)
        for review in ({"approvals": approvals, "sha256": {}}, {"approvals": {}, "sha256": hashes}):
            with self.assertRaises(ValueError):
                validate_reviews(review, hashes)

    def test_external_synthetic_suite_keeps_variants_and_separate_risk_repeats(self):
        with TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            suite = worktree / "acceptance/visitor-heldout/v0.1"
            suite.mkdir(parents=True)
            single = {"status": "draft", "scenarios": [{
                "id": "unit", "artwork_id": "test-art", "risk_repeat": True, "intent": "test",
                "category": "tests", "expected": {}, "variants": [
                    {"language": lang, "text": "test", "expect": {}} for lang in ("en", "ko")
                ],
            }]}
            multi = {"status": "draft", "scripts": [{"id": "unittest", "type": "tests", "steps": [
                {"kind": kind, "step": i} for i, kind in enumerate(("message", "browser", "message"), 1)
            ]}]}
            for name, value in (("single-turn.json", single), ("multi-turn.json", multi)):
                (suite / name).write_text(json.dumps(value))
            self.assertEqual(suite_directory(worktree), suite)
            self.assertEqual(suite_directory(suite), suite)
            self.assertRaisesRegex(ValueError, "suite-root", suite_directory, None)
            self.assertRaisesRegex(ValueError, "not frozen", verify, suite, None)
            trials = list(schedule(suite))
            counts = Counter((group, step["kind"]) for group, _, _, steps in trials for step in steps)
            self.assertEqual(counts, {("single", "message"): 2, ("risk", "message"): 4,
                                      ("scripts", "message"): 2, ("scripts", "browser"): 1})
            ids = [f"{group}/{key}/{step['step']}" for group, key, _, steps in trials for step in steps]
            self.assertEqual(len(ids), len(set(ids)))

    def test_smaller_signed_input_cannot_declare_an_independent_study(self):
        with self.assertRaisesRegex(ValueError, "coverage"):
            check_independent_coverage({"scenarios": []}, {"scripts": []})

    def test_freeze_requires_actual_review_references_bound_to_unchanged_files(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = ("single-turn.json", "multi-turn.json")
            for name in files:
                (root / name).write_text(json.dumps({"status": "frozen"}))
            digests = {f: digest(root / f) for f in files}
            (root / "MANIFEST.json").write_text(json.dumps({"sha256": digests}))
            self.assertRaisesRegex(ValueError, "Review hashes", verify, root, None)
            refs = {role: {"reviewer": "Unit test", "date": "2026-01-02", "reference": "test", "approved": True}
                    for role in ("visitor_realism", "source_and_state", "en", "es", "zh-CN", "ko")}
            record = root / "review.json"
            record.write_text(json.dumps({"sha256": digests, "approvals": refs}))
            self.assertEqual(digest(root / "MANIFEST.json"), verify(root, record)["suite_sha256"])
            _, documents = frozen_inputs(root, record)
            (root / files[0]).write_text("{}")
            self.assertEqual(documents[0], {"status": "frozen"})
            self.assertRaisesRegex(ValueError, "differ", verify, root, record)

    def test_public_and_private_checks_use_correlated_committed_result(self):
        public, run = example("public_submission_response"), example("run_request_initial")
        result, plan = example("run_result_mixed"), example("mixed_social_and_fact_plan")
        expected = {"status": result["answer"]["status"], "answer_language": "en", "auto_language_after": "en",
                    "preference_after": "auto", "retrieval": "required", "pending_after":"none",
                    "routes": ["retrieve"], "request_kinds":["conversation","artwork_fact"],
                    "citations":{"required": ["aic-28560-medium"], "any_of": [], "allowed":[]}}
        turn = public["turn"]
        before = {**public["conversation"], "revision": 0}
        checks = public_checks(expected, before, {"client_message_id":turn["client_message_id"],
                               "question":turn["question"]}, public)
        self.assertTrue(all(checks.values()), checks)
        events = [{"event":"private_workflow_result","result":result},{"event":"private_run_input","request":run},
                  {"event":"private_model_decision", "operation":"plan","decision":plan},
                  *([{"event":"model_validated"}] * result["execution"]["model_calls"]),
                  *([{"event":"model_http_attempt"}] * result["execution"]["model_calls"]),
                  *([{"event":"retrieval_attempt"}] * result["execution"]["retrieval_calls"])]
        trace = trace_verdict(expected,events,public)
        self.assertEqual(trace["latency_class"],"retrieval_or_reuse")
        self.assertTrue(all(trace["checks"].values()),trace["checks"])
        result["answer"]["text"] = "This different text did not reach the visitor."
        self.assertFalse(trace_verdict(expected,events,public)["checks"]["trace_binding"])
        self.assertIsNone(trace_verdict(expected,[],public)["latency_class"])

    def test_recorded_medium_shortcut_uses_full_planning_projection(self):
        # Use released synthetic contract examples. No visitor or private trace is checked in.
        run, public = example("run_request_initial"), example("public_submission_response")
        run["question"] = public["turn"]["question"] = "What material is this work made of?"
        plan, answer = medium_plan(run), medium_answer(run)
        result = example("run_result_mixed")
        result.update(requests=plan["requests"], request_results=answer["results"], executed_queries=plan["queries"])
        result["answer"]["text"] = answer["results"][0]["text"]
        public["turn"]["answer"]["text"] = result["answer"]["text"]
        result["conversation_update"]["memory"] = answer["memory_update"]
        events = [{"event": "private_model_decision", "operation": "plan",
                   "decision": {"visitor_task": "look_up_listed_medium", "detected_language": "en"}},
                  {"event": "private_run_input", "request": run},
                  {"event": "private_workflow_result", "result": result},
                  {"event": "retrieval_attempt"},
                  *[{"event": event} for event in ("model_http_attempt", "model_validated") for _ in range(2)]]
        expected = {"routes": ["retrieve"], "retrieval": "required", "pending_after": "none"}
        trace = trace_verdict(expected, events, public)
        self.assertTrue(all(trace["checks"].values()), trace["checks"])
        self.assertEqual(trace["route"], "retrieve")
        self.assertEqual(trace["evidence"]["plan"], plan)

    def test_pending_semantics_and_citation_groups(self):
        self.assertTrue(pending_check("local_or_whole", None, {"original_fragment":"x"}))
        self.assertFalse(pending_check("kept", None, None))
        self.assertTrue(pending_check("cleared", {"original_fragment":None}, None))
        public = example("public_submission_response")
        step = list(development_schedule())[0][-1][0]
        target = {**step["expect"],"citations":{"required":[],"any_of":[["aic-11723-medium"]],"allowed":["aic-28560-medium"]}}
        check = public_checks(target,{**public["conversation"],"revision":0},public["turn"],public)
        self.assertFalse(check["citation_ids"])

    def test_failed_public_turn_blocks_all_dependent_steps_only(self):
        class Failed:
            def __init__(self,_): pass
            def send(self,row,*_): row.update(outcome="failed")
        rows=[]
        execute(development_schedule(),"unused",None,rows.append,visitor_factory=Failed)
        self.assertEqual([r["outcome"] for r in rows],["failed","blocked","failed","blocked"])

    def test_partial_trace_leaves_private_line_available(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/"events.jsonl"
            ready={"event":"integration_ready","profile_fingerprint":"p","model_digest":"m","implementation_fingerprint":"i"}
            path.write_text(json.dumps(ready)+"\n")
            log=RuntimeLog(path)
            self.addCleanup(log.file.close)
            with path.open("a") as stream:
                stream.write('{"event":"done","request_id":"t"')
                stream.flush()
                self.assertEqual(log.events_for("t"),[])
                stream.write("}\n")
            self.assertEqual(log.events_for("t"),[{"event":"done","request_id":"t"}])

    def test_semantics_missing_transport_and_provenance_are_separate(self):
        row={"id":"synthetic","kind":"message","outcome":"delivered","duration_ms":1111,
             "expected":{}, "checks":{"status":True},"trace":{"latency_class":"direct"}}
        blocked={**row,"id":"missing","outcome":"blocked","checks":{},"trace":{}}
        rows={r["id"]:r for r in (row,blocked)}
        reviews=template(rows)
        self.assertEqual(set(reviews),{"synthetic", "missing"})
        reviews["synthetic"]["judgments"]["no_unsupported_claims"]=False
        with self.assertRaises(ValueError):check_review(rows,reviews)
        reviews["synthetic"]["reviewer"]="Test author"
        check_review(rows,reviews)
        summary=summarize(list(rows.values()),reviews)
        self.assertEqual(summary["public_warm_latency"]["direct"]["p95_ms"],1111)
        self.assertEqual(summary["semantic"]["no_unsupported_claims"]["failed"],1)
        self.assertEqual(summary["semantic"]["whole_question"]["unobserved"],2)
        changed=deepcopy(row)
        changed["checks"]["status"]=False
        with self.assertRaises(ValueError):check_review({"synthetic":changed},reviews)

    def test_threshold_cannot_pass_empty_unreviewed_or_partial_evidence(self):
        for values in ([], ["n/a"], [True, None], ["n/a", None]):
            self.assertEqual(threshold(values, 95)["status"], "pending_evidence")
        self.assertEqual(threshold([True] * 19 + [False], 95)["status"], "observed_target_met")
        self.assertEqual(threshold([False] + [None] * 99, 100)["status"], "observed_failure")

    def test_checklist_distinguishes_delivery_grounding_and_numeric_language_fields(self):
        expected = {"answer_language": "es", "status": "answered"}
        row = {"id": "a", "kind": "message", "category": "factual", "input_language": "en",
               "language_override": "es", "expected": expected, "outcome": "delivered",
               "duration_ms": 1000, "checks": {"citation_ids": True, "answer_language_field": True,
                       "saved_preference": True, "auto_language": True}, "trace": {"latency_class": "direct"}}
        blocked = {**row, "id": "b", "outcome": "blocked", "trace": {}, "checks": {}}
        partial = {**row, "id": "c", "outcome": "not_run", "trace": {}, "checks": {}}
        reviews = {"a": {"judgments": {"no_unsupported_claims": False, "citations_entail_claims": True,
                                       "explicit_language_switch": "n/a", "actual_language": True,
                                       "whole_question": True}, "supported_parts": 1, "completed_supported_parts": 1}}
        gates = quality_checklist([row, blocked, partial], reviews, development=False)
        self.assertEqual(gates["no_unsupported_claim_or_false_citation"]["failed"], 1)
        self.assertEqual(gates["no_unsupported_claim_or_false_citation"]["status"], "observed_failure")
        self.assertEqual(gates["whole_question"]["failed"], 1)
        self.assertEqual(gates["whole_question"]["unobserved"], 1)
        self.assertEqual(gates["designated_language_changes"]["passed"], 0)
        self.assertEqual(gates["language_and_preference_by_expected_reply"]["es"]["scheduled"], 3)
        self.assertEqual(gates["model_first_pass_by_visitor_language"]["en"]["unobserved"], 3)
        self.assertEqual(gates["supported_request_completion"]["status"], "pending_evidence")
        self.assertEqual(gates["warm_latency"]["paths"]["direct"]["status"], "pending_evidence")
        self.assertFalse(gates["formal_release_authorized"])

    def test_combined_reports_reject_duplicate_trials(self):
        with TemporaryDirectory() as tmp:
            d=Path(tmp)
            (d/"run.json").write_text(json.dumps({"suite_sha256":"a","source_sha256":"b","identity":{},"tokenizer_sha256":"c","evaluator_sha256":{},"review_sha256":None}))
            (d/"turns.jsonl").write_text(json.dumps({"id":"example"})+"\n")
            with self.assertRaisesRegex(ValueError,"Duplicate"):load_runs([d,d])
