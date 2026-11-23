"""Compatibility of archived source-selection decisions and their rendering."""
import unittest
from copy import deepcopy

from agent.prompts.v2.readings import source_statements
from agent.runtime.context_v2 import build_answer_input
from agent.runtime.grounding import grounded_memory, render_catalog_facts, render_evidence_gaps
from agent.runtime.errors import WorkflowError
from agent.runtime.source_check import assessment_schema, expand_assessment
from agent.runtime.validation import ContractViolation, StrictValidator
from agent.runtime.validation_v2 import ContractValidatorV2, assemble_answer
from agent.testing.replay_assessments import archived_operations, exact_support
from agent.testing.v2 import language_decision, make_run, memory_update
from agent.tools.evidence import EvidenceManifest


class SourceCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = ContractValidatorV2()
        cls.manifest = EvidenceManifest.load()

    def setUp(self):
        self.run = make_run(artwork_id="aic-11723", manifest=self.manifest,
                            question="What do the marks suggest? Are they at the bottom?")
        self.requests = [
            {"id": "r1", "original_fragment": "What do the marks suggest?",
             "intent": "What objects do the marks in the background suggest?",
             "kind": "artwork_fact", "resolution": "clear"},
            {"id": "r2", "original_fragment": "Are they at the bottom?",
             "intent": "Does the description locate the marks at the bottom?",
             "kind": "artwork_fact", "resolution": "clear"},
        ]
        self.evidence = [dict(self.manifest.records["aic-11723-description-01"])]
        self.base = self.validator.schema("FinalDecision")
        self.payload = build_answer_input(self.run, self.requests, [], self.evidence,
                                          answer_language="en", complete_scope=False, allow_refinement=False)
        self.wire = assessment_schema(self.base, self.payload)
        self.checks = [
            {"request_id": "r1", "requested_detail": "suggested objects",
             "evidence_relation": "direct_answer", "statement_ids": ["s2"]},
            {"request_id": "r2", "requested_detail": "vertical location",
             "evidence_relation": "related_only", "statement_ids": ["s1"]},
        ]

    def finish(self):
        return {"action": "finish", "source_checks": deepcopy(self.checks),
                "memory_update": memory_update(summary="The pattern was at the bottom.")}

    def test_related_fact_never_becomes_affirmative_body_or_memory(self):
        decision = expand_assessment(self.finish(), self.payload, self.wire)
        self.validator.validate("FinalDecision", decision)
        plan = {"action": "answer_from_context", "language_decision": language_decision("en"),
                "requests": self.requests}
        answer, results, proposal = assemble_answer(self.run, plan, decision, self.evidence,
                                                    self.run["context"]["catalog_facts"], self.validator)
        render_evidence_gaps(plan, answer, results)
        state = grounded_memory(self.run, plan, results, proposal, self.evidence)
        self.assertEqual(answer["status"], "partially_answered")
        self.assertIn("suggest a floral-patterned bedspread and wallpaper", results[0]["text"])
        self.assertEqual(results[0]["citation_ids"], ["aic-11723-description-01"])
        self.assertEqual((results[1]["support"], results[1]["citation_ids"]), ("insufficient", []))
        self.assertIn("can’t verify", results[1]["text"])
        self.assertIn("Source wording: The brushstrokes suggest", state["continuity_summary"])
        self.assertIn("Open here", state["continuity_summary"])
        self.assertNotIn("was at the bottom", state["continuity_summary"])

    def test_direct_motif_selection_does_not_establish_a_spatial_qualifier(self):
        payload = deepcopy(self.payload)
        payload["requests"][1]["intent"] = "Does the description mention wallpaper on the left side of the painting?"
        raw = self.finish()
        raw["source_checks"][1].update(evidence_relation="direct_answer", statement_ids=["s2"])
        decision = expand_assessment(raw, payload, self.wire)
        plan = {"action": "answer_from_context", "language_decision": language_decision("en"),
                "requests": payload["requests"]}
        answer, parts, proposed = assemble_answer(self.run, plan, decision, self.evidence,
                                                  self.run["context"]["catalog_facts"], self.validator)
        render_evidence_gaps(plan, answer, parts)
        memory = grounded_memory(self.run, plan, parts, proposed, self.evidence)
        self.assertEqual((parts[1]["support"], parts[1]["citation_ids"]), ("insufficient", []))
        self.assertEqual(answer["status"], "partially_answered")
        self.assertIn("can’t verify", parts[1]["text"])
        self.assertIn("Open here", memory["continuity_summary"])
        # The reviewed statement actually does locate the brushwork in the background.
        payload["requests"][1]["intent"] = "Are those marks in the background?"
        raw["source_checks"][1]["statement_ids"] = ["s1"]
        self.assertEqual(expand_assessment(raw, payload, self.wire)["results"][1]["support"], "supported")
        raw["source_checks"][1]["evidence_relation"] = "related_only"
        self.assertEqual(expand_assessment(raw, payload, self.wire)["results"][1]["support"], "insufficient")

    def test_selected_wording_is_scoped_to_the_current_source_and_reply_language(self):
        for language, expected in (("en", "suggest"), ("es", "sugieren"),
                                   ("zh-CN", "联想"), ("ko", "연상")):
            with self.subTest(language=language):
                payload = {**self.payload, "answer_language": language}
                wire = assessment_schema(self.base, payload)
                statements = source_statements(payload["evidence"], language, payload["catalog_facts"])
                self.assertEqual({row["citation_id"] for row in statements.values()},
                                 {"aic-11723-description-01", "aic-11723-catalog-title",
                                  "aic-11723-catalog-artist", "aic-11723-catalog-date_display"})
                decision = expand_assessment(self.finish(), payload, wire)
                self.assertIn(expected, decision["results"][0]["text"])

    def test_a_grouped_introduction_can_combine_catalog_and_artwork_provenance(self):
        raw = self.finish()
        raw["source_checks"][0]["statement_ids"] = ["c1", "c2", "s2"]
        decision = expand_assessment(raw, self.payload, self.wire)
        self.assertEqual(decision["results"][0]["citation_ids"],
                         ["aic-11723-catalog-title", "aic-11723-catalog-artist", "aic-11723-description-01"])
        self.assertIn("Woman at Her Toilette", decision["results"][0]["text"])

    def test_reject_free_prose_unknown_or_reordered_selections(self):
        for change in ({"statement_ids": ["s17"]}, {"statement_ids": []},
                       {"request_id": "r2"}, {"text": "The motif is at the bottom."}):
            with self.subTest(change=change), self.assertRaises(ContractViolation):
                decision = self.finish()
                decision["source_checks"][0].update(change)
                expand_assessment(decision, self.payload, self.wire)

    def test_catalog_parts_in_mixed_answers_cannot_append_unsourced_biography(self):
        catalog = self.run["context"]["catalog_facts"]
        artist = next(row for row in catalog if row["field"] == "artist")
        plan = {"requests": [{"kind": "conversation"}, {"kind": "catalog_fact"}],
                "language_decision": language_decision("es")}
        parts = [{"text": "¡De nada!", "support": "not_applicable", "citation_ids": []},
                 {"text": "The artist visited Spain.", "support": "supported", "citation_ids": [artist["fact_id"]]}]
        answer = {"text": ""}
        render_catalog_facts(plan, answer, parts, catalog)
        self.assertEqual(answer["text"], "¡De nada!\n\nLa ficha atribuye la obra a " + artist["value"] + ".")
        self.assertNotIn("Spain", parts[1]["text"])
        plan["language_decision"]["translation"] = {"target_language": "es"}
        parts[1]["text"] = "A supplied quotation."
        render_catalog_facts(plan, answer, parts, catalog)
        self.assertEqual(parts[1]["text"], "A supplied quotation.")

    def test_catalog_fallback_preserves_complete_values_and_other_parts_within_budget(self):
        catalog = self.run["context"]["catalog_facts"]
        date = next(row for row in catalog if row["field"] == "date_display")
        plan = {"requests": [{"kind": "catalog_fact"}, {"kind": "general_art_knowledge"}],
                "language_decision": language_decision("en")}
        other = "x" * (1998 - len(date["value"]))
        parts = [{"text": "date", "support": "supported", "citation_ids": [date["fact_id"]]},
                 {"text": other, "support": "not_applicable", "citation_ids": []}]
        answer = {}
        render_catalog_facts(plan, answer, parts, catalog)
        self.assertEqual(answer["text"], date["value"] + "\n\n" + other)
        parts[1]["text"] += "x"
        with self.assertRaises(WorkflowError):
            render_catalog_facts(plan, answer, parts, catalog)

    def test_replay_restores_exact_fitted_input_and_checks_origin(self):
        call = {"operation": "final", "schema": "FinalDecision", "request_id": "replay",
                "model_input": {**self.payload, "available_statements": {"s1": "display only"}}}
        log = {"cases": [{"turns": [{"request_id": "replay", "input": self.run}]}], "model_attempts": [call]}
        [(turn, operation, name, fitted)] = list(archived_operations(log, self.validator))
        self.assertEqual((turn["request_id"], operation, name, fitted),
                         ("replay", "final", "FinalDecision", self.payload))
        self.assertIn("available_statements", call["model_input"])
        call["model_input"]["original_question"] = "A different turn"
        with self.assertRaises(ValueError):
            list(archived_operations(log, self.validator))

    def test_replay_scores_whole_ordered_requests(self):
        decision = expand_assessment(self.finish(), self.payload, self.wire)
        gold = [{"support": "supported", "citations": ["aic-11723-description-01"]},
                {"support": "insufficient", "citations": []}]
        self.assertTrue(exact_support(decision["results"], gold))
        self.assertFalse(exact_support(list(reversed(decision["results"])), gold))
        self.assertFalse(exact_support(decision["results"][:1], gold))

    def test_bound_model_schema_preserves_a_permitted_refinement(self):
        payload = {**self.payload, "allow_refinement": True}
        wire = assessment_schema(self.validator.schema("AssessmentDecision"), payload)
        refine = {"action": "refine", "reason_code": "uncovered_request",
                  "request_ids": ["r2"], "queries": [
                      {"id": "q1", "request_ids": ["r2"], "query": "Where is the wall decoration located?"}]}
        StrictValidator(wire).validate(refine)
        self.assertEqual(expand_assessment(refine, payload, wire), refine)

    def test_unknown_edited_empty_excerpts_and_translations_preserve_base_protocol(self):
        for payload in ({**self.payload, "evidence": []},
                        {**self.payload, "evidence": [{**self.evidence[0], "excerpt": "New edition."}]},
                        {**self.payload, "translation": {"text": "Visitor quotation"}}):
            self.assertIs(assessment_schema(self.base, payload), self.base)
        self.assertNotIn("source_checks", self.base["$defs"]["FinishDecision"]["properties"])

    def test_bounded_memory_drops_oldest_complete_notes_without_skipping_a_correction(self):
        plan = {"requests": self.requests, "language_decision": language_decision("en")}
        decision = expand_assessment(self.finish(), self.payload, self.wire)
        state = self.run["conversation_state"]
        state["revision"] = 3
        older = "Turn 1, visitor question 1 (Visitor context): I prefer a very detailed explanation."
        correction = "Turn 2, visitor question 1 (Visitor context): Please keep the explanation brief."
        recent = "Turn 3, visitor question 1 (Open here): Which institution last owned it?"
        state["continuity_summary"] = "\n".join([older, correction, recent])
        def memory():
            return grounded_memory(self.run, plan, decision["results"], decision["memory_update"],
                                   self.evidence)["continuity_summary"]

        # Make exactly the oldest note exceed the budget, including a multiline legacy preamble.
        length = len(memory())
        older += " " + "x" * (1000 - length)
        state["continuity_summary"] = "\n".join(["Legacy prose\nstays together", older, correction, recent])
        summary = memory()
        self.assertTrue(summary.startswith(correction + "\n" + recent + "\nTurn 4,"))
        self.assertNotIn("very detailed", summary)
        self.assertNotIn("stays together", summary)
        self.assertLessEqual(len(summary), 1000)

    def test_visitor_statement_does_not_become_an_invented_goal_in_memory(self):
        self.run["question"] = "我原以为它是壁画。这件作品用的是什么材料？"
        requests = [
            {**self.requests[0], "original_fragment": "我原以为它是壁画。", "intent": "Does the museum call it a mural?"},
            {**self.requests[1], "original_fragment": "这件作品用的是什么材料？", "intent": "What is the medium?"},
        ]
        plan = {"requests": requests, "language_decision": language_decision("zh-CN")}
        medium = dict(self.manifest.records["aic-11723-medium"])
        parts = [{"support": "insufficient", "text": "无法确认。", "citation_ids": []},
                 {"support": "supported", "text": "布面油画。", "citation_ids": [medium["chunk_id"]]}]
        result = grounded_memory(self.run, plan, parts, memory_update(summary="The visitor asked about a mural."),
                                 [medium])
        note = result["continuity_summary"]
        self.assertEqual(note.count(self.run["question"]), 1)
        self.assertIn("Guide check 1", note)
        self.assertIn("Open here", note)
        self.assertIn("Source wording: Oil on canvas", note)
        self.assertIn(medium["chunk_id"], note)
        self.assertNotIn("mural", str(result))
        self.assertIn(self.run["question"], result["active_topic"])

    def test_eight_factual_turns_retain_latest_complete_notes_in_order(self):
        request = self.requests[0]
        plan = {"requests": [request], "language_decision": language_decision("en")}
        decision = expand_assessment(self.finish(), self.payload, self.wire)
        state = self.run["conversation_state"]
        notes = []
        for turn in range(1, 9):
            state["revision"] = turn - 1
            self.run["question"] = f"On visit {turn},\nVisitor impression: what do those marks suggest?"
            memory = grounded_memory(self.run, plan, decision["results"][:1], decision["memory_update"],
                                     self.evidence)["continuity_summary"]
            last = memory[memory.index(f"Turn {turn}, visitor message: "):]
            self.assertTrue(last.startswith(f"Turn {turn}, visitor message: "))
            self.assertIn("Source wording:", last)
            notes.append(last)
            kept = [note for note in notes if note in memory]
            self.assertEqual(memory, "\n".join(kept))
            self.assertEqual(kept, notes[-len(kept):])
            self.assertLessEqual(len(memory), 1000)
            state["continuity_summary"] = memory
        self.assertGreater(len(kept), 1)
        self.assertLess(len(kept), 8)

    def test_mixed_turn_binds_only_the_factual_results(self):
        social = {"id": "r1", "original_fragment": "Thanks", "intent": "Visitor thanks the guide.",
                  "kind": "conversation", "resolution": "clear"}
        fact = {**self.requests[0], "id": "r2"}
        payload = {**self.payload, "requests": [social, fact]}
        wire = assessment_schema(self.base, payload)
        other = {"request_id": "r1", "support": "not_applicable", "citation_ids": [], "text": "You're welcome!"}
        raw = {**self.finish(), "source_checks": [{**self.checks[0], "request_id": "r2"}], "other_results": [other],
               "conversation_acts": [{"request_id": "r1", "act": "give_thanks"}]}
        StrictValidator(wire).validate(raw)
        expanded = expand_assessment(raw, payload, wire)
        self.assertEqual(expanded["results"][0], {**other, "text": "You’re welcome!"})
        self.assertEqual(expanded["results"][1]["citation_ids"], ["aic-11723-description-01"])
        plan = {"requests": [social, fact], "language_decision": language_decision("en")}
        state = grounded_memory(self.run, plan, expanded["results"], expanded["memory_update"], self.evidence)
        self.assertIn("Visitor context", state["continuity_summary"])
        self.assertIn("Source wording: The brushstrokes suggest", state["continuity_summary"])
        self.assertNotIn("was at the bottom", state["continuity_summary"])
        raw["other_results"] = [{**other, "request_id": "r2"}]
        with self.assertRaises(ContractViolation):
            expand_assessment(raw, payload, wire)
