"""Evaluate attributed requests over the frozen four-chunk museum dataset.

Full mode evaluates segmentation, interpretation and evidence together. Isolated
mode supplies reviewed standalone wording and scores each call as one gold unit;
its oracle attribution is a diagnostic, not an end-to-end improvement.
"""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import platform
import sys
import unicodedata

from knowledge import DatasetValidationError, load_dataset
from knowledge import dataset as dataset_module
from knowledge import retrieval as retrieval_module
from .retrieval_eval import (
    DEFAULT_SEED, SuiteValidationError, _fail, _inspect_response, _object,
    _ratio, _reject_constant, _sha256, _string, _unique_object, _write_report,
)

CASE_FIELDS = {"case_id", "pair_id", "query_language", "artwork_id", "query", "requests"}
REQUEST_FIELDS = {
    "request_id", "text", "artwork_id", "facet", "status", "chunk_ids",
    "standalone_query", "rationale",
}
TRACE_FIELDS = {"start", "end", "artwork_id", "facet", "status", "chunk_ids"}
GOLD_STATUSES = {"supported", "insufficient_evidence", "out_of_scope"}
TRACE_STATUSES = GOLD_STATUSES | {"unknown_intent"}
LANGUAGES = {"en", "zh-CN"}
TOP_K = 2


def normalize(text):
    return " ".join(unicodedata.normalize("NFKC", text).split())


def retrieve_with_trace(*args, **kwargs):
    """Late lookup keeps evaluation importable before a trace implementation exists."""
    return retrieval_module.retrieve_with_trace(*args, **kwargs)


def _gold_span(case, request):
    query, text = normalize(case["query"]), normalize(request["text"])
    start = query.index(text)
    return start, start + len(text)


def _ids(value, known, label):
    if not isinstance(value, list):
        _fail(f"{label} must be an array.")
    for item in value:
        _string(item, label)
        if item not in known:
            _fail(f"{label} contains an unknown chunk ID.")
    if len(value) != len(set(value)):
        _fail(f"{label} contains duplicate chunk IDs.")


def validate_suite(suite, dataset):
    _object(suite, {"suite_id", "dataset_version", "description", "cases"}, "Suite")
    for field in ("suite_id", "dataset_version", "description"):
        _string(suite[field], field)
    if suite["dataset_version"] != dataset.dataset_version:
        _fail("Suite and dataset versions differ.")
    if not isinstance(suite["cases"], list) or not suite["cases"]:
        _fail("cases must be a nonempty array.")
    chunks = {chunk["chunk_id"]: chunk for chunk in dataset.chunks}
    pairs, case_ids = defaultdict(list), set()
    for case in suite["cases"]:
        _object(case, CASE_FIELDS, "Case")
        for field in CASE_FIELDS - {"requests"}:
            _string(case[field], field)
        if case["case_id"] in case_ids:
            _fail("Case IDs must be unique.")
        case_ids.add(case["case_id"])
        if case["query_language"] not in LANGUAGES or case["artwork_id"] not in dataset.artworks:
            _fail("Case has an unknown language or artwork.")
        if len(case["query"].strip()) > 1000:
            _fail("Query exceeds the retrieval limit.")
        if not isinstance(case["requests"], list) or not case["requests"]:
            _fail("requests must be a nonempty array.")
        request_ids, semantic_spans = set(), set()
        query = normalize(case["query"])
        for request in case["requests"]:
            _object(request, REQUEST_FIELDS, "Gold request")
            for field in REQUEST_FIELDS - {"chunk_ids"}:
                _string(request[field], field)
            if request["request_id"] in request_ids:
                _fail("Request IDs must be unique within a case.")
            request_ids.add(request["request_id"])
            text = normalize(request["text"])
            # find(..., start + 1) also rejects overlapping repeated occurrences.
            start = query.find(text)
            if start < 0 or query.find(text, start + 1) >= 0:
                _fail("Gold text must match exactly one normalized query substring.")
            if len(request["standalone_query"].strip()) > 1000:
                _fail("Standalone query exceeds the retrieval limit.")
            if request["status"] not in GOLD_STATUSES or request["artwork_id"] not in dataset.artworks:
                _fail("Gold request has an unknown status or artwork.")
            _ids(request["chunk_ids"], chunks, "Gold chunk_ids")
            if (request["status"] == "supported") != bool(request["chunk_ids"]):
                _fail("Only supported gold requests have nonempty chunk_ids.")
            outside = request["artwork_id"] != case["artwork_id"]
            if (request["status"] == "out_of_scope") != outside:
                _fail("out_of_scope must identify a different artwork; other statuses use the active work.")
            if any(chunks[c]["artwork_id"] != case["artwork_id"] for c in request["chunk_ids"]):
                _fail("Gold evidence must belong to the active artwork.")
            identity = (start, start + len(text), request["artwork_id"], request["facet"])
            if identity in semantic_spans:
                _fail("Duplicate semantic gold request for the same span, subject and facet.")
            semantic_spans.add(identity)
        pairs[case["pair_id"]].append(case)
    for pair_id, pair in pairs.items():
        if len(pair) != 2 or {c["query_language"] for c in pair} != LANGUAGES:
            _fail(f"{pair_id} must contain one English and one Chinese case.")
        def semantics(case):
            return Counter(
                (r["artwork_id"], r["facet"], r["status"], tuple(sorted(r["chunk_ids"])))
                for r in case["requests"]
            )
        if pair[0]["artwork_id"] != pair[1]["artwork_id"] or semantics(pair[0]) != semantics(pair[1]):
            _fail(f"{pair_id} must preserve scope, facets, statuses and evidence across languages.")
    return suite


