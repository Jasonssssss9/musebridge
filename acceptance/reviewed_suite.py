"""Verify the externally held visitor suite and the attributable freeze review."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path

FILES = ("single-turn.json", "multi-turn.json")
REVIEW_ROLES = ("visitor_realism", "source_and_state", "en", "es", "zh-CN", "ko")


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def suite_directory(root):
    if root is None:
        raise ValueError("Supply --suite-root pointing to the visitor-acceptance worktree or v0.1 suite directory.")
    root = Path(root).resolve()
    directory = root if (root / FILES[0]).is_file() else root / "acceptance/visitor-heldout/v0.1"
    if not all((directory / name).is_file() for name in FILES):
        raise ValueError("The external suite root must contain both visitor datasets.")
    return directory


def validate_reviews(review, digests):
    if review.get("sha256") != digests:
        raise ValueError("Review hashes must identify the two frozen files.")
    approvals = review.get("approvals", {})
    if not isinstance(approvals, dict) or set(approvals) != set(REVIEW_ROLES):
        raise ValueError("Provide the visitor, source/state and four language approvals in --review-record.")
    for role, item in approvals.items():
        if (not isinstance(item, dict) or item.get("approved") is not True
                or not all(isinstance(item.get(k), str) and item[k].strip() for k in ["reviewer", "date", "reference"])):
            raise ValueError(f"Missing attributable sign-off for {role}.")


def _frozen_snapshot(directory, scored_manifest=None):
    """Read the manifest and both frozen documents from one verified byte snapshot."""
    manifest = directory / "MANIFEST.json"
    if not manifest.is_file():
        raise ValueError("The external suite is not frozen. Complete its human reviews and freeze first.")
    manifest_bytes = manifest.read_bytes()
    manifest_hash = sha256(manifest_bytes).hexdigest()
    if scored_manifest is not None and manifest_hash != scored_manifest:
        raise ValueError("Scoring requires the freeze manifest recorded in this run.")
    original = json.loads(manifest_bytes)
    files = {name: (directory / name).read_bytes() for name in FILES}
    hashes = {name: sha256(contents).hexdigest() for name, contents in files.items()}
    if original.get("sha256") != hashes:
        raise ValueError("The independent datasets differ from their freeze manifest.")
    documents = [json.loads(files[name]) for name in FILES]
    if any(document.get("status") != "frozen" for document in documents):
        raise ValueError("The external datasets must carry their frozen status.")
    return manifest_hash, original, hashes, documents


def frozen_inputs(directory, record_path=None, *, scored_manifest=None):
    """Return the exact parsed documents whose original bytes passed the hash checks."""
    manifest_hash, original, hashes, documents = _frozen_snapshot(directory, scored_manifest)
    identity = {"suite_sha256": manifest_hash}
    if scored_manifest is None:
        review = (json.loads(record_path.read_bytes()) if record_path else original.get("reviews", {}))
        validate_reviews(review, hashes)
        identity["review_sha256"] = sha256(json.dumps(review, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return identity, documents


def verify(directory, record_path):
    identity, _ = frozen_inputs(directory, record_path)
    return identity


def unsigned_record(directory):
    """Bind a blank index of review references to the exact final dataset hashes."""
    _, _, hashes, _ = _frozen_snapshot(directory)
    return {"sha256": hashes, "approvals": {
        role: {"approved": False, "reviewer": "", "date": "", "reference": ""} for role in REVIEW_ROLES}}


def main():
    parser = argparse.ArgumentParser(description="Prepare or verify the human sign-off index offline.")
    commands = parser.add_subparsers(dest="operation", required=True)
    template = commands.add_parser("template", help="Write a new unsigned index for an already frozen suite")
    template.add_argument("--suite-root", required=True, type=Path)
    template.add_argument("--output", required=True, type=Path)
    check = commands.add_parser("verify", help="Verify the freeze and sign-off references without running the model")
    check.add_argument("--suite-root", required=True, type=Path)
    check.add_argument("--review-record", type=Path, help="Use an external index or omit for embedded approvals")
    options = parser.parse_args()
    try:
        directory = suite_directory(options.suite_root)
        if options.operation == "verify":
            print(json.dumps(verify(directory, options.review_record), indent=2))
            return
        index = unsigned_record(directory)
        # A completed sheet or previous index must never be replaced.
        with open(options.output, "x", encoding="utf-8",
                  opener=lambda path, flags: os.open(path, flags, 0o600)) as stream:
            json.dump(index, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        print(f"Wrote an unsigned review index to {options.output}")
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
