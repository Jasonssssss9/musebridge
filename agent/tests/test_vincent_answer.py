"""Contract mapping for a compact, source-bound Vincent model response."""
import json
from unittest import TestCase

from jsonschema import Draft202012Validator

from agent.contracts.codec import v3_validator
from agent.runtime.errors import WorkflowError
from agent.runtime.vincent_answer import canonical_decision, model_call


def request(key, text, kind="artwork_fact", resolution="clear"):
    return {"id": key, "original_fragment": text, "intent": text, "kind": kind, "resolution": resolution}


def payload():
    return {"scope": {"artwork_id": "getty-103jnh", "dataset_version": "getty-gallery-v2",
                      "conversation_mode": "vincent", "reference_pack_version": "vincent-irises-v1"},
            "original_question": "Which subject does your letter name?",
            "artwork_context": {"artwork_title": "Irises", "artist": "Vincent van Gogh"}, "recent_turns": [],
            "conversation_context": {"pending_clarification": None, "continuity_summary": ""},
            "catalog_facts": [{"fact_id": "title-id", "field": "title", "value": "Irises"}],
            "requests": [request("r1", "Which subject does your letter name?")],
            "evidence": [{"chunk_id": "letter-1", "source_kind": "artist_letter", "source_title": "Letter to two people",
                          "source_locator": "This passage is addressed to the second recipient.",
                          "attribution": "The letter writer", "excerpt": "I drew a pear."}],
            "allow_refinement": True, "executed_queries": [{"id": "q1", "query": "Which subject?"}]}


def answer(*parts):
    slots = {}
    for i, (refs, text) in enumerate(parts, 1):
        if refs:
            slots[f"r{i}"] = {"grounds": [{"source": ref, "spans": ["v1" if ref == "title-id" else "p1"],
                                         "finding": text} for ref in refs], "reply": text}
        else:
            slots[f"r{i}"] = {"unresolved": "The supplied passage leaves the detail open.", "reply": text}
    return {"action": "answer", "parts": slots, "topic": "drawing", "remember": "The writer described drawing a pear."}


