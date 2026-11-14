"""V2 immutable context, five-pair selection and pinned translation checks."""

from copy import deepcopy
import json
from pathlib import Path
import unittest

from agent.runtime.context_v2 import (
    build_answer_input, build_plan_input, fit_history, validate_run_context,
)
from agent.runtime.errors import WorkflowError
from agent.runtime.validation import ContractValidator
from agent.tools.catalog import canonical_catalog
from agent.tools.evidence import EvidenceManifest

_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLES = json.loads((_ROOT / "docs/contracts/agent-v2.examples.json").read_text())
_VALIDATOR = ContractValidator(_ROOT / "docs/contracts/agent-v2.schema.json")


def run_request():
    return deepcopy(next(case["payload"] for case in _EXAMPLES["schema_cases"]
                         if case["name"] == "run_request_initial"))


def history_turn(sequence, *, language="en"):
    return {
        "turn_id": f"00000000-0000-4000-8000-{sequence:012d}",
        "sequence": sequence, "question": f"Earlier question {sequence}",
        "answer_language": language, "answer_status": "conversational",
        "answer_mode": "llm", "answer_text": f"Earlier answer {sequence}",
    }


def with_history(revision=8):
    run = run_request()
    run["conversation_state"].update(revision=revision, last_answer_language="en")
    run["context"]["recent_turns"] = [history_turn(n) for n in range(max(1, revision - 4), revision + 1)]
    return run


def factual_request():
    return {"id": "r1", "original_fragment": "material", "intent": "Identify the artwork material.",
            "kind": "artwork_fact", "resolution": "clear"}


