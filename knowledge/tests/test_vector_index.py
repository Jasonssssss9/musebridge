"""Exercise the real splitter, vector store and HTTP protocol without a live account."""

from dataclasses import replace
from io import BytesIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from knowledge.dataset import load_dataset
from knowledge.errors import RetrievalError
from knowledge.vector_index import DIMENSIONS, EMBED_TIMEOUT_SECONDS, MODEL, open_index

SEED = Path(__file__).resolve().parents[1] / "data" / "aic-seed-v0.1.json"
BEDROOM = "aic-28560"
TOILETTE = "aic-11723"


def vector(x, y):
    return [x, y, *([0] * (DIMENSIONS - 2))]


class FakeEmbeddings:
    def __init__(self):
        self.calls = []

    def __call__(self, texts):
        self.calls.append(list(texts))
        return [vector(2, 0) if text == "Oil on canvas" else vector(0, 3) for text in texts]


class VectorIndexTests(unittest.TestCase):
    def setUp(self):
        self.dataset = load_dataset(SEED)
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "cache" / "index.json"
        self.embed = FakeEmbeddings()

    def open(self, *, dataset=None, embed=None):
        return open_index(dataset or self.dataset, self.path, embed=embed or self.embed)

    def test_single_batch_original_texts_one_query_and_artwork_isolation(self):
        index = self.open()
        self.assertEqual(self.embed.calls, [[chunk["excerpt"] for chunk in self.dataset.chunks]])
        query = "How were the painted marks laid down?"
        for scope in (BEDROOM, TOILETTE):
            self.assertEqual(index.rank(scope, query), [
                (scope + "-description-01", 1.0), (scope + "-medium", 0.0),
            ])
        self.assertEqual(self.embed.calls[1:], [[query], [query]])
        self.assertEqual(index.rank("missing-artwork", query), [])
        self.assertEqual(len(self.embed.calls), 3)

    def test_cache_reopens_without_provider_then_rebuilds_if_source_changes(self):
        self.open()
        self.open()
        self.assertEqual(len(self.embed.calls), 1)
        updated = dict(self.dataset.chunks[1], excerpt="New museum wording.")
        release = replace(self.dataset, chunks=(self.dataset.chunks[0], updated, *self.dataset.chunks[2:]))
        self.open(dataset=release)
        self.assertEqual(len(self.embed.calls), 2)
        self.assertIn("New museum wording.", self.embed.calls[-1])

    def test_parent_evidence_grouping_after_500_character_split(self):
        # Every word stays intact at the boundary and a child never cites a different parent.
        long_text = " ".join(f"word{i:03d}" for i in range(160))
        updated = dict(self.dataset.chunks[1], excerpt=long_text)
        release = replace(self.dataset, chunks=(self.dataset.chunks[0], updated, *self.dataset.chunks[2:]))
        index = self.open(dataset=release)
        texts = self.embed.calls[0]
        children = texts[1:-2]
        self.assertGreater(len(children), 1)
        self.assertTrue(all(len(child) <= 500 for child in children))
        self.assertEqual(" ".join(children), long_text)
        self.assertEqual(index.rank(BEDROOM, "query"), [
            (BEDROOM + "-description-01", 1.0), (BEDROOM + "-medium", 0.0),
        ])

    def test_later_child_can_supply_its_parent_best_cosine(self):
        long_text = "first " * 95 + "last section"
        updated = dict(self.dataset.chunks[1], excerpt=long_text)
        release = replace(self.dataset, chunks=(self.dataset.chunks[0], updated, *self.dataset.chunks[2:]))
        def embed(texts):
            return [vector(1, 0) if "last" in text else vector(0, 1) for text in texts]
        index = self.open(dataset=release, embed=embed)
        self.assertEqual(index.rank(BEDROOM, "last"), [
            (BEDROOM + "-description-01", 1.0), (BEDROOM + "-medium", 0.0),
        ])

    def test_malformed_and_wrong_model_cache_rebuilt(self):
        self.open()
        self.path.write_text("incomplete")
        self.open()
        stale = json.loads(self.path.read_text())
        stale["header"]["model"] = "old-embedding"
        self.path.write_text(json.dumps(stale))
        self.open()
        stale = json.loads(self.path.read_text())
        stale["vectors"][0] = [1.0]
        self.path.write_text(json.dumps(stale))
        self.open()
        self.assertEqual(len(self.embed.calls), 4)

    def test_invalid_vectors_never_publish_partial_cache(self):
        self.open()
        previous = self.path.read_bytes()
        updated = replace(self.dataset, dataset_version="test-version")
        for bad in ([], [[0.0] * DIMENSIONS] * 4, [[float("nan")] * DIMENSIONS] * 4,
                    [[True] * DIMENSIONS] * 4, [[1]] * 4):
            with self.subTest(size=len(bad)):
                with self.assertRaises(RetrievalError) as error:
                    self.open(dataset=updated, embed=lambda texts: bad)
                self.assertEqual(error.exception.code, "retrieval_unavailable")
                self.assertEqual(self.path.read_bytes(), previous)

    def test_missing_key_and_bad_query_vector_are_service_faults(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            with self.assertRaisesRegex(RetrievalError, "server-side OPENAI_API_KEY"):
                open_index(self.dataset, self.path)
            self.assertFalse(self.path.exists())
            self.open()
            # Loading a valid persisted index does not require a key.
            with self.assertRaises(RetrievalError) as error:
                open_index(self.dataset, self.path).rank(BEDROOM, "query")
            self.assertEqual(error.exception.code, "retrieval_unavailable")
        with self.assertRaisesRegex(RetrievalError, "query embedding"):
            self.open(embed=lambda texts: [[0, 1]]).rank(BEDROOM, "query")

    def test_official_api_batch_order_payload_and_timeout(self):
        rows = [{"index": i, "embedding": vector(i, 1)} for i in reversed(range(4))]
        response = BytesIO(json.dumps({"model": MODEL, "data": rows}).encode())
        response.status = 200
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), \
             patch("knowledge.vector_index.HTTPSConnection") as connect:
            connection = connect.return_value
            connection.getresponse.return_value = response
            open_index(self.dataset, self.path)
            connect.assert_called_once_with("api.openai.com", timeout=EMBED_TIMEOUT_SECONDS)
            method, path, body, headers = connection.request.call_args.args
            self.assertEqual((method, path), ("POST", "/v1/embeddings"))
            self.assertEqual(headers["Authorization"], "Bearer test-key")
            self.assertEqual(json.loads(body)["input"], [c["excerpt"] for c in self.dataset.chunks])
            self.assertEqual(json.loads(body)["model"], MODEL)
        rows = json.loads(self.path.read_text())["vectors"]
        self.assertEqual([row[0] for row in rows], list(range(4)))

    def test_invalid_provider_indices_or_failure_do_not_retry_or_expose_contents(self):
        for status, body in ((429, "secret-error"),
                             (200, json.dumps({"model": MODEL, "data": [
                                 {"index": 0, "embedding": vector(1, 0)} for _ in range(4)
                             ]}))):
            response = BytesIO(body.encode())
            response.status = status
            with patch.dict(os.environ, {"OPENAI_API_KEY": "secret-key"}), \
                 patch("knowledge.vector_index.HTTPSConnection") as connect:
                connect.return_value.getresponse.return_value = response
                with self.assertRaises(RetrievalError) as error:
                    open_index(self.dataset, self.path)
                self.assertEqual(error.exception.code, "retrieval_unavailable")
                self.assertNotIn("secret", str(error.exception))
                connect.return_value.request.assert_called_once()
                self.assertFalse(self.path.exists())
