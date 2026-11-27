"""Select a versioned data release across retrieval and Agent processes."""

import os
from pathlib import Path
import re

from .dataset import DATASET_VERSION
from .errors import DatasetValidationError


def release_version():
    return os.environ.get("DATASET_VERSION", DATASET_VERSION)


def release_path(version):
    configured = os.environ.get("SEED_PATH")
    if configured:
        return Path(configured)
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", version):
        raise DatasetValidationError("Invalid dataset version.")
    return Path(__file__).resolve().parent / "data" / f"{version}.json"
