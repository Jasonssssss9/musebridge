"""Run controlled real-Qwen development journeys through Vite and public Go."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import build_opener, HTTPCookieProcessor, ProxyHandler, Request
from uuid import uuid4

from agent.contracts.codec import ContractCodec
from agent.providers.profile import OllamaProfile, TOKENIZER_SHA256

ROOT = Path(__file__).resolve().parents[2]


def port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def save(path, value):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def call(opener, method, url, body=None, origin=None):
    headers = {}
    if body is not None:
        headers = {"Content-Type": "application/json", "Origin": origin}
    request = Request(url, data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
                      method=method, headers=headers)
    started = time.monotonic()
    try:
        response = opener.open(request, timeout=70)
    except HTTPError as error:
        response = error
    with response:
        data = response.read(1024*1024)
        return {"http_status": response.status, "request_id": response.headers.get("X-Request-ID"),
                "duration_ms": round((time.monotonic()-started)*1000), "body": json.loads(data)}


class Processes:
    def __init__(self, directory):
        self.directory, self.children, self.logs = directory, [], []

    def start(self, name, command, environment, cwd=ROOT):
        log = os.fdopen(os.open(self.directory / f"{name}.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb")
        self.logs.append(log)
        child = subprocess.Popen(command, cwd=cwd, env=environment, stdout=log,
                                 stderr=subprocess.STDOUT, start_new_session=True)
        self.children.append((name, child))
        return child

    def wait_ready(self, url, child, timeout=180):
        client = build_opener(ProxyHandler({}))
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise RuntimeError("An integration child exited before readiness; inspect its private log.")
            try:
                with client.open(url, timeout=2) as response:
                    if response.status == 200:
                        return
            except (URLError, TimeoutError, OSError):
                pass
            time.sleep(.2)
        raise RuntimeError("An integration service did not become ready within its preparation budget.")

    def close(self):
        for _, child in reversed(self.children):
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
        for _, child in reversed(self.children):
            try:
                child.wait(timeout=70)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)
        for log in self.logs:
            log.close()


def journeys():
    """Public development checks, never independent or held-out evaluation."""
    materials = {"en": "What material is this painting made of?", "es": "¿De qué material está hecha esta pintura?",
                 "zh-CN": "这幅画是什么材质？", "ko": "이 그림은 어떤 재료로 그렸나요?"}
    thanks = {"en": "Thank you!", "es": "¡Muchas gracias!", "zh-CN": "谢谢你的介绍！", "ko": "설명해 주셔서 감사합니다!"}
    for artwork in ("aic-28560", "aic-11723"):
        for language, question in materials.items():
            yield {"name": f"{artwork}-material-{language}", "artwork": artwork,
                   "turns": [{"question": question, "language": language, "status": "answered",
                              "sources": [artwork+"-medium"]}]}
        yield {"name": artwork+"-followup", "artwork": artwork, "turns": [
            {"question": materials["en"], "language": "en", "status": "answered", "sources": [artwork+"-medium"]},
            {"question": "Could you remind me of that material?", "language": "en", "status": "answered", "sources": [artwork+"-medium"]}]}
        yield {"name": artwork+"-compound", "artwork": artwork, "turns": [
            {"question": "What is the painting made of, and how many hours did the artist spend painting it?", "language": "en",
             "status": "partially_answered", "sources": [artwork+"-medium"]}]}
    for language, question in thanks.items():
        yield {"name": "direct-"+language, "artwork": "aic-28560", "turns": [
            {"question": question, "language": language, "status": "conversational", "sources": []}]}
    yield {"name": "catalog", "artwork": "aic-11723", "turns": [
        {"question": "Who painted this work?", "language": "en", "status": "answered", "sources": ["aic-11723-catalog-artist"]}]}
    yield {"name": "scope", "artwork": "aic-28560", "turns": [
        {"question": "What is today's weather in Seattle?", "language": "en", "status": "out_of_scope", "sources": []}]}
    yield {"name": "evidence-gap", "artwork": "aic-28560", "turns": [
        {"question": "How much did the first buyer pay for this painting?", "language": "en", "status": "insufficient", "sources": []}]}
    yield {"name": "language-switch", "artwork": "aic-11723", "turns": [
        {"question": materials["zh-CN"], "language": "zh-CN", "status": "answered", "sources": ["aic-11723-medium"]},
        {"question": materials["es"], "language": "es", "language_override": "es", "status": "answered", "sources": ["aic-11723-medium"]},
        {"question": materials["ko"], "language": "ko", "language_override": "ko", "status": "answered", "sources": ["aic-11723-medium"]}]}


def exercise(base_url, records=None, prefixes=None):
    codec = ContractCodec()
    if records is None:
        records = []
    for journey in journeys():
        if prefixes and not any(journey["name"].startswith(prefix) for prefix in prefixes):
            continue
        opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
        opened = call(opener, "POST", base_url+"/api/conversations", {
            "contract_version": "agent-demo-v0.3", "artwork_id": journey["artwork"]}, base_url)
        codec.validate("PublicConversationResponse", opened["body"])
        conversation = opened["body"]["conversation"]
        path = base_url+f"/api/conversations/{conversation['conversation_id']}/messages"
        for index, turn in enumerate(journey["turns"]):
            message = {"contract_version": "agent-demo-v0.3", "client_message_id": str(uuid4()),
                       "question": turn["question"], "language_override": turn.get("language_override", turn["language"] if index == 0 else None),
                       "expected_revision": conversation["revision"]}
            reply = call(opener, "POST", path, message, base_url)
            row = {"name": journey["name"], "turn": index+1, "artwork_id": journey["artwork"],
                   "input": message, "expected": turn, "response": reply, "machine_pass": False,
                   "semantic_review": "pending"}
            if reply["http_status"] == 200:
                codec.validate("PublicSubmissionResponse", reply["body"])
                actual = reply["body"]["turn"]
                sources = [source.get("chunk_id", source.get("fact_id")) for source in actual["answer"]["citations"]]
                row["machine_pass"] = (actual["answer"]["status"] == turn["status"]
                    and actual["answer_language"] == turn["language"] and sorted(sources) == sorted(turn["sources"])
                    and reply["body"]["conversation"]["revision"] == conversation["revision"]+1)
                conversation = reply["body"]["conversation"]
                replay = call(opener, "POST", path, message, base_url)
                row["replay_pass"] = replay["http_status"] == 200 and replay["body"]["turn"] == actual
                row["replay_request_id"] = replay["request_id"]
                history = call(opener, "GET", path)
                codec.validate("PublicHistoryResponse", history["body"])
                row["history_pass"] = history["body"]["conversation"] == conversation and len(history["body"]["turns"]) == conversation["revision"]
            else:
                row["error"] = reply["body"].get("error")
                history = call(opener, "GET", path)
                row["failure_atomicity_pass"] = history["body"]["conversation"]["revision"] == conversation["revision"]
            records.append(row)
            print(json.dumps({"case": row["name"], "turn": index+1, "http_status": reply["http_status"],
                              "request_id": reply["request_id"], "machine_pass": row["machine_pass"]}), flush=True)
            if not row["machine_pass"] and index+1 < len(journey["turns"]):
                records.append({"name": journey["name"], "turn": index+2, "blocked": "prior turn did not establish expected context"})
                break
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tokenizer", required=True)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--hold-seconds", type=int, default=0, help="Keep owned services available for a subsequent browser check.")
    parser.add_argument("--case-prefix", action="append", help="Run only matching published development journeys.")
    parser.add_argument("--private-decisions", action="store_true", help="Explicitly retain development input/decisions in the private report directory.")
    options = parser.parse_args()
    directory = Path(options.report_dir).resolve()
    if directory.is_relative_to(ROOT):
        parser.error("Report directory must be outside the repository.")
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    if hashlib.sha256(Path(options.tokenizer).read_bytes()).hexdigest() != TOKENIZER_SHA256:
        parser.error("Tokenizer does not match the pinned profile.")
    processes, report = Processes(directory), {"mode": "controlled-real-ollama-development", "m2_complete": False,
        "quality_review": "pending", "created_at": datetime.now(timezone.utc).isoformat()}
    def interrupted(_signal, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    environment = dict(os.environ)
    environment.update(PYTHONDONTWRITEBYTECODE="1", AGENT_SERVICE_TOKEN=secrets.token_urlsafe(32),
        AGENT_INTEGRATION_MODE="controlled-ollama", AGENT_TOKENIZER_PATH=str(Path(options.tokenizer).resolve()),
        OLLAMA_BASE_URL=options.ollama_url, AGENT_RUNTIME_EVIDENCE=str(directory/"agent-events.jsonl"),
        DATASET_VERSION="aic-seed-v0.1", SEED_PATH=str(ROOT/"knowledge/data/aic-seed-v0.1.json"))
    # Never inherit a caller's raw-decision switch accidentally.
    environment["AGENT_PRIVATE_DECISIONS"] = "development-only" if options.private_decisions else "disabled"
    try:
        report["base_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        tracked = subprocess.check_output(["git", "ls-files", "api", "agent", "knowledge", "web/src", "web/vite.config.ts", "docs/contracts"], cwd=ROOT, text=True).splitlines()
        tracked += subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "api", "agent", "knowledge", "web/scripts"], cwd=ROOT, text=True).splitlines()
        files = {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in tracked if (ROOT/name).is_file()}
        for name in ("agent/testing/runtime_evidence.py", "agent/testing/m2_smoke.py"):
            files[name] = hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
        save(directory/"source-manifest.json", files)
        report["source_manifest_sha256"] = hashlib.sha256((directory/"source-manifest.json").read_bytes()).hexdigest()
        api = directory/"musebridge-api"
        print("Building the existing Go API; starting real retrieval, Agent and Vite.", flush=True)
        build = subprocess.run(["go", "build", "-o", str(api), "./cmd/server"], cwd=ROOT/"api", env=environment,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
        if build.returncode:
            raise RuntimeError("Go API build failed; ensure existing pinned dependencies are installed.")
        report["api_binary_sha256"] = hashlib.sha256(api.read_bytes()).hexdigest()
        retrieval_port, agent_port, api_port, web_port = port(), port(), port(), port()
        retrieval_url, agent_url, api_url, web_url = [f"http://127.0.0.1:{p}" for p in (retrieval_port, agent_port, api_port, web_port)]
        environment.update(RETRIEVAL_BASE_URL=retrieval_url, AGENT_BASE_URL=agent_url,
            GENERATION_MODE="agent", PUBLIC_BASE_URL=web_url, COOKIE_SECURE="false", HOST="127.0.0.1", PORT=str(api_port),
            PUBLIC_CONTRACT="agent", API_TARGET=api_url)
        report["configuration"] = {"retrieval_url": retrieval_url, "agent_url": agent_url, "api_url": api_url, "web_url": web_url,
                                   "profile": OllamaProfile().__dict__, "tokenizer_sha256": TOKENIZER_SHA256}
        retrieval = processes.start("retrieval", [sys.executable, "-B", "-m", "knowledge.http_service", "--host", "127.0.0.1", "--port", str(retrieval_port)], environment)
        processes.wait_ready(retrieval_url+"/readyz", retrieval, 15)
        agent = processes.start("agent", [sys.executable, "-B", "-m", "uvicorn", "agent.testing.integration_app:create_app", "--factory", "--host", "127.0.0.1", "--port", str(agent_port), "--workers", "1", "--no-access-log", "--timeout-graceful-shutdown", "65"], environment)
        processes.wait_ready(agent_url+"/readyz", agent)
        application = processes.start("api", [str(api)], environment)
        processes.wait_ready(api_url+"/readyz", application, 15)
        frontend_environment = {key: value for key, value in environment.items()
                                if not key.startswith(("AGENT_", "OLLAMA_"))}
        web = processes.start("web", [shutil.which("node"), str(ROOT/"web/node_modules/vite/bin/vite.js"), "--host", "127.0.0.1", "--port", str(web_port)], frontend_environment, cwd=ROOT/"web")
        if web.poll() is not None:
            raise RuntimeError("Vite exited before the public proxy was available.")
        processes.wait_ready(web_url, web, 20)
        save(directory/"connection.json", report["configuration"])
        report["cases"] = []
        exercise(web_url, report["cases"], options.case_prefix)
        client = build_opener(ProxyHandler({}))
        report["readiness"] = {name: call(client, "GET", url+"/readyz") for name, url in (("go", api_url), ("agent", agent_url), ("retrieval", retrieval_url))}
        report["ollama_state"] = call(client, "GET", options.ollama_url+"/api/ps")["body"]
        if options.hold_seconds:
            print("Development smoke finished; owned services are available for browser checks.", flush=True)
            until = time.monotonic()+min(max(options.hold_seconds, 0), 3600)
            while time.monotonic()<until and not (directory/"release").exists():
                time.sleep(.2)
    except BaseException as error:
        report["fatal_error"] = type(error).__name__
        print("Integration stopped; inspect the private report and service logs.", flush=True)
    finally:
        processes.close()
        report["owned_services_stopped"] = True
        (directory/"connection.json").unlink(missing_ok=True)
        if (directory/"agent-events.jsonl").exists():
            events = [json.loads(line) for line in (directory/"agent-events.jsonl").read_text().splitlines()]
            for case in report.get("cases", []):
                trace = case.get("response", {}).get("request_id")
                joined = [event for event in events if event.get("request_id") == trace]
                case["runtime_counts"] = dict(Counter(event["event"] for event in joined))
                returned = [event for event in joined if event["event"] == "workflow_returned"]
                if returned:
                    execution = returned[-1]["execution"]
                    case["execution"] = execution
                    case["runtime_counts_match"] = (case["runtime_counts"].get("model_http_attempt", 0) == execution["model_calls"]
                        and case["runtime_counts"].get("retrieval_attempt", 0) == execution["retrieval_calls"])
                if case.get("replay_request_id"):
                    case["replay_no_inference"] = not any(event.get("request_id") == case["replay_request_id"]
                        and event["event"] in {"model_http_attempt", "retrieval_attempt"} for event in events)
            report["event_counts"] = dict(Counter(event["event"] for event in events))
        credential = environment["AGENT_SERVICE_TOKEN"].encode()
        report["credential_scan"] = "passed"
        for path in directory.rglob("*"):
            if path.is_file() and path.suffix in {".json", ".jsonl", ".log"} and credential in path.read_bytes():
                report["credential_scan"] = "failed"
                report["fatal_error"] = "credential_leak"
        save(directory/"report.json", report)
        print(json.dumps({"report_saved": True, "attempted": len([c for c in report.get('cases',[]) if 'response' in c]),
                          "machine_passed": sum(c.get("machine_pass", False) for c in report.get("cases", [])),
                          "formal_m2_acceptance": "pending"}), flush=True)
    return int("fatal_error" in report or any(not c.get("machine_pass", False) for c in report.get("cases", [])))


if __name__ == "__main__":
    raise SystemExit(main())
