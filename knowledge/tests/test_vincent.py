"""Exercise role scope, canonical evidence and real hybrid mechanics without a live API key."""

import http.client
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch

from knowledge.errors import DatasetValidationError, RetrievalError
from knowledge.http_service import create_server
from knowledge.vector_index import DIMENSIONS
from knowledge.vincent import ARTWORK, DATA, GALLERY, PACK, VINCENT_SCOPE, VincentHybridRetriever, load_vincent_corpus


class RecordingEmbeddings:
    """A deterministic dense signal for a query with no literal match to the target."""
    def __init__(self):
        self.calls = []

    def __call__(self, texts):
        self.calls.append(list(texts))
        vectors = []
        for text in texts:
            axis = 0 if "lilac bush" in text or text == "chromatic recollection" else 1
            vector = [0.] * DIMENSIONS
            vector[axis] = 1.
            vectors.append(vector)
        return vectors


class VincentSearchTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "vincent-index.json"
        self.embed = RecordingEmbeddings()

    def open(self):
        return VincentHybridRetriever.open(VINCENT_SCOPE, embed=self.embed, cache_path=self.path)

    def test_pin_all_scopes_before_loading_provider(self):
        for change in ({"conversation_mode": "guide"}, {"reference_pack_version": None},
                       {"dataset_version": "getty-pilot-v1"}, {"artwork_id": "getty-108hgy"},
                       {"untrusted_extra": "x"}):
            with self.subTest(change=change), self.assertRaises(DatasetValidationError):
                VincentHybridRetriever.open(dict(VINCENT_SCOPE) | change, embed=self.embed, cache_path=self.path)
        self.assertFalse(self.embed.calls)
        self.path.write_bytes((DATA / f"{GALLERY}.json").read_bytes() + b" ")
        with self.assertRaisesRegex(DatasetValidationError, "digest mismatch"):
            load_vincent_corpus(VINCENT_SCOPE, gallery_path=self.path)

    def test_no_key_required_for_canonical_records_but_required_to_search_live(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            corpus = load_vincent_corpus(VINCENT_SCOPE)
            with self.assertRaisesRegex(RetrievalError, "OPENAI_API_KEY"):
                VincentHybridRetriever.open(VINCENT_SCOPE, cache_path=self.path)
        self.assertEqual(11, len(corpus.evidence))
        self.assertEqual({ARTWORK}, {row["artwork_id"] for row in corpus.evidence.values()})
        pack = json.loads((DATA / "reference-packs" / f"{PACK}.json").read_bytes())
        for source in pack["records"]:
            self.assertEqual(corpus.evidence[source["chunk_id"]], source)

    def test_one_combined_index_cache_and_one_query_embedding(self):
        resolver = self.open()
        self.assertEqual({scope for scope, _, _ in resolver.index.passages}, {ARTWORK})
        self.assertEqual({chunk for _, chunk, _ in resolver.index.passages}, set(resolver.evidence))
        reopened = self.open()
        self.assertEqual(len(self.embed.calls), 1)
        result, trace = reopened.search("chromatic recollection")
        self.assertEqual(self.embed.calls[1:], [["chromatic recollection"]])
        self.assertEqual(trace["lexical_ranking"], [])
        self.assertEqual(trace["strategy"], "openai-hybrid-v1")
        self.assertEqual(result["evidence"], [resolver.evidence["vincent-772-garden-colors"]])
        self.assertNotIn("index_text", result["evidence"][0])
        with patch.dict(os.environ, {"MUSEBRIDGE_VECTOR_INDEX": str(self.path)}):
            with self.assertRaisesRegex(DatasetValidationError, "distinct paths"):
                self.open()

    def test_source_filters_and_cross_work_bounds(self):
        resolver = self.open()
        for query, kind in (("What did the letters say about the studies?", "artist_letter"),
                            ("What does the Getty museum report about the red pigment?", "museum_record"),
                            ("What looking exercise does the MuseBridge guide suggest?", "guide_note")):
            result, trace = resolver.search(query)
            self.assertGreater(len(result["evidence"]), 0)
            self.assertLessEqual(len(result["evidence"]), 2)
            self.assertEqual({row["source_kind"] for row in result["evidence"]}, {kind})
            self.assertTrue(trace["dense_ranking"])
        calls = len(self.embed.calls)
        result, _ = resolver.search("How does this compare with The Portal of Rouen Cathedral in Morning Light?")
        self.assertEqual(result["evidence"], [])
        self.assertEqual(len(self.embed.calls), calls)

    def test_failure_is_not_an_empty_search_or_lexical_fallback(self):
        resolver = self.open()
        resolver.index.embed = lambda _: [[-1., *([0.] * (DIMENSIONS - 1))]]
        self.assertEqual(resolver.search("chromatic recollection")[0]["evidence"], [])
        resolver.index.embed = lambda _: []
        with self.assertRaises(RetrievalError) as error:
            resolver.search("What did he write to Theo about violet irises?")
        self.assertEqual(error.exception.code, "retrieval_unavailable")

    def test_http_guide_and_role_use_separate_sources_and_top_k(self):
        resolver = self.open()
        server = create_server(seed_path=DATA / f"{GALLERY}.json", dataset_version=GALLERY, mode="hybrid",
                               vector_index=resolver.index, vincent_resolver=resolver)
        self.addCleanup(server.server_close)
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.shutdown)

        def call(path, fields=None):
            connection = http.client.HTTPConnection(*server.server_address, timeout=3)
            try:
                body = None if fields is None else json.dumps(fields).encode()
                connection.request("GET" if body is None else "POST", path, body,
                                   {"Content-Type": "application/json"})
                reply = connection.getresponse()
                self.assertEqual(reply.getheader("X-Retrieval-Strategy"), "openai-hybrid-v1")
                return reply.status, json.loads(reply.read())
            finally:
                connection.close()

        request = dict(artwork_id=ARTWORK, dataset_version=GALLERY,
                       query="What did the letters say of a lilac bush?", top_k=2)
        self.assertEqual(call("/readyz/vincent"), (200, dict(status="ok", dataset_version=GALLERY,
                                                            reference_pack_version=PACK)))
        self.assertEqual(len(self.embed.calls), 1)
        status, role = call("/retrieve/vincent", request)
        self.assertEqual(status, 200)
        self.assertIn("vincent-772-garden-colors", {r["chunk_id"] for r in role["evidence"]})
        status, guide = call("/retrieve", request)
        self.assertEqual(status, 200)
        self.assertTrue(all(r["chunk_id"].startswith(ARTWORK) for r in guide["evidence"]))
        for change, code in (({"artwork_id": "getty-108hgy"}, 404), ({"dataset_version": "old"}, 409),
                             ({"top_k": 3}, 400), ({"conversation_mode": "guide"}, 400)):
            self.assertEqual(call("/retrieve/vincent", request | change)[0], code)
        self.assertEqual(call("/retrieve", request | {"reference_pack_version": PACK})[0], 400)
        server.vincent_resolver = None
        self.assertEqual(call("/readyz/vincent")[0], 503)
        self.assertEqual(call("/retrieve/vincent", request)[0], 503)
        self.assertEqual(call("/readyz")[0], 200)