def load_suite(path, dataset):
    try:
        suite = json.loads(Path(path).read_text(encoding="utf-8"),
                           object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except SuiteValidationError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise SuiteValidationError(f"Cannot load subquestion suite: {exc}") from exc
    return validate_suite(suite, dataset)


def _inspect_trace(trace, query, response_ids, dataset, active_artwork):
    issues = set()
    if not isinstance(trace, dict) or set(trace) != {"normalized_query", "requests"}:
        return [], ["invalid_trace"]
    normalized = normalize(query)
    if trace["normalized_query"] != normalized:
        issues.add("trace_query_mismatch")
    requests = trace["requests"]
    if not isinstance(requests, list):
        return [], sorted(issues | {"invalid_trace_requests"})
    chunks = {chunk["chunk_id"]: chunk for chunk in dataset.chunks}
    attributed = set()
    for request in requests:
        if not isinstance(request, dict) or set(request) != TRACE_FIELDS:
            issues.add("invalid_trace_request")
            continue
        start, end = request["start"], request["end"]
        if (type(start) is not int or type(end) is not int
                or not 0 <= start < end <= len(normalized)):
            issues.add("invalid_trace_span")
        for field in ("artwork_id", "facet", "status"):
            if not isinstance(request[field], str) or not request[field].strip():
                issues.add("invalid_trace_request")
        # Type checks precede set/dict membership so malformed trace data is reportable.
        if not isinstance(request["artwork_id"], str) or request["artwork_id"] not in dataset.artworks:
            issues.add("unknown_trace_artwork")
        if not isinstance(request["status"], str) or request["status"] not in TRACE_STATUSES:
            issues.add("invalid_trace_status")
        ids = request["chunk_ids"]
        if not isinstance(ids, list) or any(not isinstance(c, str) for c in ids):
            issues.add("invalid_trace_chunk_ids")
            continue
        if len(ids) != len(set(ids)) or len(ids) > TOP_K:
            issues.add("invalid_trace_chunk_ids")
        attributed.update(ids)
        if any(c not in chunks or chunks[c]["artwork_id"] != active_artwork for c in ids):
            issues.add("invalid_trace_chunk_scope")
        if any(c not in response_ids for c in ids):
            issues.add("trace_evidence_not_returned")
        if request["status"] == "supported":
            if not ids or request["artwork_id"] != active_artwork:
                issues.add("inconsistent_trace_status")
        elif ids:
            issues.add("inconsistent_trace_status")
        if request["status"] == "out_of_scope" and request["artwork_id"] == active_artwork:
            issues.add("inconsistent_trace_status")
        if request["status"] in ("insufficient_evidence", "unknown_intent") and request["artwork_id"] != active_artwork:
            issues.add("inconsistent_trace_status")
    if attributed != set(response_ids):
        issues.add("unattributed_response_evidence")
    return requests, sorted(issues)


def _call(dataset, case, query):
    try:
        response, trace = retrieve_with_trace(
            dataset, artwork_id=case["artwork_id"], query=query,
            dataset_version=dataset.dataset_version, top_k=TOP_K,
        )
    except Exception:
        return [], [], [], ["retrieval_error"]
    ids, violations, issues = _inspect_response(response, dataset, case["artwork_id"], TOP_K)
    requests, trace_issues = _inspect_trace(trace, query, ids, dataset, case["artwork_id"])
    # Do not serialize malformed runtime objects into a JSON diagnostic report.
    return ids, requests if not trace_issues else [], violations, sorted(set(issues + trace_issues))


def _alignment(case, predicted):
    """Use spans and semantic labels, never returned evidence, for alignment."""
    gold = case["requests"]
    spans = [_gold_span(case, r) for r in gold]
    matches = [[] for _ in gold]
    ambiguous, extra, merges = set(), [], []
    for pi, request in enumerate(predicted):
        candidates = [gi for gi, (start, end) in enumerate(spans)
                      if max(start, request["start"]) < min(end, request["end"])]
        if not candidates:
            extra.append(pi)
            continue
        # Exact subject/facet distinguishes shared comparison and multi-facet spans.
        scores = {
            gi: (int(request["artwork_id"] == gold[gi]["artwork_id"])
                 + int(request["facet"] == gold[gi]["facet"]))
            for gi in candidates
        }
        best = max(scores.values())
        candidates = [gi for gi in candidates if scores[gi] == best]
        if len(candidates) != 1:
            ambiguous.update(candidates)
            merges.append({"predicted_index": pi, "request_ids": [gold[g]["request_id"] for g in candidates]})
        else:
            matches[candidates[0]].append(pi)
    return matches, ambiguous, extra, merges


def _unit_result(gold, predictions, *, alignment, issues=(), oracle=False):
    ids = list(dict.fromkeys(c for p in predictions for c in p["chunk_ids"]))
    unresolved = bool(issues) or alignment in {"missing", "merged"}
    matching_predictions = [
        p for p in predictions
        if oracle or (p["artwork_id"] == gold["artwork_id"] and p["facet"] == gold["facet"])
    ]
    semantic_match = oracle or bool(predictions) and len(matching_predictions) == len(predictions)
    status_match = oracle or bool(predictions) and all(p["status"] == gold["status"] for p in predictions)
    result = {
        "request_id": gold["request_id"], "text": gold["text"],
        "artwork_id": gold["artwork_id"], "facet": gold["facet"], "status": gold["status"],
        "expected_chunk_ids": list(gold["chunk_ids"]), "retrieved_chunk_ids": ids,
        "alignment": alignment, "subject_and_facet_match": bool(semantic_match),
        "status_match": bool(status_match), "matched_chunk_ids": [],
        "missed_chunk_ids": list(gold["chunk_ids"]), "unexpected_chunk_ids": [],
        "metrics": None, "negative_outcome": None, "strict_success": False,
        "issues": list(issues),
    }
    if unresolved:
        if gold["status"] != "supported":
            result["negative_outcome"] = "unresolved"
        return result
    expected = set(gold["chunk_ids"])
    correctly_bound = {c for p in matching_predictions for c in p["chunk_ids"]}
    matched = [c for c in ids if c in expected and c in correctly_bound]
    result.update(
        matched_chunk_ids=matched,
        missed_chunk_ids=[c for c in gold["chunk_ids"] if c not in matched],
        unexpected_chunk_ids=[c for c in ids if c not in expected],
    )
    if gold["status"] == "supported":
        result["metrics"] = {
            "hit": bool(matched), "all_required": expected.issubset(matched),
            "recall": len(matched) / len(expected),
        }
        result["strict_success"] = bool(
            semantic_match and status_match and expected == set(ids)
        )
    else:
        outcome = "false_return" if ids else (
            "correct_empty" if semantic_match and status_match else "unresolved"
        )
        result["negative_outcome"] = outcome
        result["strict_success"] = outcome == "correct_empty"
    return result


def _evaluate_full(dataset, case):
    ids, predictions, violations, issues = _call(dataset, case, case["query"])
    result = {
        "case_id": case["case_id"], "pair_id": case["pair_id"],
        "query_language": case["query_language"], "artwork_id": case["artwork_id"],
        "query": case["query"], "retrieved_chunk_ids": ids, "predicted_requests": predictions,
        "scope_violation_chunk_ids": violations, "issues": issues,
        "extra_predicted_indices": [], "merged_predictions": [], "split_request_ids": [],
    }
    if issues:
        units = [_unit_result(g, [], alignment="invalid", issues=issues) for g in case["requests"]]
    else:
        matches, ambiguous, extra, merges = _alignment(case, predictions)
        result.update(extra_predicted_indices=extra, merged_predictions=merges)
        units = []
        for gi, gold in enumerate(case["requests"]):
            selected = [predictions[pi] for pi in matches[gi]]
            alignment = ("merged" if gi in ambiguous else
                         "missing" if not selected else
                         "split" if len(selected) > 1 else "aligned")
            if alignment == "split":
                result["split_request_ids"].append(gold["request_id"])
            unit = _unit_result(gold, selected, alignment=alignment)
            unit["predicted_indices"] = matches[gi]
            units.append(unit)
    result["requests"] = units
    result["strict_success"] = bool(
        not issues and not result["extra_predicted_indices"]
        and not result["merged_predictions"] and all(r["strict_success"] for r in units)
    )
    return result


def _evaluate_isolated(dataset, case):
    units, all_ids, issues, violations = [], [], [], []
    for gold in case["requests"]:
        ids, predictions, scoped, call_issues = _call(dataset, case, gold["standalone_query"])
        all_ids.extend(ids)
        violations.extend(scoped)
        issues.extend(call_issues)
        # Attribution comes from the separate invocation, not runtime labels.
        unit = _unit_result(gold, [{"chunk_ids": ids}], alignment="oracle",
                            issues=call_issues, oracle=True)
        unit["standalone_query"] = gold["standalone_query"]
        unit["predicted_requests"] = predictions
        units.append(unit)
    return {
        "case_id": case["case_id"], "pair_id": case["pair_id"],
        "query_language": case["query_language"], "artwork_id": case["artwork_id"],
        "query": case["query"], "retrieved_chunk_ids": list(dict.fromkeys(all_ids)),
        "scope_violation_chunk_ids": violations, "issues": sorted(set(issues)),
        "extra_predicted_indices": [], "merged_predictions": [], "split_request_ids": [],
        "requests": units, "strict_success": all(r["strict_success"] for r in units),
    }


def summarize(results):
    units = [r for case in results for r in case["requests"]]
    positives = [r for r in units if r["status"] == "supported"]
    negatives = [r for r in units if r["status"] != "supported"]
    def negatives_summary(items):
        counts = Counter(r["negative_outcome"] for r in items)
        return {
            "count": len(items), "false_return_count": counts["false_return"],
            "correct_empty_count": counts["correct_empty"], "unresolved_count": counts["unresolved"],
            "false_return_rate": _ratio(counts["false_return"], len(items)),
            "correct_empty_rate": _ratio(counts["correct_empty"], len(items)),
            "unresolved_rate": _ratio(counts["unresolved"], len(items)),
        }
    return {
        "case_count": len(results),
        "valid_case_count": sum(not r["issues"] for r in results),
        "invalid_case_count": sum(bool(r["issues"]) for r in results),
        "gold_request_count": len(units), "positive_request_count": len(positives),
        "positive_hit_rate": _ratio(sum(bool(r["metrics"] and r["metrics"]["hit"]) for r in positives), len(positives)),
        "positive_all_required_rate": _ratio(sum(bool(r["metrics"] and r["metrics"]["all_required"]) for r in positives), len(positives)),
        "positive_mean_recall": _ratio(sum(r["metrics"]["recall"] if r["metrics"] else 0 for r in positives), len(positives)),
        "positive_unresolved_count": sum(r["metrics"] is None for r in positives),
        "positive_returned_precision": _ratio(
            sum(len(r["matched_chunk_ids"]) for r in positives if not r["issues"]),
            sum(len(r["retrieved_chunk_ids"]) for r in positives if not r["issues"]),
        ),
        "negative_requests": negatives_summary(negatives),
        "by_negative_status": {
            label: negatives_summary([r for r in negatives if r["status"] == label])
            for label in ("insufficient_evidence", "out_of_scope")
        },
        "strict_query_success_count": sum(r["strict_success"] for r in results),
        "strict_query_success_rate": _ratio(sum(r["strict_success"] for r in results), len(results)),
        "missing_request_count": sum(r["alignment"] == "missing" for r in units),
        "split_request_count": sum(len(r["split_request_ids"]) for r in results),
        "merged_prediction_count": sum(len(r["merged_predictions"]) for r in results),
        "extra_predicted_request_count": sum(len(r["extra_predicted_indices"]) for r in results),
        "scope_violation_count": sum(len(r["scope_violation_chunk_ids"]) for r in results),
    }


def evaluate(dataset, suite, *, mode="full"):
    if mode not in {"full", "isolated"}:
        raise SuiteValidationError("mode must be full or isolated.")
    validate_suite(suite, dataset)
    evaluator = _evaluate_full if mode == "full" else _evaluate_isolated
    results = [evaluator(dataset, case) for case in suite["cases"]]
    return {
        "suite_id": suite["suite_id"], "dataset_version": dataset.dataset_version,
        "mode": mode, "top_k": TOP_K,
        "attribution_policy": "span_subject_facet" if mode == "full" else "oracle_standalone_call",
        "denominator_policy": "All gold requests; missing, ambiguous and invalid units never earn credit.",
        "summary": summarize(results), "results": results,
        "by_language": {
            language: summarize([r for r in results if r["query_language"] == language])
            for language in sorted({r["query_language"] for r in results})
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    parser.add_argument("--mode", choices=("full", "isolated"), default="full")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        inputs = [args.seed, args.suite, Path(__file__), Path(retrieval_module.__file__),
                  Path(dataset_module.__file__), Path(sys.modules[_write_report.__module__].__file__)]
        if args.output and args.output.resolve() in {p.resolve() for p in inputs}:
            raise SuiteValidationError("Report path must not overwrite an input or implementation.")
        dataset = load_dataset(args.seed)
        suite = load_suite(args.suite, dataset)
        report = evaluate(dataset, suite, mode=args.mode)
        report["provenance"] = {
            "python_version": platform.python_version(), "unicode_version": unicodedata.unidata_version,
            "seed_sha256": _sha256(args.seed), "suite_sha256": _sha256(args.suite),
            "retrieval_sha256": _sha256(retrieval_module.__file__),
            "loader_sha256": _sha256(dataset_module.__file__),
            "evaluator_sha256": _sha256(__file__),
            "shared_evaluator_sha256": _sha256(sys.modules[_write_report.__module__].__file__),
        }
        encoded = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output:
            _write_report(args.output, encoded)
            print(json.dumps({"report": str(args.output), "mode": args.mode,
                              "summary": report["summary"]}, ensure_ascii=False), file=sys.stderr)
        else:
            print(encoded, end="")
    except (DatasetValidationError, SuiteValidationError, OSError, UnicodeError) as exc:
        print(f"Subquestion evaluation failed: {exc}", file=sys.stderr)
        return 2
    return 1 if report["summary"]["invalid_case_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
