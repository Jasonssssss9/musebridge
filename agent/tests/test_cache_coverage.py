"""A partial cache may lack a source for a new fact in the current artwork."""
import unittest

from agent.runtime.context_v2 import build_plan_input
from agent.runtime.followup_plan import expand_plan
from agent.testing.v2 import language_decision, make_run
from agent.tools.evidence import EvidenceManifest


class CacheCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = EvidenceManifest.load()

    def plan(self, artwork, query, chunks):
        run = make_run(artwork_id=artwork, question=query, manifest=self.manifest)
        run["conversation_state"]["verified_evidence"] = [
            dict(self.manifest.records[f"{artwork}-{suffix}"]) for suffix in chunks
        ]
        payload = build_plan_input(run, self.manifest)
        decision = {"visitor_task": "check_artwork_facts", "language_decision": language_decision(),
                    "evidence_action": "reuse_cache", "facts": [
                        {"source": {"sentence": 1}, "english_search_question": query}]}
        return expand_plan(decision, payload)

    def test_missing_topic_dispatches_model_rewrite_unchanged_for_both_artworks(self):
        for artwork, question, chunks in (
                ("aic-28560", "How does the label describe the colors of The Bedroom?", ["medium"]),
                ("aic-11723", "What material is Woman at Her Toilette made of?", ["description-01"])):
            with self.subTest(artwork=artwork):
                plan = self.plan(artwork, question, chunks)
                self.assertEqual(plan["action"], "retrieve")
                self.assertEqual(plan["queries"], [{"id": "q1", "request_ids": ["r1"], "query": question}])

    def test_present_unknown_foreign_and_complete_scope_do_not_trigger_another_lookup(self):
        for question, chunks in (
                ("What does the label say about the brushwork?", ["description-01"]),
                ("Which exact yellow pigment was used?", ["medium"]),
                ("What is Woman at Her Toilette made of?", ["description-01"]),
                ("How are the lines described?", ["medium", "description-01"])):
            with self.subTest(question=question):
                plan = self.plan("aic-28560", question, chunks)
                self.assertEqual(plan["action"], "answer_from_context")
                self.assertNotIn("queries", plan)
