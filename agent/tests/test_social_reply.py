"""Social replies classify the speech act while protecting both visitor-facing outputs."""
import unittest
from copy import deepcopy

from agent.runtime.context_v2 import build_plan_input
from agent.runtime.followup_plan import expand_plan, planning_schema
from agent.runtime.social_reply import social_plan, social_schema
from agent.runtime.grounding import render_catalog_facts
from agent.runtime.validation import ContractViolation, StrictValidator
from agent.runtime.validation_v2 import ContractValidatorV2, assemble_answer, validate_plan
from agent.testing.v2 import language_decision, make_run, memory_update
from agent.tools.evidence import EvidenceManifest


class SocialReplyTests(unittest.TestCase):
    def setUp(self):
        manifest = EvidenceManifest.load()
        self.run = make_run(question="I find this unsettling.", manifest=manifest)
        self.payload = build_plan_input(self.run, manifest)
        self.validator = ContractValidatorV2()
        self.wire = planning_schema(self.validator.schema("Plan"), self.payload)
        self.raw = {
            "action": "direct_response", "language_decision": language_decision(),
            "requests": [{"id": "r1", "original_fragment": self.run["question"],
                          "intent": "The visitor shares an uneasy reaction.",
                          "kind": "conversation", "resolution": "clear"}],
            "conversation_acts": [{"request_id": "r1", "act": "share_impression"}],
            "results": [{"request_id": "r1", "support": "not_applicable", "citation_ids": [],
                         "text": "The artist painted a storm to unsettle people."}],
            "memory_update": memory_update(topic="The artist painted a storm.", summary="It depicts a storm.")}

    def test_reaction_discards_unsourced_claim_from_both_body_and_memory_in_each_language(self):
        for code, expected in (("en", "catches your eye"), ("es", "llama la atención"),
                               ("zh-CN", "吸引你"), ("ko", "눈길")):
            with self.subTest(code=code):
                self.raw["language_decision"]["answer_language"] = code
                choice = {"visitor_task": "chat_translate_read_catalog_or_handle_mixed_requests", "plan": self.raw}
                StrictValidator(self.wire).validate(choice)
                plan = expand_plan(choice, self.payload)
                self.validator.validate("Plan", plan)
                self.assertIn(expected, plan["results"][0]["text"])
                self.assertIn("Visitor impression: “I find this unsettling.”", plan["memory_update"]["continuity_summary"])
                self.assertNotIn("storm", str(plan["memory_update"]))

    def test_incidental_act_cannot_replace_a_scoped_catalog_answer(self):
        question = "刚才提的是哪位画家？这次用英语回答。"
        run = make_run(question=question)
        payload = build_plan_input(run, EvidenceManifest.load())
        artist = next(record for record in payload["catalog_facts"] if record["field"] == "artist")
        raw = {**deepcopy(self.raw), "language_decision": {
            **language_decision("en", detected_language="zh-CN"), "explicit_instruction": {
                "preference": "en", "purpose": "turn_response", "persistence": "turn", "original_fragment": "这次用英语回答。"}},
            "requests": [{"id": "r1", "kind": "catalog_fact", "resolution": "clear",
                          "original_fragment": "刚才提的是哪位画家？", "intent": "Who is the listed artist?"}],
            "results": [{"request_id": "r1", "support": "supported", "citation_ids": [artist["fact_id"]],
                         "text": artist["value"]}]}
        choice = {"visitor_task": "chat_translate_read_catalog_or_handle_mixed_requests", "plan": raw}
        plan = expand_plan(choice, payload)
        self.assertNotIn("conversation_acts", plan)
        validate_plan(run, plan, self.validator, plan_input=payload)
        answer, parts, _ = assemble_answer(run, plan, plan, [], payload["catalog_facts"], self.validator)
        render_catalog_facts(plan, answer, parts, payload["catalog_facts"])
        self.assertIn("The catalog lists " + artist["value"], answer["text"])
        self.assertEqual(answer["citations"], [artist])
        for change in ({"conversation_acts": raw["conversation_acts"] * 2},
                       {"conversation_acts": [{"request_id": "r2", "act": "give_thanks"}]},
                       {"conversation_acts": [{"request_id": "r1", "act": "unrecognized"}]},
                       {"results": [{**raw["results"][0], "support": "not_applicable", "citation_ids": []}]},
                       {"results": [{**raw["results"][0], "citation_ids": ["a-different-artwork"]}]},
                       {"requests": [{**raw["requests"][0], "resolution": "needs_clarification"}]}):
            with self.subTest(change=change), self.assertRaises(ContractViolation):
                expand_plan({**choice, "plan": {**raw, **change}}, payload)

    def test_compact_task_reuses_safe_reply_and_binds_the_actual_visitor_words(self):
        defs = self.validator.schema("Plan")["$defs"]
        wire = {**social_schema(defs), "$defs": defs}
        for act in ("share_impression", "give_thanks", "greet"):
            with self.subTest(act=act):
                data = {"visitor_task": "respond_to_personal_impression_greeting_or_thanks",
                        "act": act, "language_decision": language_decision("ko")}
                StrictValidator(wire).validate(data)
                plan = social_plan(data, self.payload)
                self.validator.validate("Plan", plan)
                self.assertEqual(plan["results"][0]["support"], "not_applicable")
                self.assertEqual(plan["requests"][0]["original_fragment"], self.run["question"])
                self.assertNotIn("storm", str(plan))
                if act == "share_impression":
                    self.assertIn("눈길", plan["results"][0]["text"])
                    self.assertIn("Visitor impression", plan["memory_update"]["continuity_summary"])
                data["language_decision"]["translation"] = {"target_language": "ko"}
                with self.assertRaises(ContractViolation):
                    social_plan(data, self.payload)

    def test_thanks_preserves_a_pending_question_and_prior_topic(self):
        self.payload["conversation_context"].update({"continuity_summary": "Earlier question.", "active_topic": "Light"})
        self.raw["conversation_acts"][0]["act"] = "give_thanks"
        self.raw["memory_update"]["pending_update"] = {"action": "clear"}
        plan = expand_plan(self.raw, self.payload)
        self.assertEqual(plan["memory_update"], memory_update(topic="Light", summary="Earlier question."))

    def test_translation_and_substantive_requests_keep_their_own_reply(self):
        for kind, translation, acts in [
            ("general_art_knowledge", None, []),
            ("conversation", {"target_language": "es"}, []),
            ("conversation", None, [{"request_id": "r1", "act": "request_discussion_or_instruction"}]),
        ]:
            with self.subTest(kind=kind, translation=translation, acts=acts):
                raw = deepcopy(self.raw)
                raw["language_decision"]["translation"] = translation
                raw["requests"][0]["kind"] = kind
                raw["conversation_acts"] = acts
                out = expand_plan(raw, self.payload)
                self.assertEqual(out["results"], raw["results"])
                self.assertEqual(out["memory_update"], raw["memory_update"])

    def test_social_tag_is_required_bound_to_its_request_and_preserved_in_followups(self):
        with self.assertRaises(ContractViolation):
            expand_plan({**self.raw, "conversation_acts": []}, self.payload)
        with self.assertRaises(ContractViolation):
            expand_plan({**self.raw, "conversation_acts": [{"request_id": "r2", "act": "greet"}]}, self.payload)
        payload = {**self.payload, "recent_turns": [{"answer_text": "Prior turn."}]}
        followup = {"visitor_task": "chat_translate_read_catalog_or_handle_mixed_requests", "plan": self.raw}
        StrictValidator(planning_schema(self.validator.schema("Plan"), payload)).validate(followup)
        self.assertIn("catches your eye", expand_plan(followup, payload)["results"][0]["text"])
