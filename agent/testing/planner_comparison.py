"""Run frozen visitor dialogues with real Qwen and independent English retrieval controls."""
import argparse
import asyncio
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
from threading import Thread
from time import monotonic

from knowledge.http_service import create_server
from agent.providers.capabilities import implementation_fingerprint
from agent.providers.sizing import QwenPromptSizer
from agent.runtime.errors import WorkflowError
from agent.runtime.followup_plan import expand_plan
from agent.runtime.validation import ContractViolation
from agent.runtime.validation_v2 import ContractValidatorV2, assemble_answer, validate_plan
from agent.service import ModelIdentity, create_workflow_runtime
from agent.testing.probe_quality import operation_summary
from agent.testing.v2 import make_run, simulate_commit
from agent.testing.verify_ollama import ProbeModel, ProbeRetrieval, ensure_probe_identity, save
from agent.tools.evidence import EvidenceManifest
from agent.tools.retrieval import RetrievalTool


CASES = Path(__file__).with_name("planner_comparison_cases.json")


class DialogueProbeModel(ProbeModel):
    """Keep the actual fitted inputs and raw outputs in the private run report."""

    async def _json(self, method, path, **kwargs):
        reply = await super()._json(method, path, **kwargs)
        if path == "/api/chat" and self.current_attempt.get() is not None:
            self.current_attempt.get()["provider_content"] = reply.get("message", {}).get("content")
        return reply

    async def complete_structured(self, *, messages, **kwargs):
        start = len(self.attempts)
        try:
            return await super().complete_structured(messages=messages, **kwargs)
        finally:
            payload = json.loads(messages[-1]["content"])
            if "source_sentences" in payload:
                self.attempts[start]["source_sentences"] = payload.pop("source_sentences")
            self.attempts[start]["model_input"] = payload


def plan_check(run, attempts, validator, *, deadline=None):
    """Check the plan independently of lookup or final answer success."""
    attempt = next((call for call in attempts if call["operation"] == "plan"), None)
    if attempt is None or "completion" not in attempt:
        return {"valid": False if attempt and attempt["schema_result"] == "invalid" else None,
                "schema_result": attempt["schema_result"] if attempt else "unobserved"}
    plan = attempt["completion"]["data"]
    payload = attempt["model_input"]
    # The production stage checks its clock on model return and after plan validation.
    # Terminal answer assembly runs in the graph node after that deadline.
    timed_out = {"valid": None, "schema_result": attempt["schema_result"],
                 "error_code": "generation_timeout"}
    if deadline is not None and monotonic() > deadline:
        return timed_out
    try:
        plan = expand_plan(plan, payload)
        validate_plan(run, plan, validator, plan_input=payload)
        if deadline is not None and monotonic() > deadline:
            return timed_out
        if plan["action"] in {"direct_response", "clarify", "out_of_scope"}:
            assemble_answer(run, plan, plan, payload["cached_evidence"], payload["catalog_facts"], validator)
    except (ContractViolation, WorkflowError) as error:
        return {"valid": False, "schema_result": attempt["schema_result"],
                "reason": str(error.__cause__ or error)}
    return {"valid": True, "schema_result": attempt["schema_result"]}


