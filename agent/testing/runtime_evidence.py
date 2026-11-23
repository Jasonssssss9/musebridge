"""Opt-in private metadata for controlled M2 integration; no prompt logging."""
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from time import monotonic

from agent.providers.ollama import OllamaModel
from agent.runtime.service_errors import safe_failure


class RuntimeEvidence:
    """Create an owner-only, new file outside the checkout; never append/reuse."""
    def __init__(self, path, *, include_decisions=False):
        path = Path(path).resolve()
        root = Path(__file__).resolve().parents[2]
        if path.is_relative_to(root):
            raise ValueError("Runtime evidence must be outside the repository.")
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self.file = os.fdopen(descriptor, "w", encoding="utf-8")
        self.include_decisions = include_decisions

    def record(self, event, **fields):
        self.file.write(json.dumps({"event": event, "at": datetime.now(timezone.utc).isoformat(),
                                    **fields}, ensure_ascii=False, allow_nan=False) + "\n")
        self.file.flush()

    def close(self):
        self.file.close()


class EvidenceModel(OllamaModel):
    """Count actual chat attempts, including invalid decisions, without content."""
    def __init__(self, *, evidence, **options):
        super().__init__(**options)
        self.evidence = evidence
        self.operation = ContextVar("m2_model_operation", default=None)

    async def _json(self, method, path, **options):
        operation = self.operation.get()
        if path != "/api/chat" or operation is None:
            return await super()._json(method, path, **options)
        trace = options.get("trace")
        self.evidence.record("model_http_attempt", request_id=trace, operation=operation)
        result = await super()._json(method, path, **options)
        counts = {key: result.get(key) if type(result.get(key)) is int else None
                  for key in ("prompt_eval_count", "eval_count")}
        self.evidence.record("model_http_response", request_id=trace, operation=operation, **counts)
        return result

    async def complete_structured(self, **options):
        operation = options["operation"]
        trace = options["trace_context"]["request_id"]
        token = self.operation.set(operation)
        started = monotonic()
        try:
            result = await super().complete_structured(**options)
            self.evidence.record("model_validated", request_id=trace, operation=operation,
                model_name=result.model_name, model_digest=result.model_digest,
                input_tokens=result.prompt_tokens, output_tokens=result.output_tokens,
                estimated_prompt_tokens=result.estimated_prompt_tokens,
                duration_ms=result.duration_ms, server_timings_ns=result.server_timings_ns)
            if self.evidence.include_decisions:
                self.evidence.record("private_model_decision", request_id=trace, operation=operation, decision=result.data)
            return result
        except BaseException as exc:
            safe = safe_failure(exc)
            self.evidence.record("model_failed", request_id=trace, operation=operation,
                code=safe.code, phase=safe.phase, duration_ms=int((monotonic()-started)*1000))
            raise
        finally:
            self.operation.reset(token)


class EvidenceRetrieval:
    def __init__(self, delegate, evidence):
        self.delegate, self.evidence = delegate, evidence

    @property
    def retrieval_strategy_version(self):
        return self.delegate.retrieval_strategy_version

    async def search(self, tool_input, *, request_id, remaining_seconds=3):
        started = monotonic()
        self.evidence.record("retrieval_attempt", request_id=request_id,
                             artwork_id=tool_input["artwork_id"], dataset_version=tool_input["dataset_version"])
        try:
            result = await self.delegate.search(tool_input, request_id=request_id, remaining_seconds=remaining_seconds)
            self.evidence.record("retrieval_validated", request_id=request_id,
                evidence_ids=[record["chunk_id"] for record in result["candidates"]],
                duration_ms=int((monotonic()-started)*1000))
            return result
        except BaseException as exc:
            safe = safe_failure(exc)
            self.evidence.record("retrieval_failed", request_id=request_id, code=safe.code,
                phase=safe.phase, duration_ms=int((monotonic()-started)*1000))
            raise


class EvidenceExecution:
    def __init__(self, delegate, evidence):
        self.delegate, self.evidence = delegate, evidence

    async def __call__(self, run, *, request_id, budget):
        started = monotonic()
        self.evidence.record("workflow_started", request_id=request_id, run_id=run["run_id"],
            artwork_id=run["scope"]["artwork_id"], dataset_version=run["scope"]["dataset_version"],
            base_revision=run["conversation_state"]["revision"])
        if self.evidence.include_decisions:
            self.evidence.record("private_run_input", request_id=request_id, request=run)
        try:
            result = await self.delegate(run, request_id=request_id, budget=budget)
            self.evidence.record("workflow_returned", request_id=request_id, run_id=run["run_id"],
                status=result["answer"]["status"], answer_language=result["answer_language"],
                execution=result["execution"], duration_ms=int((monotonic()-started)*1000))
            if self.evidence.include_decisions:
                self.evidence.record("private_workflow_result", request_id=request_id, run_id=run["run_id"],
                                     result=result)
            return result
        except BaseException as exc:
            safe = safe_failure(exc)
            self.evidence.record("workflow_failed", request_id=request_id, run_id=run["run_id"],
                code=safe.code, phase=safe.phase, duration_ms=int((monotonic()-started)*1000))
            if self.evidence.include_decisions:
                self.evidence.record("private_validation_failure", request_id=request_id,
                    cause_type=type(exc.__cause__).__name__, cause=str(exc.__cause__)[:2000])
            raise
