"""Verify Getty authority survives selection, cache validation and model inputs."""

import os
import unittest
from unittest.mock import patch

from agent.runtime.context_v2 import build_answer_input, build_plan_input
from agent.runtime.errors import WorkflowError
from agent.testing.v2 import make_run
from agent.tools.catalog import canonical_catalog
from agent.tools.evidence import EvidenceManifest


class GettyEvidenceTests(unittest.TestCase):
    def test_guide_author_and_catalog_are_distinct_across_turns(self):
        version, artwork = "getty-pilot-v1", "getty-103rgh"
        with patch.dict(os.environ, {"DATASET_VERSION": version}, clear=True):
            manifest = EvidenceManifest.from_environment()
        run = make_run(artwork_id=artwork, manifest=manifest)
        source = dict(manifest.records[artwork + "-guide-01"])
        verified = manifest.validate_candidates(artwork, version, [source])
        self.assertEqual(verified[0]["source_kind"], "guide_note")
        catalog = canonical_catalog(manifest, artwork, version)
        self.assertEqual({fact["source_id"] for fact in catalog}, {artwork + "-record"})
        run["conversation_state"]["verified_evidence"] = verified
        plan = build_plan_input(run, manifest)
        self.assertEqual(plan["cached_evidence"][0], source)
        self.assertEqual(plan["available_source_kinds"], ["guide_note", "museum_record"])
        answer_input = build_answer_input(run, [], [], verified, answer_language="en",
                                          complete_scope=False, allow_refinement=True)
        self.assertEqual(answer_input["evidence"][0], source)
        for changed in ({**source, "source_kind": "museum_record"},
                        {k: v for k, v in source.items() if k != "source_kind"}):
            with self.subTest(changed=changed.get("source_kind")), self.assertRaises(WorkflowError):
                manifest.validate_candidates(artwork, version, [changed])
