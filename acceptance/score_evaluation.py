"""Summarize public and private observations separately from independent judgments."""
import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from math import ceil
from pathlib import Path

from acceptance.evaluation import check_independent_coverage, development_schedule, schedule_documents
from acceptance.reviewed_suite import digest, frozen_inputs, suite_directory
from acceptance.quality_gates import LIMITS_MS, quality_checklist
from agent.testing.m2_smoke import ROOT, save


# Null means missing review. "n/a" excludes an inapplicable dimension while
# preserving its explicit distinction from an unreviewed turn.
JUDGMENTS = ("actual_language", "faithful_intent_and_route", "faithful_queries", "whole_question",
             "mixed_question", "no_unsupported_claims", "citations_entail_claims",
             "conversational_usefulness", "memory_continuity", "explicit_language_switch")


def load_runs(paths):
    records, identity = {}, None
    for directory in paths:
        meta = json.loads((directory / "run.json").read_text(encoding="utf-8"))
        run_id = {k: meta[k] for k in ("suite_sha256", "source_sha256", "identity", "tokenizer_sha256", "evaluator_sha256", "review_sha256")}
        if identity is not None and identity != run_id:
            raise ValueError("Reports do not identify the same frozen suite and runtime.")
        identity = run_id
        for line in (directory / "turns.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row["id"] in records:
                raise ValueError("Duplicate trial ID. Keep repeats in their designated group.")
            records[row["id"]] = row
    return records, identity


def result_digest(row):
    return sha256(json.dumps(row, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def template(rows):
    return {key: {"result_sha256": result_digest(row), "reviewer": "", "notes": "",
                  "judgments": dict.fromkeys(JUDGMENTS), "supported_parts": None, "completed_supported_parts": None}
            for key, row in rows.items() if row["kind"] == "message"}


def check_review(rows, reviews):
    for key, item in reviews.items():
        if (key not in rows or rows[key]["kind"] != "message"
                or item.get("result_sha256") != result_digest(rows[key])):
            raise ValueError("A semantic review does not match a recorded trial.")
        values = item["judgments"]
        if set(values) != set(JUDGMENTS) or any(v is not None and type(v) is not bool and v != "n/a" for v in values.values()):
            raise ValueError("Use true, false, null or n/a for each review dimension.")
        total, done = item["supported_parts"], item["completed_supported_parts"]
        if (total is not None or done is not None) and not (type(total) is int and type(done) is int and 0 <= done <= total):
            raise ValueError("Supported-part counts must be a valid numerator and denominator.")
        if rows[key]["outcome"] != "delivered" and (done not in (None, 0) or values["whole_question"] is True):
            raise ValueError("An undelivered reply cannot complete a factual part or the whole question.")
        gold_sources = rows[key].get("expected", {}).get("citations", {})
        if total == 0 and (gold_sources.get("required") or gold_sources.get("any_of")):
            raise ValueError("A source-required trial needs an answerable factual-part denominator.")
        if (any(v is not None for v in values.values()) or total is not None) and not item.get("reviewer", "").strip():
            raise ValueError("A judgment needs an attributable reviewer.")


def fraction(values):
    true = sum(v is True for v in values)
    false = sum(v is False for v in values)
    return {"passed": true, "failed": false, "unobserved": sum(v is None for v in values),
            "not_applicable": sum(v == "n/a" for v in values), "assessed": true + false,
            "scheduled": len(values)}


def summarize(rows, reviews):
    messages = [r for r in rows if r["kind"] == "message"]
    answered = [r for r in messages if r["outcome"] == "delivered"]
    observed = {r["id"]: {**r.get("checks", {}), **r.get("trace", {}).get("checks", {})} for r in messages}
    dimensions = {"scope_and_revision", "committed_history", "status", "citation_ids", "answer_language_field",
                  "saved_preference", "auto_language", "trace_binding", "observed_model_calls",
                  "observed_retrieval_calls", "route", "request_kinds", "retrieval_policy", "pending_label"}
    dimensions.update(key for checks in observed.values() for key in checks)
    machine = {}
    for name in sorted(dimensions):
        label = {"route": "routes", "request_kinds": "request_kinds"}.get(name)
        samples = []
        for row in messages:
            absent = "n/a" if label and label not in row["expected"] else None
            samples.append(observed[row["id"]].get(name, absent))
        machine[name] = fraction(samples)
    semantic = {k: fraction([reviews.get(r["id"], {}).get("judgments", {}).get(k) for r in messages])
                for k in JUDGMENTS}
    parts = [reviews[r["id"]] for r in messages if reviews.get(r["id"], {}).get("supported_parts") is not None]
    latencies = defaultdict(list)
    for row in answered:
        kind = row.get("trace", {}).get("latency_class")
        if kind in LIMITS_MS:
            latencies[kind].append(row["duration_ms"])
    timing = {}
    for key, limit in LIMITS_MS.items():
        values = sorted(latencies[key])
        p95 = values[ceil(.95 * len(values))-1] if values else None
        timing[key] = {"samples": len(values), "p95_ms": p95, "target_ms": limit,
                       "within_target": None if p95 is None else p95 <= limit}
    return {"scheduled_messages": len(messages), "messages_recorded": sum(r["outcome"] != "not_run" for r in messages), "delivered": len(answered), "outcomes": dict(Counter(r["outcome"] for r in rows)),
            "machine": machine, "semantic": semantic, "supported_parts": {
                "turns_reviewed": len(parts), "completed": sum(v["completed_supported_parts"] for v in parts),
                "total": sum(v["supported_parts"] for v in parts)},
            "public_warm_latency": timing,
            "unclassified_or_undelivered_latency": len(messages) - sum(map(len, latencies.values()))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--suite-root", type=Path, help="External suite used for independent runs")
    parser.add_argument("--reviews", type=Path)
    parser.add_argument("--template", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    opts = parser.parse_args()
    for dest in (opts.output, opts.template):
        if dest and (dest.resolve().is_relative_to(ROOT) or dest.exists()):
            parser.error("Write to a new file in a private directory outside the checkout.")
    if opts.template and opts.output.resolve() == opts.template.resolve():
        parser.error("Use different paths for the review template and summary.")
    rows, identity = load_runs(opts.runs)
    development = identity["suite_sha256"] is None
    if development:
        selection = development_schedule()
    else:
        try:
            directory = suite_directory(opts.suite_root)
            _, documents = frozen_inputs(directory, scored_manifest=identity["suite_sha256"])
            check_independent_coverage(*documents)
        except (OSError, ValueError) as error:
            parser.error(str(error))
        selection = schedule_documents(*documents)
    # Keep the full denominator visible even when only one batch was supplied.
    planned = {f"{g}/{key}/step-{s['step']}": {
        "id": f"{g}/{key}/step-{s['step']}", "group": g, "artwork_id": s["artwork_id"],
        "category": category, "kind": s["kind"], "input_language": s.get("language"),
        "expected": s["expect"], "outcome": "not_run", "language_override": s.get("language_override"),
        "instruction_label": s.get("instruction"), "question": s.get("text"), "visitor_intent": s.get("intent"),
        "browser_action": s.get("action"),
    } for g, key, category, steps in selection for s in steps}
    if rows.keys() - planned.keys():
        parser.error("The input contains trial IDs outside the current suite.")
    for key, recorded in rows.items():
        fields = ("group", "artwork_id", "category", "kind", "expected", "input_language", "language_override",
                  "instruction_label", "question", "visitor_intent")
        if recorded["kind"] == "browser" and recorded["outcome"] != "blocked":
            fields = (*fields, "browser_action")
        if any(recorded.get(field) != planned[key].get(field) for field in fields):
            parser.error("Recorded trial metadata differs from the authenticated schedule.")
    reviews = json.loads(opts.reviews.read_text(encoding="utf-8")) if opts.reviews else {}
    check_review(rows, reviews)
    if opts.template:
        save(opts.template, template(rows))
    missing = sorted(planned.keys() - rows.keys())
    all_rows = {**planned, **rows}
    slices = {}
    for dimension, field in (("group", "group"), ("scenario_category", "category"),
                             ("input_language", "input_language"), ("artwork", "artwork_id")):
        grouped = defaultdict(list)
        for row in all_rows.values():
            if row["kind"] == "message":
                grouped[row[field]].append(row)
        slices[dimension] = {name: summarize(group, reviews) for name, group in grouped.items()}
    for name, extractor in (("expected_reply_language", lambda r: r["expected"]["answer_language"]),
                            ("observed_route", lambda r: r.get("trace", {}).get("route") or "unobserved")):
        groups = defaultdict(list)
        for row in all_rows.values():
            if row["kind"] == "message":
                groups[extractor(row)].append(row)
        slices[name] = {key: summarize(subset, reviews) for key, subset in groups.items()}
    report = {"study": "development" if development else "frozen_independent", "candidate": identity, "planned_steps": len(planned), "missing_steps": missing,
              "overall": summarize(list(all_rows.values()), reviews), "breakdown": slices,
              "release_targets": quality_checklist(list(all_rows.values()), reviews, development),
              "independent_browser_signoff": "requires separate browser observation",
              "release_decision": "requires team review"}
    report["scorer_sha256"] = {name: digest(Path(__file__).with_name(name))
                               for name in ("score_evaluation.py", "quality_gates.py")}
    save(opts.output, report)
    print(json.dumps({"planned_steps": len(planned), "recorded": len(rows), "delivered": report["overall"]["delivered"]}))


if __name__ == "__main__":
    main()
