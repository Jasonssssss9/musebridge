"""MuseBridge's model-free, artwork-scoped retrieval core."""

from .dataset import DATASET_VERSION, Dataset, load_dataset
from .errors import DatasetValidationError, RetrievalError
from .retrieval import retrieve

__all__ = [
    "DATASET_VERSION",
    "Dataset",
    "DatasetValidationError",
    "RetrievalError",
    "load_dataset",
    "retrieve",
]
