import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from knowledge import load_dataset
from eval import subquestion_eval as evaluator
from eval.retrieval_eval import _projection

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "knowledge/data/aic-seed-v0.1.json"
BEDROOM, TOILETTE = "aic-28560", "aic-11723"
MEDIUM, DESCRIPTION = BEDROOM + "-medium", BEDROOM + "-description-01"


def gold(request_id, text, facet, status, ids, artwork=BEDROOM, standalone=None):
    return {
        "request_id": request_id, "text": text, "artwork_id": artwork,
        "facet": facet, "status": status, "chunk_ids": list(ids),
        "standalone_query": standalone or text, "rationale": "Reviewed against the released excerpts.",
    }


def suite_for(requests, query=None):
    query = query or " ".join(r["text"] for r in requests)
    # Wording need not be translated for arithmetic tests; pairing validates semantics.
    return {
        "suite_id": "unit-test-v1", "dataset_version": "aic-seed-v0.1",
        "description": "Synthetic arithmetic and attribution checks.",
        "cases": [
            {"case_id": "case-" + lang, "pair_id": "pair", "query_language": lang,
             "artwork_id": BEDROOM, "query": query, "requests": copy.deepcopy(requests)}
            for lang in ("en", "zh-CN")
        ],
    }


class SubquestionEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset(SEED)
        cls.records = {
            chunk["chunk_id"]: _projection(chunk, cls.dataset.sources[chunk["source_id"]])
            for chunk in cls.dataset.chunks
        }

    def prediction(self, query, text, facet, status, ids, artwork=BEDROOM):
        normalized, text = evaluator.normalize(query), evaluator.normalize(text)
        start = normalized.index(text)
        return {
            "start": start, "end": start + len(text), "artwork_id": artwork,
            "facet": facet, "status": status, "chunk_ids": list(ids),
        }

    def runtime(self, query, predictions, ids=None):
        if ids is None:
            ids = list(dict.fromkeys(c for r in predictions for c in r["chunk_ids"]))
        return (
            {"dataset_version": self.dataset.dataset_version,
             "evidence": [copy.deepcopy(self.records[c]) for c in ids]},
            {"normalized_query": evaluator.normalize(query), "requests": copy.deepcopy(predictions)},
        )

    def evaluate_fixed(self, suite, predictions, ids=None):
        response = self.runtime(suite["cases"][0]["query"], predictions, ids)
        with patch.object(evaluator, "retrieve_with_trace", return_value=response):
            return evaluator.evaluate(self.dataset, suite)

    def test_fixed_denominator_arithmetic_and_negative_status_breakdown(self):
        requests = [
            gold("medium", "What medium?", "medium", "supported", [MEDIUM]),
            gold("marks", "What marks?", "brushwork", "supported", [DESCRIPTION]),
            gold("fiber", "Which fibre?", "support_fibre", "insufficient_evidence", []),
            gold("foreign", "What about Woman at Her Toilette?", "brushwork", "out_of_scope", [], TOILETTE),
        ]
        suite = suite_for(requests)
        query = suite["cases"][0]["query"]
        predictions = [
            self.prediction(query, requests[0]["text"], "medium", "supported", [MEDIUM]),
            # Missing positive request remains in the denominator.
            self.prediction(query, requests[2]["text"], "support_fibre", "insufficient_evidence", []),
            self.prediction(query, requests[3]["text"], "brushwork", "out_of_scope", [], TOILETTE),
        ]
        report = self.evaluate_fixed(suite, predictions)
        summary = report["summary"]
        self.assertEqual(summary["positive_request_count"], 4)
        self.assertEqual(summary["positive_hit_rate"], 0.5)
        self.assertEqual(summary["positive_mean_recall"], 0.5)
        self.assertEqual(summary["positive_unresolved_count"], 2)
        self.assertEqual(summary["negative_requests"]["correct_empty_count"], 4)
        self.assertEqual(summary["by_negative_status"]["out_of_scope"]["count"], 2)
        self.assertEqual(summary["strict_query_success_count"], 0)

    def test_wrong_facet_on_negative_half_still_counts_false_return(self):
        requests = [
            gold("medium", "What medium?", "medium", "supported", [MEDIUM]),
            gold("fibre", "Which fibre?", "support_fibre", "insufficient_evidence", []),
        ]
        suite = suite_for(requests)
        query = suite["cases"][0]["query"]
        predictions = [
            self.prediction(query, requests[0]["text"], "medium", "supported", [MEDIUM]),
            self.prediction(query, requests[1]["text"], "medium", "supported", [MEDIUM]),
        ]
        report = self.evaluate_fixed(suite, predictions)
        self.assertEqual(report["summary"]["positive_hit_rate"], 1)
        self.assertEqual(report["summary"]["negative_requests"]["false_return_rate"], 1)
        negative = report["results"][0]["requests"][1]
        self.assertFalse(negative["subject_and_facet_match"])
        self.assertEqual(negative["unexpected_chunk_ids"], [MEDIUM])
        self.assertFalse(report["results"][0]["strict_success"])

    def test_correct_chunk_on_wrong_half_does_not_rescue_missing_positive(self):
        requests = [
            gold("medium", "What medium?", "medium", "supported", [MEDIUM]),
            gold("fibre", "Which fibre?", "support_fibre", "insufficient_evidence", []),
        ]
        suite = suite_for(requests)
        query = suite["cases"][0]["query"]
        report = self.evaluate_fixed(suite, [
            self.prediction(query, requests[1]["text"], "medium", "supported", [MEDIUM])
        ])
        self.assertEqual(report["summary"]["positive_hit_rate"], 0)
        self.assertEqual(report["summary"]["negative_requests"]["false_return_count"], 2)

    def test_wrong_facet_cannot_earn_positive_credit_from_shared_chunk(self):
        suite = suite_for([gold("marks", "Describe the marks.", "brushwork", "supported", [DESCRIPTION])])
        query = suite["cases"][0]["query"]
        report = self.evaluate_fixed(suite, [self.prediction(query, query, "color", "supported", [DESCRIPTION])])
        self.assertEqual(report["summary"]["positive_hit_rate"], 0)
        self.assertEqual(report["results"][0]["requests"][0]["missed_chunk_ids"], [DESCRIPTION])

    def test_correct_binding_keeps_hit_credit_but_extra_facet_fails_strict(self):
        query = "Describe the marks."
        suite = suite_for([gold("marks", query, "brushwork", "supported", [DESCRIPTION])])
        report = self.evaluate_fixed(suite, [
            self.prediction(query, query, "brushwork", "supported", [DESCRIPTION]),
            self.prediction(query, query, "color", "supported", [DESCRIPTION]),
        ])
        self.assertEqual(report["summary"]["positive_hit_rate"], 1)
        self.assertEqual(report["summary"]["positive_all_required_rate"], 1)
        self.assertEqual(report["summary"]["strict_query_success_count"], 0)
        self.assertFalse(report["results"][0]["requests"][0]["subject_and_facet_match"])

    def test_merged_unattributed_result_is_not_copied_to_both_gold_units(self):
        requests = [
            gold("first", "What material?", "medium", "supported", [MEDIUM]),
            gold("second", "Which canvas fibre?", "support_fibre", "insufficient_evidence", []),
        ]
        suite = suite_for(requests)
        query = suite["cases"][0]["query"]
        report = self.evaluate_fixed(suite, [
            self.prediction(query, query, "unclassified", "supported", [MEDIUM])
        ])
        summary = report["summary"]
        self.assertEqual(summary["merged_prediction_count"], 2)
        self.assertEqual(summary["positive_hit_rate"], 0)
        self.assertEqual(summary["negative_requests"]["unresolved_count"], 2)
        self.assertEqual(summary["negative_requests"]["correct_empty_count"], 0)
        self.assertFalse(report["results"][0]["strict_success"])

    def test_split_predictions_union_once_per_gold_request(self):
        query = "Explain material and visible marks."
        suite = suite_for([gold("combined", query, "combined_description", "supported", [MEDIUM, DESCRIPTION])])
        report = self.evaluate_fixed(suite, [
            self.prediction(query, "material", "combined_description", "supported", [MEDIUM]),
            self.prediction(query, "visible marks", "combined_description", "supported", [DESCRIPTION]),
        ])
        summary = report["summary"]
        self.assertEqual(summary["positive_request_count"], 2)
        self.assertEqual(summary["split_request_count"], 2)
        self.assertEqual(summary["positive_mean_recall"], 1)
        self.assertEqual(summary["strict_query_success_rate"], 1)

    def test_comparison_shared_span_aligns_by_subject_and_facet(self):
        query = "Compare this brushwork with Woman at Her Toilette."
        suite = suite_for([
            gold("local", query, "brushwork", "supported", [DESCRIPTION]),
            gold("foreign", query, "brushwork", "out_of_scope", [], TOILETTE),
        ], query=query)
        report = self.evaluate_fixed(suite, [
            self.prediction(query, query, "brushwork", "supported", [DESCRIPTION]),
            self.prediction(query, query, "brushwork", "out_of_scope", [], TOILETTE),
        ])
        self.assertEqual(report["summary"]["merged_prediction_count"], 0)
        self.assertEqual(report["summary"]["strict_query_success_rate"], 1)

    def test_extra_prediction_and_unexpected_chunk_prevent_strict_success(self):
        query = "What medium? Context only."
        suite = suite_for([gold("medium", "What medium?", "medium", "supported", [MEDIUM])], query)
        report = self.evaluate_fixed(suite, [
            self.prediction(query, "What medium?", "medium", "supported", [MEDIUM, DESCRIPTION]),
            self.prediction(query, "Context only.", "unknown", "unknown_intent", []),
        ])
        self.assertEqual(report["summary"]["positive_all_required_rate"], 1)
        self.assertEqual(report["summary"]["positive_returned_precision"], 0.5)
        self.assertEqual(report["summary"]["extra_predicted_request_count"], 2)
        self.assertEqual(report["summary"]["strict_query_success_count"], 0)

    def test_empty_unknown_or_missing_is_not_explicit_negative_success(self):
        suite = suite_for([gold("fibre", "Which fibre?", "support_fibre", "insufficient_evidence", [])])
        query = suite["cases"][0]["query"]
        for predictions in (
            [],
            [self.prediction(query, query, "unknown", "unknown_intent", [])],
        ):
            with self.subTest(predictions=predictions):
                report = self.evaluate_fixed(suite, predictions)
                self.assertEqual(report["summary"]["negative_requests"]["unresolved_count"], 2)
                self.assertEqual(report["summary"]["negative_requests"]["correct_empty_count"], 0)
                self.assertIsNone(report["summary"]["positive_hit_rate"])

    def test_invalid_projection_has_no_credit_and_keeps_denominators(self):
        suite = suite_for([gold("medium", "What medium?", "medium", "supported", [MEDIUM])])
        query = suite["cases"][0]["query"]
        response = self.runtime(query, [self.prediction(query, query, "medium", "supported", [MEDIUM])])
        response[0]["evidence"][0]["excerpt"] = "Invented statement"
        with patch.object(evaluator, "retrieve_with_trace", return_value=response):
            report = evaluator.evaluate(self.dataset, suite)
        self.assertEqual(report["summary"]["invalid_case_count"], 2)
        self.assertEqual(report["summary"]["positive_request_count"], 2)
        self.assertEqual(report["summary"]["positive_hit_rate"], 0)
        self.assertIsNone(report["results"][0]["requests"][0]["metrics"])

    def test_foreign_evidence_is_invalid_not_a_retrieval_success(self):
        suite = suite_for([gold("medium", "What medium?", "medium", "supported", [MEDIUM])])
        query = suite["cases"][0]["query"]
        foreign = TOILETTE + "-medium"
        response = self.runtime(query, [self.prediction(query, query, "medium", "supported", [foreign])])
        with patch.object(evaluator, "retrieve_with_trace", return_value=response):
            report = evaluator.evaluate(self.dataset, suite)
        self.assertEqual(report["summary"]["scope_violation_count"], 2)
        self.assertEqual(report["summary"]["positive_hit_rate"], 0)

    def test_trace_validation_handles_malformed_shapes_without_crashing(self):
        suite = suite_for([gold("medium", "What medium?", "medium", "supported", [MEDIUM])])
        query = suite["cases"][0]["query"]
        valid = self.runtime(query, [self.prediction(query, query, "medium", "supported", [MEDIUM])])
        mutations = [
            lambda t: t.update(normalized_query="different"),
            lambda t: t.update(requests={}),
            lambda t: t["requests"][0].update(start=True),
            lambda t: t["requests"][0].update(end=999),
            lambda t: t["requests"][0].update(artwork_id=[]),
            lambda t: t["requests"][0].update(status=[]),
            lambda t: t["requests"][0].update(chunk_ids=[{}]),
            lambda t: t["requests"][0].update(chunk_ids=[]),
            lambda t: t.update(requests=[]),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                response = copy.deepcopy(valid)
                mutation(response[1])
                with patch.object(evaluator, "retrieve_with_trace", return_value=response):
                    report = evaluator.evaluate(self.dataset, suite)
                self.assertEqual(report["summary"]["invalid_case_count"], 2)

    def test_nfkc_offsets_are_unicode_indices(self):
        query = "😀 Ｗｈａｔ   medium?"
        suite = suite_for([gold("medium", "What medium?", "medium", "supported", [MEDIUM])], query)
        normalized = evaluator.normalize(query)
        prediction = self.prediction(query, "What medium?", "medium", "supported", [MEDIUM])
        self.assertEqual(prediction["start"], 2)
        self.assertEqual(normalized[prediction["start"]:prediction["end"]], "What medium?")
        self.assertEqual(self.evaluate_fixed(suite, [prediction])["summary"]["strict_query_success_rate"], 1)

    def test_isolated_calls_only_standalone_text_with_active_scope(self):
        requests = [
            gold("medium", "What medium?", "medium", "supported", [MEDIUM], standalone="What is this made of?"),
            gold("foreign", "And Woman at Her Toilette?", "brushwork", "out_of_scope", [], TOILETTE,
                 standalone="Describe Woman at Her Toilette brushwork."),
        ]
        suite = suite_for(requests)
        calls = []
        def runtime(dataset, **kwargs):
            calls.append(kwargs)
            query = kwargs["query"]
            if query == requests[0]["standalone_query"]:
                predictions = [self.prediction(query, query, "medium", "supported", [MEDIUM])]
            else:
                predictions = [self.prediction(query, query, "brushwork", "out_of_scope", [], TOILETTE)]
            return self.runtime(query, predictions)
        with patch.object(evaluator, "retrieve_with_trace", side_effect=runtime):
            report = evaluator.evaluate(self.dataset, suite, mode="isolated")
        self.assertEqual(report["summary"]["strict_query_success_rate"], 1)
        self.assertEqual(report["attribution_policy"], "oracle_standalone_call")
        self.assertEqual(len(calls), 4)
        for call in calls:
            self.assertEqual(set(call), {"artwork_id", "query", "dataset_version", "top_k"})
            self.assertEqual(call["artwork_id"], BEDROOM)
            self.assertEqual(call["top_k"], 2)
            self.assertIn(call["query"], {r["standalone_query"] for r in requests})

    def test_gold_validation_rejects_ambiguous_spans_pairs_and_leaked_scope(self):
        valid = suite_for([gold("medium", "What medium?", "medium", "supported", [MEDIUM])])
        mutations = [
            lambda s: s["cases"][0].update(query="What medium? What medium?"),
            lambda s: s["cases"][0]["requests"][0].update(text="absent"),
            lambda s: s["cases"][1]["requests"][0].update(facet="different"),
            lambda s: s["cases"][0]["requests"][0].update(chunk_ids=[MEDIUM, MEDIUM]),
            lambda s: s["cases"][0]["requests"][0].update(chunk_ids=[TOILETTE + "-medium"]),
            lambda s: s["cases"][0]["requests"][0].update(status="insufficient_evidence"),
            lambda s: s["cases"][0]["requests"][0].update(status="out_of_scope", chunk_ids=[]),
            lambda s: s["cases"][0]["requests"].append(copy.deepcopy(s["cases"][0]["requests"][0])),
            lambda s: s.update(extra=True),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                suite = copy.deepcopy(valid)
                mutation(suite)
                with self.assertRaises(evaluator.SuiteValidationError):
                    evaluator.validate_suite(suite, self.dataset)

    def test_load_suite_rejects_duplicate_json_properties(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text('{"suite_id":"one","suite_id":"two"}', encoding="utf-8")
            with self.assertRaises(evaluator.SuiteValidationError):
                evaluator.load_suite(path, self.dataset)

    def test_cli_outputs_provenance_and_protects_inputs(self):
        suite = suite_for([gold("medium", "What medium?", "medium", "supported", [MEDIUM])])
        query = suite["cases"][0]["query"]
        response = self.runtime(query, [self.prediction(query, query, "medium", "supported", [MEDIUM])])
        with tempfile.TemporaryDirectory() as directory:
            path, output = Path(directory) / "suite.json", Path(directory) / "report.json"
            path.write_text(json.dumps(suite), encoding="utf-8")
            with patch.object(evaluator, "retrieve_with_trace", return_value=response), patch("sys.stderr", new=io.StringIO()):
                self.assertEqual(evaluator.main(["--suite", str(path), "--output", str(output)]), 0)
                report = json.loads(output.read_text(encoding="utf-8"))
                self.assertIn("evaluator_sha256", report["provenance"])
                self.assertEqual(report["mode"], "full")
                self.assertEqual(evaluator.main(["--suite", str(path), "--output", str(path)]), 2)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), suite)


if __name__ == "__main__":
    unittest.main()
