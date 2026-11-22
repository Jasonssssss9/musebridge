"""Run frozen visitor messages through the public API and retain private evidence."""
import argparse
from collections import Counter
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPCookieProcessor, HTTPRedirectHandler, ProxyHandler
from uuid import uuid4

from agent.contracts.codec import ContractCodec
from agent.testing.m2_smoke import ROOT, call, save
from acceptance.evaluation import (GROUPS, check_independent_coverage, development_schedule, public_checks,
                                   schedule, schedule_documents, trace_verdict)
from acceptance.reviewed_suite import digest, frozen_inputs, suite_directory


VERSION = "agent-demo-v0.3"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class RuntimeLog:
    """Consume complete event lines only, retaining observations for the submitted trace."""
    def __init__(self, path):
        self.file = path.open(encoding="utf-8")
        self.identity = {}
        self.observed = set()
        self.events_for(None)
        if not self.identity:
            self.file.close()
            raise ValueError("The runtime log needs an integration_ready event before evaluation.")

    def events_for(self, request_id):
        events = []
        while True:
            position = self.file.tell()
            line = self.file.readline()
            if not line.endswith("\n"):
                self.file.seek(position)
                return events
            event = json.loads(line)
            self.observed.add(event["event"])
            if event["event"] == "integration_ready":
                self.identity = {key: event[key] for key in ("model_digest", "profile_fingerprint",
                                                             "implementation_fingerprint")}
            if request_id and event.get("request_id") == request_id:
                events.append(event)


class PublicVisitor:
    def __init__(self, origin):
        self.origin = origin
        self.client = build_opener(ProxyHandler({}), NoRedirect(), HTTPCookieProcessor(CookieJar()))
        self.codec = ContractCodec()
        self.conversations = {}
        self.turns = {}
        self.projected_state = {}

    def request(self, method, path, body=None):
        return call(self.client, method, self.origin + path, body, self.origin if body is not None else None)

    def open(self, artwork):
        reply = self.request("POST", "/api/conversations", {"contract_version": VERSION, "artwork_id": artwork})
        if reply["http_status"] != 200:
            raise ValueError("Cannot create or restore the conversation.")
        self.codec.validate("PublicConversationResponse", reply["body"])
        opened = reply["body"]["conversation"]
        before = self.conversations.get(artwork)
        if opened["artwork_id"] != artwork or (before is None and opened["revision"] != 0) or (before and before != opened):
            raise ValueError("Restored conversation differs from committed public state.")
        self.conversations[artwork] = opened
        return opened

    @staticmethod
    def path(conversation):
        return f"/api/conversations/{conversation['conversation_id']}/messages"

    def history(self, conversation):
        reply = self.request("GET", self.path(conversation))
        if reply["http_status"] != 200:
            raise ValueError("Conversation history is unavailable.")
        self.codec.validate("PublicHistoryResponse", reply["body"])
        return reply["body"]

    def send(self, row, step, traces):
        artwork = step["artwork_id"]
        before = self.conversations.get(artwork) or self.open(artwork)
        message = {"contract_version": VERSION, "expected_revision": before["revision"], "question": step["text"],
                   "client_message_id": str(uuid4()), "language_override": step["language_override"]}
        response = self.request("POST", self.path(before), message)
        row.update(request_id=response["request_id"], duration_ms=response["duration_ms"],
                   http_status=response["http_status"], public_response=response["body"])
        if response["http_status"] != 200:
            row["outcome"] = "failed"
            events = traces.events_for(row["request_id"])
            row["trace"] = trace_verdict(step["expect"], events, {})
            history = self.history(before)
            row["checks"] = {"failure_state_unchanged": history["conversation"] == before
                             and history["turns"] == self.turns.get(artwork, [])}
            return
        self.codec.validate("PublicSubmissionResponse", response["body"])
        checks = public_checks(step["expect"], before, message, response["body"])
        after, turn = response["body"]["conversation"], response["body"]["turn"]
        history = self.history(after)
        expected_turns = [*self.turns.get(artwork, []), turn]
        checks["committed_history"] = (history["conversation"] == after and history["pending_request"] is None
            and history["turns"] == expected_turns and len(expected_turns) == after["revision"])
        row.update(checks=checks, outcome="delivered" if checks["scope_and_revision"] and checks["committed_history"]
                   else "unverified_commit")
        if row["outcome"] == "delivered":
            self.conversations[artwork] = after
            self.turns[artwork] = expected_turns
        try:
            trace = trace_verdict(step["expect"], traces.events_for(row["request_id"]), response["body"])
        except (ValueError, AssertionError, KeyError) as exc:
            row["trace_error"] = type(exc).__name__
            return
        row["trace"] = trace
        if row["outcome"] == "delivered" and trace["evidence"]:
            prior = trace["evidence"]["previous_state"]
            if artwork in self.projected_state:
                checks["observed_previous_state_matches"] = self.projected_state[artwork] == prior
            self.projected_state[artwork] = trace["evidence"]["projected_state_after_public_commit"]

    def navigate(self, row, step):
        # Restore the same cookie-backed conversation via the public API. DOM
        # rendering, viewport and waiting behavior require independent browser review.
        conversation = self.open(step["artwork_id"])
        history = self.history(conversation)
        consistent = (history["conversation"] == conversation and history["pending_request"] is None
                      and history["turns"] == self.turns.get(step["artwork_id"], []))
        row.update(outcome="api_restored" if consistent else "failed", browser_observation=None,
                   conversation_id=conversation["conversation_id"], revision=conversation["revision"])


