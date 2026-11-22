"""Ordinary language settings survive neutral gestures and narrowly bound imperatives."""
import unittest

from agent.runtime.context_v2 import build_plan_input
from agent.runtime.followup_plan import expand_plan, source_sentences
from agent.runtime.language_binding import bind_setting_scope
from agent.runtime.validation_v2 import resolve_language
from agent.testing.v2 import language_decision, make_run, memory_update
from agent.tools.evidence import EvidenceManifest


def setting(preference, fragment):
    return {"preference": preference, "purpose": "turn_response", "persistence": "turn", "original_fragment": fragment}


class LanguageBindingTests(unittest.TestCase):
    def test_neutral_gesture_preserves_auto_and_uses_the_effective_language(self):
        for override, saved, automatic, wanted, text in (
            (None, "auto", "es", "es", "¡De nada!"), (None, "en", "es", "en", "You’re welcome!"),
            ("ko", "auto", "es", "ko", "천만에요!"), ("auto", "ko", "es", "es", "¡De nada!"),
        ):
            with self.subTest(override=override, saved=saved, auto=automatic):
                run = make_run(question="谢谢，刚才的解释帮到我了。", language_override=override)
                run["conversation_state"].update(language_preference=saved, auto_language=automatic)
                payload = build_plan_input(run, EvidenceManifest.load())
                choice = {"visitor_task": "respond_to_personal_impression_greeting_or_thanks", "act": "give_thanks",
                          "language_decision": language_decision("ko", detected_language="zh-CN")}
                plan = expand_plan(choice, payload)
                decision = plan["language_decision"]
                self.assertEqual((decision["detected_language"], decision["answer_language"]), ("und", wanted))
                self.assertEqual(plan["results"][0]["text"], text)
                resolved = resolve_language(run, decision)
                self.assertEqual(resolved["next_auto_language"], automatic)
                # Next neutral turn should still inherit the previous substantive language.
                run["language_override"] = None
                run["conversation_state"].update(auto_language=resolved["next_auto_language"],
                                                 language_preference=resolved["next_language_preference"])
                choice["act"] = "greet"
                next_plan = expand_plan(choice, build_plan_input(run, EvidenceManifest.load()))
                self.assertEqual(next_plan["language_decision"]["answer_language"], wanted)

    def test_impression_and_explicit_turn_instruction_still_use_the_visitor_decision(self):
        run = make_run(question="A mí me parece hermosa.")
        payload = build_plan_input(run, EvidenceManifest.load())
        choice = {"visitor_task": "respond_to_personal_impression_greeting_or_thanks", "act": "share_impression",
                  "language_decision": language_decision("es")}
        self.assertEqual(expand_plan(choice, payload)["language_decision"], choice["language_decision"])
        run["question"] = "Gracias. Solo por esta vez, responde en inglés."
        payload = build_plan_input(run, EvidenceManifest.load())
        choice.update(act="give_thanks", language_decision={
            **language_decision("en", detected_language="es"),
            "explicit_instruction": setting("en", "Solo por esta vez, responde en inglés.")})
        plan = expand_plan(choice, payload)
        self.assertEqual(plan["language_decision"], choice["language_decision"])
        self.assertEqual(resolve_language(run, plan["language_decision"])["next_language_preference"], "auto")

    def test_mixed_thanks_and_substantive_request_keep_the_input_language(self):
        run = make_run(question="Gracias. ¿Quién es el artista?")
        payload = build_plan_input(run, EvidenceManifest.load())
        artist = next(row for row in payload["catalog_facts"] if row["field"] == "artist")
        plan = {"action": "direct_response", "language_decision": language_decision("es"),
                "requests": [{"id": "r1", "kind": "conversation", "resolution": "clear",
                              "intent": "The visitor thanks the guide.", "original_fragment": "Gracias."},
                             {"id": "r2", "kind": "catalog_fact", "resolution": "clear",
                              "intent": "Who is the artist?", "original_fragment": "¿Quién es el artista?"}],
                "results": [{"request_id": "r1", "support": "not_applicable", "text": "OK", "citation_ids": []},
                            {"request_id": "r2", "support": "supported", "text": artist["value"],
                             "citation_ids": [artist["fact_id"]]}],
                "memory_update": memory_update(), "conversation_acts": [{"request_id": "r1", "act": "give_thanks"}]}
        bound = expand_plan({"visitor_task": "chat_translate_read_catalog_or_handle_mixed_requests", "plan": plan},
                            payload)
        self.assertEqual(bound["language_decision"], plan["language_decision"])
        self.assertEqual(resolve_language(run, bound["language_decision"])["next_auto_language"], "es")
        self.assertEqual(bound["results"][0]["text"], "¡De nada!")

    def bind(self, message, part, target, *, history=(), pending=None, translation=None, body=None):
        run = make_run(question=message)
        payload = build_plan_input(run, EvidenceManifest.load())
        payload.update(recent_turns=list(history))
        payload["conversation_context"]["pending_clarification"] = pending
        plan = {"action": "retrieve", "language_decision": {**language_decision(target),
                "explicit_instruction": setting(target, part), "translation": translation},
                "requests": [{"kind": "artwork_fact", "resolution": "clear", "original_fragment": body or message}]}
        return bind_setting_scope(plan, payload, source_sentences(message))["language_decision"]["explicit_instruction"]

    def test_complete_unqualified_commands_in_each_input_language_follow_the_persistent_default(self):
        for message, code in (("Please reply in Korean.", "ko"), ("Responde en chino.", "zh-CN"),
                              ("请用西班牙语回答。", "es"), ("영어로 답변해 주세요.", "en")):
            with self.subTest(message=message):
                result = self.bind(message, message, code)
                self.assertEqual((result["purpose"], result["persistence"]), ("conversation_setting", "conversation"))
        body = "How does the label describe the painting surface and medium?"
        result = self.bind(body + " Answer in Spanish.", "Answer in Spanish.", "es", body=body)
        self.assertEqual(result, {**setting("es", "Answer in Spanish."), "purpose": "conversation_setting",
                                  "persistence": "conversation"})

    def test_quoted_temporary_translated_ambiguous_and_contextual_commands_are_untouched(self):
        for message, fragment, target, extra in (
            ("Answer in Spanish just for this turn.", "Answer in Spanish", "es", {}),
            ("Did the artist say “Answer in Spanish.”?", "Answer in Spanish.", "es", {}),
            ("What does it show this time? Answer in Spanish.", "Answer in Spanish.", "es",
             {"body": "What does it show this time?"}),
            ("What if the label means something else? Answer in Spanish.", "Answer in Spanish.", "es",
             {"body": "What if the label means something else?"}),
            ("Responde en español solo esta vez.", "Responde en español", "es", {}),
            ("这一次请用韩语回答。", "请用韩语回答。", "ko", {}),
            ("이번 답변만 영어로 답변해 주세요.", "영어로 답변해 주세요.", "en", {}),
            ("Answer in Spanish.", "Answer in Spanish.", "ko", {}),
            ("Answer in Spanish.", "Answer in Spanish.", "es", {"translation": {"target_language": "es"}}),
            ("Answer in Spanish.", "Answer in Spanish.", "es", {"pending": {"source": "earlier"}}),
            ("Answer in Spanish.", "Answer in Spanish.", "es", {"history": [{"question": "Only once?"}]}),
        ):
            with self.subTest(message=message, extra=extra):
                self.assertEqual(self.bind(message, fragment, target, **extra), setting(target, fragment))
