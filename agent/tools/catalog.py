"""Canonical v2 catalog facts projected from the verified release snapshot.

Catalog fields are not retrieval chunks. Their provenance comes from the same
immutable source mapping loaded with the evidence manifest, without rereading
or fetching the source after startup.
"""

from functools import lru_cache
from pathlib import Path

from agent.runtime.errors import WorkflowError
from agent.runtime.validation import ContractValidator, ContractViolation
from agent.tools.evidence import EvidenceManifest

CATALOG_FIELDS = ("title", "artist", "date_display")
CATALOG_NORMALIZATION = "Released catalog metadata; not a verbatim museum excerpt."
_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "docs/contracts/agent-v2.schema.json"


def _invalid_catalog() -> WorkflowError:
    return WorkflowError(
        "agent_scope_mismatch", "The supplied catalog does not match the configured release.",
        phase="context",
    )


@lru_cache(maxsize=1)
def _validator() -> ContractValidator:
    return ContractValidator(_SCHEMA_PATH)


def canonical_catalog(
    manifest: EvidenceManifest, artwork_id: str, dataset_version: str
) -> list[dict]:
    """Return title, artist and displayed date, preserving exact released values.

    Every call returns detached dictionaries. The associated source is selected
    by its released artwork ID and catalog source URL, not by a guessed chunk
    name or by assuming a chunk attribution is the source institution.
    """
    try:
        artwork = manifest.artwork(artwork_id, dataset_version)
        sources = [source for source in manifest.sources.values()
                   if source["artwork_id"] == artwork_id
                   and source["source_url"] == artwork["source_url"]]
        if len(sources) != 1:
            raise _invalid_catalog()
        source = sources[0]
        records = []
        for name in CATALOG_FIELDS:
            record = {
                "kind": "catalog",
                "fact_id": f"{artwork_id}-catalog-{name}",
                "artwork_id": artwork_id,
                "dataset_version": dataset_version,
                "field": name,
                "value": artwork[name],
                "source_id": source["source_id"],
                "source_title": source["source_title"],
                "source_url": source["source_url"],
                "source_locator": f"artworks[artwork_id={artwork_id}].{name}",
                "attribution": source["institution"],
                "normalization": CATALOG_NORMALIZATION,
            }
            _validator().validate("CatalogFact", record)
            if not record["value"].strip():
                raise _invalid_catalog()
            records.append(record)
        return records
    except (WorkflowError, ContractViolation, KeyError, TypeError) as exc:
        raise _invalid_catalog() from exc


def validate_catalog(
    manifest: EvidenceManifest, artwork_id: str, dataset_version: str, records: object
) -> list[dict]:
    """Compare the complete ordered catalog projection before any model call."""
    canonical = canonical_catalog(manifest, artwork_id, dataset_version)
    if type(records) is not list or records != canonical:
        raise _invalid_catalog()
    return canonical
