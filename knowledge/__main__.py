"""Run a single retrieval query without starting an HTTP server."""

import argparse
import json
import os
from pathlib import Path
import sys

from . import DatasetValidationError, RetrievalError, load_dataset, retrieve

from .release import release_path, release_version
from .retrieval import validate_request
from .strategy import MODES, configured_index


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seed", type=Path,
        default=None,
        help="Path to the reviewed JSON seed.",
    )
    parser.add_argument("--artwork-id", required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--dataset-version", default=release_version())
    parser.add_argument("--top-k", type=int, default=2)
    parser.add_argument("--mode", choices=MODES, default=os.environ.get("MUSEBRIDGE_RETRIEVAL_MODE", "hybrid"))
    args = parser.parse_args()
    if args.mode not in MODES:
        parser.error("Retrieval mode must be hybrid or lexical.")
    try:
        dataset = load_dataset(args.seed or release_path(args.dataset_version), expected_version=args.dataset_version)
        request = dict(artwork_id=args.artwork_id, query=args.query, dataset_version=args.dataset_version, top_k=args.top_k)
        validate_request(dataset, **request)
        result = retrieve(dataset, vector_index=configured_index(dataset, args.mode), **request)
    except (DatasetValidationError, RetrievalError) as exc:
        code = exc.code if isinstance(exc, RetrievalError) else "invalid_dataset"
        print(json.dumps({"error": {"code": code, "message": str(exc)}}), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
