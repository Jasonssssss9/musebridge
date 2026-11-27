"""Validate image-backed museum records and distinguish original visitor guides."""

import re
from urllib.parse import urlsplit

from .dataset import (
    ARTWORK_FIELDS, SOURCE_FIELDS, CHUNK_FIELDS, _equal, _fail, _object, _string,
    _unique, _utc_timestamp, _snapshot,
)

IMAGE_FIELDS = frozenset({"image_credit", "image_alt", "image_license", "image_source_url", "suggested_questions"})


def _rows(root, name, required, optional=frozenset()):
    rows = root[name]
    if not isinstance(rows, list) or not rows:
        _fail(name, "expected a nonempty array")
    for index, row in enumerate(rows):
        allowed = optional.intersection(row) if isinstance(row, dict) else set()
        _object(row, required | allowed, f"{name}[{index}]")
    return rows


def _strings(values, path):
    if not isinstance(values, list) or not values:
        _fail(path, "expected a nonempty array")
    for value in values:
        _string(value, path)
    if len(values) != len(set(values)):
        _fail(path, "duplicate value")


def _https(value, label):
    try:
        parsed = urlsplit(_string(value, label))
    except ValueError:
        _fail(label, "invalid source URL")
    if parsed.scheme != "https" or not parsed.hostname:
        _fail(label, "expected an HTTPS source")
    return parsed


def load_getty(root, version):
    artworks = _rows(root, "artworks", ARTWORK_FIELDS | IMAGE_FIELDS)
    sources = _rows(root, "sources", SOURCE_FIELDS | {"source_kind"}, {"based_on_source_id"})
    chunks = _rows(root, "chunks", CHUNK_FIELDS)
    by_artwork = _unique(artworks, "artwork_id", "artworks")
    by_source = _unique(sources, "source_id", "sources")
    _unique(chunks, "chunk_id", "chunks")
    for work in artworks:
        key = work["artwork_id"]
        if not re.fullmatch(r"getty-[a-z0-9-]+", key):
            _fail(key, "invalid artwork identifier")
        for field in (ARTWORK_FIELDS | IMAGE_FIELDS) - {"museum_record_id", "suggested_questions"}:
            _string(work[field], f"{key}.{field}")
        if type(work["museum_record_id"]) is not int or work["museum_record_id"] < 1:
            _fail(key, "expected a positive museum record ID")
        _equal(work["slug"], key, key)
        _equal(work["dataset_version"], version, key)
        _equal(work["image_url"], f"/artworks/{key}.jpg", key)
        record_url = _https(work["source_url"], f"{key}.source_url")
        if (record_url.hostname not in {"www.getty.edu", "getty.edu"}
                or record_url.path.rstrip("/").split("/")[-1].lower() != key.removeprefix("getty-")):
            _fail(key, "catalog URL must identify this Getty object")
        _https(work["image_source_url"], f"{key}.image_source_url")
        _strings(work["suggested_questions"], f"{key}.suggested_questions")

    official = {}
    for source in sources:
        key = source["source_id"]
        for field in SOURCE_FIELDS:
            _string(source[field], f"{key}.{field}")
        work = by_artwork.get(source["artwork_id"])
        if work is None:
            _fail(key, "unknown artwork")
        _equal(source["source_url"], work["source_url"], key)
        _equal(source["source_language"], "en", key)
        _utc_timestamp(source["retrieved_at"], f"{key}.retrieved_at")
        _https(source["terms_url"], f"{key}.terms_url")
        if source["source_kind"] == "museum_record":
            if work["artwork_id"] in official or source.get("based_on_source_id") is not None:
                _fail(key, "expected one original catalog source per artwork")
            _equal(source["institution"], work["museum"], key)
            official[work["artwork_id"]] = key
        elif source["source_kind"] == "guide_note":
            _equal(source["institution"], "MuseBridge", key)
            if "musebridge" not in source["source_title"].lower():
                _fail(key, "guide title must identify its author")
        else:
            _fail(key, "unknown source kind")
    _equal(set(official), set(by_artwork), "museum records")
    for source in sources:
        if source["source_kind"] == "guide_note":
            _equal(source.get("based_on_source_id"), official[source["artwork_id"]], source["source_id"])

    for chunk in chunks:
        key = chunk["chunk_id"]
        for field in CHUNK_FIELDS - {"topics"}:
            _string(chunk[field], f"{key}.{field}", max_length=4000 if field == "excerpt" else None)
        source = by_source.get(chunk["source_id"])
        if source is None:
            _fail(key, "unknown source")
        _equal(chunk["artwork_id"], source["artwork_id"], key)
        _equal(chunk["attribution"], source["institution"], key)
        if source["source_kind"] == "guide_note":
            _equal(chunk["license"], "In Copyright", key)
            _equal(chunk["license_url"], "https://rightsstatements.org/vocab/InC/1.0/", key)
        _https(chunk["license_url"], f"{key}.license_url")
        _strings(chunk["topics"], f"{key}.topics")
    _equal({c["source_id"] for c in chunks}, set(by_source), "source coverage")
    return _snapshot(version, by_artwork, by_source, chunks)
