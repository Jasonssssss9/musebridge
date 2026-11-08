"""Load the reviewed JSON release into immutable, validated records."""

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping
import unicodedata

from .errors import DatasetValidationError

DATASET_VERSION = "aic-seed-v0.1"
INSTITUTION = "Art Institute of Chicago"
TERMS_URL = "https://www.artic.edu/terms"
CC0_URL = "https://creativecommons.org/publicdomain/zero/1.0/"
CC_BY_URL = "https://creativecommons.org/licenses/by/4.0/"
ARTWORK_IDS = frozenset({"aic-28560", "aic-11723"})
ARTWORK_FIELDS = frozenset(
    "artwork_id slug title artist date_display museum museum_record_id "
    "accession_number source_url image_url dataset_version".split()
)
SOURCE_FIELDS = frozenset(
    "source_id artwork_id source_title source_url source_language "
    "institution retrieved_at terms_url".split()
)
CHUNK_FIELDS = frozenset(
    "chunk_id artwork_id source_id source_locator excerpt license "
    "license_url attribution normalization topics".split()
)
CHUNK_TOPICS = {
    "aic-28560-medium": frozenset({"material"}),
    "aic-28560-description-01": frozenset(
        {"brushwork", "energy", "color", "perspective"}
    ),
    "aic-11723-medium": frozenset({"material"}),
    "aic-11723-description-01": frozenset({"brushwork", "background"}),
}
Record = Mapping[str, object]


@dataclass(frozen=True, slots=True)
class Dataset:
    """A validated release; records and topic collections are read-only."""

    dataset_version: str
    artworks: Mapping[str, Record]
    sources: Mapping[str, Record]
    chunks: tuple[Record, ...]


def _fail(path: str, message: str) -> None:
    raise DatasetValidationError(f"{path}: {message}")


def _object(value: object, fields: frozenset[str], path: str) -> dict:
    if not isinstance(value, dict):
        _fail(path, "expected a JSON object")
    missing, unknown = fields - value.keys(), value.keys() - fields
    if missing or unknown:
        _fail(path, f"missing fields {sorted(missing)}; unknown fields {sorted(unknown)}")
    return value


