"""Rehearse Vincent's text persona against fixed Irises evidence on the pinned Qwen model."""
import argparse
import asyncio
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic

from agent.providers.ollama import OllamaModel
from agent.runtime.errors import WorkflowError


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "knowledge" / "data"
PROMPT = ROOT / "agent" / "prompts" / "vincent" / "role.md"
ARTWORK = "getty-103jnh"
FIELDS = ("chunk_id", "source_kind", "source_title", "source_locator", "excerpt", "attribution")
MEMORY_SCHEMA = {
    "type": "object",
    "properties": {
        "active_topic": {"oneOf": [{"type": "null"}, {"type": "string", "minLength": 1, "maxLength": 300}]},
        "continuity_summary": {"type": "string", "maxLength": 1000},
    },
    "required": ["active_topic", "continuity_summary"],
    "additionalProperties": False,
}
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "cited_chunk_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        "answer": {"type": "string", "minLength": 1, "pattern": r"\S"},
        "memory_update": MEMORY_SCHEMA,
    },
    "required": ["cited_chunk_ids", "answer", "memory_update"],
    "additionalProperties": False,
}


def read_release(path):
    raw = path.read_bytes()
    digest = sha256(raw).hexdigest()
    if digest != path.with_suffix(".sha256").read_text(encoding="ascii").strip():
        raise ValueError(f"Evidence release digest mismatch: {path.name}")
    return json.loads(raw), digest


def load_sources():
    gallery, gallery_digest = read_release(DATA / "getty-gallery-v2.json")
    pack, pack_digest = read_release(DATA / "reference-packs" / "vincent-irises-v1.json")
    if pack["artwork_id"] != ARTWORK or pack["dataset_version"] != gallery["dataset_version"]:
        raise ValueError("The Vincent reference pack does not match the Irises gallery record.")
    artwork = next(work for work in gallery["artworks"] if work["artwork_id"] == ARTWORK)
    sources = {s["source_id"]: s for s in gallery["sources"] if s["artwork_id"] == ARTWORK}
    rows = [{**sources[c["source_id"]], **c} for c in gallery["chunks"] if c["artwork_id"] == ARTWORK]
    rows.extend(pack["records"])
    evidence = {}
    for row in rows:
        chunk = row["chunk_id"]
        if row["artwork_id"] != ARTWORK or chunk in evidence:
            raise ValueError(f"Invalid Irises evidence identity: {chunk}")
        evidence[chunk] = {key: row[key] for key in FIELDS}
    releases = {
        "gallery": {"version": gallery["dataset_version"], "sha256": gallery_digest},
        "reference_pack": {"version": pack["reference_pack_version"], "sha256": pack_digest},
    }
    return {key: artwork[key] for key in ("title", "artist", "date_display")}, evidence, releases


def validate_cases(conversations, evidence):
    seen = set()
    if not conversations:
        raise ValueError("Provide at least one conversation.")
    for case in conversations:
        if not case["id"] or case["id"] in seen or not case["turns"]:
            raise ValueError("Each conversation needs a distinct ID and at least one turn.")
        seen.add(case["id"])
        for turn in case["turns"]:
            ids = turn["evidence_ids"]
            if not turn["question"].strip() or len(set(ids)) != len(ids) or set(ids) - evidence.keys():
                raise ValueError(f"Invalid question or fixed evidence in {case['id']}: {ids}")


async def run_dialogues(conversations, model, prompt, artwork, evidence, report, checkpoint, timeout):
    """A failed turn ends its dialogue. Its unvalidated output never becomes visitor history."""
    validate_cases(conversations, evidence)
    for case_number, case in enumerate(conversations, start=1):
        dialogue = {"id": case["id"], "turns": []}
        report["conversations"].append(dialogue)
        history = []
        recent_citations = []
        previous_memory = None
        for number, turn in enumerate(case["turns"], start=1):
            current = turn["evidence_ids"]
            cached = [cid for cid in recent_citations if cid not in current]
            record = {"question": turn["question"], "current_evidence_ids": current, "cached_evidence_ids": cached}
            dialogue["turns"].append(record)
            payload = {
                "artwork": artwork,
                "history": history,
                "previous_memory": previous_memory,
                "current_evidence": [evidence[cid] for cid in current],
                "cached_evidence": [evidence[cid] for cid in cached],
                "available_source_kinds": list(dict.fromkeys(evidence[cid]["source_kind"] for cid in [*current, *cached])),
                "question": turn["question"],
            }
            messages = [{"role": "system", "content": prompt},
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
            try:
                completion = await model.complete_structured(
                    operation="final", messages=messages, output_schema=OUTPUT_SCHEMA, profile=model.profile,
                    deadline=monotonic() + timeout, trace_context={"request_id": f"vincent-rehearsal-{case_number}-{number}"},
                )
            except WorkflowError as error:
                record["error"] = error.as_dict()
                checkpoint()
                break
            result = completion.data
            record.update(output=result, duration_ms=completion.duration_ms,
                          prompt_tokens=completion.prompt_tokens, output_tokens=completion.output_tokens)
            citations = result["cited_chunk_ids"]
            unknown = sorted(set(citations) - set(current) - set(cached))
            if len(citations) != len(set(citations)) or unknown:
                record["error"] = {"code": "invalid_citations", "outside_offered_evidence": unknown}
                checkpoint()
                break
            recent_citations = list(dict.fromkeys([*citations, *recent_citations]))[:8]
            history.append({"question": turn["question"], "answer": result["answer"]})
            history = history[-5:]
            previous_memory = result["memory_update"]
            checkpoint()


async def run(options):
    from agent.providers.sizing import QwenPromptSizer
    from agent.testing.verify_ollama import save

    cases = options.cases.read_bytes()
    prompt = PROMPT.read_bytes()
    conversations = json.loads(cases)["conversations"]
    artwork, evidence, releases = load_sources()
    validate_cases(conversations, evidence)
    model = OllamaModel(base_url=options.base_url, sizer=QwenPromptSizer(options.tokenizer))
    report = {
        "evaluation": "Fixed-evidence Vincent text rehearsal", "artwork_id": ARTWORK,
        "prompt_sha256": sha256(prompt).hexdigest(), "cases_sha256": sha256(cases).hexdigest(),
        "evidence_releases": releases, "profile": asdict(model.profile), "conversations": [],
    }

    def checkpoint():
        save(options.output, report)

    try:
        await model.verify(deadline=monotonic() + 60)
        report["metadata_fingerprint"] = model.metadata_fingerprint
        await model.warm(deadline=monotonic() + 120)
        await run_dialogues(conversations, model, prompt.decode("utf-8"), artwork, evidence,
                            report, checkpoint, options.timeout)
    except WorkflowError as error:
        report["error"] = error.as_dict()
    finally:
        checkpoint()
        await model.aclose()
    return 1 if "error" in report or any("error" in t for c in report["conversations"] for t in c["turns"]) else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=120, help="Seconds per model turn.")
    options = parser.parse_args()
    if options.timeout <= 0 or options.cases.resolve() == options.output.resolve():
        parser.error("Use a positive timeout and a separate output file.")
    return asyncio.run(run(options))


if __name__ == "__main__":
    raise SystemExit(main())
