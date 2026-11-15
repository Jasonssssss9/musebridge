"""B's boundary checks against shared fixtures; no inference or service starts."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import typing
import unittest

from agent.contracts import ContractCodec
from agent.contracts import models
from agent.contracts.errors import ContractFailure, map_agent_failure
from agent.contracts.validation import check_run_request, check_run_result
from agent.runtime.errors import WorkflowError
from agent.runtime.validation import ContractViolation
from agent.tools.evidence import EvidenceManifest

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = json.loads((ROOT / "docs/contracts/agent-v2.examples.json").read_text())
POLICIES = json.loads((ROOT / "docs/contracts/agent-v2.errors.json").read_text())


def fixture(name):
    return deepcopy(next(case["payload"] for case in EXAMPLES["schema_cases"]
                         if case["name"] == name))


class BoundaryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.codec = ContractCodec()
        cls.manifest = EvidenceManifest.load()

    def test_all_shared_structural_cases_and_json_round_trips(self):
        self.assertEqual(len(EXAMPLES["schema_cases"]), 100)
        for case in EXAMPLES["schema_cases"]:
            with self.subTest(case=case["name"]):
                raw = json.dumps(case["payload"], ensure_ascii=False).encode()
                if case["valid"]:
                    decoded = self.codec.decode(case["definition"], raw)
                    encoded = self.codec.encode(case["definition"], decoded)
                    self.assertEqual(json.loads(encoded), case["payload"])
                else:
                    with self.assertRaises(ContractViolation):
                        self.codec.decode(case["definition"], raw)

    def test_generated_static_annotations_and_source_hash(self):
        schema = ROOT / "docs/contracts/agent-v2.schema.json"
        self.assertEqual(hashlib.sha256(schema.read_bytes()).hexdigest(), models.SCHEMA_SHA256)
        for name, value in vars(models).items():
            if typing.is_typeddict(value):
                with self.subTest(model=name):
                    self.assertTrue(typing.get_type_hints(value, include_extras=True))

    def test_nullable_presence_and_integer_lexemes(self):
        message = fixture("public_submission")
        message["language_override"] = None
        raw = json.dumps(message)
        self.assertIsNone(self.codec.decode_public_message(raw)["language_override"])
        for field, value in [("expected_revision", 0.0), ("expected_revision", "0"),
                             ("expected_revision", False), ("expected_revision", None),
                             ("language_override", "")]:
            bad = dict(message, **{field: value})
            with self.subTest(field=field, value=value), self.assertRaises(ContractFailure):
                self.codec.decode_public_message(json.dumps(bad))
        for field in ("language_override", "expected_revision"):
            bad = deepcopy(message)
            del bad[field]
            with self.subTest(missing=field), self.assertRaises(ContractFailure):
                self.codec.decode_public_message(json.dumps(bad))

    def test_strict_raw_json_unicode_and_depth(self):
        for raw in ('{"status":"ok","status":"ok"}', '{"status":"ok"} {}',
                    '{"status":"ok","metadata":NaN}', '{"status":"ok","metadata":1e309}',
                    '{"status":"ok","metadata":"\\ud800"}',
                    '{"status":"ok","metadata":"\\u0080"}', b'\xff',
                    '{"status":"ok","metadata":' + '[' * 65 + '0' + ']' * 65 + '}'):
            with self.subTest(raw=str(raw)[:40]), self.assertRaises(ContractViolation):
                self.codec.decode("HealthResponse", raw)

    def test_public_versions_normalization_sizes_and_unicode_lengths(self):
        message = fixture("public_submission")
        message["question"] = "　 Why  this?  "
        parsed = self.codec.decode_public_message(json.dumps(message))
        self.assertEqual(parsed["question"], "Why  this?")
        self.assertEqual(message["question"], "　 Why  this?  ")
        message["contract_version"] = "future-version"
        with self.assertRaises(ContractFailure) as raised:
            self.codec.decode_public_message(json.dumps(message))
        self.assertEqual(raised.exception.status, 409)
        message["expected_revision"] = "0"
        with self.assertRaises(ContractFailure) as raised:
            self.codec.decode_public_message(json.dumps(message))
        self.assertEqual(raised.exception.status, 400)
        with self.assertRaises(ContractFailure) as raised:
            self.codec.decode_public_message(b" " * 8193)
        self.assertEqual(raised.exception.status, 413)
        for length, valid in ((500, True), (501, False)):
            message = fixture("public_submission")
            message["question"] = "韩" * length
            raw = json.dumps(message, ensure_ascii=False)
            if valid:
                self.assertEqual(self.codec.decode_public_message(raw)["question"], message["question"])
            else:
                with self.assertRaises(ContractFailure):
                    self.codec.decode_public_message(raw)

    def test_open_response_metadata_is_detached_and_closed_inputs_reject_it(self):
        source = fixture("public_create_restore")["conversation"]
        source["REVISION"] = "inert metadata"
        source["metadata"] = {"decimal": 1.25}
        decoded = self.codec.decode("PublicConversation", json.dumps(source))
        self.assertEqual(decoded["revision"], source["revision"])
        decoded["metadata"]["decimal"] = 99
        self.assertEqual(source["metadata"]["decimal"], 1.25)
        message = fixture("public_submission")
        message["extra"] = "not allowed"
        with self.assertRaises(ContractFailure):
            self.codec.decode_public_message(json.dumps(message))

    def test_existing_run_context_validator_is_reused(self):
        original = fixture("run_request_initial")
        accepted = check_run_request(original, self.manifest)
        self.assertEqual(accepted, original)
        accepted["question"] = "changed copy"
        self.assertNotEqual(accepted, original)
        bad = deepcopy(original)
        bad["conversation_state"]["conversation_id"] = "99999999-9999-4999-8999-999999999999"
        with self.assertRaises(WorkflowError):
            check_run_request(bad, self.manifest)

    def test_terminal_result_is_bound_to_run_and_does_not_mutate_it(self):
        run = fixture("run_request_initial")
        result = fixture("run_result_mixed")
        before = deepcopy((run, result))
        validated = check_run_result(run, result)
        validated["answer"]["text"] = "changed copy"
        self.assertEqual((run, result), before)
        mutations = (
            ("scope", lambda r: r["scope"].update(artwork_id="aic-11723")),
            ("revision", lambda r: r["conversation_update"].update(base_revision=1)),
            ("language", lambda r: r.update(answer_language="ko")),
            ("catalog", lambda r: r["catalog_snapshot"][0].update(value="Forged title")),
            ("text", lambda r: r["answer"].update(text="Altered text")),
            ("count", lambda r: r["execution"].update(retrieval_calls=2)),
            ("citation", lambda r: r["request_results"][1].update(citation_ids=["not-retrieved"])),
            ("snapshot", lambda r: r["answer"]["citations"][0].update(excerpt="Forged excerpt")),
        )
        for name, change in mutations:
            bad = deepcopy(result)
            change(bad)
            with self.subTest(case=name), self.assertRaises((ContractViolation, WorkflowError)):
                check_run_result(run, bad)

    def test_catalog_and_local_clarification_results(self):
        for name in ("catalog_only_run_result", "partial_answer_with_local_clarification_result"):
            run = fixture("run_request_initial")
            result = fixture(name)
            run["question"] = " ".join(request["original_fragment"] for request in result["requests"])
            with self.subTest(case=name):
                self.assertEqual(check_run_result(run, result), result)

    def test_required_nullable_union_field_round_trip(self):
        value = {"action": "set", "source": "current_message", "request_id": None}
        self.assertEqual(json.loads(self.codec.encode("PendingUpdate", value)), value)
        del value["request_id"]
        with self.assertRaises(ContractViolation):
            self.codec.encode("PendingUpdate", value)

    def test_all_shared_error_policies_and_safe_prose(self):
        for code, (private, public, public_code, retryable) in POLICIES["policies"].items():
            error = {"code": code, "message": "private payload should never escape",
                     "retryable": retryable, "phase": "admission"}
            with self.subTest(code=code):
                mapped = map_agent_failure(private, error)
                self.assertEqual((mapped.status, mapped.code, mapped.retryable),
                                 (public, public_code, retryable))
                self.assertEqual(mapped.reconcile, code == "agent_run_in_progress")
                self.assertNotIn("private payload", mapped.public_error()["message"])
                self.assertEqual(map_agent_failure(418, error).code, "agent_invalid_response")


if __name__ == "__main__":
    unittest.main()
