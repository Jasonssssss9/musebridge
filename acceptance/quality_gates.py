"""Evidence for the section 13.3 thresholds. This report cannot activate the service."""
from math import ceil

LANGUAGES = ("en", "es", "zh-CN", "ko")
LIMITS_MS = {"direct": 12000, "retrieval_or_reuse": 30000, "refined": 50000}


def threshold(samples, percent):
    passed = sum(v is True for v in samples)
    failed = sum(v is False for v in samples)
    missing = sum(v is None for v in samples)
    eligible = passed + failed + missing
    # Even perfect answers on every missing trial could not erase these failures.
    if eligible and (passed + missing) * 100 < percent * eligible:
        status = "observed_failure"
    elif not eligible or missing:
        status = "pending_evidence"
    else:
        status = "observed_target_met"
    return {"target_percent": percent, "passed": passed, "failed": failed, "unobserved": missing,
            "not_applicable": len(samples) - eligible, "scheduled": len(samples), "status": status}


def together(*values):
    if any(v is False for v in values):
        return False
    return True if all(v is True for v in values) else None


def quality_checklist(rows, reviews, development):
    messages = [r for r in rows if r["kind"] == "message"]

    def checks(row):
        return {**row.get("checks", {}), **row.get("trace", {}).get("checks", {})}

    def judged(row, key, required=True):
        value = reviews.get(row["id"], {}).get("judgments", {}).get(key)
        if not required and value == "n/a":
            return "n/a"
        if row["outcome"] == "not_run":
            return None
        if row["outcome"] != "delivered":
            # These are visitor outcome metrics. A failed/blocked message
            # cannot deliver a correct reply. Grounding or model failure cause
            # requires actual observations and is evaluated separately below.
            return False if required or value is not None else None
        return value if type(value) is bool else None

    def first_pass(row):
        trace = row.get("trace", {})
        if "generation_invalid_response" in trace.get("failure_codes", []):
            return False
        if row["outcome"] != "delivered":
            return None
        return together(checks(row).get("trace_binding"), checks(row).get("observed_model_calls"))

    def language(row):
        c = checks(row)
        return together(judged(row, "actual_language"), c.get("answer_language_field"),
                        c.get("saved_preference"), c.get("auto_language"))

    def routing(row):
        c = checks(row)
        query = judged(row, "faithful_queries", required=False)
        generated = bool(row.get("trace", {}).get("evidence", {}).get("result", {}).get("executed_queries"))
        # A successful empty-lookup policy still requires that a generated
        # query preserve the visitor meaning. Exemption applies only without one.
        fidelity = True if query == "n/a" and not generated and c.get("trace_binding") else query
        return together(judged(row, "faithful_intent_and_route"), fidelity,
                        c.get("route") if "routes" in row["expected"] else True)

    def language_table(measure, target, field):
        result = {"overall": threshold([measure(r) for r in messages], target)}
        for language_code in LANGUAGES:
            subset = [r for r in messages if field(r) == language_code]
            result[language_code] = threshold([measure(r) for r in subset], target)
        return result

    def explicit_switch(row):
        designated = row.get("language_override") is not None or bool(row.get("instruction_label"))
        choice = judged(row, "explicit_language_switch", required=designated)
        if choice == "n/a":
            return choice
        return together(choice, language(row))

    def marked_grounded(row):
        if row["outcome"] != "delivered":
            return None
        return together(judged(row, "no_unsupported_claims"), judged(row, "citations_entail_claims"),
                        checks(row).get("citation_ids"))

    def supported_parts():
        complete = count = covered = 0
        for row in messages:
            item = reviews.get(row["id"], {})
            total = item.get("supported_parts")
            if total is not None:
                complete += item["completed_supported_parts"]
                count += total
                covered += 1
        if covered != len(messages) or not count:
            status = "pending_evidence"
        else:
            status = "observed_target_met" if complete * 100 >= 95 * count else "observed_failure"
        return {"target_percent": 95, "completed_parts": complete, "answerable_parts": count,
                "trials_with_reviewed_denominator": covered, "scheduled_trials": len(messages), "status": status}

    def timing():
        per_route = {}
        unknown = len(messages)
        for route, limit in LIMITS_MS.items():
            times = sorted(r["duration_ms"] for r in messages if r["outcome"] == "delivered"
                           and r.get("trace", {}).get("latency_class") == route)
            unknown -= len(times)
            p95 = times[ceil(.95 * len(times)) - 1] if times else None
            per_route[route] = {"target_ms": limit, "observed_p95_ms": p95, "classified_samples": len(times)}
        for row in per_route.values():
            if not row["classified_samples"] or unknown:
                row["status"] = "pending_evidence"
            else:
                row["status"] = ("observed_target_met" if row["observed_p95_ms"] <= row["target_ms"]
                                 else "observed_failure")
        return {"paths": per_route, "unclassified_or_undelivered": unknown,
                "hard_deadlines": "requires the separate deadline and failure-trace checks"}

    result = {
        "study": "development_rehearsal" if development else "frozen_independent",
        "model_first_pass_by_visitor_language": language_table(first_pass, 98, lambda r: r["input_language"]),
        "language_and_preference_by_expected_reply": language_table(language, 98, lambda r: r["expected"].get("answer_language")),
        "language_and_preference_by_visitor_language": language_table(language, 98, lambda r: r["input_language"]),
        "designated_language_changes": threshold([explicit_switch(r) for r in messages], 100),
        "routing_and_query_by_visitor_language": language_table(routing, 95, lambda r: r["input_language"]),
        "supported_request_completion": supported_parts(),
        "whole_question": threshold([judged(r, "whole_question") for r in messages], 90),
        "mixed_question": threshold([judged(r, "mixed_question", required=(r["category"] == "mixed" or
                                      r["expected"].get("status") == "partially_answered")) for r in messages], 90),
        "no_unsupported_claim_or_false_citation": threshold([marked_grounded(r) for r in messages], 100),
        "conversation_and_gap_continuation": threshold([judged(r, "conversational_usefulness", required=(
            r["category"] == "direct_conversation" or r["expected"].get("status") == "conversational"))
            for r in messages], 90),
        "state_semantics": threshold([together(judged(r, "memory_continuity"), checks(r).get("pending_label"))
                                      for r in messages], 95),
        "observed_revision_scope": threshold([together(checks(r).get("scope_and_revision"),
            checks(r).get("committed_history")) if r["outcome"] == "delivered"
            else checks(r).get("failure_state_unchanged") for r in messages], 100),
        "warm_latency": timing(),
        "deterministic_fixture_suite": "requires separately identified integration and regression results",
        "independent_browser_approval": "requires separate attributable browser observation",
        "formal_release_authorized": False,
    }
    return result
