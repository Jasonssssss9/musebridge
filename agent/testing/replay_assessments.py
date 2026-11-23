"""Replay assessment decisions on exact recorded evidence, requests and history."""
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
from agent.runtime.validation import ContractViolation
from agent.runtime.validation_v2 import ContractValidatorV2
from agent.testing.planner_comparison import DialogueProbeModel
from agent.testing.verify_ollama import ensure_probe_identity, fit_probe_call, save


def archived_operations(baseline, validator):
    turns = {turn["request_id"]: turn for case in baseline["cases"] for turn in case["turns"]}
    for call in baseline["model_attempts"]:
        operation = call["operation"]
        if operation not in {"assess", "final"}:
            continue
        payload = dict(call["model_input"])
        # Older dialogue probes stored this derived display field in model_input.
        payload.pop("available_statements", None)
        validator.validate("AnswerInput" if payload["allow_refinement"] else "FinalInput", payload)
        name = "AssessmentDecision" if payload["allow_refinement"] else "FinalDecision"
        if call["schema"] != name or (operation == "final" and payload["allow_refinement"]):
            raise ValueError("The recorded operation disagrees with its fitted input.")
        turn = turns[call["request_id"]]
        if payload["original_question"] != turn["input"]["question"]:
            raise ValueError("The recorded operation belongs to a different visitor question.")
        yield turn, operation, name, payload


def exact_support(results, gold):
    return [(r["support"], set(r["citation_ids"])) for r in results] == [
        (part["support"], set(part["citations"])) for part in gold]


async def evaluate(opts):
    source = opts.baseline.read_bytes()
    baseline = json.loads(source)
    validator = ContractValidatorV2()
    operations = list(archived_operations(baseline, validator))
    if not operations or not baseline["implementation_unchanged"]:
        raise ValueError("Provide a stable dialogue report with actual assessment calls.")
    report = {"baseline_sha256": sha256(source).hexdigest(), "case_sha256": baseline["case_sha256"],
              "implementation_at_start": implementation_fingerprint(), "operations": [], "model_attempts": []}
    model = DialogueProbeModel(report, base_url=opts.base_url, sizer=QwenPromptSizer(opts.tokenizer))
    try:
        await model.verify(deadline=monotonic() + 60)
        report.update(profile=asdict(model.profile), metadata_fingerprint=model.metadata_fingerprint)
        if report["profile"] != baseline["profile"] or report["metadata_fingerprint"] != baseline["metadata_fingerprint"]:
            raise ValueError("The real model or inference profile differs from the archived report.")
        await model.warm(deadline=monotonic() + 120)
        for turn, operation, name, payload in operations:
            trace = turn["request_id"]
            row = {"request_id": trace, "operation": operation, "support_and_citations": None}
            report["operations"].append(row)
            try:
                await ensure_probe_identity(model, report, trace)
                schema, messages = fit_probe_call(model, operation, payload, validator.schema(name))
                deadline = monotonic() + 15
                reply = await model.complete_structured(
                    operation=operation, messages=messages,
                    output_schema=schema, profile=model.profile, deadline=deadline, trace_context={"request_id": trace})
                decision = reply.data
                validator.validate(name, decision)
                if monotonic() > deadline:
                    raise WorkflowError("generation_timeout", "The assessment deadline was exceeded.",
                                        phase="assessment", retryable=True)
                row["decision"] = decision
                if decision["action"] == "finish":
                    row["support_and_citations"] = exact_support(decision["results"], turn["gold"]["parts"])
            except (WorkflowError, ContractViolation) as error:
                row["error"] = error.as_dict() if isinstance(error, WorkflowError) else {"code": "generation_invalid_response"}
            save(opts.report, report)
            print(trace, operation, row.get("decision", {}).get("action", "failed"), flush=True)
    finally:
        await model.aclose()
    rows = report["operations"]
    report["summary"] = {"operations": len(rows), "finished": sum(r["support_and_citations"] is not None for r in rows),
                         "matched": sum(r["support_and_citations"] is True for r in rows),
                         "errors": sum("error" in r for r in rows)}
    report["implementation_unchanged"] = report["implementation_at_start"] == implementation_fingerprint()
    save(opts.report, report)
    print(json.dumps(report["summary"]))
    return report["implementation_unchanged"] and report["summary"]["errors"] == 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    opts = parser.parse_args()
    if opts.report.exists() or opts.report.resolve().is_relative_to(Path(__file__).resolve().parents[2]):
        parser.error("Use a new private report path outside the repository.")
    raise SystemExit(0 if asyncio.run(evaluate(opts)) else 1)


if __name__ == "__main__":
    main()
