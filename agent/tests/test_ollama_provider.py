"""Provider boundaries using deterministic HTTP responses, without a GPU."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import tempfile
from time import monotonic
import unittest
from unittest.mock import patch

import httpx
from jsonschema.exceptions import ValidationError
from tokenizers import Tokenizer, models, normalizers, pre_tokenizers

from agent.providers.capabilities import CHECKS, make_record, read_record, validate_record
from agent.providers.grammar import grammar_schema
from agent.providers.ollama import OllamaModel
from agent.providers.profile import OllamaProfile
from agent.providers.prepare import download_tokenizer
from agent.providers.sizing import QwenPromptSizer
from agent.runtime.errors import WorkflowError
from agent.runtime.validation import StrictValidator
from agent.runtime.validation_v2 import ContractValidatorV2

PROFILE = OllamaProfile()
MESSAGES = [{"role": "system", "content": "Return the required JSON."}, {"role": "user", "content": "Hello"}]
SCHEMA = {"type": "object", "properties": {"text": {"type": "string", "allOf": [{"pattern": "\\S"}]}},
          "required": ["text"], "additionalProperties": False}


class Sizer:
    def __init__(self, count=100):
        self.count = count

    def __call__(self, messages, schema):
        return self.count

    def verify_model(self, info):
        if info != {"synthetic": True}:
            raise ValueError("Synthetic tokenizer mismatch.")


def response(value, *, status=200, headers=None):
    data = value if type(value) is bytes else json.dumps(value).encode()
    return httpx.Response(status, headers={"Content-Type": "application/json", **(headers or {})}, stream=httpx.ByteStream(data))


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.reply = {"model": PROFILE.model_name, "message": {"role": "assistant", "content": '{"text":"Hello"}'},
                      "done": True, "done_reason": "stop", "prompt_eval_count": 100, "eval_count": 5,
                      "total_duration": 1000000, "load_duration": 0, "prompt_eval_duration": 100, "eval_duration": 200}
        self.version = PROFILE.server_version
        self.digest = PROFILE.model_digest
        self.chat = None
        self.show = {"renderer": "qwen3.5", "parser": "qwen3.5", "template_source": "renderer_parser",
                     "details": {"quantization_level": "Q4_K_M"}, "capabilities": ["completion"], "model_info": {"synthetic": True}}

        async def handle(request):
            self.calls.append(request)
            if request.url.path == "/api/version":
                return response({"version": self.version})
            if request.url.path == "/api/tags":
                return response({"models": [{"name": PROFILE.model_name, "digest": self.digest}]})
            if request.url.path == "/api/show":
                return response(self.show)
            if request.url.path == "/api/chat":
                return await self.chat(request) if self.chat else response(self.reply)
            if request.url.path == "/api/generate":
                return response({"done": True, "done_reason": "load"})
            if request.url.path == "/api/ps":
                return response({"models": [{"name": PROFILE.model_name, "digest": self.digest, "context_length": 16384}]})
            raise AssertionError("An unexpected endpoint was requested.")

        self.model = OllamaModel(base_url="http://synthetic-test.invalid", sizer=Sizer(), transport=httpx.MockTransport(handle))
        self.addAsyncCleanup(self.model.aclose)
        await self.model.verify(deadline=monotonic()+2)

    async def call(self, **updates):
        options = dict(operation="plan", messages=deepcopy(MESSAGES), output_schema=deepcopy(SCHEMA),
                       profile=PROFILE, deadline=monotonic()+2, trace_context={"request_id": "synthetic-provider-test"})
        options.update(updates)
        return await self.model.complete_structured(**options)

    async def expect_code(self, code, **options):
        with self.assertRaises(WorkflowError) as caught:
            await self.call(**options)
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn("PRIVATE", str(caught.exception))

    async def test_real_wire_options_identity_usage_and_no_hidden_retries(self):
        result = await self.call()
        self.assertEqual(result.data, {"text": "Hello"})
        self.assertEqual((result.model_name, result.model_digest), (PROFILE.model_name, PROFILE.model_digest))
        self.assertEqual((result.prompt_tokens, result.output_tokens, result.done_reason), (100, 5, "stop"))
        requests = [request for request in self.calls if request.url.path == "/api/chat"]
        self.assertEqual(len(requests), 1)
        payload = json.loads(requests[0].content)
        self.assertEqual(payload["options"], {"num_ctx": 16384, "num_predict": 2048, "temperature": 0, "presence_penalty": 0})
        self.assertFalse(payload["stream"])
        self.assertFalse(payload["think"])
        self.assertFalse(payload["truncate"])
        self.assertEqual(payload["messages"], MESSAGES)
        self.assertNotIn("allOf", payload["format"]["properties"]["text"])
        self.assertEqual(requests[0].headers["X-Request-ID"], "synthetic-provider-test")

    async def test_stop_identity_message_and_usage_failures_are_rejected(self):
        changes = [{"done": False}, {"done_reason": "length"}, {"done_reason": "load"},
                   {"model": "another-model"}, {"message": {"role": "user", "content": "{}"}},
                   {"message": {"role": "assistant", "thinking": "PRIVATE REASONING", "content": "{}"}},
                   {"message": {"role": "assistant", "tool_calls": [{}], "content": "{}"}},
                   {"prompt_eval_count": 101}, {"eval_count": 2048}, {"eval_count": True},
                   {"eval_count": 0}, {"prompt_eval_count": 5.0}, {"total_duration": -1},
                   {"remote_host": "https://example.invalid"}, {"error": "PRIVATE SERVER ERROR"}]
        original = deepcopy(self.reply)
        for change in changes:
            with self.subTest(change=change):
                self.reply = {**original, **change}
                await self.expect_code("generation_invalid_response")

    async def test_original_schema_is_enforced_after_broader_generation_grammar(self):
        for content in ('{"text":{}}', '{"text":" "}', '{"text":"one","text":"two"}',
                        '{"text":NaN}', '{"text":"ok","extra":true}', '```json\n{"text":"ok"}\n```',
                        '{"text":"\\ud800"}', '[]'):
            with self.subTest(content=content):
                self.reply["message"]["content"] = content
                await self.expect_code("generation_invalid_response")

    async def test_outer_json_headers_sizes_and_redirects_are_bounded(self):
        values = [(b'{"done":true,"done":false}', {}, 200), (b'{"bad":NaN}', {}, 200),
                  (b"{}", {"Content-Type": "application/x-ndjson"}, 200),
                  (b"{}", {"Content-Encoding": "gzip"}, 200),
                  (b"{}", {"Content-Length": "9999999"}, 200),
                  (b"{}", {"Content-Length": "1"}, 200),
                  (b" " * (256*1024+1), {}, 200), (b"PRIVATE", {"Location": "https://example.invalid"}, 302)]
        for body, headers, status in values:
            with self.subTest(headers=headers, status=status):
                async def chat(_request):
                    return response(body, headers=headers, status=status)
                self.chat = chat
                await self.expect_code("generation_invalid_response")
        self.assertTrue(all(request.url.host == "synthetic-test.invalid" for request in self.calls))

    async def test_unavailable_and_transport_errors_are_typed(self):
        for status in (404, 429, 500, 503):
            async def chat(_request):
                return response(b"PRIVATE PROVIDER BODY", status=status)
            self.chat = chat
            await self.expect_code("generation_unavailable")
        async def connection(request):
            raise httpx.ConnectError("PRIVATE CONNECTION", request=request)
        self.chat = connection
        await self.expect_code("generation_unavailable")

    async def test_entire_response_read_obeys_stage_deadline_and_closes_stream(self):
        closed = asyncio.Event()

        class SlowStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"{"
                await asyncio.sleep(1)
                yield b"}"

            async def aclose(self):
                closed.set()

        async def chat(_request):
            return httpx.Response(200, headers={"Content-Type": "application/json"}, stream=SlowStream())
        self.chat = chat
        await self.expect_code("generation_timeout", deadline=monotonic()+0.03)
        self.assertTrue(closed.is_set())

    async def test_cancellation_propagates_to_provider_transport(self):
        started, cancelled = asyncio.Event(), asyncio.Event()
        async def chat(_request):
            started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.set()
                raise
        self.chat = chat
        pending = asyncio.create_task(self.call())
        await started.wait()
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertTrue(cancelled.is_set())

    async def test_context_overflow_stops_before_any_inference_request(self):
        self.model.sizer = Sizer(14000)
        await self.expect_code("agent_context_limit")
        self.assertFalse(any(request.url.path == "/api/chat" for request in self.calls))

    async def test_model_or_server_identity_drift_stops_before_generation(self):
        self.digest = "0"*64
        await self.expect_code("service_not_ready")
        self.assertFalse(self.model.verified)
        self.assertFalse(any(request.url.path == "/api/chat" for request in self.calls))

    async def test_identity_is_rechecked_after_generation(self):
        async def chat(_request):
            self.digest = "0"*64
            return response(self.reply)
        self.chat = chat
        await self.expect_code("service_not_ready")
        self.assertFalse(self.model.verified)

    async def test_profile_and_model_metadata_cannot_be_silently_changed(self):
        await self.model.warm(deadline=monotonic()+2)
        self.show["system"] = "PRIVATE OVERRIDE"
        with self.assertRaises(WorkflowError) as caught:
            await self.model.verify(deadline=monotonic()+2)
        self.assertEqual(caught.exception.code, "service_not_ready")
        with self.assertRaises(ValueError):
            OllamaModel(base_url="http://localhost", sizer=Sizer(), profile=replace(PROFILE, context_tokens=8192))

    def test_urls_require_explicit_origin_without_embedded_credentials(self):
        for url in ("file:///tmp/model", "http://user:PRIVATE@localhost", "http://localhost/path", "http://localhost?key=PRIVATE"):
            with self.assertRaises(ValueError):
                OllamaModel(base_url=url, sizer=Sizer())

    def test_capability_record_is_bound_to_versions_model_assets_and_age(self):
        record = make_record(self.model, {key: True for key in CHECKS})
        self.assertTrue(validate_record(record, self.model))
        for field in ("metadata_fingerprint", "profile_fingerprint", "implementation_fingerprint", "tokenizer_sha256"):
            bad = deepcopy(record); bad[field] = "changed"
            self.assertFalse(validate_record(bad, self.model))
        bad = deepcopy(record); bad["created_at"] = "2020-01-01T00:00:00Z"
        self.assertFalse(validate_record(bad, self.model))
        bad = deepcopy(record); bad["checks"]["sizing"] = 1
        self.assertFalse(validate_record(bad, self.model))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/"record.json"
            path.write_text(json.dumps(record))
            self.assertEqual(read_record(path, self.model), record)
            path.write_text('{"duplicate":1,"duplicate":2}')
            self.assertIsNone(read_record(path, self.model))


class SizingAndGrammarTests(unittest.TestCase):
    def test_explicit_tokenizer_download_checks_integrity_and_reuses_existing_asset(self):
        data = b"synthetic-tokenizer-asset"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"tokenizer.json"
            with patch("agent.providers.prepare.TOKENIZER_BYTES", len(data)), patch("agent.providers.prepare.TOKENIZER_SHA256", hashlib.sha256(data).hexdigest()), patch("agent.providers.prepare.urllib.request.urlopen", return_value=io.BytesIO(data)) as fetch:
                download_tokenizer(path)
                self.assertEqual(path.read_bytes(), data)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                download_tokenizer(path)
                fetch.assert_called_once()

    def test_bad_download_and_preexisting_partial_do_not_overwrite_user_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"tokenizer.json"
            partial = path.with_name(path.name+".partial")
            partial.write_bytes(b"existing partial")
            with patch("agent.providers.prepare.urllib.request.urlopen", return_value=io.BytesIO(b"wrong")):
                with self.assertRaises(FileExistsError): download_tokenizer(path)
            self.assertEqual(partial.read_bytes(), b"existing partial")
            partial.unlink()
            with patch("agent.providers.prepare.urllib.request.urlopen", return_value=io.BytesIO(b"wrong")):
                with self.assertRaises(ValueError): download_tokenizer(path)
            self.assertFalse(path.exists())
            self.assertFalse(partial.exists())

    def test_all_actual_operation_schemas_keep_structure_without_modifying_originals(self):
        validator = ContractValidatorV2()
        for name in ("Plan", "AssessmentDecision", "FinalDecision"):
            schema = validator.schema(name)
            original = deepcopy(schema)
            grammar = grammar_schema(schema)
            self.assertEqual(schema, original)
            self.assertEqual(grammar["$ref"], schema["$ref"])
            self.assertNotIn('"allOf"', json.dumps(grammar))
            self.assertNotIn('"pattern"', json.dumps(grammar))
        broad = grammar_schema(SCHEMA)
        StrictValidator(broad).validate({"text": " "})
        with self.assertRaises(ValidationError):
            StrictValidator(SCHEMA).validate({"text": " "})

    def test_generation_grammar_preserves_types_for_direct_scope_and_clarification(self):
        grammar = grammar_schema(ContractValidatorV2().schema("Plan"))
        defs = grammar["$defs"]
        item = defs["DirectResponsePlan"]["properties"]["requests"]["items"]
        validator = StrictValidator({"$defs": defs, **item})
        request = dict(id="r1", original_fragment="Who made it?", intent="Identify the artist.",
                       kind="catalog_fact", resolution="clear")
        self.assertTrue(validator.is_valid(request))
        self.assertFalse(validator.is_valid(dict(request, kind="artwork_fact")))
        self.assertTrue(validator.is_valid(dict(request, kind="artwork_fact", resolution="needs_clarification")))
        self.assertFalse(validator.is_valid({}))

        for route, actions in (("ClarificationPlan", {"set"}), ("ScopePlan", {"clear", "keep"})):
            memory = defs[route]["properties"]["memory_update"]
            validator = StrictValidator({"$defs": defs, **memory})
            for action in ("keep", "clear", "set"):
                pending = {"action": action}
                if action == "set":
                    pending.update(source="current_message", request_id=None)
                record = {"active_topic": None, "continuity_summary": "Visitor asked about the maker.",
                          "pending_update": pending}
                self.assertEqual(validator.is_valid(record), action in actions)
                if route == "ClarificationPlan" and action == "set":
                    pending["request_id"] = "r1"
                    self.assertFalse(validator.is_valid(record))
            self.assertFalse(validator.is_valid({}))

    def test_bpe_sizer_verifies_assets_and_accounts_for_utf8_normalization_and_wrapper(self):
        alphabet = sorted(pre_tokenizers.ByteLevel.alphabet())
        tokenizer = Tokenizer(models.BPE(vocab={text:index for index,text in enumerate(alphabet)}, merges=[]))
        tokenizer.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False, use_regex=False)
        tokenizer.normalizer = normalizers.NFC()
        raw = tokenizer.to_str().encode()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"tokenizer.json"; path.write_bytes(raw)
            with patch("agent.providers.sizing.TOKENIZER_BYTES", len(raw)), patch("agent.providers.sizing.TOKENIZER_SHA256", hashlib.sha256(raw).hexdigest()):
                sizer = QwenPromptSizer(path)
                messages = deepcopy(MESSAGES); messages[1]["content"] = "e\u0301 中文 🎨"
                plain, nfc = sizer.counts(messages)
                self.assertNotEqual(plain, nfc)
                self.assertEqual(sizer(messages, {}), max(plain, nfc)+64)
                self.assertGreater(plain, sum(len(item["content"]) for item in messages))
                vocab = sizer.definition["model"]["vocab"]
                info = {"tokenizer.ggml.model":"gpt2", "tokenizer.ggml.pre":"qwen35", "tokenizer.ggml.add_bos_token":False,
                        "tokenizer.ggml.tokens":sorted(vocab, key=vocab.get), "tokenizer.ggml.merges":[]}
                sizer.verify_model(info)
                info["tokenizer.ggml.tokens"][0] = "corrupt"
                with self.assertRaises(ValueError): sizer.verify_model(info)
                path.write_bytes(raw+b" ")
                with self.assertRaises(ValueError): QwenPromptSizer(path)


if __name__ == "__main__":
    unittest.main()
