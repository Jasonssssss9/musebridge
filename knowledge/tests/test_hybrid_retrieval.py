"""Exercise hybrid fusion through the same service entry point used by the Agent."""

import http.client
import json
import threading
import unittest
from unittest.mock import Mock, patch

from knowledge import DATASET_VERSION, RetrievalError, load_dataset
from knowledge.http_service import DEFAULT_SEED, create_server
from knowledge.retrieval import DENSE_MIN_COSINE, fuse_rankings, retrieve_with_trace
from knowledge.strategy import configured_index

BEDROOM = "aic-28560"
TOILETTE = "aic-11723"


class HybridTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset(DEFAULT_SEED)

    def search(self, query, scores, *, artwork_id=BEDROOM, top_k=2):
        index = Mock()
        index.rank.return_value = scores
        response, trace = retrieve_with_trace(
            self.dataset, query=query, artwork_id=artwork_id, dataset_version=DATASET_VERSION,
            top_k=top_k, vector_index=index,
        )
        return response["evidence"], trace, index

    def test_planner_keyword_query_recovers_verified_excerpt_for_each_artwork(self):
        for artwork, query in ((BEDROOM, "The Bedroom van Gogh brushwork description"),
                               (TOILETTE, "Woman at Her Toilette Morisot brushwork description")):
            description = artwork + "-description-01"
            hits, trace, index = self.search(query, [(description, 0.62), (artwork + "-medium", 0.12)], artwork_id=artwork)
            self.assertEqual([hit["chunk_id"] for hit in hits], [description])
            self.assertFalse(trace["lexical_ranking"])
            original = next(chunk for chunk in self.dataset.chunks if chunk["chunk_id"] == description)
            self.assertEqual(hits[0]["excerpt"], original["excerpt"])
            index.rank.assert_called_once_with(artwork, "brushwork description")

    def test_both_routes_contribute_and_limit_applies_after_deduplication(self):
        medium, description = BEDROOM + "-medium", BEDROOM + "-description-01"
        self.assertEqual(fuse_rankings([medium], [description, medium]), [medium, description])
        for k in (1, 2):
            hits, _, _ = self.search("What material is this work made of?",
                                      [(description, 0.83), (medium, 0.42)], top_k=k)
            self.assertEqual([hit["chunk_id"] for hit in hits], [medium, description][:k])
        hits, _, _ = self.search("What material is this work made of?", [(description, 0.70)])
        self.assertEqual([hit["chunk_id"] for hit in hits], [medium, description])

    def test_scope_and_cutoff_also_apply_to_dense_only_candidates(self):
        hits, _, _ = self.search("Where did the painter buy lunch?", [
            (TOILETTE + "-description-01", 0.91), (BEDROOM + "-medium", DENSE_MIN_COSINE - 0.01),
        ])
        self.assertEqual(hits, [])
        hits, _, index = self.search("Woman at Her Toilette brushwork description", [])
        self.assertEqual(hits, [])
        index.rank.assert_not_called()

    def test_only_local_fragment_is_sent_for_mixed_artwork_request(self):
        _, _, index = self.search("What material is The Bedroom made of? And what brush marks does Woman at Her Toilette show?", [])
        scope, query = index.rank.call_args.args
        self.assertEqual(scope, BEDROOM)
        self.assertIn("material", query)
        self.assertNotIn("Toilette", query)

    def test_known_uncovered_detail_does_not_trigger_a_related_semantic_candidate(self):
        for work, query in ((BEDROOM, "Which exact pigment did Van Gogh use for the blue walls?"),
                            (TOILETTE, "What variety of flower is shown in the wallpaper?")):
            hits, _, index = self.search(query, [(work + "-description-01", 0.81)], artwork_id=work)
            self.assertEqual(hits, [])
            index.rank.assert_not_called()

    def test_excluding_an_uncovered_detail_preserves_an_unrecognized_second_query(self):
        query = "How wide is this painting? Which features of the painted surface make the scene feel unsettled?"
        evidence, _, index = self.search(query, [(BEDROOM + "-description-01", 0.53)])
        self.assertEqual([item["chunk_id"] for item in evidence], [BEDROOM + "-description-01"])
        _, embedded = index.rank.call_args.args
        self.assertIn("unsettled", embedded)
        self.assertNotIn("wide", embedded)

    def test_compound_query_can_return_both_source_records(self):
        ids = [BEDROOM + "-description-01", BEDROOM + "-medium"]
        hits, _, _ = self.search("How does the museum describe the brushwork, and what is its medium?", [(ids[0], 0.68)])
        self.assertEqual({hit["chunk_id"] for hit in hits}, set(ids))

    def test_hybrid_requires_key_even_with_a_warm_index(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": ""}):
            with self.assertRaisesRegex(RetrievalError, "OPENAI_API_KEY") as caught:
                configured_index(self.dataset, "hybrid")
            self.assertEqual(caught.exception.code, "retrieval_unavailable")

    def test_http_returns_strategy_and_canonical_candidates_or_technical_failure(self):
        index = Mock()
        index.rank.side_effect = [[(BEDROOM + "-description-01", 0.75)],
                                  RetrievalError("retrieval_unavailable", "Embedding provider unavailable.")]
        server = create_server(mode="hybrid", vector_index=index)
        with server:
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
            thread.start()
            try:
                for expected_status in (200, 503):
                    connection = http.client.HTTPConnection(*server.server_address, timeout=2)
                    try:
                        connection.request("POST", "/retrieve", json.dumps({
                            "dataset_version": DATASET_VERSION, "artwork_id": BEDROOM,
                            "query": "The Bedroom van Gogh brushwork description", "top_k": 1,
                        }), {"Content-Type": "application/json"})
                        response = connection.getresponse()
                        body = json.load(response)
                        self.assertEqual(response.status, expected_status)
                        self.assertEqual(response.getheader("X-Retrieval-Strategy"), "openai-hybrid-v1")
                        if expected_status == 200:
                            self.assertEqual(body["evidence"][0]["chunk_id"], BEDROOM + "-description-01")
                        else:
                            self.assertEqual(body["error"]["code"], "retrieval_unavailable")
                    finally:
                        connection.close()
            finally:
                server.shutdown()
                thread.join()
