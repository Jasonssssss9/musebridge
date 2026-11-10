"""Evaluate retrieval evidence separately from answer generation."""

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import platform
import sys
import tempfile
import unicodedata

from knowledge import DatasetValidationError, load_dataset, retrieve
from knowledge import dataset as dataset_module
from knowledge import query as query_module
from knowledge import retrieval as retrieval_module

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEED = ROOT / "knowledge/data/aic-seed-v0.1.json"
DEFAULT_SUITE = ROOT / "eval/aic-retrieval-v0.1.json"
CATEGORIES = frozenset({
    "paraphrase", "semantic_gap", "multi_topic",
    "lexical_confusion", "scope_confusion", "evidence_gap",
})
LANGUAGES = frozenset({"en", "zh-CN"})
ANSWERABILITY = frozenset({"full", "partial", "none"})
CASE_FIELDS = frozenset({
    "case_id", "pair_id", "category", "query_language", "artwork_id",
    "query", "answerability", "relevant_chunk_ids", "rationale",
})


class SuiteValidationError(ValueError):
    """An evaluation suite does not match its dataset or annotation schema."""


def _fail(message):
    raise SuiteValidationError(message)


def _object(value, fields, label):
    if not isinstance(value, dict) or set(value) != fields:
        _fail(f"{label} must be an object with exactly {sorted(fields)}.")


