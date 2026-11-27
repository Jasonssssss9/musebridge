"""Release-level checks for the curated twenty-work gallery and its bundled images."""

from collections import defaultdict
from hashlib import sha256
from pathlib import Path
import unittest

from knowledge.dataset import load_dataset


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "knowledge/data"
VERSION = "getty-gallery-v2"


class GettyGalleryReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed = DATA / (VERSION + ".json")
        cls.gallery = load_dataset(cls.seed, expected_version=VERSION)

    def test_curated_roster_and_release_digest(self):
        expected = (
            "103JNH 103QT5 103QTZ 10A2Y1 103RC9 103RK8 103QTM 109Q8G 103QTF 103QSE "
            "11409C 108HGY 103RGH 103RFP 104G0S 103QTR 103QTJ 103QSB 103QSK 103RR6"
        ).split()
        self.assertEqual(list(self.gallery.artworks), ["getty-" + code.lower() for code in expected])
        self.assertEqual(self.seed.with_suffix(".sha256").read_text().strip(),
                         sha256(self.seed.read_bytes()).hexdigest())

    def test_every_work_has_its_own_image_excerpts_and_distinct_looking_notes(self):
        chunks = defaultdict(list)
        for chunk in self.gallery.chunks:
            chunks[chunk["artwork_id"]].append(chunk)
        image_hashes = set()
        for key, art in self.gallery.artworks.items():
            with self.subTest(artwork=key):
                image = (ROOT / "web/public" / art["image_url"].lstrip("/")).read_bytes()
                self.assertTrue(image.startswith(b"\xff\xd8") and image.endswith(b"\xff\xd9"))
                image_hashes.add(sha256(image).digest())
                guides, descriptions = [], []
                for chunk in chunks[key]:
                    source = self.gallery.sources[chunk["source_id"]]
                    if source["source_kind"] == "guide_note":
                        guides.append(chunk["excerpt"])
                    elif chunk["source_locator"].startswith("Object description"):
                        descriptions.append(chunk["excerpt"])
                self.assertGreaterEqual(len(set(guides)), 2)
                self.assertTrue(descriptions)
        self.assertEqual(len(image_hashes), len(self.gallery.artworks))

    def test_existing_pilot_citations_keep_their_original_evidence(self):
        pilot = load_dataset(DATA / "getty-pilot-v1.json", expected_version="getty-pilot-v1")
        current = {chunk["chunk_id"]: chunk for chunk in self.gallery.chunks}
        for chunk in pilot.chunks:
            with self.subTest(chunk=chunk["chunk_id"]):
                self.assertEqual(current.get(chunk["chunk_id"]), chunk)