def _string(value: object, path: str, *, max_length: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(path, "expected a nonempty string")
    if any(
        unicodedata.category(char) == "Cs"
        or (unicodedata.category(char) == "Cc" and char not in "\t\n\r")
        for char in value
    ):
        _fail(path, "contains an invalid control character or unpaired surrogate")
    if max_length is not None and len(value) > max_length:
        _fail(path, f"must contain at most {max_length} Unicode code points")
    return value


def _equal(value: object, expected: object, path: str) -> None:
    if value != expected:
        _fail(path, f"expected {expected!r}")


def _records(value: object, count: int, fields: frozenset[str], path: str) -> list[dict]:
    if not isinstance(value, list) or len(value) != count:
        _fail(path, f"expected an array of {count} records")
    return [_object(record, fields, f"{path}[{index}]") for index, record in enumerate(value)]


def _unique(records: list[dict], key: str, path: str) -> dict[str, dict]:
    result = {}
    for index, record in enumerate(records):
        identifier = _string(record[key], f"{path}[{index}].{key}")
        if identifier in result:
            _fail(path, f"duplicate {key}: {identifier}")
        result[identifier] = record
    return result


def _utc_timestamp(value: str, path: str) -> None:
    if not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)", value
    ):
        _fail(path, "expected an RFC 3339 UTC timestamp")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(path, "invalid calendar date or time")


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("$", f"duplicate JSON property: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    _fail("$", f"invalid JSON constant: {value}")


def _freeze(record: dict) -> Record:
    return MappingProxyType({
        key: tuple(value) if isinstance(value, list) else value
        for key, value in record.items()
    })


def load_dataset(
    path: str | Path, *, expected_version: str = DATASET_VERSION
) -> Dataset:
    """Read and validate the two-artwork seed; never fetch data or fall back.

    The expected version is an explicit configuration pin. Validation checks
    structure and provenance relationships, not the authenticity of changed
    text; use the published SHA-256 to identify the released file.
    """
    _string(expected_version, "expected_version")
    try:
        content = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        raise DatasetValidationError(f"Cannot read seed: {exc}") from exc
    try:
        root = json.loads(
            content,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_constant,
        )
    except DatasetValidationError:
        raise
    except (ValueError, RecursionError) as exc:
        # json may raise ValueError for Python's integer conversion limit.
        raise DatasetValidationError(f"Invalid seed JSON: {exc}") from exc

    _object(root, frozenset({"dataset_version", "artworks", "sources", "chunks"}), "$")
    version = _string(root["dataset_version"], "$.dataset_version")
    _equal(version, expected_version, "$.dataset_version")
    artworks = _records(root["artworks"], 2, ARTWORK_FIELDS, "$.artworks")
    sources = _records(root["sources"], 2, SOURCE_FIELDS, "$.sources")
    chunks = _records(root["chunks"], 4, CHUNK_FIELDS, "$.chunks")
    by_artwork = _unique(artworks, "artwork_id", "$.artworks")
    _equal(set(by_artwork), ARTWORK_IDS, "$.artworks")
    _unique(artworks, "slug", "$.artworks")

    for index, artwork in enumerate(artworks):
        prefix = f"$.artworks[{index}]"
        for field in ARTWORK_FIELDS - {"museum_record_id", "image_url"}:
            _string(artwork[field], f"{prefix}.{field}")
        artwork_id = artwork["artwork_id"]
        record_id = artwork["museum_record_id"]
        if type(record_id) is not int or record_id <= 0:
            _fail(f"{prefix}.museum_record_id", "expected a positive integer")
        _equal(artwork_id, f"aic-{record_id}", f"{prefix}.artwork_id")
        _equal(artwork["slug"], artwork_id, f"{prefix}.slug")
        _equal(artwork["dataset_version"], version, f"{prefix}.dataset_version")
        _equal(artwork["museum"], INSTITUTION, f"{prefix}.museum")
        _equal(artwork["image_url"], None, f"{prefix}.image_url")
        _equal(
            artwork["source_url"], f"https://api.artic.edu/api/v1/artworks/{record_id}",
            f"{prefix}.source_url",
        )

    by_source = _unique(sources, "source_id", "$.sources")
    _equal(set(by_source), {"aic-api-28560", "aic-api-11723"}, "$.sources")
    for index, source in enumerate(sources):
        prefix = f"$.sources[{index}]"
        for field in SOURCE_FIELDS:
            _string(source[field], f"{prefix}.{field}")
        artwork = by_artwork.get(source["artwork_id"])
        if artwork is None:
            _fail(f"{prefix}.artwork_id", "references an unknown artwork")
        _equal(source["source_id"], f'aic-api-{artwork["museum_record_id"]}', f"{prefix}.source_id")
        _equal(source["source_url"], artwork["source_url"], f"{prefix}.source_url")
        _equal(
            source["source_title"],
            artwork["title"] + " — Art Institute of Chicago API record",
            f"{prefix}.source_title",
        )
        _equal(source["source_language"], "en", f"{prefix}.source_language")
        _equal(source["institution"], INSTITUTION, f"{prefix}.institution")
        _equal(source["terms_url"], TERMS_URL, f"{prefix}.terms_url")
        _utc_timestamp(source["retrieved_at"], f"{prefix}.retrieved_at")

    by_chunk = _unique(chunks, "chunk_id", "$.chunks")
    _equal(set(by_chunk), set(CHUNK_TOPICS), "$.chunks")
    for index, chunk in enumerate(chunks):
        prefix = f"$.chunks[{index}]"
        for field in CHUNK_FIELDS - {"topics"}:
            _string(chunk[field], f"{prefix}.{field}", max_length=4000 if field == "excerpt" else None)
        artwork_id, chunk_id = chunk["artwork_id"], chunk["chunk_id"]
        if artwork_id not in by_artwork:
            _fail(f"{prefix}.artwork_id", "references an unknown artwork")
        source = by_source.get(chunk["source_id"])
        if source is None:
            _fail(f"{prefix}.source_id", "references an unknown source")
        _equal(source["artwork_id"], artwork_id, f"{prefix}.source_id")
        suffix = "-medium" if chunk_id.endswith("-medium") else "-description-01"
        _equal(chunk_id, artwork_id + suffix, f"{prefix}.chunk_id")
        medium = suffix == "-medium"
        _equal(
            chunk["source_locator"],
            "data.medium_display" if medium else "data.description, paragraph 1",
            f"{prefix}.source_locator",
        )
        _equal(chunk["license"], "CC0-1.0" if medium else "CC-BY-4.0", f"{prefix}.license")
        _equal(chunk["license_url"], CC0_URL if medium else CC_BY_URL, f"{prefix}.license_url")
        _equal(chunk["attribution"], INSTITUTION, f"{prefix}.attribution")
        normalization = "none" if medium else "HTML removed; whitespace normalized."
        if chunk_id == "aic-11723-description-01":
            normalization = (
                "HTML removed; whitespace normalized; "
                "the source's split floral ligature normalized to floral."
            )
        _equal(chunk["normalization"], normalization, f"{prefix}.normalization")
        topics = chunk["topics"]
        if not isinstance(topics, list) or not topics:
            _fail(f"{prefix}.topics", "expected a nonempty array")
        for topic in topics:
            _string(topic, f"{prefix}.topics")
        if len(topics) != len(set(topics)):
            _fail(f"{prefix}.topics", "duplicate topic")
        _equal(set(topics), CHUNK_TOPICS[chunk_id], f"{prefix}.topics")

    return Dataset(
        dataset_version=version,
        artworks=MappingProxyType({key: _freeze(value) for key, value in by_artwork.items()}),
        sources=MappingProxyType({key: _freeze(value) for key, value in by_source.items()}),
        chunks=tuple(_freeze(chunk) for chunk in chunks),
    )
