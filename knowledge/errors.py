"""Typed failures for the data loader and retrieval core."""


class DatasetValidationError(ValueError):
    """The configured seed cannot be loaded as a valid dataset."""


class RetrievalError(ValueError):
    """A retrieval request violates the contract or selects an unknown scope."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
