"""Getty retrieval must keep gallery and editorial provenance through ranking."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from knowledge.dataset import load_dataset
from knowledge.errors import DatasetValidationError
from knowledge.retrieval import retrieve


VERSION = "getty-pilot-v1"
SEED = Path(__file__).resolve().parents[1] / "data" / f"{VERSION}.json"


class GettyRetrievalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset(SEED, expected_version=VERSION)

    def search(self, artwork, query, **kwargs):
        return retrieve(self.dataset, artwork_id=artwork, query=query,
                        dataset_version=VERSION, **kwargs)["evidence"]

    def test_pilot_questions_select_the_expected_sources(self):
        for key in ("getty-108hgy", "getty-103rgh", "getty-103rfp"):
            fact, guide, _ = self.dataset.artworks[key]["suggested_questions"]
            with self.subTest(artwork=key):
                records = self.search(key, fact)
                self.assertIn(key + "-description-02", [r["chunk_id"] for r in records])
                self.assertTrue(all(r["source_kind"] == "museum_record" for r in records))
                notes = self.search(key, guide)
                self.assertEqual([r["chunk_id"] for r in notes], [key + "-guide-01"])
                self.assertEqual(notes[0]["attribution"], "MuseBridge")

    def test_looking_prompts_preserve_the_speaker_of_the_suggestion(self):
        key = "getty-103rgh"
        guide_queries = (
            "Starry Night Edvard Munch guide suggestion where to look first night scene",
            "Which detail should a visitor focus on in this scene?",
            "What would you recommend looking at in the foreground?",
            "Give me a close-looking exercise for this work.",
            "Where to begin looking at the fence and shore?",
            "What does the MuseBridge guide suggest about looking at this painting at the Getty?",
            "What is MuseBridge's recommendation for this Getty painting?",
        )
        official_queries = (
            "Where does the Getty suggest I look first in the night scene?",
            "What is the museum guide's suggestion about the fence?",
            "MuseBridge, what does the curator say about the fence?",
            "What does Getty's own record say about the fence mentioned in the MuseBridge guide?",
            "Does the fence suggest a shadow?",
        )
        for query, kind in [(q, "guide_note") for q in guide_queries] + [
                (q, "museum_record") for q in official_queries]:
            with self.subTest(query=query):
                records = self.search(key, query)
                self.assertTrue(records)
                self.assertEqual({r["source_kind"] for r in records}, {kind})
                self.assertEqual({r["artwork_id"] for r in records}, {key})

    def test_common_catalog_paraphrases_work_for_each_artwork(self):
        for key in self.dataset.artworks:
            with self.subTest(artwork=key):
                self.assertEqual(self.search(key, "How large is this work?", top_k=1)[0]["chunk_id"],
                                 key + "-dimensions")
                self.assertEqual(self.search(key, "What is this made of?", top_k=1)[0]["chunk_id"],
                                 key + "-medium")
                overview, = self.search(key, "Give me an overview.", top_k=1)
                self.assertTrue(overview["source_locator"].startswith("Object description"))

    def test_dense_neighbors_obey_artwork_and_source_kind(self):
        class Dense:
            def rank(self, artwork, query):
                return [("getty-108hgy-guide-01", .99), ("getty-103rgh-medium", .98),
                        ("getty-108hgy-medium", .97)]
        records = self.search("getty-108hgy", "What medium does the Getty list?",
                              vector_index=Dense())
        self.assertEqual([r["chunk_id"] for r in records], ["getty-108hgy-medium"])
        self.assertEqual(self.search("getty-108hgy", "Which medium was used for Starry Night?",
                                     vector_index=Dense()), [])

    def test_incorrect_parent_or_guide_attribution_is_rejected(self):
        original = json.loads(SEED.read_bytes())
        for field in ("based_on_source_id", "attribution"):
            data = deepcopy(original)
            if field == "based_on_source_id":
                guide = next(s for s in data["sources"] if s["source_kind"] == "guide_note")
                guide[field] = next(s["source_id"] for s in data["sources"]
                                    if s["artwork_id"] != guide["artwork_id"])
            else:
                next(c for c in data["chunks"] if c["attribution"] == "MuseBridge")[field] = "Getty"
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "data.json"
                path.write_text(json.dumps(data))
                with self.assertRaises(DatasetValidationError):
                    load_dataset(path, expected_version=VERSION)
