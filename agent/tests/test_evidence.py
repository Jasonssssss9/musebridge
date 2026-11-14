"""Release integrity, artwork isolation, and immutable evidence union checks."""

from dataclasses import FrozenInstanceError
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agent.runtime.errors import WorkflowError
from agent.tools.evidence import (
    DATASET_VERSION,
    DEFAULT_SEED_PATH,
    EVIDENCE_FIELDS,
    EvidenceManifest,
    SEED_SHA256,
)


BEDROOM = "aic-28560"
TOILETTE = "aic-11723"


class EvidenceManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = EvidenceManifest.load()

    def record(self, chunk_id):
        return dict(self.manifest.records[chunk_id])

    def assert_invalid(self, function, *args, **kwargs):
        with self.assertRaises(WorkflowError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, "retrieval_invalid_response")
        self.assertFalse(caught.exception.retryable)

    def test_hash_and_membership_come_from_reviewed_release(self):
        self.assertEqual(self.manifest.sha256, SEED_SHA256)
        self.assertEqual(self.manifest.dataset_version, DATASET_VERSION)
        self.assertEqual(set(self.manifest.artworks), {BEDROOM, TOILETTE})
        self.assertEqual(len(self.manifest.records), 4)
        for artwork_id in (BEDROOM, TOILETTE):
            ids = self.manifest.chunk_ids_by_artwork[artwork_id]
            self.assertEqual(len(ids), 2)
            self.assertEqual(ids, {key for key, record in self.manifest.records.items() if record["artwork_id"] == artwork_id})
        for record in self.manifest.records.values():
            self.assertEqual(set(record), set(EVIDENCE_FIELDS))

    def test_manifest_and_returned_snapshots_cannot_mutate_trusted_state(self):
        with self.assertRaises(FrozenInstanceError):
            self.manifest.dataset_version = "changed"
        with self.assertRaises(TypeError):
            self.manifest.records[BEDROOM + "-medium"]["excerpt"] = "Changed"
        with self.assertRaises(TypeError):
            self.manifest.artworks[BEDROOM]["title"] = "Changed"
        metadata = self.manifest.artwork(BEDROOM, DATASET_VERSION)
        metadata["title"] = "Changed"
        self.assertEqual(self.manifest.artwork(BEDROOM, DATASET_VERSION)["title"], "The Bedroom")
        candidate = self.record(BEDROOM + "-medium")
        accepted = self.manifest.validate_candidates(BEDROOM, DATASET_VERSION, [candidate])
        accepted[0]["excerpt"] = "Changed"
        self.assertEqual(candidate["excerpt"], "Oil on canvas")
        self.assertEqual(self.manifest.records[candidate["chunk_id"]]["excerpt"], "Oil on canvas")

    def test_modified_missing_and_wrong_release_seed_fail_closed(self):
        content = DEFAULT_SEED_PATH.read_bytes()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "seed.json"
            modified = content.replace(b"Oil on canvas", b"Oil on wood", 1)
            path.write_bytes(modified)
            with self.assertRaises(ValueError):
                EvidenceManifest.load(path)
            path.write_bytes(content)
            with self.assertRaises(ValueError):
                EvidenceManifest.load(path, expected_version="aic-seed-next")
            with self.assertRaises(ValueError):
                EvidenceManifest.load(path, expected_sha256="0" * 64)
            with self.assertRaises(ValueError):
                EvidenceManifest.load(path, expected_sha256="not-a-hash")
            with self.assertRaises(ValueError):
                EvidenceManifest.load(Path(directory) / "missing.json")
            malformed = b'{"dataset_version":"aic-seed-v0.1"}'
            path.write_bytes(malformed)
            with self.assertRaises(ValueError):
                EvidenceManifest.load(path, expected_sha256=hashlib.sha256(malformed).hexdigest())

    def test_rejects_every_changed_canonical_field(self):
        for field in EVIDENCE_FIELDS:
            with self.subTest(field=field):
                candidate = self.record(BEDROOM + "-medium")
                candidate[field] += " changed"
                self.assert_invalid(self.manifest.validate_candidates, BEDROOM, DATASET_VERSION, [candidate])

    def test_rejects_wrong_artwork_version_ids_duplicates_and_size(self):
        candidate = self.record(BEDROOM + "-medium")
        cases = [
            (TOILETTE, DATASET_VERSION, [candidate]),
            (BEDROOM, "aic-seed-next", [candidate]),
            ("aic-missing", DATASET_VERSION, []),
            (BEDROOM, DATASET_VERSION, [candidate, dict(candidate)]),
            (BEDROOM, DATASET_VERSION, [candidate] * 3),
            (BEDROOM, DATASET_VERSION, None),
            (BEDROOM, DATASET_VERSION, [None]),
            (BEDROOM, DATASET_VERSION, [{"chunk_id": candidate["chunk_id"]}]),
        ]
        for args in cases:
            with self.subTest(args=args[:2]):
                self.assert_invalid(self.manifest.validate_candidates, *args)

    def test_compatible_extra_metadata_is_removed(self):
        candidate = self.record(BEDROOM + "-medium")
        candidate["ranking"] = {"score": 1, "debug": "untrusted"}
        accepted = self.manifest.validate_candidates(BEDROOM, DATASET_VERSION, [candidate])
        self.assertEqual(set(accepted[0]), set(EVIDENCE_FIELDS))

    def test_merge_preserves_first_occurrence_and_complete_scope_uses_ids(self):
        medium = self.record(BEDROOM + "-medium")
        description = self.record(BEDROOM + "-description-01")
        union = self.manifest.merge(BEDROOM, DATASET_VERSION, [description], [medium, description])
        self.assertEqual([record["chunk_id"] for record in union], [description["chunk_id"], medium["chunk_id"]])
        self.assertFalse(self.manifest.complete_scope(BEDROOM, DATASET_VERSION, []))
        self.assertFalse(self.manifest.complete_scope(BEDROOM, DATASET_VERSION, [medium]))
        self.assertTrue(self.manifest.complete_scope(BEDROOM, DATASET_VERSION, union))
        union[0]["excerpt"] = "Changed after merge"
        self.assertNotEqual(description["excerpt"], union[0]["excerpt"])
        self.assert_invalid(self.manifest.complete_scope, BEDROOM, DATASET_VERSION, [medium, medium])

    def test_conflicting_cross_call_duplicate_and_foreign_union_fail(self):
        medium = self.record(BEDROOM + "-medium")
        changed = dict(medium, excerpt="A conflicting repeated record")
        self.assert_invalid(self.manifest.merge, BEDROOM, DATASET_VERSION, [medium], [changed])
        foreign = self.record(TOILETTE + "-medium")
        self.assert_invalid(self.manifest.merge, BEDROOM, DATASET_VERSION, [medium], [foreign])


if __name__ == "__main__":
    unittest.main()
