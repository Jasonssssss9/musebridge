"""Structural C-side ports for the provider and budget owned by B.

The provider method follows agent-design.md section 8.1. Concrete profile,
completion, transport and deadline implementations remain B's responsibility.
"""
from __future__ import annotations

from typing import Protocol


class RunBudget(Protocol):
    def check_active(self) -> None:
        """Raise a typed timeout/cancellation error when the run cannot continue."""

    def remaining_seconds(self) -> float:
        """Return remaining monotonic time from B's single run deadline."""


class ModelProfile(Protocol):
    """The profile fields needed for prompt sizing; B verifies full identity."""

    profile_id: str
    context_tokens: int
    plan_output_tokens: int
    answer_output_tokens: int


class StructuredCompletion(Protocol):
    """C consumes validated data; B retains identity, stop, usage and timing.

    The actual completion must also carry the provider/model identity,
    completion reason, available token usage and durations required by the
    design. A provider failure raises WorkflowError instead of returning data.
    """

    data: dict


class StructuredModel(Protocol):
    async def complete_structured(
        self, *, operation: str, messages: list[dict[str, str]], output_schema: dict,
        profile: ModelProfile, deadline: float, trace_context: dict[str, str],
    ) -> StructuredCompletion:
        """Return one validated decision through the documented provider API.

        deadline is an absolute monotonic timestamp. B bounds gate wait,
        transport, full response read and decoding by this deadline. B owns
        the shared inference gate, model/tokenizer identity, strict outer and
        inner JSON parsing, truncation rejection and typed provider errors.
        Cancellation propagates. There are no retries or silent repairs.
        """


class EvidenceSearch(Protocol):
    async def search(self, tool_input: dict, *, request_id: str,
                     remaining_seconds: float) -> dict:
        """Call only the configured retrieval service with injected scope."""
