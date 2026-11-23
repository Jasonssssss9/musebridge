"""Local operator capability attestations, bound to model and implementation."""
from datetime import datetime, timezone
import hashlib
from pathlib import Path

from agent.providers.profile import OllamaProfile, TOKENIZER_SHA256
from agent.runtime.validation import ContractViolation, strict_json
from agent.tools.retrieval import STRATEGIES, configured_mode

RECORD_VERSION = "ollama-capability-v1"
CHECKS = ("four_language_workflows", "full_schema", "five_pair_context", "sizing",
          "final_operation", "loaded_context", "output_reserve")


def implementation_fingerprint():
    root = Path(__file__).resolve().parents[2]
    paths = [root / "docs/contracts/agent-v2.schema.json",
             root / "knowledge/data/aic-seed-v0.1.json",
             root / "agent/testing/verify_ollama.py",
             root / "agent/testing/probe_quality.py",
             root / "agent/testing/v2.py", root / "agent/service.py"]
    for directory in ("agent/prompts/v2", "agent/providers", "agent/workflows", "agent/tools", "knowledge"):
        paths.extend(sorted(path for path in (root / directory).iterdir() if path.suffix in {".py", ".md"}))
    paths.extend(sorted((root / "agent/runtime").glob("*.py")))
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.relative_to(root)).encode())
        digest.update(b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def make_record(model, checks):
    if not model.verified or not model.metadata_fingerprint:
        raise ValueError("Verify the actual model before recording capabilities.")
    return {"record_version": RECORD_VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
            "profile_fingerprint": model.profile.fingerprint(),
            "retrieval_strategy_version": STRATEGIES[configured_mode()],
            "implementation_fingerprint": implementation_fingerprint(),
            "metadata_fingerprint": model.metadata_fingerprint,
            "tokenizer_sha256": TOKENIZER_SHA256,
            "languages": ["en", "es", "zh-CN", "ko"], "checks": dict(checks)}


def validate_record(record, model, *, now=None):
    """A trusted local file is an attestation, not a signed quality certificate."""
    expected = {"record_version", "created_at", "profile_fingerprint", "implementation_fingerprint",
                "metadata_fingerprint", "tokenizer_sha256", "languages", "checks", "retrieval_strategy_version"}
    try:
        now = now or datetime.now(timezone.utc)
        created = datetime.fromisoformat(record["created_at"])
        age = (now - created).total_seconds()
        return (set(record) == expected and record["record_version"] == RECORD_VERSION
                and -300 <= age <= 86400 and created.utcoffset() is not None
                and record["profile_fingerprint"] == OllamaProfile().fingerprint()
                and record["implementation_fingerprint"] == implementation_fingerprint()
                and record["retrieval_strategy_version"] == STRATEGIES[configured_mode()]
                and record["metadata_fingerprint"] == model.metadata_fingerprint
                and record["tokenizer_sha256"] == TOKENIZER_SHA256
                and record["languages"] == ["en", "es", "zh-CN", "ko"]
                and set(record["checks"]) == set(CHECKS)
                and all(record["checks"][key] is True for key in CHECKS))
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def read_record(path, model):
    try:
        source = Path(path)
        if source.stat().st_size > 16384:
            return None
        value = strict_json(source.read_bytes(), max_bytes=16384)
        return value if validate_record(value, model) else None
    except (OSError, ValueError, ContractViolation):
        return None
