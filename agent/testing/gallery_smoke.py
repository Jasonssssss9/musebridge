"""Ask the first museum question and viewing prompt for each work through live Go."""
import argparse
from http.cookiejar import CookieJar
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, ProxyHandler, build_opener
from uuid import uuid4

from agent.testing.m2_smoke import call


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origin", help="Exact browser origin used by the Go API")
    parser.add_argument("--connect-url", help="Local Vite origin for a host behind a phone hotspot forward")
    args = parser.parse_args()
    origin = args.origin.rstrip("/")
    if urlsplit(origin).scheme not in ("http", "https"):
        parser.error("Supply the HTTP(S) origin of the running gallery.")
    base = (args.connect_url or origin).rstrip("/")
    client = build_opener(ProxyHandler({}))
    works = call(client, "GET", base + "/api/artworks")["body"]["artworks"]
    passed = total = 0
    for work in works:
        for question, kind in zip(work["suggested_questions"][:2], ("museum_record", "guide_note"), strict=True):
            total += 1
            client = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
            opened = call(client, "POST", base + "/api/conversations", {
                "contract_version": "agent-demo-v0.3", "artwork_id": work["artwork_id"],
            }, origin)
            print(f"\n{work['title']} | {kind}\n{question}", flush=True)
            if "conversation" not in opened["body"]:
                print("Conversation unavailable:", opened["body"].get("error", {}).get("code", opened["http_status"]))
                continue
            conversation = opened["body"]["conversation"]
            reply = call(client, "POST", f"{base}/api/conversations/{conversation['conversation_id']}/messages", {
                "contract_version": "agent-demo-v0.3", "client_message_id": str(uuid4()),
                "expected_revision": conversation["revision"], "language_override": "en", "question": question,
            }, origin)
            if "turn" not in reply["body"]:
                print("Answer unavailable:", reply["body"].get("error", {}).get("code", reply["http_status"]))
                continue
            turn = reply["body"]["turn"]
            answer = turn["answer"]
            print(answer["text"])
            print("Sources:", ", ".join(c.get("chunk_id", c.get("fact_id", "")) for c in answer["citations"]))
            if (answer["mode"] == "llm" and answer["status"] == "answered"
                    and turn["answer_language"] == "en" and answer["citations"]
                    and all(c["artwork_id"] == work["artwork_id"] and c.get("source_kind") == kind
                            for c in answer["citations"])):
                passed += 1
    print(f"\n{passed}/{total} answers met the source and response checks. Review the printed wording separately.")
    return int(total == 0 or passed != total)


if __name__ == "__main__":
    raise SystemExit(main())