def _string(value, label):
    if not isinstance(value, str) or not value.strip():
        _fail(f"{label} must be a nonempty string.")
    if any(
        unicodedata.category(char) == "Cs"
        or (unicodedata.category(char) == "Cc" and char not in "\t\n\r")
        for char in value
    ):
        _fail(f"{label} contains invalid characters.")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail(f"Duplicate JSON property: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    _fail(f"Invalid JSON constant: {value}")


def validate_suite(suite, dataset):
    """Validate gold references and pairing without guessing semantic labels."""
    _object(suite, {"suite_id", "dataset_version", "description", "cases"}, "Suite")
    for field in ("suite_id", "dataset_version", "description"):
        _string(suite[field], field)
    if suite["dataset_version"] != dataset.dataset_version:
        _fail("Suite and dataset versions differ.")
    cases = suite["cases"]
    if not isinstance(cases, list) or not cases:
        _fail("cases must be a nonempty array.")
    chunks = {chunk["chunk_id"]: chunk for chunk in dataset.chunks}
    case_ids = set()
    pairs = defaultdict(list)
    for index, case in enumerate(cases):
        label = f"cases[{index}]"
        _object(case, CASE_FIELDS, label)
        for field in CASE_FIELDS - {"relevant_chunk_ids"}:
            _string(case[field], f"{label}.{field}")
        if case["case_id"] in case_ids:
            _fail("Case IDs must be unique.")
        case_ids.add(case["case_id"])
        if case["category"] not in CATEGORIES or case["query_language"] not in LANGUAGES:
            _fail(f"{label} has an unsupported category or language.")
        if case["answerability"] not in ANSWERABILITY:
            _fail(f"{label} has an unsupported answerability label.")
        if case["artwork_id"] not in dataset.artworks:
            _fail(f"{label} references an unknown artwork.")
        if len(case["query"].strip()) > 1000:
            _fail(f"{label}.query exceeds the retrieval query limit.")
        relevant = case["relevant_chunk_ids"]
        if not isinstance(relevant, list):
            _fail(f"{label}.relevant_chunk_ids must be an array.")
        for chunk_id in relevant:
            _string(chunk_id, f"{label}.relevant_chunk_ids")
            if chunk_id not in chunks or chunks[chunk_id]["artwork_id"] != case["artwork_id"]:
                _fail(f"{label} has an unknown or out-of-scope gold chunk.")
        if len(set(relevant)) != len(relevant):
            _fail(f"{label} repeats a gold chunk.")
        if (case["answerability"] == "none") != (len(relevant) == 0):
            _fail(f"{label}: full/partial need evidence; none must have empty gold.")
        pairs[case["pair_id"]].append(case)
    for pair_id, pair in pairs.items():
        if len(pair) != 2 or {case["query_language"] for case in pair} != LANGUAGES:
            _fail(f"{pair_id} must contain exactly one English and one Chinese case.")
        first, second = pair
        for field in ("category", "artwork_id", "answerability"):
            if first[field] != second[field]:
                _fail(f"{pair_id} differs in {field}.")
        if set(first["relevant_chunk_ids"]) != set(second["relevant_chunk_ids"]):
            _fail(f"{pair_id} must use the same semantic gold in both languages.")
    return suite


def load_suite(path, dataset):
    try:
        raw = Path(path).read_text(encoding="utf-8")
        suite = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except SuiteValidationError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise SuiteValidationError(f"Cannot load evaluation suite: {exc}") from exc
    return validate_suite(suite, dataset)


def _projection(chunk, source):
    evidence = {key: value for key, value in chunk.items() if key != "topics"}
    for field in ("source_title", "source_url", "source_language"):
        evidence[field] = source[field]
    return evidence


def _inspect_response(response, dataset, artwork_id, top_k):
    """Reject contract violations before giving any retrieval credit."""
    issues, ids, violations = set(), [], []
    if not isinstance(response, dict):
        return ids, violations, ["invalid_envelope"]
    if response.get("dataset_version") != dataset.dataset_version:
        issues.add("dataset_version_mismatch")
    evidence = response.get("evidence")
    if not isinstance(evidence, list):
        return ids, violations, sorted(issues | {"invalid_evidence_list"})
    if len(evidence) > top_k:
        issues.add("result_limit_exceeded")
    chunks = {chunk["chunk_id"]: chunk for chunk in dataset.chunks}
    for item in evidence:
        if not isinstance(item, dict) or not isinstance(item.get("chunk_id"), str):
            issues.add("invalid_evidence")
            continue
        chunk_id = item["chunk_id"]
        ids.append(chunk_id)
        chunk = chunks.get(chunk_id)
        if item.get("artwork_id") != artwork_id or (
            chunk is not None and chunk["artwork_id"] != artwork_id
        ):
            violations.append(chunk_id)
            issues.add("wrong_artwork")
        if chunk is None:
            issues.add("unknown_chunk")
            continue
        expected = _projection(chunk, dataset.sources[chunk["source_id"]])
        if any(key not in item or item[key] != value for key, value in expected.items()):
            issues.add("invalid_evidence")
    if len(ids) != len(set(ids)):
        issues.add("duplicate_chunk")
    return ids, violations, sorted(issues)


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def summarize(results, top_k):
    """Macro positive metrics, separate negative false positives, explicit coverage."""
    valid = [result for result in results if not result["issues"]]
    positives = [result for result in valid if result["relevant_chunk_ids"]]
    negatives = [result for result in valid if not result["relevant_chunk_ids"]]
    false_positives = sum(result["negative_false_positive"] for result in negatives)
    count = len(positives)
    return {
        "case_count": len(results),
        "valid_case_count": len(valid),
        "invalid_case_count": len(results) - len(valid),
        "positive_case_count": count,
        "negative_case_count": len(negatives),
        "hit_rate_at_k": _ratio(sum(r["metrics"]["hit_at_k"] for r in positives), count),
        "precision_at_k": _ratio(sum(len(r["matched_chunk_ids"]) for r in positives), count * top_k),
        "recall_at_k": _ratio(sum(r["metrics"]["recall_at_k"] for r in positives), count),
        "mrr_at_k": _ratio(sum(r["metrics"]["reciprocal_rank"] for r in positives), count),
        "all_relevant_retrieved_rate_at_k": _ratio(
            sum(r["metrics"]["all_relevant_found"] for r in positives), count,
        ),
        "positive_returned_precision": _ratio(
            sum(len(r["matched_chunk_ids"]) for r in positives),
            sum(len(r["retrieved_chunk_ids"]) for r in positives),
        ),
        "negative_false_positive_count": false_positives,
        "negative_false_positive_rate": _ratio(false_positives, len(negatives)),
        "scope_violation_count": sum(len(r["scope_violation_chunk_ids"]) for r in results),
    }


def evaluate(dataset, suite, *, top_k=2):
    if type(top_k) is not int or not 1 <= top_k <= 5:
        raise SuiteValidationError("top_k must be an integer from 1 to 5.")
    validate_suite(suite, dataset)
    results = []
    for case in suite["cases"]:
        result = {key: case[key] for key in (
            "case_id", "pair_id", "category", "query_language", "artwork_id", "query",
            "answerability",
        )}
        gold = list(case["relevant_chunk_ids"])
        result.update(
            relevant_chunk_ids=gold, retrieved_chunk_ids=[],
            matched_chunk_ids=[], missed_chunk_ids=[], unexpected_chunk_ids=[],
            scope_violation_chunk_ids=[], issues=[], metrics=None,
            negative_false_positive=None,
        )
        try:
            response = retrieve(
                dataset, artwork_id=case["artwork_id"], query=case["query"],
                dataset_version=suite["dataset_version"], top_k=top_k,
            )
        except Exception:
            result["issues"] = ["retrieval_error"]
        else:
            ids, violations, issues = _inspect_response(
                response, dataset, case["artwork_id"], top_k,
            )
            result.update(
                retrieved_chunk_ids=ids, scope_violation_chunk_ids=violations, issues=issues,
            )
            if not issues:
                matched = [chunk_id for chunk_id in ids if chunk_id in gold]
                result.update(
                    matched_chunk_ids=matched,
                    missed_chunk_ids=[chunk_id for chunk_id in gold if chunk_id not in ids],
                    unexpected_chunk_ids=[chunk_id for chunk_id in ids if chunk_id not in gold],
                )
                if gold:
                    first_rank = next(
                        (rank for rank, chunk_id in enumerate(ids, 1) if chunk_id in gold), None
                    )
                    result["metrics"] = {
                        "hit_at_k": int(bool(matched)),
                        "precision_at_k": len(matched) / top_k,
                        "recall_at_k": len(matched) / len(gold),
                        "reciprocal_rank": 1 / first_rank if first_rank else 0.0,
                        "all_relevant_found": set(gold).issubset(ids),
                    }
                else:
                    result["negative_false_positive"] = bool(ids)
        results.append(result)
    report = {
        "suite_id": suite["suite_id"],
        "dataset_version": dataset.dataset_version,
        "top_k": top_k,
        "summary": summarize(results, top_k),
        "results": results,
    }
    for key, field in (
        ("by_language", "query_language"),
        ("by_category", "category"),
        ("by_answerability", "answerability"),
    ):
        report[key] = {
            label: summarize([r for r in results if r[field] == label], top_k)
            for label in sorted({r[field] for r in results})
        }
    return report


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_report(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n",
            dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False,
        ) as output:
            temporary = Path(output.name)
            output.write(text)
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--top-k", type=int, choices=range(1, 6), default=2)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        inputs = [args.seed, args.suite, Path(__file__),
                  Path(retrieval_module.__file__), Path(dataset_module.__file__), Path(query_module.__file__)]
        if args.output and args.output.resolve() in {path.resolve() for path in inputs}:
            raise SuiteValidationError("The report path must not overwrite an input or implementation file.")
        dataset = load_dataset(args.seed)
        suite = load_suite(args.suite, dataset)
        report = evaluate(dataset, suite, top_k=args.top_k)
        report["provenance"] = {
            "python_version": platform.python_version(),
            "unicode_version": unicodedata.unidata_version,
            "seed_sha256": _sha256(args.seed),
            "suite_sha256": _sha256(args.suite),
            "retrieval_sha256": _sha256(retrieval_module.__file__),
            "query_sha256": _sha256(query_module.__file__),
            "loader_sha256": _sha256(dataset_module.__file__),
            "evaluator_sha256": _sha256(__file__),
        }
        encoded = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output:
            _write_report(args.output, encoded)
            print(json.dumps({
                "report": str(args.output),
                "top_k": args.top_k,
                "summary": report["summary"],
            }, ensure_ascii=False), file=sys.stderr)
        else:
            print(encoded, end="")
    except (DatasetValidationError, SuiteValidationError, OSError, UnicodeError) as exc:
        print(f"Evaluation failed: {exc}", file=sys.stderr)
        return 2
    return 1 if report["summary"]["invalid_case_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
