"""Summarize private M2 runtime evidence without declaring unreviewed quality."""
import argparse
import json
from pathlib import Path

from agent.testing.m2_smoke import ROOT, development_passed, save


def summarize(runtime, browser, labels):
    cases = [case for case in runtime.get("cases", []) if "response" in case]
    failed = [{"case": case["name"], "turn": case["turn"], "request_id": case["response"]["request_id"],
               "http_status": case["response"]["http_status"], "error": (case.get("error") or {}).get("code")}
              for case in cases if not case.get("machine_pass")]
    browser_cases = browser.get("cases", [])
    browser_passed = sum(case.get("pass") is True for case in browser_cases)
    valid_transport = {case["artwork_id"] for case in cases if case["response"]["http_status"] == 200}
    label_preflight = (labels.get("review_kind") == "automated-source-and-annotated-state-preflight"
                       and labels.get("single_scenarios") == 60 and labels.get("single_trials") == 240
                       and labels.get("multi_scripts") == 16 and labels.get("issues") == [])
    gates = {
        "both_artworks_real_public_path": "passed" if valid_transport == {"aic-28560", "aic-11723"} else "incomplete",
        "development_expectations": "passed" if development_passed(runtime) else "failures_recorded",
        "automated_browser": "passed" if browser_cases and browser_passed == len(browser_cases) and not browser.get("fatal_error") else "partial",
        "heldout_label_preflight": "passed" if label_preflight else "incomplete_or_issues_recorded",
        "human_language_and_semantic_reviews": "pending",
        "independent_quality_evaluation": "not_run_unfrozen_suite",
        "candidate_commit_and_team_release_review": "pending",
        "formal_model_capability_gate": "unchanged_not_attested",
    }
    return {"base_commit": runtime.get("base_commit"), "source_manifest_sha256": runtime.get("source_manifest_sha256"),
        "mode": runtime["mode"], "m2_complete": False, "formal_activation_allowed": False, "gates": gates,
        "warm_public_latency": runtime.get("warm_public_latency", {}),
        "scheduled_development_trials": len(runtime.get("cases", [])),
        "blocked_development_trials": sum("blocked" in row for row in runtime.get("cases", [])),
        "development_trials": len(cases), "machine_expectations_passed": sum(bool(case.get("machine_pass")) for case in cases),
        "failed_development_cases": failed, "browser_cases": len(browser_cases), "browser_checks_passed": browser_passed,
        "failure_atomicity": {"checked": sum("failure_atomicity_pass" in case for case in cases),
                              "passed": sum(bool(case.get("failure_atomicity_pass")) for case in cases)},
        "runtime_event_counts": runtime.get("event_counts", {}),
        "replay_checks": {"checked": sum("replay_pass" in case for case in cases), "passed": sum(bool(case.get("replay_pass")) for case in cases)},
        "remaining_owners": {
            "A": ["Review/freeze independently authored questions with language-competent reviewers", "Record independent real-browser visitor acceptance"],
            "B": ["Rerun affected integration checks after C changes", "Commit the reviewed candidate only with owner authorization"],
            "C": ["Diagnose recorded follow-up and mixed-language planning failures", "Run semantic evaluation against the frozen independent suite"],
            "team": ["Review evidence entailment, full-question completion, memory and actual reply language", "Meet agreed quality and warm-latency gates before normal activation"]}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-report", required=True)
    parser.add_argument("--browser-report", required=True)
    parser.add_argument("--label-review", required=True)
    parser.add_argument("--output", required=True)
    options = parser.parse_args()
    output = Path(options.output).resolve()
    if output.is_relative_to(ROOT):
        parser.error("Handoff results must remain outside the repository.")
    report = summarize(*[json.loads(Path(path).read_text(encoding="utf-8-sig")) for path in
        (options.runtime_report, options.browser_report, options.label_review)])
    save(output, report)
    print(json.dumps({"m2_complete": False, "gates": report["gates"], "remaining_owners": report["remaining_owners"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
