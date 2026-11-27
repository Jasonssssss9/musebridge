"""Evaluate the real Vincent ranker and the real model workflow as separate experiments."""
import argparse
import asyncio
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
from threading import Thread
from time import monotonic
from uuid import uuid4


CASES = Path(__file__).with_name("vincent_hybrid_cases.json")
DATA = CASES.parents[2] / "knowledge" / "data"
GALLERY = DATA / "getty-gallery-v2.json"
PACK = DATA / "reference-packs" / "vincent-irises-v1.json"
ARTWORK = "getty-103jnh"
VERSION = "getty-gallery-v2"


def save(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def evidence_checks(expected, ids, required):
    """An adjacent passage about paint or a flower is allowed for an unsupported detail."""
    if expected == "supported":
        return {"any_required": bool(set(required).intersection(ids)),
                "all_required": set(required).issubset(ids)}
    if expected == "unrelated":
        return {"unrelated_false_return": bool(ids)}
    return {}


def counts(rows, key):
    values = [row["checks"][key] for row in rows if key in row.get("checks", {})]
    return {"positive": sum(values), "observed": len(values)}


def retrieval_stage(cases, report, checkpoint):
    from knowledge.dataset import load_dataset
    from knowledge.errors import RetrievalError
    from knowledge.retrieval import retrieve_with_trace
    from knowledge.strategy import configured_index
    from knowledge.vincent import VINCENT_SCOPE, VincentHybridRetriever, load_vincent_corpus

    resolver = VincentHybridRetriever.open(VINCENT_SCOPE)
    corpus = load_vincent_corpus(VINCENT_SCOPE)
    report["scope"] = dict(VINCENT_SCOPE)
    report["probes"] = rows = []
    for case in cases:
        for number, spec in enumerate(case["turns"], 1):
            if spec["expected"] == "social":
                continue
            for kind, query in (("visitor_verbatim", spec["question"]),
                                ("human_standalone", spec["control_query"])):
                row = {"case": case["id"], "turn": number, "input": kind, "query": query,
                       "expected": spec["expected"], "required_ids": spec["required_ids"]}
                rows.append(row)
                try:
                    result, trace = resolver.search(query, top_k=2)
                except RetrievalError as error:
                    row["error"] = error.code
                else:
                    hits = result["evidence"]
                    ids = [record["chunk_id"] for record in hits]
                    row.update(ids=ids, lexical=trace["lexical_ranking"], dense=trace["dense_ranking"],
                               strategy=trace["strategy"], checks=evidence_checks(spec["expected"], ids, spec["required_ids"]))
                    row["checks"]["canonical_and_scoped"] = (
                        len(hits) <= 2 and len(set(ids)) == len(ids)
                        and all(record == corpus.evidence.get(record["chunk_id"]) for record in hits)
                    )
                checkpoint()
    # Compare identical text under the ordinary guide scope on this and another work.
    gallery = load_dataset(GALLERY, expected_version=VERSION)
    guide_vectors = configured_index(gallery, "hybrid")
    scoped_ids = {record["chunk_id"] for record in gallery.chunks}
    isolation = report["isolation"] = []
    for work in (ARTWORK, "getty-103qt5"):
        result, _ = retrieve_with_trace(gallery, artwork_id=work, dataset_version=VERSION, top_k=2,
            query="How have researchers explained the loss of the red pigment from the paint?", vector_index=guide_vectors)
        ids = [record["chunk_id"] for record in result["evidence"]]
        isolation.append({"mode": "guide", "artwork_id": work, "ids": ids,
                          "passed": all(record["artwork_id"] == work and record["chunk_id"] in scoped_ids
                                        for record in result["evidence"])})
    for field, value in (("artwork_id", "getty-103qt5"), ("conversation_mode", "guide"),
                         ("dataset_version", "getty-pilot-v1"), ("reference_pack_version", "other")):
        try:
            load_vincent_corpus({**VINCENT_SCOPE, field: value})
        except ValueError:
            rejected = True
        else:
            rejected = False
        isolation.append({"incorrect_vincent_scope_field": field, "passed": rejected})
    report["summary"] = {}
    for kind in ("visitor_verbatim", "human_standalone"):
        selected = [row for row in rows if row["input"] == kind]
        report["summary"][kind] = dict(planned=len(selected), errors=sum("error" in row for row in selected),
            **{key: counts(selected, key) for key in ("any_required", "all_required", "unrelated_false_return",
                                                       "canonical_and_scoped")})
    checkpoint()


def workflow_checks(spec, result):
    answer = result["answer"]
    citation_ids = [c.get("chunk_id", c.get("fact_id")) for c in answer["citations"]]
    expected = spec["expected"]
    checks = {"english_field": result["answer_language"] == "en"}
    if expected == "supported":
        checks["all_required_cited"] = set(spec["required_ids"]).issubset(citation_ids)
        checks["status"] = answer["status"] == "answered"
    elif expected == "gap":
        checks["status"] = answer["status"] in {"insufficient", "partially_answered"}
    elif expected == "social":
        checks.update(status=answer["status"] == "conversational", uncited=not citation_ids)
    else:
        checks.update(status=answer["status"] == "out_of_scope", uncited=not citation_ids)
    return checks


def visitor_run(manifest, question, state, history, *, conversation_id=None):
    from agent.testing.v2 import make_run
    from knowledge.vincent import VINCENT_SCOPE

    run = make_run(artwork_id=ARTWORK, question=question, conversation_state=state, history=history[-5:],
                   manifest=manifest)
    incoming = run["conversation_state"]
    if state is None:
        conversation_id = conversation_id or str(uuid4())
        run["conversation_id"] = conversation_id
        incoming.update(conversation_id=conversation_id, scope=dict(VINCENT_SCOPE),
                        state_version="conversation-state-v2", language_preference="en", auto_language="en")
    run["scope"] = dict(VINCENT_SCOPE)
    run["contract_version"] = "agent-run-v3"
    run["execution"]["workflow_id"] = "museum_qa_v3"
    return run


def case_suite(path):
    raw = path.read_bytes()
    suite = json.loads(raw)
    identity = {"name": suite["name"], "file": path.name, "sha256": sha256(raw).hexdigest()}
    return suite["conversations"], identity


def schema_receipt(schema):
    reference = schema.get("$ref", "").rsplit("/", 1)[-1]
    encoded = json.dumps(schema, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    return {"schema_name": reference or schema.get("title"), "schema_properties": list(schema.get("properties", {})),
            "schema_sha256": sha256(encoded).hexdigest()}


def decision_action(data):
    """Extract the decision label for comparison, retaining the complete original model output."""
    decision = data.get("next")
    return decision.get("action") if isinstance(decision, dict) else data.get("action")


def traced_model(report, **options):
    """Extend the shared provider trace with the exact schema and compact routing action."""
    from agent.testing.verify_ollama import ProbeModel

    class VincentTrace(ProbeModel):
        async def complete_structured(self, *, output_schema, **params):
            index = len(self.attempts)
            try:
                return await super().complete_structured(output_schema=output_schema, **params)
            finally:
                row = self.attempts[index]
                row.update(schema_receipt(output_schema))
                if "completion" in row:
                    row["action"] = decision_action(row["completion"]["data"])

    return VincentTrace(report, **options)


async def workflow_stage(args, cases, report, checkpoint):
    from agent.contracts.codec import v3_validator
    from agent.service import ModelIdentity, create_workflow_runtime_v3
    from agent.runtime.errors import WorkflowError
    from agent.providers.sizing import QwenPromptSizer
    from agent.testing.verify_ollama import ProbeRetrieval, ensure_probe_identity
    from agent.testing.v2 import simulate_commit
    from agent.tools.evidence import EvidenceManifest
    from agent.workflows.museum_qa_v3 import vincent_manifest
    from agent.tools.retrieval import RetrievalTool
    from knowledge.http_service import create_server
    from knowledge.vincent import VINCENT_SCOPE

    gallery = EvidenceManifest.load(GALLERY, expected_version=VERSION,
                                    expected_sha256=GALLERY.with_suffix(".sha256").read_text().strip())
    manifest = vincent_manifest(gallery)
    report.update(scope=dict(VINCENT_SCOPE), turns=[], model_attempts=[], lookups=[])
    model = traced_model(report, base_url=args.base_url, sizer=QwenPromptSizer(args.tokenizer))
    server = None
    runtime = None
    try:
        await model.verify(deadline=monotonic() + 60)
        report.update(profile=asdict(model.profile), metadata_fingerprint=model.metadata_fingerprint)
        await model.warm(deadline=monotonic() + 120)
        origin = args.retrieval_url
        if not origin:
            server = create_server("127.0.0.1", 0, seed_path=GALLERY, dataset_version=VERSION,
                                   mode="hybrid", enable_vincent=True)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            origin = f"http://127.0.0.1:{server.server_port}"
        tool = ProbeRetrieval(report, manifest, base_url=origin, mode="hybrid", vincent=True)
        runtime = create_workflow_runtime_v3(model=model, profile=model.profile, prompt_sizer=model.sizer,
            manifest=gallery, retrieval=RetrievalTool(gallery, base_url=origin, mode="hybrid"),
            role_manifest=manifest, role_retrieval=tool, readiness=lambda: model.verified,
            identity=ModelIdentity(model.profile.model_name, model.profile.model_digest))
        for case in cases:
            state = None
            history = []
            conversation_id = str(uuid4())
            previous_failed = False
            for index, spec in enumerate(case["turns"], 1):
                trace = f"vincent-{case['id']}-{index}"
                row = {"case": case["id"], "turn": index, "trace": trace, "question": spec["question"],
                       "conversation_id": conversation_id, "submitted": False, "after_failed_turn": previous_failed,
                       "complete_prior_context": len(history) == index - 1,
                       "expected": spec["expected"], "review": spec["review"]}
                report["turns"].append(row)
                try:
                    await ensure_probe_identity(model, report, trace)
                    run = visitor_run(manifest, spec["question"], state, history, conversation_id=conversation_id)
                    row["input_revision"] = run["conversation_state"]["revision"]
                    row["input_memory"] = run["conversation_state"]["continuity_summary"]
                    row["input_cache_ids"] = [r["chunk_id"] for r in run["conversation_state"]["verified_evidence"]]
                    row["submitted"] = True
                    snapshot = await runtime.submit(run, request_id=trace)
                    result = snapshot["result"]
                    state, turn = simulate_commit(run, result, validate=v3_validator().validate)
                    history.append(turn)
                    row.update(outcome="delivered", result=result, checks=workflow_checks(spec, result),
                               retrieval_strategy=result["execution"]["retrieval_strategy_version"],
                               semantic_review="pending")
                except WorkflowError as error:
                    row.update(outcome="failed", error=error.as_dict())
                previous_failed = row["outcome"] != "delivered"
                # This stage never sends required IDs or control queries to the planner or retriever.
                available = set(row.get("input_cache_ids", []))
                for lookup in report["lookups"]:
                    if lookup["request_id"] == trace:
                        available.update(c["chunk_id"] for c in lookup.get("candidates", []))
                if spec["expected"] == "supported":
                    row["all_required_available"] = set(spec["required_ids"]).issubset(available)
                row["model_operations"] = [dict(operation=a["operation"], action=a.get("action"),
                                                schema_sha256=a["schema_sha256"], outcome=a["outcome"])
                                           for a in report["model_attempts"] if a["request_id"] == trace]
                checkpoint()
        delivered = [row for row in report["turns"] if row["outcome"] == "delivered"]
        report["summary"] = {"planned": report["planned_turns"], "submitted": sum(r["submitted"] for r in report["turns"]),
                             "delivered": len(delivered),
                             "complete_prior_context": sum(r["complete_prior_context"] for r in report["turns"]),
                             "delivered_with_complete_prior_context": sum(r["complete_prior_context"] for r in delivered),
                             "structural_and_expected_fields_pass": sum(all(r["checks"].values()) for r in delivered),
                             "semantic_review": "Review the spoken answer and memory separately against each rubric."}
    except WorkflowError as error:
        report["admission_error"] = error.as_dict()
    finally:
        if runtime is not None:
            await runtime.close()
        if server is not None:
            server.shutdown()
            server.server_close()
            thread.join()
        await model.aclose()

def implementation():
    root = CASES.parents[2]
    files = {Path(__file__).resolve(), CASES.with_name("v2.py"),
             root / "docs/contracts/agent-v2.schema.json", root / "docs/contracts/agent-v3.patch.json"}
    for directory in ("agent", "knowledge"):
        files.update(p for p in (root / directory).rglob("*.py") if not {"tests", "testing"}.intersection(p.parts))
    prompts = sorted((root / "agent/prompts").rglob("*.md"))
    files.update(prompts)
    digest = sha256()
    for path in sorted(files):
        digest.update(str(path.relative_to(root)).encode() + b"\0" + sha256(path.read_bytes()).digest())
    return {"code_and_prompts_sha256": digest.hexdigest(),
            "prompt_sha256": {str(p.relative_to(root)): sha256(p.read_bytes()).hexdigest() for p in prompts}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("retrieval", "workflow"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", type=Path, default=CASES, help="Visitor suite and its independently assigned review criteria.")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434", help="Ollama origin for workflow runs.")
    parser.add_argument("--tokenizer", type=Path, help="Pinned local Qwen tokenizer.")
    parser.add_argument("--retrieval-url", help="Existing Vincent-enabled knowledge origin. Default: ephemeral localhost service.")
    args = parser.parse_args()
    if args.stage == "workflow" and not args.tokenizer:
        parser.error("A workflow run needs --tokenizer.")
    if args.output.exists():
        parser.error("Use a new output path so an earlier observation stays intact.")
    cases, suite = case_suite(args.cases)
    inputs = [args.cases, GALLERY, PACK]
    report = {"stage": args.stage, "suite": suite, "planned_turns": sum(len(c["turns"]) for c in cases),
              "implementation": implementation(), "inputs_sha256": {p.name: sha256(p.read_bytes()).hexdigest() for p in inputs}}

    def checkpoint():
        save(args.output, report)

    try:
        if args.stage == "retrieval":
            retrieval_stage(cases, report, checkpoint)
        else:
            asyncio.run(workflow_stage(args, cases, report, checkpoint))
    except (ValueError, OSError, RuntimeError) as error:
        report["stage_error"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        checkpoint()
    if "stage_error" in report or report.get("admission_error"):
        return 1
    if args.stage == "retrieval":
        return int(any("error" in row for row in report["probes"]) or
                   not all(item["passed"] for item in report["isolation"]))
    return int(any(turn.get("outcome") != "delivered" for turn in report["turns"]))


if __name__ == "__main__":
    raise SystemExit(main())
