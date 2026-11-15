"""Explicitly download only the pinned public tokenizer, never model weights."""
import argparse
import hashlib
from pathlib import Path
import urllib.request

from agent.providers.profile import TOKENIZER_BYTES, TOKENIZER_SHA256, TOKENIZER_URL


def download_tokenizer(path):
    path = Path(path)
    if path.exists():
        if path.stat().st_size == TOKENIZER_BYTES and hashlib.sha256(path.read_bytes()).hexdigest() == TOKENIZER_SHA256:
            return
        raise ValueError("The output already exists with different contents.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    digest, size, owned = hashlib.sha256(), 0, False
    try:
        with urllib.request.urlopen(TOKENIZER_URL, timeout=60) as response, temporary.open("xb") as target:
            owned = True
            while block := response.read(65536):
                size += len(block)
                if size > TOKENIZER_BYTES:
                    raise ValueError("The download exceeds the pinned tokenizer size.")
                digest.update(block)
                target.write(block)
        if size != TOKENIZER_BYTES or digest.hexdigest() != TOKENIZER_SHA256:
            raise ValueError("The tokenizer download failed integrity verification.")
        temporary.chmod(0o600)
        if path.exists():
            raise ValueError("The output was created by another operation.")
        temporary.replace(path)
    finally:
        if owned and temporary.exists():
            temporary.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    options = parser.parse_args()
    download_tokenizer(options.output)
    print("The pinned tokenizer is ready.")


if __name__ == "__main__":
    main()
