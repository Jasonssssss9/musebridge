"""Test-only M1 services: real HTTP/workflow/retrieval, scripted model decisions.

This module is never imported by production factories. It cannot run a general
chat session or verify real-model readiness, token counts or answer quality.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager, contextmanager
from copy import deepcopy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import signal
import socket
import sys
import threading

import httpx
import uvicorn

from agent.app import create_app
from agent.runtime.errors import WorkflowError
from agent.service import ModelIdentity, create_workflow_runtime
from agent.testing.v2 import (
    FixtureProfile, ScriptedModel, fixture_prompt_sizer, medium_answer, medium_plan,
)
from agent.tools.evidence import EvidenceManifest
from agent.tools.retrieval import RetrievalTool
from knowledge.http_service import create_server

MODE = "m1-scripted-model-real-http-retrieval"
QUESTION = "What material is this painting made of?"
ARTWORKS = ("aic-28560", "aic-11723")
MODEL_NAME = "SYNTHETIC-M1-TEST-ONLY"
FAULTS = ("none", "foreign-evidence")


def _private_file(path):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    return os.fdopen(descriptor, "w", encoding="utf-8")


class AuditLog:
    """Record only explicit trace/identity fields, never headers or raw bodies."""

    def __init__(self, path=None):
        self._lock = threading.Lock()
        self._events = []
        self._file = _private_file(path) if path is not None else None

    def record(self, event, **fields):
        entry = {"event": event, **fields}
        with self._lock:
            if self._file is not None:
                self._file.write(json.dumps(entry, allow_nan=False) + "\n")
                self._file.flush()
            self._events.append(deepcopy(entry))

    def snapshot(self):
        with self._lock:
            return deepcopy(self._events)

    def close(self):
        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None


class MaterialScript:
    """Stateless decisions for one English acceptance question, not inference.

    Each decision sees only the actual provider messages. Assessment requires
    an accepted passage in those messages; it cannot manufacture tool results.
    A separate ScriptedModel validates each decision against the supplied schema.
    """

    def __init__(self, audit):
        self.audit = audit

    async def complete_structured(self, **call):
        operation = call["operation"]
        payload = json.loads(call["messages"][-1]["content"])
        artwork = payload["catalog_facts"][0]["artwork_id"]
        self.audit.record("model_call", request_id=call["trace_context"]["request_id"],
                          operation=operation, artwork_id=artwork)
        if (payload["original_question"] != QUESTION or artwork not in ARTWORKS
                or payload["recent_turns"]):
            raise WorkflowError("generation_invalid_response", "Unsupported M1 test input.",
                                phase="planning")
        script_input = {
            "question": payload["original_question"],
            "context": payload["artwork_context"],
            "scope": {"artwork_id": artwork},
        }
        if operation == "plan" and not payload["cached_evidence"]:
            decision = medium_plan(script_input)
        elif operation == "assess" and payload["answer_language"] == "en":
            evidence = payload["evidence"]
            if (len(evidence) != 1 or evidence[0]["artwork_id"] != artwork
                    or evidence[0]["chunk_id"] != artwork + "-medium"
                    or len(payload["executed_queries"]) != 1):
                raise WorkflowError("generation_invalid_response", "Missing M1 lookup evidence.",
                                    phase="assessment")
            decision = medium_answer(script_input,
                text="The museum lists the medium as: " + evidence[0]["excerpt"])
        else:
            raise WorkflowError("generation_invalid_response", "Unexpected M1 model operation.",
                                phase="planning" if operation == "plan" else "assessment")
        return await ScriptedModel(decision).complete_structured(**call)


class _ObservedRetrieval:
    def __init__(self, delegate, audit):
        self.delegate, self.audit = delegate, audit

    async def search(self, tool_input, **options):
        fields = {"request_id": options["request_id"], "artwork_id": tool_input["artwork_id"]}
        try:
            result = await self.delegate.search(tool_input, **options)
        except WorkflowError as exc:
            self.audit.record("retrieval_rejected", **fields, code=exc.code,
                              phase=exc.phase, boundary="RetrievalTool.search")
            raise
        self.audit.record("retrieval_accepted", **fields,
                          evidence_ids=[item["chunk_id"] for item in result["candidates"]])
        return result


def _observe_http(server, manifest, audit, fault):
    original_handler = server.RequestHandlerClass

    class M1RetrievalHandler(original_handler):
        def _send_json(self, status, payload, **options):
            if self.command == "POST" and status == 200 and "evidence" in payload:
                original_ids = [record["chunk_id"] for record in payload["evidence"]]
                if fault == "foreign-evidence":
                    other = next(artwork for artwork in ARTWORKS if artwork != self._artwork_id)
                    payload = {**payload, "evidence": [dict(manifest.records[other + "-medium"])]}
                self._chunk_ids = [record["chunk_id"] for record in payload["evidence"]]
                audit.record("retrieval_http_response", request_id=self._request_id,
                    artwork_id=self._artwork_id, dataset_version=payload["dataset_version"],
                    status=status, original_evidence_ids=original_ids,
                    evidence_ids=self._chunk_ids, fault=fault,
                    injection_boundary="retrieval HTTP response serialization" if fault != "none" else None)
            return super()._send_json(status, payload, **options)

    server.RequestHandlerClass = M1RetrievalHandler


class _HarnessServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self):
        # The enclosing test/CLI owns signals and orderly shutdown of both servers.
        yield


@dataclass
class Services:
    agent_url: str
    retrieval_url: str
    fault: str
    audit: AuditLog
    runtime: object
    retrieval_server: object
    retrieval_thread: threading.Thread
    serving: asyncio.Task

    def connection_info(self):
        return {"event": "services_ready", "mode": MODE, "fault": self.fault,
                "agent_url": self.agent_url, "retrieval_url": self.retrieval_url,
                "dataset_version": "aic-seed-v0.1", "contract_version": "agent-run-v2",
                "model_name": MODEL_NAME}


@asynccontextmanager
async def running_services(*, service_token, fault="none", audit_path=None):
    """Start real loopback servers and close them on success, failure or cancel."""
    if fault not in FAULTS:
        raise ValueError("Unsupported M1 test fault.")
    audit = AuditLog(audit_path)
    retrieval = thread = sock = server = serving = runtime = None
    try:
        manifest = EvidenceManifest.load()
        retrieval = create_server("127.0.0.1", 0)
        _observe_http(retrieval, manifest, audit, fault)
        thread = threading.Thread(target=retrieval.serve_forever,
                                  kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        retrieval_url = f"http://127.0.0.1:{retrieval.server_address[1]}"
        runtime = create_workflow_runtime(
            model=MaterialScript(audit), profile=FixtureProfile(),
            prompt_sizer=fixture_prompt_sizer, manifest=manifest,
            retrieval=_ObservedRetrieval(RetrievalTool(manifest, base_url=retrieval_url), audit),
            identity=ModelIdentity(MODEL_NAME, "SYNTHETIC-NOT-A-REAL-MODEL-DIGEST"),
            readiness=lambda: True,
        )
        app = create_app(runtime, service_token=service_token)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        agent_url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        server = _HarnessServer(uvicorn.Config(app, log_config=None, log_level="error",
            access_log=False, lifespan="on", http="h11", ws="none", timeout_graceful_shutdown=1))
        serving = asyncio.create_task(server.serve(sockets=[sock]), name="m1-agent-http")
        async with asyncio.timeout(5):
            while not server.started:
                if serving.done():
                    await serving
                    raise RuntimeError("The M1 Agent stopped during startup.")
                await asyncio.sleep(0.01)
            async with httpx.AsyncClient(trust_env=False, timeout=2) as client:
                for origin in (retrieval_url, agent_url):
                    response = await client.get(origin + "/readyz")
                    response.raise_for_status()
                    if response.json()["dataset_version"] != manifest.dataset_version:
                        raise RuntimeError("The M1 dataset readiness check failed.")
        services = Services(agent_url, retrieval_url, fault, audit, runtime, retrieval, thread, serving)
        audit.record("services_ready", mode=MODE, fault=fault, dataset_version=manifest.dataset_version)
        yield services
    finally:
        try:
            if server is not None:
                server.should_exit = True
            if runtime is not None:
                await runtime.close(drain_seconds=0.25)
            try:
                if serving is not None:
                    try:
                        await asyncio.wait_for(asyncio.shield(serving), 5)
                    except (TimeoutError, asyncio.CancelledError):
                        serving.cancel()
                        await asyncio.gather(serving, return_exceptions=True)
                        raise
            finally:
                if sock is not None:
                    sock.close()
        finally:
            if retrieval is not None:
                if thread is not None and thread.ident is not None:
                    await asyncio.to_thread(retrieval.shutdown)
                    await asyncio.to_thread(thread.join, 5)
                retrieval.server_close()
            audit.record("services_stopped", mode=MODE)
            audit.close()


async def _run_cli(directory, service_token, fault):
    # Runtime reports belong outside the checkout, never in source control.
    root = Path(__file__).resolve().parents[2]
    directory = directory.resolve()
    if directory == root or root in directory.parents:
        raise ValueError("M1 runtime files must be outside the repository.")
    directory.mkdir(mode=0o700)
    connection_file = directory / "connection.json"
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        async with running_services(service_token=service_token, fault=fault,
                                    audit_path=directory / "audit.jsonl") as services:
            info = services.connection_info()
            with _private_file(connection_file) as stream:
                json.dump(info, stream)
                stream.write("\n")
            print(json.dumps(info), flush=True)
            stopping = asyncio.create_task(stop.wait())
            try:
                done, _ = await asyncio.wait((stopping, services.serving),
                                              return_when=asyncio.FIRST_COMPLETED)
                if services.serving in done:
                    await services.serving
                    raise RuntimeError("The M1 Agent stopped unexpectedly.")
            finally:
                stopping.cancel()
                await asyncio.gather(stopping, return_exceptions=True)
    finally:
        connection_file.unlink(missing_ok=True)
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
    print(json.dumps({"event": "services_stopped", "mode": MODE}), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True,
                        help="New private directory outside the repository; parent must exist.")
    parser.add_argument("--fault", choices=FAULTS, default="none",
                        help="Test-only corruption at retrieval HTTP response serialization.")
    args = parser.parse_args(argv)
    try:
        asyncio.run(_run_cli(args.state_dir, os.environ.get("AGENT_SERVICE_TOKEN", ""), args.fault))
    except Exception as exc:
        # Configuration, headers and raw exception messages may contain secrets.
        print(json.dumps({"event": "startup_or_runtime_failed", "error_type": type(exc).__name__}),
              file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
