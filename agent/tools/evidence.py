"""Read-only release manifest and exact provenance checks for retrieved evidence."""

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from types import MappingProxyType
from typing import Mapping

from knowledge import DatasetValidationError, load_dataset

from agent.runtime.errors import WorkflowError


DATASET_VERSION = "aic-seed-v0.1"
SEED_SHA256 = "3703fd4c9fcefb8e9fba20f23a5cb70e612c62ef7cb3f594a15d319ebc18d6ac"
DEFAULT_SEED_PATH = Path(__file__).resolve().parents[2] / "knowledge" / "data" / f"{DATASET_VERSION}.json"
EVIDENCE_FIELDS = (
    "chunk_id", "artwork_id", "source_id", "source_title", "source_url",
    "source_language", "source_locator", "excerpt", "license", "license_url",
    "attribution", "normalization",
)


def _invalid(message: str) -> WorkflowError:
    return WorkflowError("retrieval_invalid_response", message, phase="retrieval")


@dataclass(frozen=True, slots=True)
class EvidenceManifest:
    """Immutable canonical evidence derived from a verified release snapshot.

    Runtime methods return detached dictionaries. Extra response metadata is
    discarded before records can enter workflow state or model inputs.
    """

    dataset_version: str
    sha256: str
    artworks: Mapping[str, Mapping[str, object]]
    records: Mapping[str, Mapping[str, str]]
    chunk_ids_by_artwork: Mapping[str, frozenset[str]]
    sources: Mapping[str, Mapping[str, object]] = field(
        default_factory=lambda: MappingProxyType({})
    )

    @classmethod
    def load(
        cls,
        path: str | Path = DEFAULT_SEED_PATH,
        *,
        expected_version: str = DATASET_VERSION,
        expected_sha256: str = SEED_SHA256,
    ) -> "EvidenceManifest":
        """Verify one byte snapshot, then reuse the existing seed validator.

        The temporary snapshot makes hash verification and parsing consume the
        same bytes, even if the configured source file changes during startup.
        No source URLs are fetched and the configured seed is never modified.
        """
        if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise ValueError("The seed SHA-256 must contain 64 lowercase hexadecimal characters.")
        try:
            content = Path(path).read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            if digest != expected_sha256:
                raise ValueError("The seed does not match the configured SHA-256.")
            with TemporaryDirectory(prefix="musebridge-manifest-") as directory:
                snapshot = Path(directory) / "seed.json"
                snapshot.write_bytes(content)
                dataset = load_dataset(snapshot, expected_version=expected_version)
        except (OSError, DatasetValidationError) as exc:
            raise ValueError("The configured seed cannot produce a verified release manifest.") from exc

        records = {}
        scope_ids = {artwork_id: set() for artwork_id in dataset.artworks}
        for chunk in dataset.chunks:
            source = dataset.sources[chunk["source_id"]]
            record = {
                field: source[field] if field in {"source_title", "source_url", "source_language"} else chunk[field]
                for field in EVIDENCE_FIELDS
            }
            records[chunk["chunk_id"]] = MappingProxyType(record)
            scope_ids[chunk["artwork_id"]].add(chunk["chunk_id"])
        return cls(
            dataset_version=dataset.dataset_version,
            sha256=digest,
            artworks=MappingProxyType({key: MappingProxyType(dict(value)) for key, value in dataset.artworks.items()}),
            records=MappingProxyType(records),
            chunk_ids_by_artwork=MappingProxyType({key: frozenset(value) for key, value in scope_ids.items()}),
            sources=MappingProxyType({key: MappingProxyType(dict(value))
                                      for key, value in dataset.sources.items()}),
        )

    def artwork(self, artwork_id: str, dataset_version: str) -> dict:
        """Return trusted artwork metadata after checking the complete scope."""
        self._scope(artwork_id, dataset_version)
        return dict(self.artworks[artwork_id])

    def _scope(self, artwork_id: str, dataset_version: str) -> None:
        if dataset_version != self.dataset_version:
            raise _invalid("The evidence release does not match the configured manifest.")
        if not isinstance(artwork_id, str) or artwork_id not in self.artworks:
            raise _invalid("The artwork is outside the configured evidence manifest.")

    def validate_candidates(
        self,
        artwork_id: str,
        dataset_version: str,
        candidates: object,
        *,
        limit: int = 2,
    ) -> list[dict]:
        """Reject the whole response unless every canonical field is released."""
        self._scope(artwork_id, dataset_version)
        if not isinstance(candidates, list) or not 0 <= len(candidates) <= limit:
            raise _invalid("The evidence candidate list has an invalid shape or size.")
        accepted = []
        seen = set()
        for candidate in candidates:
            if not isinstance(candidate, dict) or not all(field in candidate for field in EVIDENCE_FIELDS):
                raise _invalid("An evidence candidate is missing required fields.")
            chunk_id = candidate["chunk_id"]
            if not isinstance(chunk_id, str) or chunk_id not in self.chunk_ids_by_artwork[artwork_id]:
                raise _invalid("An evidence candidate belongs to an unknown or different artwork.")
            if chunk_id in seen:
                raise _invalid("One retrieval response contains a duplicate evidence ID.")
            canonical = {field: candidate[field] for field in EVIDENCE_FIELDS}
            if canonical != self.records[chunk_id]:
                raise _invalid("An evidence candidate differs from the reviewed release.")
            accepted.append(dict(self.records[chunk_id]))
            seen.add(chunk_id)
        return accepted

    def merge(
        self,
        artwork_id: str,
        dataset_version: str,
        existing: list[dict],
        incoming: list[dict],
    ) -> list[dict]:
        """Deduplicate validated lookups while preserving first-seen order."""
        prior = self.validate_candidates(artwork_id, dataset_version, existing, limit=8)
        added = self.validate_candidates(artwork_id, dataset_version, incoming, limit=2)
        seen = {record["chunk_id"] for record in prior}
        for record in added:
            if record["chunk_id"] not in seen:
                prior.append(record)
                seen.add(record["chunk_id"])
        if len(prior) > 8:
            raise _invalid("The evidence union exceeds the workflow limit.")
        return prior

    def complete_scope(self, artwork_id: str, dataset_version: str, evidence: list[dict]) -> bool:
        """Report manifest coverage, not completeness of museum knowledge."""
        accepted = self.validate_candidates(artwork_id, dataset_version, evidence, limit=8)
        return {record["chunk_id"] for record in accepted} == self.chunk_ids_by_artwork[artwork_id]
