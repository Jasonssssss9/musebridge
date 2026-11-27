"""Offline release checks for the Vincent evidence pack and its C handoff."""

from hashlib import sha256
import json
from pathlib import Path
import re
import unittest

import jsonpatch
from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "knowledge" / "data"
PACK = DATA / "reference-packs" / "vincent-irises-v1.json"
CONTRACTS = ROOT / "docs" / "contracts"


class VincentReferencePackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pack = json.loads(PACK.read_bytes())
        cls.gallery = json.loads((DATA / "getty-gallery-v2.json").read_bytes())
        base = json.loads((CONTRACTS / "agent-v2.schema.json").read_bytes())
        patch = json.loads((CONTRACTS / "agent-v3.patch.json").read_bytes())
        schema = jsonpatch.apply_patch(base, patch)
        schema["$ref"] = "#/$defs/VincentReferencePack"
        Draft202012Validator.check_schema(schema)
        cls.validator = Draft202012Validator(schema, format_checker=FormatChecker())

    def test_manifest_bytes_and_schema(self):
        self.assertEqual(PACK.with_suffix(".sha256").read_text().strip(),
                         sha256(PACK.read_bytes()).hexdigest())
        self.validator.validate(self.pack)

    def test_identities_are_disjoint_from_gallery(self):
        records = self.pack["records"]
        chunks = [record["chunk_id"] for record in records]
        sources = {record["source_id"] for record in records}
        self.assertEqual(len(chunks), len(set(chunks)))
        self.assertTrue(set(chunks).isdisjoint(sources))
        reserved = {row["source_id"] for row in self.gallery["sources"]}
        reserved.update(row["chunk_id"] for row in self.gallery["chunks"])
        for artwork in self.gallery["artworks"]:
            for field in ("title", "artist", "date_display"):
                reserved.add(f"{artwork['artwork_id']}-catalog-{field}")
        self.assertTrue(reserved.isdisjoint(set(chunks) | sources))
        self.assertEqual(self.pack["dataset_version"], self.gallery["dataset_version"])

    def test_full_source_coverage_in_handoff(self):
        handoff = (ROOT / "docs" / "vincent-evidence.md").read_text()
        pack_ids = {r["chunk_id"] for r in self.pack["records"]}
        gallery_ids = {r["chunk_id"] for r in self.gallery["chunks"]
                       if r["artwork_id"] == self.pack["artwork_id"]}
        cited = set(re.findall(r"`((?:vincent-|getty-103jnh-)[a-z0-9-]+)`", handoff))
        self.assertTrue(pack_ids <= cited)
        self.assertFalse(cited - pack_ids - gallery_ids)
        self.assertEqual({r["source_kind"] for r in self.pack["records"]},
                         {"artist_letter", "museum_record"})

    def test_wrong_artwork_and_license_pair_are_rejected(self):
        record = self.pack["records"][0]
        invalid_records = (
            {**record, "artwork_id": "getty-103qt5"},
            {**record, "license": "CC-BY-4.0",
             "license_url": "https://creativecommons.org/licenses/by-nc-sa/4.0/"},
            {**record, "license": "CC-BY-NC-SA-4.0",
             "license_url": "https://creativecommons.org/licenses/by/4.0/"},
        )
        for invalid in invalid_records:
            with self.subTest(invalid=invalid):
                self.assertFalse(self.validator.is_valid({**self.pack, "records": [invalid]}))
