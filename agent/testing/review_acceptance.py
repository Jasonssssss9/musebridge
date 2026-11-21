"""Read-only B label preflight; never executes or freezes held-out questions."""
import argparse
import hashlib
import json
from pathlib import Path

LANGUAGES = {"en", "es", "zh-CN", "ko"}
ROOT = Path(__file__).resolve().parents[2]


def audit(suite_root):
    suite_root = Path(suite_root)
    directory = suite_root/"acceptance/visitor-heldout/v0.1"
    single = json.loads((directory/"single-turn.json").read_text())
    multi = json.loads((directory/"multi-turn.json").read_text())
    seed_path = ROOT/"knowledge/data/aic-seed-v0.1.json"
    seed = json.loads(seed_path.read_text())
    sources = {item["chunk_id"]: item["artwork_id"] for item in seed["chunks"]}
    for item in seed["artworks"]:
        sources.update({item["artwork_id"]+"-catalog-"+field: item["artwork_id"] for field in ("title", "artist", "date_display")})
    issues, references, transitions = [], 0, 0

    def check_sources(where, artwork, expected):
        nonlocal references
        citations = expected.get("citations", {})
        ids = list(citations.get("required", []))+list(citations.get("allowed", []))
        ids += [source for group in citations.get("any_of", []) for source in group]
        for source in ids:
            references += 1
            if sources.get(source) != artwork:
                issues.append({"case": where, "check": "citation_scope", "source_id": source})

    for scenario in single["scenarios"]:
        check_sources(scenario["id"], scenario["artwork_id"], scenario["expected"])
    for script in multi["scripts"]:
        states = {}
        for step in script["steps"]:
            if step["kind"] != "message":
                continue
            where = f"{script['id']}/t{step['visitor_turn']}"
            expected, artwork = step["expect"], step["artwork_id"]
            check_sources(where, artwork, expected)
            preference, automatic = states.get(artwork, ("auto", "en"))
            candidate = step["language_override"] if step["language_override"] is not None else preference
            instruction = step.get("instruction") or {}
            effective = instruction.get("language", candidate)
            if step["language"] in LANGUAGES:
                automatic = step["language"]
            answer = automatic if effective == "auto" else effective
            if instruction.get("type") == "setting":
                candidate = instruction["language"]
            actual = {"answer_language": answer, "preference_after": candidate, "auto_language_after": automatic}
            for name, value in actual.items():
                if expected[name] != value:
                    issues.append({"case": where, "check": name, "expected_label": expected[name], "from_annotations": value})
            states[artwork] = (candidate, automatic)
            transitions += 1
    return {"review_kind": "automated-source-and-annotated-state-preflight", "model_executed": False,
        "question_text_exposed": False, "single_scenarios": len(single["scenarios"]),
        "single_trials": sum(len(item["variants"]) for item in single["scenarios"]),
        "multi_scripts": len(multi["scripts"]), "annotated_transitions_checked": transitions,
        "source_references_checked": references, "issues": issues,
        "suite_sha256": {name: hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in ("single-turn.json", "multi-turn.json")},
        "seed_sha256": hashlib.sha256(seed_path.read_bytes()).hexdigest(),
        "suite_status": {"single": single["status"], "multi": multi["status"]},
        "manual_source_meaning_review": "pending", "pending_clarification_semantics_review": "pending",
        "language_reviewer_signoffs": dict.fromkeys(sorted(LANGUAGES), "pending"),
        "independent_evaluation_ready": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite-root", required=True)
    parser.add_argument("--report", required=True)
    options = parser.parse_args()
    destination = Path(options.report).resolve()
    if destination.is_relative_to(ROOT):
        parser.error("Review reports must remain outside the repository.")
    report = audit(options.suite_root)
    from agent.testing.m2_smoke import save
    save(destination, report)
    print(json.dumps({key: report[key] for key in ("single_scenarios", "single_trials", "multi_scripts",
        "annotated_transitions_checked", "source_references_checked", "issues", "independent_evaluation_ready")}, ensure_ascii=False))
    return int(bool(report["issues"]))


if __name__ == "__main__":
    raise SystemExit(main())
