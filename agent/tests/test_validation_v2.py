"""V2 contract/relationship tests; scripted decisions do not establish model quality."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from agent.runtime.errors import WorkflowError
from agent.runtime.validation import ContractViolation, model_decision
from agent.runtime.validation_v2 import (
    ContractValidatorV2, assemble_answer, derive_pending, derive_status,
    resolve_language, resolve_translation, validate_plan, validate_refinement,
)

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = json.loads((ROOT / "docs/contracts/agent-v2.examples.json").read_text())
SHAPES = {case["name"]: case for case in EXAMPLES["schema_cases"]}
RUNTIME = {case["name"]: case for case in EXAMPLES["runtime_cases"]}


def example(name):
    return deepcopy(SHAPES[name]["payload"])


def run_for(question=None):
    run = example("run_request_initial")
    if question is not None:
        run["question"] = question
    return run


class V2ValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = ContractValidatorV2()

    def assert_invalid(self, function, *args, **kwargs):
        with self.assertRaises(WorkflowError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, "generation_invalid_response")
        return caught.exception

    def test_all_shared_structural_examples(self):
        for case in EXAMPLES["schema_cases"]:
            with self.subTest(case=case["name"]):
                if case["valid"]:
                    self.validator.validate(case["definition"], case["payload"])
                else:
                    with self.assertRaises(ContractViolation):
                        self.validator.validate(case["definition"], case["payload"])

    def test_provider_schema_is_self_contained_and_detached(self):
        from jsonschema import Draft202012Validator
        for name in ("Plan", "AssessmentDecision", "FinalDecision"):
            schema = self.validator.schema(name)
            Draft202012Validator.check_schema(schema)
            self.assertNotIn("RunRequest", schema["$defs"])
            schema["$defs"].clear()
            self.assertIn(name, self.validator.schema(name)["$defs"])

    def test_shared_language_resolution_cases(self):
        for case in EXAMPLES["runtime_cases"]:
            if case["kind"] != "language_resolution":
                continue
            with self.subTest(case=case["name"]):
                payload = case["payload"]
                run = run_for(payload["question"])
                run["language_override"] = payload.get("language_override")
                run["conversation_state"].update(payload.get("conversation_context", {}))
                decision = payload.get("language_decision", payload.get("plan", {}).get("language_decision"))
                if not case["valid"]:
                    self.assert_invalid(resolve_language, run, decision)
                    continue
                actual = resolve_language(run, decision)
                for key, value in case["expected"].items():
                    if key != "action":
                        self.assertEqual(actual[key], value)

    def test_language_source_binding_and_persistence_are_strict(self):
        run = run_for("Spanish only for this reply.")
        decision = {"detected_language": "und", "explicit_instruction": {
            "preference": "es", "original_fragment": run["question"],
            "purpose": "turn_response", "persistence": "turn"},
            "translation": None, "answer_language": "es"}
        self.assertEqual(resolve_language(run, decision)["next_language_preference"], "auto")
        for mutation in ("fragment", "persistence", "language"):
            bad = deepcopy(decision)
            if mutation == "fragment": bad["explicit_instruction"]["original_fragment"] = "Not said."
            elif mutation == "persistence": bad["explicit_instruction"]["persistence"] = "conversation"
            else: bad["answer_language"] = "ko"
            self.assert_invalid(resolve_language, run, bad)

    def test_exact_translation_is_bound_to_actual_fitted_history(self):
        case = RUNTIME["translation_uses_exact_available_answer"]["payload"]
        run = run_for(case["question"])
        decision = {"detected_language": "und", "explicit_instruction": None,
                    "translation": deepcopy(case["translation"]), "answer_language": "ko"}
        turns = deepcopy(case["recent_turns"])
        self.assertEqual(resolve_translation(run, decision, recent_turns=turns), case["resolved_translation"])
        self.assert_invalid(resolve_translation, run, decision, recent_turns=[])
        self.assertEqual(turns, case["recent_turns"])
        decision["translation"]["source"] = {"kind": "history_excerpt", "sequence": 1,
                                                "excerpt": "oil on canvas"}
        self.assertEqual(resolve_translation(run, decision, recent_turns=turns)["text"], "oil on canvas")
        decision["translation"]["source"]["excerpt"] = "a summarized oil painting"
        self.assert_invalid(resolve_translation, run, decision, recent_turns=turns)

    def test_translation_current_quote_is_literal_and_detached(self):
        run = run_for('Translate "The artist used cobalt blue" into Korean.')
        decision = {"detected_language": "und", "explicit_instruction": None,
                    "answer_language": "ko", "translation": {
                        "target_language": "ko", "original_fragment": run["question"],
                        "source": {"kind": "current_quote", "excerpt": "The artist used cobalt blue"}}}
        result = resolve_translation(run, decision)
        self.assertEqual(result["text"], "The artist used cobalt blue")
        result["source"]["excerpt"] = "changed"
        self.assertEqual(decision["translation"]["source"]["excerpt"], "The artist used cobalt blue")
        decision["translation"]["source"]["excerpt"] = "Museum verified cobalt blue"
        self.assert_invalid(resolve_translation, run, decision)

    def test_missing_translation_source_only_allows_whole_clarification(self):
        for case in EXAMPLES["runtime_cases"]:
            if case["kind"] != "translation_route_binding":
                continue
            with self.subTest(case=case["name"]):
                payload = case["payload"]
                run = run_for(payload["question"])
                run["conversation_state"]["language_preference"] = payload.get("saved_preference", "auto")
                if case["valid"]:
                    validate_plan(run, payload["plan"], self.validator)
                else:
                    self.assert_invalid(validate_plan, run, payload["plan"], self.validator)

    def test_shared_plan_relationship_examples(self):
        for case in EXAMPLES["runtime_cases"]:
            if case["kind"] != "plan_binding":
                continue
            with self.subTest(case=case["name"]):
                payload = case["payload"]
                plan = payload["plan"]
                question = payload.get("question", " ".join(r["original_fragment"] for r in plan["requests"]))
                run = run_for(question)
                run["conversation_state"]["verified_evidence"] = payload.get("cached_evidence", [])
                args = {"complete_scope": payload.get("complete_scope", False)}
                if case["valid"]: validate_plan(run, plan, self.validator, **args)
                else: self.assert_invalid(validate_plan, run, plan, self.validator, **args)

    def test_plans_reject_reordered_ids_false_quotes_and_duplicate_queries(self):
        run = run_for()
        base = example("mixed_social_and_fact_plan")
        mutations = []
        bad = deepcopy(base); bad["requests"][0]["id"] = "r3"; mutations.append(bad)
        bad = deepcopy(base); bad["requests"][0]["original_fragment"] = "Never asked"; mutations.append(bad)
        bad = deepcopy(base); second = deepcopy(bad["queries"][0]); second["id"] = "q2"
        second["query"] = second["query"].upper(); bad["queries"].append(second); mutations.append(bad)
        bad = deepcopy(base); bad["queries"][0]["id"] = "q2"; mutations.append(bad)
        bad = deepcopy(base); bad["queries"][0]["query"] += " "; mutations.append(bad)
        for bad in mutations:
            self.assert_invalid(validate_plan, run, bad, self.validator)

    def test_reuse_requires_nonempty_cache_and_direct_catalog_remains_allowed(self):
        plan = example("answer_from_verified_context")
        run = run_for(plan["requests"][0]["original_fragment"])
        self.assert_invalid(validate_plan, run, plan, self.validator)
        run["conversation_state"]["verified_evidence"] = example("run_result_mixed")["evidence_snapshot"]
        validate_plan(run, plan, self.validator, complete_scope=True)
        catalog = example("catalog_only_direct_answer")
        run["question"] = " ".join(r["original_fragment"] for r in catalog["requests"])
        validate_plan(run, catalog, self.validator, complete_scope=True)

    def test_refinement_uses_only_selected_clear_requests_and_fresh_queries(self):
        plan = example("mixed_social_and_fact_plan")
        refine = {"action": "refine", "reason_code": "poor_candidate_relevance", "request_ids": ["r2"],
                  "queries": [{"id": "q2", "request_ids": ["r2"], "query": "What is the painting support?"}]}
        kwargs = {"complete_scope": False, "allow_refinement": True}
        validate_refinement(plan, refine, plan["queries"], self.validator, **kwargs)
        for key in ("complete_scope", "allow_refinement"):
            flags = dict(kwargs); flags[key] = not flags[key]
            self.assert_invalid(validate_refinement, plan, refine, plan["queries"], self.validator, **flags)
        for mutation in ("ids", "query", "selection", "links"):
            bad = deepcopy(refine)
            if mutation == "ids": bad["queries"][0]["id"] = "q1"
            elif mutation == "query": bad["queries"][0]["query"] = plan["queries"][0]["query"].upper()
            elif mutation == "selection": bad["request_ids"] = ["r1"]
            else: bad["queries"][0]["request_ids"] = ["r1"]
            self.assert_invalid(validate_refinement, plan, bad, plan["queries"], self.validator, **kwargs)
        reused = example("answer_from_verified_context")
        initial = deepcopy(refine); initial["request_ids"] = ["r1"]
        initial["queries"][0].update(id="q1", request_ids=["r1"])
        validate_refinement(reused, initial, [], self.validator, **kwargs)

    def test_refinement_cannot_target_ambiguous_or_catalog_requests(self):
        plan = example("retrieval_preserves_one_local_clarification")
        refine = {"action": "refine", "reason_code": "uncovered_request", "request_ids": ["r2"],
                  "queries": [{"id": "q2", "request_ids": ["r2"], "query": "Identify the object."}]}
        self.assert_invalid(validate_refinement, plan, refine, plan["queries"], self.validator,
                            complete_scope=False, allow_refinement=True)
        plan["requests"][1].update(kind="catalog_fact", resolution="clear")
        self.assert_invalid(validate_refinement, plan, refine, plan["queries"], self.validator,
                            complete_scope=False, allow_refinement=True)

    def test_shared_status_examples(self):
        self.assert_invalid(derive_status, [], [])
        duplicate = example("direct_appreciation")
        self.assert_invalid(derive_status, duplicate["requests"] * 2, duplicate["results"] * 2)
        for case in EXAMPLES["runtime_cases"]:
            if case["kind"] == "status_derivation":
                with self.subTest(case=case["name"]):
                    payload = case["payload"]
                    self.assertEqual(derive_status(payload["requests"], payload["results"]), case["expected"]["status"])

    def test_mixed_answer_keeps_sources_and_only_unresolved_pending_focus(self):
        case = RUNTIME["local_clarification_answers_known_fact"]
        payload = case["payload"]
        run = run_for(payload["question"]); run["conversation_state"] = deepcopy(payload["prior_state"])
        plan = payload["plan"]; result = payload["result"]
        decision = {"action": "finish", "results": deepcopy(result["request_results"]),
                    "memory_update": deepcopy(result["conversation_update"]["memory"])}
        actual, parts, memory = assemble_answer(run, plan, decision, result["evidence_snapshot"],
                                              result["catalog_snapshot"], self.validator)
        self.assertEqual(actual, result["answer"])
        self.assertEqual(derive_pending(run["conversation_state"], run["question"], plan, decision, 1),
                         case["expected"]["pending_clarification"])
        actual["citations"][0]["excerpt"] = "mutated"
        memory["pending_update"]["request_id"] = "r1"
        self.assertEqual(result["evidence_snapshot"][0]["excerpt"], "Oil on canvas")
        self.assertEqual(decision["memory_update"]["pending_update"]["request_id"], "r2")
        bad = deepcopy(decision); bad["memory_update"]["pending_update"]["request_id"] = "r1"
        self.assert_invalid(assemble_answer, run, plan, bad, result["evidence_snapshot"],
                            result["catalog_snapshot"], self.validator)

    def test_catalog_sources_cannot_support_non_catalog_claims(self):
        run = run_for(); evidence = example("run_result_mixed")["evidence_snapshot"]
        catalog = run["context"]["catalog_facts"]
        plan = example("mixed_social_and_fact_plan"); decision = example("mixed_finished_response")
        good, _, _ = assemble_answer(run, plan, decision, evidence, catalog, self.validator)
        self.assertEqual(good["status"], "answered")
        decision["results"][1]["citation_ids"] = [catalog[0]["fact_id"]]
        self.assert_invalid(assemble_answer, run, plan, decision, evidence, catalog, self.validator)
        plan = example("catalog_only_direct_answer")
        run["question"] = " ".join(r["original_fragment"] for r in plan["requests"])
        result, _, _ = assemble_answer(run, plan, plan, [], catalog, self.validator)
        self.assertEqual(result["status"], "answered")
        plan["results"][0]["citation_ids"] = [evidence[0]["chunk_id"]]
        self.assert_invalid(assemble_answer, run, plan, plan, evidence, catalog, self.validator)

    def test_answer_rejects_unknown_sources_foreign_scope_and_duplicate_records(self):
        run = run_for(); plan = example("mixed_social_and_fact_plan")
        decision = example("mixed_finished_response")
        evidence = example("run_result_mixed")["evidence_snapshot"]; catalog = run["context"]["catalog_facts"]
        bad = deepcopy(decision); bad["results"][1]["citation_ids"] = ["invented"]
        self.assert_invalid(assemble_answer, run, plan, bad, evidence, catalog, self.validator)
        self.assert_invalid(assemble_answer, run, plan, decision, evidence + evidence, catalog, self.validator)
        bad_evidence = deepcopy(evidence); bad_evidence[0]["artwork_id"] = "aic-11723"
        self.assert_invalid(assemble_answer, run, plan, decision, bad_evidence, catalog, self.validator)
        bad_catalog = deepcopy(catalog); bad_catalog[0]["dataset_version"] = "other"
        self.assert_invalid(assemble_answer, run, plan, decision, evidence, bad_catalog, self.validator)

    def test_answer_rejects_omission_reordering_frozen_resolution_and_overflow(self):
        run = run_for(); plan = example("mixed_social_and_fact_plan")
        decision = example("mixed_finished_response")
        evidence = example("run_result_mixed")["evidence_snapshot"]; catalog = run["context"]["catalog_facts"]
        mutations = []
        bad = deepcopy(decision); bad["results"].pop(); mutations.append(bad)
        bad = deepcopy(decision); bad["results"].reverse(); mutations.append(bad)
        bad = deepcopy(decision); bad["results"][0]["support"] = "clarification_needed"
        bad["memory_update"]["pending_update"] = {"action": "set", "source": "current_message", "request_id": "r1"}
        mutations.append(bad)
        bad = deepcopy(decision)
        for part in bad["results"]: part["text"] = "x" * 1000
        mutations.append(bad)
        for bad in mutations:
            self.assert_invalid(assemble_answer, run, plan, bad, evidence, catalog, self.validator)

    def test_whole_clarification_scope_and_direct_dialogue(self):
        for name, status in (("planner_clarification", "clarification_needed"),
                             ("planner_scope_guidance", "out_of_scope"),
                             ("direct_appreciation", "conversational")):
            plan = example(name)
            run = run_for(" ".join(r["original_fragment"] for r in plan.get("requests", [])) or "Why?")
            answer, parts, memory = assemble_answer(run, plan, plan, [], run["context"]["catalog_facts"], self.validator)
            self.assertEqual(answer["status"], status)
            self.assertEqual(answer["citations"], [])
            if name != "direct_appreciation": self.assertEqual(parts, [])

    def test_shared_whole_pending_memory_transitions(self):
        for case in EXAMPLES["runtime_cases"]:
            if case["kind"] != "memory_transition": continue
            with self.subTest(case=case["name"]):
                p = case["payload"]
                if p["answer"]["status"] == "clarification_needed":
                    plan = {"action": "clarify", "text": p["answer"]["text"], "memory_update": p["memory_update"]}
                else:
                    plan = {"action": "direct_response", "requests": [], "results": [], "memory_update": p["memory_update"]}
                args = (p["prior_state"], p["question"], plan, plan, p["prior_state"]["revision"] + 1)
                if case["valid"]:
                    self.assertEqual(derive_pending(*args), case["expected"]["pending_clarification"])
                else: self.assert_invalid(derive_pending, *args)

    def test_focused_followup_preserves_old_quote_and_current_focused_intent(self):
        case = RUNTIME["focused_followup_does_not_invent_old_literal_fragment"]
        p = case["payload"]; state = run_for()["conversation_state"]
        state.update(revision=p["current_revision"], pending_clarification=deepcopy(p["prior_pending"]))
        plan = {"action": "answer_from_context", "requests": [p["request"]]}
        decision = {"action": "finish", "results": [{"request_id": "r1", "support": "clarification_needed",
                    "text": p["clarifying_text"], "citation_ids": []}], "memory_update": {
                        "active_topic": None, "continuity_summary": "", "pending_update": p["pending_update"]}}
        self.assertEqual(derive_pending(state, p["question"], plan, decision, 2), case["expected"]["pending_clarification"])
        self.assert_invalid(derive_pending, state, p["question"], plan, decision, 3)

    def test_direct_route_requires_a_clear_non_artwork_request(self):
        plan = example("direct_appreciation")
        plan["requests"][0]["resolution"] = "needs_clarification"
        plan["results"][0]["support"] = "clarification_needed"
        plan["memory_update"]["pending_update"] = {
            "action": "set", "source": "current_message", "request_id": "r1"}
        run = run_for(plan["requests"][0]["original_fragment"])
        self.assert_invalid(validate_plan, run, plan, self.validator)

    def test_whole_clarification_cannot_discard_local_or_other_independent_parts(self):
        plan = example("retrieval_preserves_one_local_clarification")
        run = run_for(" ".join(r["original_fragment"] for r in plan["requests"]))
        catalog = run["context"]["catalog_facts"]
        decision = {"action": "clarify", "text": "Which detail do you mean?", "memory_update": {
            "active_topic": None, "continuity_summary": "",
            "pending_update": {"action": "set", "source": "current_message", "request_id": None}}}
        self.assert_invalid(assemble_answer, run, plan, decision, [], catalog, self.validator)
        plan = example("mixed_social_and_fact_plan")
        run["question"] = " ".join(r["original_fragment"] for r in plan["requests"])
        self.assert_invalid(assemble_answer, run, plan, decision, [], catalog, self.validator)

    def test_global_translation_length_clarification_remains_available(self):
        plan = example("mixed_social_and_fact_plan")
        run = run_for("Translate the previous answer into Korean. " +
                      " ".join(r["original_fragment"] for r in plan["requests"]))
        plan["language_decision"] = {
            "detected_language": "en", "explicit_instruction": None, "answer_language": "ko",
            "translation": {"target_language": "ko", "original_fragment": "Translate the previous answer into Korean.",
                            "source": {"kind": "history_answer", "sequence": 1}}}
        decision = {"action": "clarify", "text": "번역할 부분을 더 짧게 선택해 주시겠어요?", "memory_update": {
            "active_topic": "A translation that exceeds the response budget", "continuity_summary": "",
            "pending_update": {"action": "set", "source": "current_message", "request_id": None}}}
        answer, parts, memory = assemble_answer(run, plan, decision, [], run["context"]["catalog_facts"], self.validator)
        self.assertEqual(answer["status"], "clarification_needed")
        self.assertEqual(parts, [])
        self.assertIsNone(memory["pending_update"]["request_id"])

    def test_invalid_model_json_and_errors_do_not_echo_untrusted_payloads(self):
        secret = "VISITOR-PRIVATE-MARKER"
        for raw in ('{"action":"x","action":"' + secret + '"}', '{"text":NaN}', 'not json ' + secret):
            error = self.assert_invalid(model_decision, raw, "Plan", self.validator, phase="planning")
            self.assertNotIn(secret, str(error))
            self.assertNotIn(secret, json.dumps(error.as_dict()))


if __name__ == "__main__":
    unittest.main()
