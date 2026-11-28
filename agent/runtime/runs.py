"""Bounded in-memory executions owned by runs rather than HTTP connections."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
import json
import math
from time import monotonic

from agent.contracts.codec import ContractCodec, PRIVATE_VERSION, WORKFLOW_ID, PROFILE_ID
from agent.contracts.validation import check_run_result
from agent.runtime.budgets import RunBudget
from agent.runtime.errors import WorkflowError
from agent.runtime.service_errors import failure, safe_failure
from agent.runtime.validation import ContractViolation


def validate_run_shape(value: dict, codec: ContractCodec, *, private_version=PRIVATE_VERSION,
                       workflow_id=WORKFLOW_ID) -> dict:
    """Check types without losing an unsupported identifier's original value.

    Existing-run identity must precede new-run profile/readiness checks. Only
    well-typed identity strings are substituted in this validation copy.
    """
    try:
        original = deepcopy(value)
        shape = deepcopy(value)
        if type(shape) is not dict or type(shape.get("contract_version")) is not str:
            raise ContractViolation("Invalid contract identity.")
        execution = shape.get("execution")
        if type(execution) is not dict or any(type(execution.get(key)) is not str
                                              for key in ("workflow_id", "profile_id")):
            raise ContractViolation("Invalid execution identity.")
        shape["contract_version"] = private_version
        execution.update(workflow_id=workflow_id, profile_id=PROFILE_ID)
        codec.validate("RunRequest", shape)
        if shape["question"] != shape["question"].strip():
            raise ContractViolation("Original text must arrive trimmed.")
        return original
    except (ContractViolation, TypeError, KeyError) as exc:
        raise failure("invalid_request") from exc


@dataclass
class _Run:
    request: dict
    identity: str
    budget: RunBudget
    snapshot: dict
    admitted_at: float
    task: asyncio.Task | None = None
    worker: asyncio.Task | None = None
    terminal_at: float | None = None
    quiescent: bool = False


class RunRegistry:
    def __init__(self, executor=None, *, validate_input=None, ready=None,
                 active_limit: int = 8, terminal_limit: int = 1000,
                 retention_seconds: float = 600, clock=monotonic, wall_clock=None,
                 codec=None, private_version=PRIVATE_VERSION, workflow_id=WORKFLOW_ID,
                 check_result=check_run_result, shared_with=None):
        if (type(active_limit) is not int or not 1 <= active_limit <= 8
                or type(terminal_limit) is not int or not 1 <= terminal_limit <= 1000
                or not math.isfinite(retention_seconds) or not 0 < retention_seconds <= 600):
            raise ValueError("Run limits must stay within the v2 profile.")
        self.executor = executor
        self.validate_input = validate_input
        self.ready = ready or (lambda: False)
        self.active_limit = active_limit
        self.terminal_limit = terminal_limit
        self.retention_seconds = retention_seconds
        self.clock, self.wall_clock = clock, wall_clock
        self.codec = codec or ContractCodec()
        self.private_version, self.workflow_id = private_version, workflow_id
        self.check_result = check_result
        # Both API versions admit work to the same pool and reserve each run UUID once.
        self._runs: dict[str, _Run] = {} if shared_with is None else shared_with._runs
        self._lock = asyncio.Lock() if shared_with is None else shared_with._lock
        self._closing = False

    def is_ready(self) -> bool:
        try:
            return not self._closing and self.executor is not None and self.validate_input is not None and self.ready() is True
        except Exception:
            return False

    def _finish(self, record: _Run, *, result=None, error=None) -> None:
        if record.snapshot["state"] != "processing":
            return
        record.snapshot = {"contract_version": record.request["contract_version"],
                           "run_id": record.request["run_id"],
                           "state": "completed" if error is None else "failed",
                           "result": deepcopy(result), "error": error.as_dict() if error else None}
        record.terminal_at = self.clock()

    def _prune(self) -> None:
        now = self.clock()
        for record in self._runs.values():
            if record.snapshot["state"] == "processing" and now >= record.budget.deadline:
                record.budget.cancel()
                self._finish(record, error=failure("request_timeout", "validation"))
                if record.task is not None:
                    record.task.cancel()
                if record.worker is not None:
                    record.worker.cancel()
        terminals = sorted((record.terminal_at, key) for key, record in self._runs.items()
                           if record.terminal_at is not None
                           and (record.quiescent or (record.task is not None and record.task.done()
                                and (record.worker is None or record.worker.done()))))
        for terminal_at, key in terminals:
            if now - terminal_at >= self.retention_seconds:
                del self._runs[key]
        terminals = [(at, key) for at, key in terminals if key in self._runs]
        for _, key in terminals[:max(0, len(terminals) - self.terminal_limit)]:
            del self._runs[key]

    @staticmethod
    def _terminal(record: _Run) -> dict:
        if record.snapshot["state"] == "failed":
            error = record.snapshot["error"]
            raise WorkflowError(error["code"], error["message"], retryable=error["retryable"], phase=error["phase"])
        return deepcopy(record.snapshot)

    async def submit(self, request: dict, *, request_id: str) -> dict:
        request = validate_run_shape(request, self.codec, private_version=self.private_version,
                                     workflow_id=self.workflow_id)
        identity = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        async with self._lock:
            self._prune()
            existing = self._runs.get(request["run_id"])
            if existing is not None:
                if existing.request["contract_version"] != self.private_version or existing.identity != identity:
                    raise failure("agent_run_conflict")
                if existing.snapshot["state"] == "processing":
                    raise failure("agent_run_in_progress")
                return self._terminal(existing)
            admitted_at = self.clock()
            budget = RunBudget.from_utc(request["execution"]["deadline_at"], clock=self.clock, wall_clock=self.wall_clock)
            if (request["contract_version"] != self.private_version
                    or request["execution"]["workflow_id"] != self.workflow_id
                    or request["execution"]["profile_id"] != PROFILE_ID):
                raise failure("agent_contract_mismatch")
            if self.validate_input is not None:
                self.validate_input(deepcopy(request))
            if not self.is_ready():
                raise failure("service_not_ready", "capacity")
            # Count still-running workers too, even if a timed-out run is already
            # terminal. A noncooperative injected worker cannot bypass the cap.
            active = sum(record.snapshot["state"] == "processing"
                         or (record.task is not None and not record.task.done())
                         or (record.worker is not None and not record.worker.done())
                         for record in self._runs.values())
            if active >= self.active_limit:
                raise failure("agent_busy", "capacity")
            budget.check_active()
            record = _Run(request=request, identity=identity, budget=budget, admitted_at=admitted_at,
                          snapshot={"contract_version": self.private_version, "run_id": request["run_id"],
                                    "state": "processing", "result": None, "error": None})
            self._runs[request["run_id"]] = record
            record.task = asyncio.create_task(self._execute(record, request_id), name="musebridge-agent-run")
        # The registry retains the task. Cancellation/loss of the POST waiter
        # does not cancel an accepted run or cause another execution.
        try:
            await asyncio.shield(record.task)
        except asyncio.CancelledError:
            if record.task.cancelled() and record.snapshot["state"] == "failed":
                return self._terminal(record)
            raise
        return self._terminal(record)

    async def _execute(self, record: _Run, request_id: str) -> None:
        try:
            # asyncio uses the process monotonic clock. The injectable registry
            # clock is only for deterministic retention/admission tests.
            async with asyncio.timeout(record.budget.remaining_seconds()):
                record.budget.check_active()
                record.worker = asyncio.create_task(
                    self.executor(deepcopy(record.request), request_id=request_id, budget=record.budget),
                    name="musebridge-agent-workflow")
                # The deadline supervises the worker without waiting for its
                # cancellation acknowledgement. Noncooperative late work keeps
                # its capacity slot until it actually exits.
                result = await asyncio.shield(record.worker)
                record.budget.check_active()
                result = deepcopy(result)
                result["execution"]["duration_ms"] = int((self.clock() - record.admitted_at) * 1000)
                result = self.check_result(record.request, result)
                snapshot = {"contract_version": self.private_version, "run_id": record.request["run_id"],
                            "state": "completed", "result": result, "error": None}
                self.codec.encode("RunSnapshot", snapshot)
                record.budget.check_active()
            async with self._lock:
                record.budget.check_active()
                record.quiescent = record.worker.done()
                self._finish(record, result=result)
                self._prune()
        except asyncio.CancelledError:
            code = record.budget.cancel_code or ("request_timeout" if record.budget.remaining_seconds() <= 0 else "generation_unavailable")
            async with self._lock:
                record.quiescent = record.worker is None or record.worker.done()
                self._finish(record, error=failure(code, "capacity"))
                self._prune()
        except Exception as exc:
            error = failure("request_timeout", "validation") if record.budget.remaining_seconds() <= 0 else safe_failure(exc)
            if record.budget.remaining_seconds() > 0 and isinstance(exc, ContractViolation):
                error = failure("agent_internal_error", "validation")
            async with self._lock:
                record.quiescent = record.worker is None or record.worker.done()
                self._finish(record, error=error)
                self._prune()
        finally:
            if record.worker is not None:
                if not record.worker.done():
                    record.budget.cancel(record.budget.cancel_code or record.snapshot["error"]["code"])
                    record.worker.cancel()

                def released(task):
                    record.quiescent = True
                    # Retrieve late exceptions without exposing provider text
                    # through asyncio's unhandled-task logger.
                    if not task.cancelled():
                        task.exception()

                record.worker.add_done_callback(released)

    async def get(self, run_id: str) -> dict:
        async with self._lock:
            self._prune()
            record = self._runs.get(run_id)
            if record is None or record.request["contract_version"] != self.private_version:
                raise failure("agent_run_not_found")
            return deepcopy(record.snapshot)

    async def close(self, drain_seconds: float = 65.0) -> None:
        if not math.isfinite(drain_seconds) or not 0 <= drain_seconds <= 65:
            raise ValueError("Drain budget must be between zero and 65 seconds.")
        async with self._lock:
            self._closing = True
            active = list({task for record in self._runs.values()
                           for task in (record.task, record.worker)
                           if task is not None and not task.done()})
        if not active:
            return
        cancellation_margin = min(0.1, drain_seconds)
        _, pending = await asyncio.wait(active, timeout=max(0, drain_seconds - cancellation_margin))
        async with self._lock:
            for record in self._runs.values():
                if record.task in pending or record.worker in pending:
                    record.budget.cancel("service_not_ready")
                    self._finish(record, error=failure("service_not_ready", "capacity"))
                    for task in (record.task, record.worker):
                        if task is not None and not task.done():
                            task.cancel()
        if pending:
            await asyncio.wait(pending, timeout=cancellation_margin)
