"""Replay the first evidence assessment of recorded development turns without a model."""
import argparse
import json
from pathlib import Path

from agent.runtime.followup_plan import expand_plan
from agent.runtime.validation_v2 import ContractValidatorV2, validate_plan
from agent.tools.evidence import EvidenceManifest
from knowledge.dataset import load_dataset
from knowledge.http_service import DEFAULT_SEED
from knowledge.retrieval import retrieve


def chunk_ids(records):
    return {item["chunk_id"] for item in records}


def compare(report):
    manifest = EvidenceManifest.load()
    dataset = load_dataset(DEFAULT_SEED)
    validator = ContractValidatorV2()
    plans = {a["request_id"]: a for a in report["model_attempts"] if a["operation"] == "plan"}
    assessments = {}
    for attempt in report["model_attempts"]:
        if attempt["operation"] == "assess":
            assessments.setdefault(attempt["request_id"], attempt)
    counts = dict(turns=0, assessed_in_archive=0, turns_requiring_chunks=0,
                  archive_all_required_available=0, replay_all_required_available=0)
    changes = []
    still_missing = []
    for case in report["cases"]:
        for turn in case["turns"]:
            counts["turns"] += 1
            trace = turn["request_id"]
            attempt = plans[trace]
            payload = attempt["model_input"]
            plan = expand_plan(attempt["completion"]["data"], payload)
            run = turn["input"]
            validate_plan(run, plan, validator, plan_input=payload, complete_scope=payload["complete_scope"])
            previous = set()
            if trace in assessments:
                counts["assessed_in_archive"] += 1
                previous = chunk_ids(assessments[trace]["model_input"]["evidence"])

            evidence = []
            if plan["action"] in {"retrieve", "answer_from_context"}:
                scope = run["scope"]
                evidence = payload["cached_evidence"]
                for query in plan.get("queries", []):
                    if manifest.complete_scope(**scope, evidence=evidence):
                        break
                    result = retrieve(dataset, **scope, query=query["query"], top_k=2)
                    evidence = manifest.merge(**scope, existing=evidence, incoming=result["evidence"])
            current = chunk_ids(evidence)
            # Catalog IDs have a separate channel. Count only turns that actually
            # require a released descriptive or medium chunk in the development gold.
            required = {cid for part in turn["gold"]["parts"] for cid in part["citations"] if cid in manifest.records}
            if required:
                if not required <= current:
                    still_missing.append({"request_id": trace, "missing_chunks": sorted(required - current)})
                counts["turns_requiring_chunks"] += 1
                counts["archive_all_required_available"] += required <= previous
                counts["replay_all_required_available"] += required <= current
            old_route = attempt["canonical_plan"]["action"]
            if old_route != plan["action"] or current != previous:
                changes.append(dict(request_id=trace, archive_route=old_route, replay_route=plan["action"],
                                    required=sorted(required), archived=sorted(previous), replayed=sorted(current)))
    return {**counts, "changes": changes, "still_missing": still_missing}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True, help="Completed private development planner report")
    report = json.loads(parser.parse_args().baseline.read_bytes())
    if not report.get("implementation_unchanged"):
        parser.error("Use a completed development run with a stable implementation.")
    print(json.dumps(compare(report), indent=2))


if __name__ == "__main__":
    main()