class ContextV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = EvidenceManifest.load()

    def validate(self, run):
        return validate_run_context(run, self.manifest, validator=_VALIDATOR)

    def test_five_pairs_and_original_message_are_projected_without_application_identity(self):
        run = with_history()
        run["question"] = "What about its material?"
        accepted = self.validate(run)
        payload = build_plan_input(accepted, self.manifest)
        _VALIDATOR.validate("PlanInput", payload)
        self.assertEqual([turn["sequence"] for turn in payload["recent_turns"]], [4, 5, 6, 7, 8])
        self.assertEqual(payload["original_question"], run["question"])
        self.assertEqual(payload["catalog_facts"], run["context"]["catalog_facts"])
        self.assertNotIn("revision", payload["conversation_context"])
        self.assertNotIn("conversation_id", payload["conversation_context"])
        for key in ["run_id", "conversation_id", "client_message_id", "execution", "scope"]:
            self.assertNotIn(key, payload)
        self.assertFalse(payload["complete_scope"])
        self.assertEqual(payload["query_policy"]["search_language"], "en")

    def test_all_four_languages_are_allowed_and_history_does_not_override_preference(self):
        for language in ["en", "es", "zh-CN", "ko"]:
            with self.subTest(language=language):
                run = with_history(1)
                run["context"]["recent_turns"][0]["answer_language"] = language
                run["conversation_state"]["last_answer_language"] = language
                run["conversation_state"]["language_preference"] = "es"
                run["language_override"] = "auto"
                payload = build_plan_input(self.validate(run), self.manifest)
                self.assertEqual(payload["conversation_context"]["language_preference"], "es")
                self.assertEqual(payload["language_override"], "auto")
                self.assertEqual(payload["recent_turns"][0]["answer_language"], language)

    def test_history_rejects_missing_latest_gaps_order_duplicates_and_six_pairs(self):
        original = with_history()
        changes = []
        for values in [[3, 4, 5, 6, 7], [4, 5, 7, 8], [5, 4, 6, 7, 8], [3, 4, 5, 6, 7, 8]]:
            run = deepcopy(original)
            run["context"]["recent_turns"] = [history_turn(n) for n in values]
            changes.append(run)
        duplicate = deepcopy(original)
        duplicate["context"]["recent_turns"][1]["turn_id"] = duplicate["context"]["recent_turns"][0]["turn_id"]
        changes.append(duplicate)
        for field, value in [("sequence", True), ("answer_mode", "fixture"),
                             ("answer_status", "unsupported_question"), ("answer_language", "ko")]:
            run = deepcopy(original)
            run["context"]["recent_turns"][-1][field] = value
            changes.append(run)
        for run in changes:
            with self.subTest(history=run["context"]["recent_turns"]), self.assertRaises(WorkflowError) as caught:
                self.validate(run)
            self.assertEqual((caught.exception.code, caught.exception.phase), ("invalid_request", "context"))

    def test_initial_state_and_envelope_consistency_fail_before_projection(self):
        mutations = [
            lambda run: run.update(question=" leading space"),
            lambda run: run["conversation_state"].update(conversation_id=run["run_id"]),
            lambda run: run["conversation_state"]["scope"].update(artwork_id="aic-11723"),
            lambda run: run["conversation_state"].update(last_answer_language="en"),
            lambda run: run["conversation_state"].update(continuity_summary="Uncommitted memory"),
            lambda run: run["conversation_state"].update(language_preference="ko"),
        ]
        for mutate in mutations:
            run = run_request()
            mutate(run)
            with self.assertRaises(WorkflowError) as caught:
                self.validate(run)
            self.assertEqual(caught.exception.code, "invalid_request")

    def test_catalog_and_artwork_scope_mismatch_are_distinct_input_failures(self):
        for field in ["artwork_title", "artist"]:
            run = run_request()
            run["context"][field] += " changed"
            with self.assertRaises(WorkflowError) as caught:
                self.validate(run)
            self.assertEqual(caught.exception.code, "agent_scope_mismatch")
        run = run_request()
        run["context"]["catalog_facts"][2]["value"] = "1900"
        with self.assertRaises(WorkflowError) as caught:
            self.validate(run)
        self.assertEqual(caught.exception.code, "agent_scope_mismatch")
        run = run_request()
        run["scope"]["dataset_version"] = "different-release"
        with self.assertRaises(WorkflowError) as caught:
            self.validate(run)
        self.assertEqual(caught.exception.code, "agent_scope_mismatch")

    def test_pending_can_outlive_window_but_cannot_forge_fragment_or_future_sequence(self):
        run = with_history()
        run["conversation_state"]["pending_clarification"] = {
            "original_question": "What is that object?", "original_fragment": "that object",
            "unresolved_intent": "Identify the object the visitor means.",
            "question": "Which object do you mean?", "created_sequence": 2,
        }
        self.validate(run)
        focused = deepcopy(run)
        focused["conversation_state"]["pending_clarification"]["original_fragment"] = None
        self.validate(focused)
        for field, value in [("original_fragment", "not in the original"), ("created_sequence", 9)]:
            broken = deepcopy(run)
            broken["conversation_state"]["pending_clarification"][field] = value
            with self.assertRaises(WorkflowError) as caught:
                self.validate(broken)
            self.assertEqual(caught.exception.code, "invalid_request")

    def test_entire_bad_cache_is_integrity_failure_with_context_phase(self):
        medium = dict(self.manifest.records["aic-28560-medium"])
        foreign = dict(self.manifest.records["aic-11723-medium"])
        cases = [[dict(medium, excerpt="Changed")], [medium, foreign], [medium, medium],
                 [None], [dict(medium, kind="catalog")],
                 canonical_catalog(self.manifest, "aic-28560", self.manifest.dataset_version)]
        for cache in cases:
            run = with_history(1)
            run["conversation_state"]["verified_evidence"] = deepcopy(cache)
            with self.subTest(cache=cache), self.assertRaises(WorkflowError) as caught:
                self.validate(run)
            self.assertEqual((caught.exception.code, caught.exception.phase),
                             ("retrieval_invalid_response", "context"))

    def test_complete_scope_ignores_three_catalog_records_and_uses_only_retrieval_cache(self):
        run = with_history(1)
        self.assertFalse(build_plan_input(self.validate(run), self.manifest)["complete_scope"])
        cache = [dict(self.manifest.records["aic-28560-medium"])]
        run["conversation_state"]["verified_evidence"] = cache
        self.assertFalse(build_plan_input(self.validate(run), self.manifest)["complete_scope"])
        cache.append(dict(self.manifest.records["aic-28560-description-01"]))
        self.assertTrue(build_plan_input(self.validate(run), self.manifest)["complete_scope"])

    def test_input_and_projection_are_detached_with_compatible_cache_metadata_removed(self):
        run = with_history(1)
        medium = dict(self.manifest.records["aic-28560-medium"], ranking={"score": 1})
        run["conversation_state"]["verified_evidence"] = [medium]
        accepted = self.validate(run)
        payload = build_plan_input(accepted, self.manifest)
        self.assertNotIn("ranking", accepted["conversation_state"]["verified_evidence"][0])
        payload["catalog_facts"][0]["value"] = "Changed"
        payload["cached_evidence"][0]["excerpt"] = "Changed"
        payload["recent_turns"][0]["answer_text"] = "Changed"
        self.assertEqual(accepted["context"]["catalog_facts"][0]["value"], "The Bedroom")
        self.assertEqual(run["conversation_state"]["verified_evidence"][0]["excerpt"], "Oil on canvas")
        self.assertEqual(run["context"]["recent_turns"][0]["answer_text"], "Earlier answer 1")

    def test_answer_projection_accepts_fitted_history_and_pins_exact_translation(self):
        run = self.validate(with_history())
        run["context"]["recent_turns"] = run["context"]["recent_turns"][-1:]
        translation = {"target_language": "ko", "source": {"kind": "history_answer", "sequence": 4},
                       "text": "Earlier answer 4"}
        evidence = [dict(self.manifest.records["aic-28560-medium"])]
        payload = build_answer_input(run, [factual_request()], [], evidence,
                                     answer_language="ko", complete_scope=False, allow_refinement=False,
                                     translation=translation)
        _VALIDATOR.validate("FinalInput", payload)
        self.assertEqual(payload["translation"]["text"], "Earlier answer 4")
        self.assertEqual(len(payload["recent_turns"]), 1)
        payload["translation"]["text"] = "Changed"
        payload["evidence"][0]["excerpt"] = "Changed"
        self.assertEqual(translation["text"], "Earlier answer 4")
        self.assertEqual(evidence[0]["excerpt"], "Oil on canvas")
        for complete, refine in [(True, True), (1, False), (False, 0)]:
            with self.assertRaises(WorkflowError):
                build_answer_input(run, [factual_request()], [], evidence, answer_language="en",
                                   complete_scope=complete, allow_refinement=refine)

    def test_history_fitting_keeps_required_catalog_memory_and_translation(self):
        run = self.validate(with_history())
        translation = {"target_language": "ko", "source": {"kind": "history_answer", "sequence": 4},
                       "text": "Earlier answer 4"}
        payload = build_answer_input(run, [factual_request()], [], [], answer_language="ko",
                                     complete_scope=False, allow_refinement=False, translation=translation)
        original = deepcopy(payload)
        observed = []
        def sizer(messages, schema):
            data = json.loads(messages[1]["content"])
            observed.append(data)
            return 100 + len(data["recent_turns"]) * 100
        fitted = fit_history("assess", payload, {}, prompt_sizer=sizer,
                             context_limit=170, output_tokens=50, safety_tokens=20)
        self.assertEqual(fitted["recent_turns"], [])
        self.assertEqual([len(x["recent_turns"]) for x in observed], [5, 4, 3, 2, 1, 0])
        for key in payload.keys() - {"recent_turns"}:
            self.assertEqual(fitted[key], payload[key])
        self.assertEqual(payload, original)

    def test_required_input_overflow_is_not_silent_translation_truncation(self):
        payload = build_plan_input(self.validate(run_request()), self.manifest)
        original = deepcopy(payload)
        with self.assertRaises(WorkflowError) as caught:
            fit_history("plan", payload, {}, prompt_sizer=lambda messages, schema: 101,
                        context_limit=100, output_tokens=1, safety_tokens=0)
        self.assertEqual(caught.exception.code, "agent_context_limit")
        self.assertEqual(payload, original)

    def test_context_budget_and_injected_sizer_contract_are_strict(self):
        payload = build_plan_input(self.validate(run_request()), self.manifest)
        for count in [-1, True, 1.5]:
            with self.subTest(count=count), self.assertRaises(ValueError):
                fit_history("plan", payload, {}, prompt_sizer=lambda messages, schema: count,
                            context_limit=16384, output_tokens=2048)
        for limit, output, safety in [(True, 10, 0), (100, 0, 0), (100, 10, -1)]:
            with self.assertRaises(ValueError):
                fit_history("plan", payload, {}, prompt_sizer=lambda messages, schema: 0,
                            context_limit=limit, output_tokens=output, safety_tokens=safety)


if __name__ == "__main__":
    unittest.main()
