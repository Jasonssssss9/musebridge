"""Check the offline conversation and source boundaries of the Vincent rehearsal."""
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from jsonschema import Draft202012Validator

from agent.testing.vincent_rehearsal import OUTPUT_SCHEMA, load_sources, read_release, run_dialogues, validate_cases


LETTER = "vincent-772-garden-colors"
MEDIUM = "getty-103jnh-medium"


def reply(cites, text="I wrote of violet irises."):
    return {"answer": text, "cited_chunk_ids": cites,
            "memory_update": {"active_topic": "irises", "continuity_summary": text}}


def turn(question, ids):
    return {"question": question, "evidence_ids": ids}


class RecordedModel:
    profile = object()

    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.requests = []

    async def complete_structured(self, **request):
        self.requests.append(request)
        output = next(self.outputs)
        Draft202012Validator(OUTPUT_SCHEMA).validate(output)
        return SimpleNamespace(data=output, duration_ms=1, prompt_tokens=10, output_tokens=5)


class VincentRehearsalTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.artwork, cls.evidence, cls.releases = load_sources()

    def test_source_release_bytes_must_match_sibling_digest(self):
        self.assertEqual(self.releases["gallery"]["version"], "getty-gallery-v2")
        self.assertEqual(self.releases["reference_pack"]["version"], "vincent-irises-v1")
        with TemporaryDirectory() as directory:
            path = Path(directory) / "test.json"
            path.write_bytes(b"{}")
            path.with_suffix(".sha256").write_text(sha256(path.read_bytes()).hexdigest())
            path.write_bytes(b"{ }")
            with self.assertRaisesRegex(ValueError, "Evidence release digest mismatch"):
                read_release(path)

    async def rehearse(self, cases, outputs):
        report = {"conversations": []}
        model = RecordedModel(outputs)
        written = []
        await run_dialogues(cases, model, "persona", self.artwork, self.evidence,
                            report, lambda: written.append(json.dumps(report)), 60)
        return model, report["conversations"], written

    async def test_forward_actual_answers_memory_and_only_prior_cited_evidence(self):
        cases = [
            {"id": "first", "turns": [turn("What did you write?", [LETTER, MEDIUM]), turn("And the garden?", [])]},
            {"id": "fresh", "turns": [turn("Hello!", [])]},
        ]
        first = reply([LETTER])
        model, dialogues, _ = await self.rehearse(cases, [first, reply([LETTER]), reply([], "Hello!")])
        requests = [json.loads(r["messages"][1]["content"]) for r in model.requests]
        self.assertEqual(model.requests[0]["operation"], "final")
        self.assertEqual(model.requests[0]["output_schema"], OUTPUT_SCHEMA)
        order = ["cited_chunk_ids", "answer", "memory_update"]
        self.assertEqual(list(OUTPUT_SCHEMA["properties"]), order)
        self.assertEqual(OUTPUT_SCHEMA["required"], order)
        initial, follow_up, fresh = requests
        self.assertTrue(all(list(payload)[-2:] == ["available_source_kinds", "question"] for payload in requests))
        self.assertEqual(initial["available_source_kinds"], ["artist_letter", "museum_record"])
        self.assertEqual(follow_up["available_source_kinds"], ["artist_letter"])
        self.assertEqual(fresh["available_source_kinds"], [])
        self.assertEqual(initial["current_evidence"][0]["source_kind"], "artist_letter")
        self.assertEqual(initial["current_evidence"][1]["excerpt"], "Oil on canvas")
        self.assertIn("attribution", initial["current_evidence"][0])
        self.assertEqual(follow_up["history"], [{"question": "What did you write?", "answer": first["answer"]}])
        self.assertEqual(follow_up["previous_memory"], first["memory_update"])
        self.assertEqual([r["chunk_id"] for r in follow_up["cached_evidence"]], [LETTER])
        self.assertEqual(follow_up["current_evidence"], [])
        self.assertEqual((fresh["cached_evidence"], fresh["history"], fresh["previous_memory"]), ([], [], None))
        self.assertEqual(dialogues[0]["turns"][1]["cached_evidence_ids"], [LETTER])

    async def test_eight_most_recent_sources_survive_source_free_turns(self):
        ids = list(self.evidence)[:10]
        cited_again = [ids[8], ids[4], ids[9]]
        turns = [turn("What do these records say?", ids[:8]), turn("What else?", ids[8:])]
        turns.extend(turn("Thank you", []) for _ in range(6))
        outputs = [reply(ids[:8]), reply(cited_again)] + [reply([], "You're welcome.") for _ in range(6)]
        model, _, _ = await self.rehearse([{"id": "longer", "turns": turns}], outputs)
        last = json.loads(model.requests[-1]["messages"][1]["content"])
        self.assertEqual(len(last["history"]), 5)
        expected = cited_again + [ids[i] for i in (0, 1, 2, 3, 5)]
        self.assertEqual([item["chunk_id"] for item in last["cached_evidence"]], expected)

    async def test_unoffered_citation_is_recorded_and_aborts_only_its_dialogue(self):
        cases = [
            {"id": "invalid", "turns": [turn("What color?", [LETTER]), turn("Why?", [])]},
            {"id": "fresh", "turns": [turn("Hello", [])]},
        ]
        invalid = reply([MEDIUM])
        model, dialogues, checkpoints = await self.rehearse(cases, [invalid, reply([], "Hello!")])
        self.assertEqual(len(model.requests), 2)
        self.assertEqual(len(dialogues[0]["turns"]), 1)
        record = dialogues[0]["turns"][0]
        self.assertEqual(record["output"], invalid)
        self.assertEqual(record["error"]["outside_offered_evidence"], [MEDIUM])
        self.assertEqual(json.loads(checkpoints[0])["conversations"][0]["turns"][0], record)
        self.assertNotIn("error", dialogues[1]["turns"][0])

    def test_validate_fixed_input_and_duplicate_model_citations(self):
        for ids in ([LETTER, LETTER], ["getty-other-work"]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                validate_cases([{"id": "a", "turns": [turn("What color?", ids)]}], self.evidence)
        self.assertFalse(Draft202012Validator(OUTPUT_SCHEMA).is_valid(reply([LETTER, LETTER])))
