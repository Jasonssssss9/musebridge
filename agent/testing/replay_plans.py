"""Replay saved PLAN calls against their original fitted conversation inputs."""
import argparse
import asyncio
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic

from agent.providers.capabilities import implementation_fingerprint
from agent.providers.sizing import QwenPromptSizer
from agent.runtime.errors import WorkflowError
from agent.runtime.validation_v2 import ContractValidatorV2
from agent.testing.planner_comparison import DialogueProbeModel, plan_check
from agent.testing.verify_ollama import ensure_probe_identity, fit_probe_call, save


def frozen_turns(baseline):
    """Join each recorded model input to the visitor run used for semantic validation."""
    turns = {turn["request_id"]: (case, turn)
             for case in baseline["cases"] for turn in case["turns"]}
    seen = set()
    for attempt in baseline["model_attempts"]:
        if attempt["operation"] != "plan":
            continue
        trace = attempt["request_id"]
        if trace in seen:
            raise ValueError(f"Multiple PLAN attempts for {trace}.")
        seen.add(trace)
        case, turn = turns[trace]
        payload = dict(attempt["model_input"])
        payload.pop("source_sentences", None)  # Prompt-only field in older reports.
        yield case, turn, payload


def summary(rows):
    def counts(group):
        return {
            "turns": len(group),
            "baseline_accepted": sum(row["baseline"]["valid"] is True for row in group),
            "candidate_accepted": sum(row["candidate"]["valid"] is True for row in group),
            "candidate_unobserved": sum(row["candidate"]["valid"] is None for row in group),
            "newly_accepted": [row["request_id"] for row in group
                               if row["baseline"]["valid"] is False and row["candidate"]["valid"] is True],
            "regressions": [row["request_id"] for row in group
                            if row["baseline"]["valid"] is True and row["candidate"]["valid"] is False],
        }
    return {"first": counts([row for row in rows if row["number"] == 1]),
            "follow_up": counts([row for row in rows if row["number"] > 1])}


async def replay_turn(model, validator, report, case, turn, payload):
    trace = turn["request_id"]
    row = {"request_id": trace, "case_id": case["id"], "number": turn["number"],
           "language": case["language"], "baseline": turn["plan_check"]}
    validator.validate("PlanInput", payload)
    attempts = report["model_attempts"]
    offset = len(attempts)
    started = monotonic()
    deadline = None
    try:
        await ensure_probe_identity(model, report, trace)
        schema, messages = fit_probe_call(model, "plan", payload, validator.schema("Plan"))
        deadline = monotonic() + 10.0
        await model.complete_structured(operation="plan", messages=messages,
                                        output_schema=schema, profile=model.profile, deadline=deadline,
                                        trace_context={"request_id": trace})
    except WorkflowError as error:
        row["error"] = error.as_dict()
    calls = attempts[offset:]
    row["candidate"] = plan_check(turn["input"], calls, validator, deadline=deadline)
    if row["candidate"].get("error_code") == "generation_timeout":
        row["error"] = WorkflowError("generation_timeout", "The model stage exceeded its deadline.",
                                     phase="planning", retryable=True).as_dict()
    row["elapsed_ms"] = round((monotonic() - started) * 1000)
    return row


async def evaluate(options):
    source = options.baseline.read_bytes()
    baseline = json.loads(source)
    turns = list(frozen_turns(baseline))
    if not turns or not baseline["implementation_unchanged"]:
        raise ValueError("Provide a completed baseline with recorded PLAN calls and a stable implementation.")
    report = {"baseline_sha256": sha256(source).hexdigest(), "case_sha256": baseline["case_sha256"],
              "implementation_at_start": implementation_fingerprint(), "turns": [], "model_attempts": []}
    model = DialogueProbeModel(report, base_url=options.base_url, sizer=QwenPromptSizer(options.tokenizer))
    validator = ContractValidatorV2()
    try:
        await model.verify(deadline=monotonic() + 60)
        report.update(profile=asdict(model.profile), metadata_fingerprint=model.metadata_fingerprint)
        if report["profile"] != baseline["profile"] or report["metadata_fingerprint"] != baseline["metadata_fingerprint"]:
            raise ValueError("The verified model or profile differs from the recorded baseline.")
        await model.warm(deadline=monotonic() + 120)
        for case, turn, payload in turns:
            row = await replay_turn(model, validator, report, case, turn, payload)
            report["turns"].append(row)
            save(options.report, report)
            print(row["request_id"], row["candidate"], flush=True)
    finally:
        await model.aclose()
    report["summary"] = summary(report["turns"])
    report["implementation_unchanged"] = report["implementation_at_start"] == implementation_fingerprint()
    save(options.report, report)
    print(json.dumps(report["summary"], indent=2))
    return report["implementation_unchanged"] and all(row["candidate"]["valid"] is not None
                                                      for row in report["turns"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path, help="Private planner_comparison report")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--tokenizer", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    options = parser.parse_args()
    if options.report.exists() or options.report.resolve().is_relative_to(Path(__file__).resolve().parents[2]):
        parser.error("Use a new private report path outside the repository.")
    raise SystemExit(0 if asyncio.run(evaluate(options)) else 1)


if __name__ == "__main__":
    main()
