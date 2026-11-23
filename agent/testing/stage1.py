"""Measure compound answer coverage and evidence gaps with real Qwen and retrieval.

Run development and fresh cases separately. Inspect each answer and memory against
the source-based review rubric. Check that each visible claim and its citation
match the visitor's subquestion, that gaps refer to this lookup, and that the
English memory preserves only supported or explicitly attributed claims. Check
the actual reply language in the text. The machine score covers the wire fields.
This development tool never issues readiness records.
"""
import argparse
import asyncio
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
from threading import Thread
from time import monotonic

from knowledge.http_service import create_server
from agent.tools.retrieval import configured_mode
from agent.providers.capabilities import implementation_fingerprint
from agent.providers.sizing import QwenPromptSizer
from agent.runtime.errors import WorkflowError
from agent.service import ModelIdentity, create_workflow_runtime
from agent.testing.probe_quality import operation_summary
from agent.testing.v2 import make_run
from agent.testing.verify_ollama import ProbeModel, ProbeRetrieval, ensure_probe_identity, save
from agent.tools.evidence import EvidenceManifest


CASES = Path(__file__).with_name("stage1_cases.json")


def score(gold, result):
    layouts = gold["evidence_layouts"]
    expected_ids = {chunk for chunk in layouts[0] if chunk}
    status = ("insufficient" if not expected_ids else
              "partially_answered" if None in layouts[0] else "answered")
    actual_parts = [(part["support"], part["citation_ids"]) for part in result["request_results"]]
    valid_parts = [[("supported", [chunk]) if chunk else ("insufficient", []) for chunk in layout]
                   for layout in layouts]
    return {
        "status": result["answer"]["status"] == status,
        "parts_and_citations_in_order": actual_parts in valid_parts,
        "answer_citations": {c.get("chunk_id", c.get("fact_id")) for c in result["answer"]["citations"]} == expected_ids,
        "language_field": result["answer_language"] == gold["language"],
    }


async def evaluate(options):
    case_file = CASES.with_name("stage1_confirmation.json") if options.suite == "confirmation" else CASES
    labels = (options.case_file or case_file).read_bytes()
    suite = json.loads(labels)[options.suite]
    report = {"suite": options.suite, "labels_sha256": sha256(labels).hexdigest(),
              "implementation_at_start": implementation_fingerprint(),
              "cases": [], "model_attempts": [], "lookups": []}
    manifest = EvidenceManifest.load()
    model = ProbeModel(report, base_url=options.base_url, sizer=QwenPromptSizer(options.tokenizer))
    mode = configured_mode()
    report["retrieval_mode"] = mode
    server = create_server("127.0.0.1", 0, mode=mode)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    runtime = None
    try:
        await model.verify(deadline=monotonic() + 60)
        report.update(profile=asdict(model.profile), metadata_fingerprint=model.metadata_fingerprint)
        await model.warm(deadline=monotonic() + 120)
        tool = ProbeRetrieval(report, manifest, mode=mode, base_url=f"http://127.0.0.1:{server.server_port}")
        runtime = create_workflow_runtime(model=model, profile=model.profile, prompt_sizer=model.sizer,
            manifest=manifest, retrieval=tool, identity=ModelIdentity(model.profile.model_name, model.profile.model_digest),
            readiness=lambda: model.verified)
        for gold in suite:
            trace = "stage1-" + gold["id"]
            case = {"gold": gold, "request_id": trace, "checks": None, "machine_passed": False}
            try:
                await ensure_probe_identity(model, report, trace)
                run = make_run(artwork_id=gold["artwork_id"], question=gold["question"], manifest=manifest)
                case["input"] = run
                case["snapshot"] = await runtime.submit(run, request_id=trace)
            except WorkflowError as error:
                case.update(error_code=error.code, phase=error.phase)
            else:
                case["checks"] = score(gold, case["snapshot"]["result"])
                case["machine_passed"] = all(case["checks"].values())
            report["cases"].append(case)
            save(options.report, report)
            print(gold["id"], case["machine_passed"], case.get("error_code", ""), flush=True)
    finally:
        if runtime is not None:
            await runtime.close()
        server.shutdown()
        server.server_close()
        thread.join()
        await model.aclose()
    report["operations"] = operation_summary(report["model_attempts"])
    report["implementation_unchanged"] = report["implementation_at_start"] == implementation_fingerprint()
    report["machine_passed"] = sum(case["machine_passed"] for case in report["cases"])
    save(options.report, report)
    print(f"{report['machine_passed']}/{len(suite)} machine checks passed. Review prose and memory separately.")
    return report["machine_passed"] == len(suite) and report["implementation_unchanged"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True, choices=("development", "fresh", "confirmation"))
    parser.add_argument("--case-file", type=Path, help="Optional labeled JSON file containing the selected suite.")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--tokenizer", required=True)
    parser.add_argument("--report", required=True, type=Path)
    options = parser.parse_args()
    if options.report.resolve().is_relative_to(Path(__file__).resolve().parents[2]) or options.report.exists():
        parser.error("Use a new report path outside the repository.")
    raise SystemExit(0 if asyncio.run(evaluate(options)) else 1)


if __name__ == "__main__":
    main()