def execute(journeys, origin, traces, record, visitor_factory=PublicVisitor):
    for group, key, category, steps in journeys:
        visitor = visitor_factory(origin)
        blocked = False
        for step in steps:
            row = {"id": f"{group}/{key}/step-{step['step']}", "group": group, "category": category,
                   "artwork_id": step["artwork_id"], "kind": step["kind"], "outcome": "blocked",
                   "input_language": step.get("language"), "question": step.get("text"),
                   "language_override": step.get("language_override"), "instruction_label": step.get("instruction"),
                   "expected": step["expect"], "visitor_intent": step.get("intent")}
            if not blocked:
                try:
                    if step["kind"] == "message":
                        visitor.send(row, step, traces)
                    else:
                        row["browser_action"] = step["action"]
                        visitor.navigate(row, step)
                except (ValueError, URLError, OSError) as exc:
                    row.update(outcome="error", error_type=type(exc).__name__)
                blocked = row["outcome"] not in ("delivered", "api_restored")
            record(row)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "run"))
    parser.add_argument("--group", choices=("all", *GROUPS, "development"), default="all")
    parser.add_argument("--suite-root", type=Path, help="External official worktree or versioned suite directory")
    parser.add_argument("--review-record", type=Path, help="References to the completed reviews for this freeze")
    parser.add_argument("--stack", type=Path, help="Private directory of the running controlled M2 stack")
    parser.add_argument("--output", type=Path, help="New private directory outside this checkout")
    options = parser.parse_args()
    development = options.group == "development"
    identity = {"suite_sha256": None, "review_sha256": None}
    try:
        directory = None if development else suite_directory(options.suite_root)
        if development:
            plans = list(development_schedule())
        elif options.command == "run":
            identity, documents = frozen_inputs(directory, options.review_record)
            check_independent_coverage(*documents)
            plans = list(schedule_documents(*documents, options.group))
        else:
            plans = list(schedule(directory, options.group))
    except (OSError, ValueError) as exc:
        parser.error(f"{exc} No visitor messages sent.")
    expected = Counter(step["kind"] for _, _, _, steps in plans for step in steps)
    if options.command == "plan":
        print(json.dumps({"group": options.group, "fresh_visitors": len(plans), "steps": expected,
                          "model_calls_made": 0, "manifest_present": bool(directory and (directory / "MANIFEST.json").is_file())}))
        return 0
    if not options.stack or not options.output:
        parser.error("run requires --stack and --output")
    output = options.output.resolve()
    if output.is_relative_to(ROOT) or output.exists():
        parser.error("Use a new private output directory outside this repository.")
    config = json.loads((options.stack / "connection.json").read_text(encoding="utf-8"))
    origin = config["web_url"]
    site = urlsplit(origin)
    if site.scheme != "http" or site.hostname != "127.0.0.1" or site.username or site.password or site.path or site.query or site.fragment:
        parser.error("Use the controlled loopback Vite origin.")
    traces = RuntimeLog(options.stack / "agent-events.jsonl")
    if not {"private_run_input", "private_model_decision", "private_workflow_result"} <= traces.observed:
        traces.file.close()
        parser.error("The stack needs a successful private-decisions development run before this evaluation.")
    output.mkdir(mode=0o700)
    evaluator_dir = Path(__file__).resolve().parent
    run = {"group": options.group, "evaluator_sha256": {
               name: digest(evaluator_dir / name) for name in ("evaluation.py", "run_evaluation.py", "reviewed_suite.py")},
           **identity,
           "source_sha256": digest(options.stack / "source-manifest.json"), "identity": traces.identity,
           "tokenizer_sha256": config["tokenizer_sha256"], "planned": expected}
    save(output / "run.json", run)
    counts = Counter()
    machine_failures = 0
    incomplete_traces = 0
    try:
        descriptor = os.open(output / "turns.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as ledger:
            def record(row):
                nonlocal machine_failures, incomplete_traces
                if row["outcome"] == "delivered":
                    trace = row.get("trace", {})
                    checks = {**row["checks"], **trace.get("checks", {})}
                    machine_failures += any(value is False for value in checks.values())
                    incomplete_traces += int(not trace.get("evidence"))
                ledger.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                ledger.flush()
                counts[row["outcome"]] += 1
            execute(plans, origin, traces, record)
    finally:
        traces.file.close()
    print(json.dumps({"group": options.group, "outcomes": counts, "machine_failed_turns": machine_failures,
                      "unobserved_private_results": incomplete_traces, "semantic_review": "pending"}))
    return int(bool(machine_failures or incomplete_traces or
                    any(counts[k] for k in ("blocked", "unverified_commit", "failed", "error"))))


if __name__ == "__main__":
    raise SystemExit(main())
