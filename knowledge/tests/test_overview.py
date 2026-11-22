"""Whole-work overviews have a narrow source type and preserve artwork isolation."""
from pathlib import Path
import unittest

from knowledge import DATASET_VERSION, load_dataset, retrieve
from knowledge.query import parse_requests


class OverviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset(Path(__file__).resolve().parents[1] / "data" / f"{DATASET_VERSION}.json")

    def facets(self, query, artwork="aic-28560"):
        return [r.facet for r in parse_requests(query, artwork)[1]]

    def evidence(self, artwork, query):
        answer = retrieve(self.dataset, artwork_id=artwork, dataset_version=DATASET_VERSION, query=query)
        return [row["chunk_id"] for row in answer["evidence"]]

    def test_explicit_intro_and_whole_work_appearance_use_only_the_scoped_description(self):
        for artwork, title, artist in (("aic-28560", "The Bedroom", "Vincent van Gogh"),
                                       ("aic-11723", "Woman at Her Toilette", "Berthe Morisot")):
            for query in (f"How does the museum describe {title}?",
                          f"What does the museum say about the appearance of {title} by {artist}?",
                          f"What brief overview does the museum provide of “{title}”?",
                          "Could you give me a short introduction to this painting?",
                          "What does the current artwork look like overall?",
                          "请简单介绍一下这幅画。", "馆方如何描述这件作品的整体外观？"):
                with self.subTest(artwork=artwork, query=query):
                    self.assertEqual(self.evidence(artwork, query), [artwork + "-description-01"])

    def test_overview_never_supplies_a_missing_position_origin_identity_or_historical_fact(self):
        for query in ("Can you give me an overview?", "How does the label describe it?",
                      "What about the one over there?", "What does that look like?",
                      "What does the museum say about the appearance of the chair in The Bedroom?",
                      "How does the museum describe The Bedroom before restoration?",
                      "Who provided the introduction to The Bedroom?", "Who owned the room in The Bedroom?",
                      "Where is the wallpaper in Woman at Her Toilette?", "请告诉我这里的历史。",
                      "How does the museum describe the ownership history of this artwork?",
                      "Could you give me a short introduction to this painting's pigments?"):
            with self.subTest(query=query):
                self.assertNotIn("description_overview", self.facets(query))
        self.assertNotIn("description_overview", self.facets("How does the museum describe the colors in The Bedroom?"))

    def test_foreign_title_isolated_even_if_a_whole_work_description_exists(self):
        self.assertEqual(self.evidence("aic-28560", "How does the museum describe Woman at Her Toilette?"), [])
        self.assertEqual(self.evidence("aic-11723", "What does The Bedroom look like?"), [])
