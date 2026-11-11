"""Run a single retrieval query without starting an HTTP server."""

import argparse
import json
from pathlib import Path
import sys

from . import DATASET_VERSION, DatasetValidationError, RetrievalError, load_dataset, retrieve


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seed", type=Path,
        default=Path(__file__).resolve().parent / "data" / f"{DATASET_VERSION}.json",
        help="Path to the reviewed JSON seed.",
    )
    parser.add_argument("--artwork-id", required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--dataset-version", default=DATASET_VERSION)
    parser.add_argument("--top-k", type=int, default=2)
    args = parser.parse_args()
    try:
        dataset = load_dataset(args.seed, expected_version=args.dataset_version)
        result = retrieve(
            dataset, artwork_id=args.artwork_id, query=args.query,
            dataset_version=args.dataset_version, top_k=args.top_k,
        )
    except (DatasetValidationError, RetrievalError) as exc:
        code = exc.code if isinstance(exc, RetrievalError) else "invalid_dataset"
        print(json.dumps({"error": {"code": code, "message": str(exc)}}), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
