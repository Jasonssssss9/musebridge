"""One monotonic run budget and the shared per-process inference gate."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import math
from time import monotonic

from agent.runtime.service_errors import failure


class RunBudget:
    def __init__(self, deadline: float, *, clock=monotonic):
        if not math.isfinite(deadline):
            raise ValueError("A finite monotonic deadline is required.")
        self.deadline = deadline
        self.clock = clock
        self.cancel_code: str | None = None

    @classmethod
    def from_utc(cls, timestamp: str, *, clock=monotonic, wall_clock=None):
        wall_clock = wall_clock or (lambda: datetime.now(timezone.utc))
        remaining = (datetime.fromisoformat(timestamp) - wall_clock()).total_seconds()
        if remaining <= 0:
            raise failure("request_timeout")
        return cls(clock() + min(55.0, remaining), clock=clock)

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline - self.clock())

    def cancel(self, code: str = "request_timeout") -> None:
        self.cancel_code = code

    def check_active(self) -> None:
        if self.clock() >= self.deadline:
            raise failure("request_timeout", "validation")
        if self.cancel_code:
            raise failure(self.cancel_code, "capacity")


class InferenceGate:
    """Share one instance across planning, answering and direct evaluation."""

    def __init__(self):
        self._slot = asyncio.Semaphore(1)
        self._inflight: set[asyncio.Task] = set()

    def wrap(self, provider):
        return GatedModel(provider, self)


class GatedModel:
    """Deadline includes slot wait and the provider's complete operation.

    Provider transport/identity/decoding remain phase 4. This wrapper adds no
    retries, format repair or fallback and propagates cancellation unchanged.
    """

    def __init__(self, provider, gate: InferenceGate):
        self.provider = provider
        self.gate = gate

    async def complete_structured(self, *, operation, messages, output_schema,
                                  profile, deadline, trace_context):
        phase = {"plan": "planning", "assess": "assessment", "final": "final_generation"}.get(operation)
        if phase is None:
            raise failure("generation_invalid_response", "validation")
        if not math.isfinite(deadline) or deadline <= monotonic():
            raise failure("generation_timeout", phase)
        acquired, worker = False, None
        try:
            async with asyncio.timeout_at(deadline):
                await self.gate._slot.acquire()
                acquired = True
                worker = asyncio.create_task(self.provider.complete_structured(
                    operation=operation, messages=messages, output_schema=output_schema,
                    profile=profile, deadline=deadline, trace_context=trace_context),
                    name="musebridge-model-operation")
                self.gate._inflight.add(worker)
                result = await asyncio.shield(worker)
                if monotonic() >= deadline:
                    raise failure("generation_timeout", phase)
                return result
        except TimeoutError as exc:
            raise failure("generation_timeout", phase) from exc
        finally:
            if acquired:
                if worker is None or worker.done():
                    self.gate._slot.release()
                    if worker is not None:
                        self.gate._inflight.discard(worker)
                else:
                    worker.cancel()

                    def released(task):
                        self.gate._inflight.discard(task)
                        self.gate._slot.release()
                        if not task.cancelled():
                            task.exception()

                    worker.add_done_callback(released)