async def run_dialogue(gold, report, runtime, model, manifest, options):
    case = {"id": gold["id"], "language": gold["language"], "artwork_id": gold["artwork_id"], "turns": []}
    report["cases"].append(case)
    state = None
    history = []
    validator = ContractValidatorV2()
    for number, specs in enumerate(gold["turns"], 1):
        trace = f"planner-{gold['id']}-{number}"
        entry = {"number": number, "request_id": trace, "gold": specs, "delivered": False}
        case["turns"].append(entry)
        if number > 1 and len(history) != number - 1:
            entry["outcome"] = "blocked_by_undelivered_previous_turn"
        else:
            run = make_run(artwork_id=gold["artwork_id"], question=specs["question"],
                           conversation_state=state, history=history, manifest=manifest)
            entry["input"] = run
            offset = len(report["model_attempts"])
            start = monotonic()
            try:
                await ensure_probe_identity(model, report, trace)
                entry["snapshot"] = await runtime.submit(run, request_id=trace)
            except WorkflowError as error:
                entry.update(outcome="failed", error=error.as_dict())
            else:
                result = entry["snapshot"]["result"]
                state, pair = simulate_commit(run, result)
                history.append(pair)
                entry.update(outcome="delivered", delivered=True, next_state=state, history_turn=pair)
            entry["elapsed_ms"] = round((monotonic() - start) * 1000)
            entry["plan_check"] = plan_check(run, report["model_attempts"][offset:], validator)
        save(options.report, report)
        print(trace, entry["outcome"], flush=True)


async def manual_controls(cases, report, tool, version):
    """Each gold query gets a fresh scoped lookup, including blocked visitor turns."""
    for case in cases:
        for number, turn in enumerate(case["turns"], 1):
            for index, part in enumerate(turn["parts"], 1):
                query = part["manual_query"]
                if query is None:
                    continue
                trace = f"manual-{case['id']}-{number}-{index}"
                tool_input = {"tool_version": "evidence-tool-v1", "artwork_id": case["artwork_id"],
                              "dataset_version": version, "query_id": "q1", "request_ids": [f"r{index}"],
                              "query": query, "top_k": 2}
                record = {"case_id": case["id"], "turn": number, "part": index, "request_id": trace,
                          "input": tool_input, "expected_citations": part["citations"]}
                report["manual_controls"].append(record)
                try:
                    response = await tool.search(tool_input, request_id=trace)
                except WorkflowError as error:
                    record["error"] = error.as_dict()
                else:
                    record["candidates"] = response["candidates"]


async def evaluate(options):
    labels = options.case_file.read_bytes()
    cases = json.loads(labels)["cases"]
    report = {"case_sha256": sha256(labels).hexdigest(), "implementation_at_start": implementation_fingerprint(),
              "cases": [], "model_attempts": [], "lookups": [], "manual_controls": []}
    manifest = EvidenceManifest.load()
    model = DialogueProbeModel(report, base_url=options.base_url, sizer=QwenPromptSizer(options.tokenizer))
    server = create_server("127.0.0.1", 0)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    runtime = None
    try:
        await manual_controls(cases, report, RetrievalTool(manifest, base_url=origin), manifest.dataset_version)
        save(options.report, report)
        await model.verify(deadline=monotonic() + 60)
        report.update(profile=asdict(model.profile), metadata_fingerprint=model.metadata_fingerprint)
        await model.warm(deadline=monotonic() + 120)
        runtime = create_workflow_runtime(
            model=model, profile=model.profile, prompt_sizer=model.sizer, manifest=manifest,
            retrieval=ProbeRetrieval(report, manifest, base_url=origin),
            identity=ModelIdentity(model.profile.model_name, model.profile.model_digest),
            readiness=lambda: model.verified,
        )
        for gold in cases:
            await run_dialogue(gold, report, runtime, model, manifest, options)
    finally:
        if runtime is not None:
            await runtime.close()
        server.shutdown()
        server.server_close()
        thread.join()
        await model.aclose()
    report["operations"] = operation_summary(report["model_attempts"])
    report["implementation_unchanged"] = report["implementation_at_start"] == implementation_fingerprint()
    save(options.report, report)
    return report["implementation_unchanged"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--case-file", type=Path, default=CASES)
    parser.add_argument("--tokenizer", required=True)
    parser.add_argument("--report", required=True, type=Path)
    options = parser.parse_args()
    if options.report.exists() or options.report.resolve().is_relative_to(Path(__file__).resolve().parents[2]):
        parser.error("Use a new private report path outside the repository.")
    raise SystemExit(0 if asyncio.run(evaluate(options)) else 1)


if __name__ == "__main__":
    main()
