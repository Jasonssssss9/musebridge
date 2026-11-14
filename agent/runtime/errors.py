"""Typed workflow failures for the infrastructure layer to serialize."""


class WorkflowError(Exception):
    """A safe error message; never include visitor, provider or source payloads."""

    def __init__(self, code: str, message: str, *, phase: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.phase = phase
        self.retryable = retryable

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message,
                "retryable": self.retryable, "phase": self.phase}
