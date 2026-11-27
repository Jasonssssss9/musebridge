"""Ask published gallery questions or an independent case set through the public Go API."""
import argparse
from contextlib import nullcontext
from http.cookiejar import CookieJar
import json
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, ProxyHandler, build_opener
from uuid import uuid4

from agent.testing.m2_smoke import call


def run_case(work, case, origin, base):
    client = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
    opened = call(client, "POST", base + "/api/conversations", {
        "contract_version": "agent-demo-v0.3", "artwork_id": work["artwork_id"],
    }, origin)
    if "conversation" not in opened["body"]:
        return opened
    conversation = opened["body"]["conversation"]
    return call(client, "POST", f"{base}/api/conversations/{conversation['conversation_id']}/messages", {
        "contract_version": "agent-demo-v0.3", "client_message_id": str(uuid4()),
        "expected_revision": conversation["revision"], "language_override": case.get("language_override"),
        "question": case["question"],
    }, origin)


def check_reply(work, case, reply):
    turn = reply["body"].get("turn", {})
    answer = turn.get("answer", {})
    citations = answer.get("citations", [])
    kind = case.get("source_kind")
    scope_matches = all(c["artwork_id"] == work["artwork_id"] for c in citations)
    source_matches = (bool(citations) and all(c.get("source_kind") == kind for c in citations)
                      if kind else not citations)
    chunk_ids = {c.get("chunk_id") for c in citations}
    expected_ids = set(case.get("expected_chunk_ids", []))
    checks = {"http": reply["http_status"] == 200, "llm": answer.get("mode") == "llm",
              "status": answer.get("status") == case.get("status", "answered"),
              "language": turn.get("answer_language") == case.get("answer_language", "en"),
              "artwork": scope_matches, "source_kind": source_matches, "chunk_ids": expected_ids <= chunk_ids}
    return {"artwork_id": work["artwork_id"], "dataset_version": work["dataset_version"],
            "title": work["title"], "case": case, "http_status": reply["http_status"],
            "request_id": reply.get("request_id"), "duration_ms": reply.get("duration_ms"),
            "error": reply["body"].get("error", {}).get("code"),
            "answer_language": turn.get("answer_language"), "status": answer.get("status"),
            "text": answer.get("text"), "citations": citations, "checks": checks,
            "passed": all(checks.values())}


def published_cases(works, suggestion=None):
    kinds = ("museum_record", "guide_note", "museum_record")
    indices = (0, 1) if suggestion is None else (suggestion - 1,)
    for work in works:
        for index in indices:
            yield {"artwork_id": work["artwork_id"], "question": work["suggested_questions"][index],
                   "source_kind": kinds[index],
                   "language_override": "en", "answer_language": "en"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origin", help="Exact browser origin configured for the Go API")
    parser.add_argument("--connect-url", help="Local Vite origin for a host behind a phone hotspot forward")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--suggestion", choices=(1, 2, 3), type=int,
                           help="Test one published button on every artwork (default: first two buttons).")
    selection.add_argument("--cases", type=Path, help="JSON array of cases with artwork_id, question, source_kind "
                        "(null when no source is expected), optional status, answer_language, "
                        "language_override and expected_chunk_ids.")
    parser.add_argument("--report", type=Path, help="Create a new JSONL file with the public answers and citations.")
    args = parser.parse_args()
    origin = args.origin.rstrip("/")
    if urlsplit(origin).scheme not in ("http", "https"):
        parser.error("Supply the HTTP(S) origin of the running gallery.")
    base = (args.connect_url or origin).rstrip("/")
    works = call(build_opener(ProxyHandler({})), "GET", base + "/api/artworks")["body"]["artworks"]
    catalog = {work["artwork_id"]: work for work in works}
    cases = json.loads(args.cases.read_text()) if args.cases else list(published_cases(works, args.suggestion))
    if not cases or any(c["artwork_id"] not in catalog for c in cases):
        parser.error("Every case must identify an artwork in the active release.")
    passed = 0
    with args.report.open("x", encoding="utf-8") if args.report else nullcontext() as report:
        for case in cases:
            work = catalog[case["artwork_id"]]
            print(f"\n{work['title']} | {case.get('source_kind')}\n{case['question']}", flush=True)
            try:
                reply = run_case(work, case, origin, base)
            except (URLError, TimeoutError) as error:
                reply = {"http_status": None, "body": {"error": {"code": type(error).__name__}}}
            result = check_reply(work, case, reply)
            print(result["text"] or f"Answer unavailable ({result['error']})")
            print("Sources:", ", ".join(c.get("chunk_id", c.get("fact_id", "")) for c in result["citations"]))
            if report:
                print(json.dumps(result, ensure_ascii=False), file=report, flush=True)
            passed += result["passed"]
    print(f"\n{passed}/{len(cases)} answers met the source and response checks. Review the wording separately.")
    return int(passed != len(cases))


if __name__ == "__main__":
    raise SystemExit(main())
