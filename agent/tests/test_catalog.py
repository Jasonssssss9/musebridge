"""Catalog provenance and separation from retrieval evidence for v2."""

from copy import deepcopy
from dataclasses import replace
import json
from types import MappingProxyType
import unittest

from agent.runtime.errors import WorkflowError
from agent.tools.catalog import canonical_catalog, validate_catalog, CATALOG_NORMALIZATION
from agent.tools.evidence import EvidenceManifest, DEFAULT_SEED_PATH


class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = EvidenceManifest.load()
        cls.seed = json.loads(DEFAULT_SEED_PATH.read_text())

    def test_exact_released_fields_and_source_identity_for_both_artworks(self):
        sources = {source["source_id"]: source for source in self.seed["sources"]}
        for artwork in self.seed["artworks"]:
            with self.subTest(artwork=artwork["artwork_id"]):
                facts = canonical_catalog(self.manifest, artwork["artwork_id"], self.manifest.dataset_version)
                self.assertEqual([fact["field"] for fact in facts], ["title", "artist", "date_display"])
                for fact in facts:
                    source = sources[fact["source_id"]]
                    self.assertEqual(fact["value"], artwork[fact["field"]])
                    self.assertEqual(fact["attribution"], source["institution"])
                    self.assertEqual(fact["source_url"], source["source_url"])
                    self.assertEqual(fact["source_title"], source["source_title"])
                    self.assertEqual(fact["source_locator"],
                                     f"artworks[artwork_id={artwork['artwork_id']}].{fact['field']}")
                    self.assertEqual(fact["normalization"], CATALOG_NORMALIZATION)
                    self.assertNotIn("excerpt", fact)
                    self.assertNotIn("license", fact)
                    self.assertNotIn("chunk_id", fact)
                self.assertEqual(facts[-1]["value"], artwork["date_display"])
        self.assertEqual(canonical_catalog(self.manifest, "aic-11723", self.manifest.dataset_version)[2]["value"], "1875–80")

    def test_catalog_and_manifest_sources_are_detached_and_immutable(self):
        facts = canonical_catalog(self.manifest, "aic-28560", self.manifest.dataset_version)
        facts[0]["value"] = "A replacement title"
        self.assertEqual(canonical_catalog(self.manifest, "aic-28560", self.manifest.dataset_version)[0]["value"], "The Bedroom")
        with self.assertRaises(TypeError):
            self.manifest.sources["aic-api-28560"]["institution"] = "Changed"
        with self.assertRaises(TypeError):
            self.manifest.sources["new"] = {}

    def test_each_changed_field_foreign_scope_order_and_extra_record_fail(self):
        facts = canonical_catalog(self.manifest, "aic-28560", self.manifest.dataset_version)
        cases = []
        for key in facts[0]:
            changed = deepcopy(facts)
            changed[0][key] += " changed"
            cases.append(changed)
        cases.extend([None, facts[:2], facts + [deepcopy(facts[0])], list(reversed(facts)),
                      canonical_catalog(self.manifest, "aic-11723", self.manifest.dataset_version)])
        extra = deepcopy(facts)
        extra[0]["excerpt"] = "A fabricated quotation"
        cases.append(extra)
        for candidate in cases:
            with self.subTest(candidate=candidate), self.assertRaises(WorkflowError) as caught:
                validate_catalog(self.manifest, "aic-28560", self.manifest.dataset_version, candidate)
            self.assertEqual(caught.exception.code, "agent_scope_mismatch")
            self.assertEqual(caught.exception.phase, "context")

    def test_catalog_does_not_enter_retrieval_records_or_make_scope_complete(self):
        before_ids = self.manifest.chunk_ids_by_artwork["aic-28560"]
        facts = canonical_catalog(self.manifest, "aic-28560", self.manifest.dataset_version)
        self.assertFalse(self.manifest.complete_scope("aic-28560", self.manifest.dataset_version, []))
        self.assertEqual(self.manifest.chunk_ids_by_artwork["aic-28560"], before_ids)
        self.assertTrue(all(fact["fact_id"] not in self.manifest.records for fact in facts))
        with self.assertRaises(WorkflowError):
            self.manifest.validate_candidates("aic-28560", self.manifest.dataset_version, facts, limit=8)

    def test_unknown_scope_and_missing_associated_source_fail_closed(self):
        for artwork, version in [("aic-missing", self.manifest.dataset_version), ("aic-28560", "changed")]:
            with self.subTest(artwork=artwork), self.assertRaises(WorkflowError) as caught:
                canonical_catalog(self.manifest, artwork, version)
            self.assertEqual(caught.exception.code, "agent_scope_mismatch")
        broken = replace(self.manifest, sources=MappingProxyType({}))
        with self.assertRaises(WorkflowError):
            canonical_catalog(broken, "aic-28560", self.manifest.dataset_version)

    def test_source_institution_is_not_assumed_from_chunk_attribution(self):
        # Exercise provenance selection without mutating the real immutable release.
        source = dict(self.manifest.sources["aic-api-28560"])
        source["institution"] = "Explicit source institution"
        sources = dict(self.manifest.sources)
        sources[source["source_id"]] = MappingProxyType(source)
        fixture_manifest = replace(self.manifest, sources=MappingProxyType(sources))
        facts = canonical_catalog(fixture_manifest, "aic-28560", fixture_manifest.dataset_version)
        self.assertEqual(facts[0]["attribution"], "Explicit source institution")
        self.assertNotEqual(facts[0]["attribution"], self.manifest.records["aic-28560-medium"]["attribution"])


if __name__ == "__main__":
    unittest.main()
