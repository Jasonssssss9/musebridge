"""Exercise evaluation arithmetic, provenance checks, and the local benchmark CLI."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from eval import retrieval_eval
from knowledge import load_dataset, retrieve


ROOT = Path(__file__).resolve().parents[2]
VERSION = "aic-seed-v0.1"
SEED_PATH = ROOT / "knowledge" / "data" / f"{VERSION}.json"
SUITE_PATH = ROOT / "eval" / "aic-retrieval-v0.1.json"
BEDROOM = "aic-28560"
TOILETTE = "aic-11723"
MEDIUM = f"{BEDROOM}-medium"
DESCRIPTION = f"{BEDROOM}-description-01"

CATEGORIES = {
    "paraphrase", "semantic_gap", "multi_topic", "lexical_confusion",
    "scope_confusion", "evidence_gap",
}


def small_suite():
    cases = []
    for pair_id, category, answerability, gold, english, chinese in (
        (
            "full", "multi_topic", "full", [MEDIUM, DESCRIPTION],
            "Describe the medium and the brushwork.", "请说明材质和笔触。",
        ),
        (
            "partial", "multi_topic", "partial", [DESCRIPTION],
            "Describe the brushwork and give its date.", "请描述笔触并给出具体日期。",
        ),
        (
            "none", "evidence_gap", "none", [],
            "How much did the canvas cost?", "这块画布花了多少钱？",
        ),
    ):
        for language, query in (("en", english), ("zh-CN", chinese)):
            cases.append({
                "case_id": f"{pair_id}-{language}",
                "pair_id": pair_id,
                "category": category,
                "query_language": language,
                "artwork_id": BEDROOM,
                "query": query,
                "answerability": answerability,
                "relevant_chunk_ids": gold.copy(),
                "rationale": "A controlled fixture for evaluating retrieval metrics.",
            })
    return {
        "suite_id": "controlled-evaluation-v0.1",
        "dataset_version": VERSION,
        "description": "Three bilingual pairs for evaluator arithmetic.",
        "cases": cases,
    }


def response(*hits):
    return {"dataset_version": VERSION, "evidence": list(hits)}


class EvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset(SEED_PATH)
        cls.medium = cls.get_hit(BEDROOM, "material")
        cls.description = cls.get_hit(BEDROOM, "brushwork")
        cls.other_medium = cls.get_hit(TOILETTE, "material")

    @classmethod
    def get_hit(cls, artwork_id, query):
        return retrieve(cls.dataset, artwork_id=artwork_id, query=query,
                        dataset_version=VERSION)["evidence"][0]

    def write_suite(self, directory, suite):
        path = Path(directory) / "suite.json"
        path.write_text(json.dumps(suite, ensure_ascii=False), encoding="utf-8")
        return path

    def test_released_suite_runs_with_real_retrieval_and_has_bilingual_pairs(self):
        suite = retrieval_eval.load_suite(SUITE_PATH, self.dataset)
        self.assertEqual(len(suite["cases"]), 48)
        self.assertEqual({case["category"] for case in suite["cases"]}, CATEGORIES)
        self.assertEqual(len({case["pair_id"] for case in suite["cases"]}), 24)

        for top_k in (1, 2, 5):
            with self.subTest(top_k=top_k):
                report = retrieval_eval.evaluate(self.dataset, suite, top_k=top_k)
                self.assertEqual(report["top_k"], top_k)
                self.assertEqual(report["summary"]["case_count"], 48)
                self.assertEqual(report["summary"]["valid_case_count"], 48)
                self.assertEqual(report["summary"]["scope_violation_count"], 0)
                self.assertEqual(report["by_language"]["en"]["case_count"], 24)
                self.assertEqual(report["by_language"]["zh-CN"]["case_count"], 24)
                self.assertEqual(set(report["by_category"]), CATEGORIES)
                self.assertTrue(all(len(row["retrieved_chunk_ids"]) <= top_k
                                    for row in report["results"]))

    def test_metrics_distinguish_rank_coverage_and_empty_gold(self):
        suite = small_suite()
        controlled = (
            response(self.medium),
            response(),
            response(self.medium, self.description),
            response(self.description),
            response(self.medium),
            response(),
        )
        by_query = {case["query"]: result for case, result in zip(suite["cases"], controlled)}

        def fake_retrieve(dataset, *, artwork_id, query, dataset_version, top_k):
            self.assertIs(dataset, self.dataset)
            self.assertEqual((artwork_id, dataset_version, top_k), (BEDROOM, VERSION, 2))
            return by_query[query]

        with patch.object(retrieval_eval, "retrieve", side_effect=fake_retrieve):
            report = retrieval_eval.evaluate(self.dataset, suite)
        summary = report["summary"]
        self.assertEqual(summary["case_count"], 6)
        self.assertEqual(summary["valid_case_count"], 6)
        self.assertEqual(summary["invalid_case_count"], 0)
        self.assertEqual(summary["positive_case_count"], 4)
        self.assertEqual(summary["negative_case_count"], 2)
        self.assertEqual(summary["hit_rate_at_k"], 0.75)
        self.assertEqual(summary["precision_at_k"], 0.375)
        self.assertEqual(summary["recall_at_k"], 0.625)
        self.assertEqual(summary["mrr_at_k"], 0.625)
        self.assertEqual(summary["all_relevant_retrieved_rate_at_k"], 0.5)
        self.assertEqual(summary["positive_returned_precision"], 0.75)
        self.assertEqual(summary["negative_false_positive_count"], 1)
        self.assertEqual(summary["negative_false_positive_rate"], 0.5)
        self.assertEqual(summary["scope_violation_count"], 0)
        self.assertEqual(report["by_language"]["en"]["recall_at_k"], 0.75)
        self.assertEqual(report["by_language"]["zh-CN"]["recall_at_k"], 0.5)
        self.assertEqual(report["by_answerability"]["partial"]["mrr_at_k"], 0.75)
        self.assertIsNone(report["by_category"]["evidence_gap"]["recall_at_k"])
        self.assertIsNone(report["by_category"]["multi_topic"]["negative_false_positive_rate"])

        rows = {row["case_id"]: row for row in report["results"]}
        self.assertEqual(rows["full-en"]["matched_chunk_ids"], [MEDIUM])
        self.assertEqual(rows["full-en"]["missed_chunk_ids"], [DESCRIPTION])
        self.assertEqual(rows["full-zh-CN"]["metrics"]["reciprocal_rank"], 0.0)
        self.assertEqual(rows["partial-en"]["retrieved_chunk_ids"], [MEDIUM, DESCRIPTION])
        self.assertEqual(rows["partial-en"]["matched_chunk_ids"], [DESCRIPTION])
        self.assertEqual(rows["partial-en"]["unexpected_chunk_ids"], [MEDIUM])
        self.assertEqual(rows["partial-en"]["metrics"]["reciprocal_rank"], 0.5)
        self.assertIsNone(rows["none-en"]["metrics"])
        self.assertIs(rows["none-en"]["negative_false_positive"], True)
        self.assertIs(rows["none-zh-CN"]["negative_false_positive"], False)

    def test_invalid_evidence_does_not_improve_relevance_metrics(self):
        suite = small_suite()
        suite["cases"] = suite["cases"][:2]
        fabricated = {**self.description, "excerpt": "Invented museum text"}
        invalid_responses = (
            (response(self.medium, self.other_medium), [self.other_medium["chunk_id"]]),
            (response(self.medium, fabricated), []),
            (response(self.medium, self.medium), []),
            ({"dataset_version": "wrong-release", "evidence": [self.medium]}, []),
            ({"dataset_version": VERSION, "evidence": "not an array"}, []),
        )
        for invalid, scope_violations in invalid_responses:
            with self.subTest(response=invalid):
                with patch.object(retrieval_eval, "retrieve",
                                  side_effect=[invalid, response(self.medium)]):
                    report = retrieval_eval.evaluate(self.dataset, suite)
                row = next(result for result in report["results"]
                           if result["case_id"] == "full-en")
                self.assertTrue(row["issues"])
                self.assertIsNone(row["metrics"])
                self.assertEqual(row["scope_violation_chunk_ids"], scope_violations)
                self.assertEqual(report["summary"]["scope_violation_count"], len(scope_violations))
                self.assertEqual(report["summary"]["invalid_case_count"], 1)
                self.assertEqual(report["summary"]["positive_case_count"], 1)
                self.assertEqual(report["summary"]["recall_at_k"], 0.5)

    def test_additive_response_fields_do_not_invalidate_provenance(self):
        suite = small_suite()
        suite["cases"] = suite["cases"][:2]
        annotated = response({**self.medium, "diagnostic": True}, self.description)
        annotated["trace"] = "optional"
        with patch.object(retrieval_eval, "retrieve", return_value=annotated):
            report = retrieval_eval.evaluate(self.dataset, suite)
        self.assertEqual(report["summary"]["invalid_case_count"], 0)
        self.assertEqual(report["summary"]["recall_at_k"], 1.0)

    def test_suite_validation_rejects_invalid_labels_and_pairs(self):
        def change(case_index, **fields):
            return lambda suite: suite["cases"][case_index].update(fields)

        edits = (
            ("duplicate case id", change(1, case_id="full-en")),
            ("missing language counterpart", change(1, query_language="en")),
            ("mismatched pair metadata", change(1, category="paraphrase")),
            ("unknown artwork", change(0, artwork_id="aic-00000")),
            ("foreign gold", change(0, relevant_chunk_ids=[f"{TOILETTE}-medium"])),
            ("duplicate gold", change(0, relevant_chunk_ids=[MEDIUM, MEDIUM])),
            ("empty positive gold", change(0, relevant_chunk_ids=[])),
            ("nonempty negative gold", change(4, relevant_chunk_ids=[MEDIUM])),
            ("missing half pair", lambda suite: suite["cases"].pop()),
            ("wrong release", lambda suite: suite.update(dataset_version="wrong-release")),
        )
        with tempfile.TemporaryDirectory() as directory:
            valid = small_suite()
            self.assertEqual(
                retrieval_eval.load_suite(self.write_suite(directory, valid), self.dataset),
                valid,
            )
            for label, edit in edits:
                with self.subTest(label=label):
                    invalid = deepcopy(valid)
                    edit(invalid)
                    path = self.write_suite(directory, invalid)
                    with self.assertRaises(retrieval_eval.SuiteValidationError):
                        retrieval_eval.load_suite(path, self.dataset)

    def test_evaluate_checks_input_before_retrieval(self):
        invalid = small_suite()
        invalid["cases"][0]["relevant_chunk_ids"] = [f"{TOILETTE}-medium"]
        with patch.object(retrieval_eval, "retrieve") as mocked:
            with self.assertRaises(retrieval_eval.SuiteValidationError):
                retrieval_eval.evaluate(self.dataset, invalid)
            for top_k in (True, 0, 6, "2", 2.0):
                with self.subTest(top_k=top_k):
                    with self.assertRaises(ValueError):
                        retrieval_eval.evaluate(self.dataset, small_suite(), top_k=top_k)
        mocked.assert_not_called()

    def run_cli(self, suite_path, *arguments):
        return subprocess.run(
            [sys.executable, "-m", "eval.retrieval_eval", "--suite", str(suite_path),
             "--seed", str(SEED_PATH), "--top-k", "2", *arguments],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=10,
        )

    def test_cli_returns_zero_on_a_valid_run_with_semantic_misses(self):
        suite = small_suite()
        suite["cases"] = suite["cases"][:2]
        suite["cases"][0]["query"] = "Explain the crinkles."
        suite["cases"][1]["query"] = "请解释这些褶皱。"
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_suite(directory, suite)
            result = self.run_cli(path)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["summary"]["valid_case_count"], 2)
        self.assertEqual(report["summary"]["hit_rate_at_k"], 0.0)
        self.assertEqual(report["summary"]["recall_at_k"], 0.0)
        self.assertIsNone(report["summary"]["positive_returned_precision"])

    def test_cli_writes_the_same_report_to_file_or_stdout(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_suite(directory, small_suite())
            output_path = Path(directory) / "report.json"
            printed = self.run_cli(path)
            written = self.run_cli(path, "--output", str(output_path))
            self.assertEqual(printed.returncode, 0, printed.stderr)
            self.assertEqual(written.returncode, 0, written.stderr)
            self.assertEqual(written.stdout, "")
            self.assertEqual(json.loads(printed.stdout),
                             json.loads(output_path.read_text(encoding="utf-8")))

    def test_cli_returns_two_for_an_invalid_suite(self):
        suite = small_suite()
        suite["dataset_version"] = "wrong-release"
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_cli(self.write_suite(directory, suite))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertTrue(result.stderr)

    def test_cli_will_not_overwrite_the_suite_with_its_report(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_suite(directory, small_suite())
            original = path.read_bytes()
            result = self.run_cli(path, "--output", str(path))
            self.assertEqual(result.returncode, 2)
            self.assertEqual(path.read_bytes(), original)

    def test_cli_returns_one_for_invalid_runtime_evidence(self):
        suite = small_suite()
        suite["cases"] = suite["cases"][:2]
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_suite(directory, suite)
            with (
                patch.object(retrieval_eval, "retrieve", return_value=response(self.other_medium)),
                redirect_stdout(io.StringIO()) as output,
                redirect_stderr(io.StringIO()),
            ):
                code = retrieval_eval.main(["--suite", str(path), "--seed", str(SEED_PATH)])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())["summary"]["invalid_case_count"], 2)


if __name__ == "__main__":
    unittest.main()