class VincentAnswerTests(TestCase):
    def test_projection_preserves_source_voice_and_locator_without_duplicate_summary(self):
        data = payload()
        data["recent_turns"] = [{"sequence": 1, "question": "Earlier visitor text.", "answer_text": "Earlier reply."}]
        data["conversation_context"]["continuity_summary"] = "Old topic, already present in the history."
        schema, messages = model_call(data, fresh_ids={"letter-1"})
        body = json.loads(messages[1]["content"])
        self.assertEqual(list(body)[-1], "visitor_question")
        self.assertNotIn("earlier_conversation", body)
        self.assertEqual(body["sources"], [{"ref": "letter-1", "kind": "artist_letter", "source": "Letter to two people",
                                           "voice": "Distinguish the letter writer from anyone the writer quotes.",
                                           "locator": {"l1": data["evidence"][0]["source_locator"]},
                                           "credit": "The letter writer", "passage": {"p1": "I drew a pear."},
                                           "availability": "current search"}])
        self.assertEqual(body["recent_turns"][0]["visitor"], "Earlier visitor text.")
        self.assertNotIn("Earlier visitor text.", messages[0]["content"])
        self.assertTrue(Draft202012Validator(schema).is_valid(answer((["letter-1"], "I drew a pear."))))
        data["recent_turns"][0]["sequence"] = 3
        restored = json.loads(model_call(data, fresh_ids=set())[1][1]["content"])
        self.assertEqual(restored["earlier_conversation"], data["conversation_context"]["continuity_summary"])
        self.assertEqual(restored["sources"][0]["availability"], "verified earlier citation")

    def test_part_status_and_citations_follow_model_choices_with_verbatim_prose_and_memory(self):
        data = payload()
        data["requests"] += [request("r2", "Can we tell its weight?"), request("r3", "What is its title?", "catalog_fact")]
        raw = answer((["letter-1"], "I wrote about drawing a pear."),
                     ([], "I can’t tell its weight from this letter."), (["title-id"], "The title is Irises."))
        raw["remember"] = "The artist drew a pear. Its weight remains open."
        result = canonical_decision(raw, data)
        v3_validator().validate("AssessmentDecision", result)
        self.assertEqual([p["support"] for p in result["results"]], ["supported", "insufficient", "supported"])
        self.assertEqual([p["citation_ids"] for p in result["results"]], [["letter-1"], [], ["title-id"]])
        self.assertEqual([p["text"] for p in result["results"]], [p["reply"] for p in raw["parts"].values()])
        self.assertEqual(result["memory_update"]["continuity_summary"], raw["remember"])
        # A supported negative response carries its source in the same slot.
        negative = canonical_decision(answer((["letter-1"], "No. I wrote about a pear.")), payload())
        self.assertEqual((negative["results"][0]["support"], negative["results"][0]["citation_ids"]),
                         ("supported", ["letter-1"]))

    def test_finding_is_bound_to_a_source_and_stays_out_of_spoken_prose(self):
        data = payload()
        raw = answer((["letter-1"], "I wrote about drawing a pear."))
        raw["parts"]["r1"]["grounds"][0]["finding"] = "The writer mentions a pear in a letter."
        result = canonical_decision(raw, data)
        self.assertEqual(result["results"][0]["text"], "I wrote about drawing a pear.")
        self.assertEqual(result["results"][0]["citation_ids"], ["letter-1"])
        del raw["parts"]["r1"]["grounds"][0]["finding"]
        with self.assertRaises(WorkflowError):
            canonical_decision(raw, data)

    def test_ground_selects_literal_passage_or_locator_spans_only_from_its_own_record(self):
        data = payload()
        data["evidence"].append({**data["evidence"][0], "chunk_id": "another-letter",
                                 "excerpt": "She drew a jug. She gave it to her neighbor."})
        _, messages = model_call(data)
        cards = json.loads(messages[1]["content"])["sources"]
        for record, card in zip(data["evidence"], cards):
            for field, original in (("passage", "excerpt"), ("locator", "source_locator")):
                for span in card[field].values():
                    self.assertIn(span, record[original])
        self.assertEqual(cards[1]["passage"]["p2"], "She gave it to her neighbor.")
        raw = answer((["letter-1"], "The letter was addressed to the second recipient."))
        finding = raw["parts"]["r1"]["grounds"][0]
        finding["spans"] = ["l1"]
        self.assertEqual(canonical_decision(raw, data)["results"][0]["citation_ids"], ["letter-1"])
        for spans in (["p2"], ["v1"], [], ["l1", "l1"]):
            with self.subTest(spans=spans), self.assertRaises(WorkflowError):
                finding["spans"] = spans
                canonical_decision(raw, data)
        del finding["spans"]
        with self.assertRaises(WorkflowError):
            canonical_decision(raw, data)

    def test_unresolved_detail_has_no_ground_or_citation_and_keeps_model_memory(self):
        data = payload()
        data["evidence"] = []
        raw = answer(([], "I can’t tell when the drawing was finished."))
        raw["parts"]["r1"]["unresolved"] = "The completion date remains open."
        raw["remember"] = "The visitor asked when the drawing was finished. The date remains open."
        result = canonical_decision(raw, data)
        self.assertEqual(result["results"][0]["support"], "insufficient")
        self.assertEqual(result["results"][0]["citation_ids"], [])
        self.assertEqual(result["memory_update"]["continuity_summary"], raw["remember"])
        raw["parts"]["r1"]["grounds"] = [{"source": "letter-1", "spans": ["p1"], "finding": "A completion date."}]
        with self.assertRaises(WorkflowError):
            canonical_decision(raw, data)

    def test_source_identity_is_stable_across_turns_and_memory_passes_through(self):
        data = payload()
        data["evidence"].insert(0, {**data["evidence"][0], "chunk_id": "another-letter"})
        schema, messages = model_call(data)
        self.assertEqual([c["ref"] for c in json.loads(messages[1]["content"])["sources"]],
                         ["another-letter", "letter-1"])
        raw = answer((["letter-1"], "I wrote about drawing a pear."))
        raw["remember"] = "The writer described a pear in letter-1."
        self.assertTrue(Draft202012Validator(schema).is_valid(raw))
        result = canonical_decision(raw, data)
        self.assertEqual(result["results"][0]["citation_ids"], ["letter-1"])
        self.assertEqual(result["memory_update"]["continuity_summary"], raw["remember"])

    def test_no_foreign_or_irrelevant_source_can_be_published(self):
        data = payload()
        data["requests"].append(request("r2", "What's the title?", "catalog_fact"))
        for first, second in ((["unavailable-letter"], ["title-id"]), (["title-id"], ["title-id"]), (["letter-1"], ["letter-1"]), (["letter-1", "letter-1"], ["title-id"])):
            with self.subTest(first=first, second=second), self.assertRaises(WorkflowError):
                canonical_decision(answer((first, "First."), (second, "Second.")), data)

    def test_refinement_remains_explicit_bounded_and_request_linked(self):
        data = payload()
        raw = {"action": "search_again", "reason": "uncovered_request",
               "searches": [{"for": ["r1"], "perspective": "artist_letter", "query": "What did the artist sketch in the earlier letter?"}]}
        result = canonical_decision(raw, data)
        v3_validator().validate("RefineDecision", result)
        self.assertEqual(result["queries"], [{"id": "q2", "request_ids": ["r1"], "query": raw["searches"][0]["query"] + " Consult the artist's letters."}])
        data["allow_refinement"] = False
        with self.assertRaises(WorkflowError):
            canonical_decision(raw, data)

    def test_model_authors_whole_and_local_clarification(self):
        data = payload()
        ask = {"action": "ask", "question": "Which subject caught your eye?",
               "topic": "subject", "remember": "The visitor has not yet specified the subject."}
        with self.assertRaises(WorkflowError):
            canonical_decision(ask, data)
        data["translation"] = {"target_language": "en", "source": "prior reply"}
        result = canonical_decision(ask, data)
        v3_validator().validate("FinalDecision", result)
        del data["translation"]
        self.assertEqual(result["memory_update"]["pending_update"],
                         {"action": "set", "source": "current_message", "request_id": None})
        data["requests"].append(request("r2", "And that one?", resolution="needs_clarification"))
        raw = answer((["letter-1"], "A pear."))
        raw["parts"]["r2"] = {"reply": "Which one?"}
        result = canonical_decision(raw, data)
        self.assertEqual(result["results"][1]["support"], "clarification_needed")
        self.assertEqual(result["memory_update"]["pending_update"]["request_id"], "r2")
        with self.assertRaises(WorkflowError):
            canonical_decision(ask, data)
        data["conversation_context"]["pending_clarification"] = {"question": "Which one?"}
        raw["pending"] = "pending_clarification"
        self.assertEqual(canonical_decision(raw, data)["memory_update"]["pending_update"]["source"], "pending_clarification")
