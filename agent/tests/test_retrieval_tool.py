"""Transport fault coverage and actual retrieval HTTP integration for both artworks."""

import asyncio
import json
import threading
import time
import unittest
from unittest.mock import patch

import httpx

from knowledge.http_service import create_server

from agent.runtime.errors import WorkflowError
from agent.tools.evidence import DATASET_VERSION, DEFAULT_SEED_PATH, EvidenceManifest
from agent.tools.retrieval import MAX_RESPONSE_BYTES, RETRIEVAL_STRATEGY_VERSION, RetrievalTool


BEDROOM = "aic-28560"
TOILETTE = "aic-11723"
REQUEST_ID = "c-m1-retrieval-test"


def tool_input(artwork_id=BEDROOM, query="What is the medium of this artwork?"):
    return {
        "tool_version": "evidence-tool-v1", "query_id": "q1", "request_ids": ["r1"],
        "query": query, "artwork_id": artwork_id, "dataset_version": DATASET_VERSION, "top_k": 2,
    }


class Chunks(httpx.AsyncByteStream):
    def __init__(self, *chunks, delay=0):
        self.chunks = chunks
        self.delay = delay
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield chunk

    async def aclose(self):
        self.closed = True


def response(payload=None, *, raw=None, status=200, headers=None, stream=None):
    default_headers = [("Content-Type", "application/json; charset=utf-8"), ("X-Request-ID", REQUEST_ID)]
    if headers is not None:
        default_headers = headers
    if raw is None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return httpx.Response(status, headers=default_headers, stream=stream or Chunks(raw))


class RetrievalToolTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = EvidenceManifest.load()

    def payload(self, artwork_id=BEDROOM):
        return {"dataset_version": DATASET_VERSION, "evidence": [dict(self.manifest.records[artwork_id + "-medium"])]}

    async def run_response(self, result, *, input_value=None, remaining_seconds=3.0):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: result)) as client:
            tool = RetrievalTool(self.manifest, base_url="http://retrieval.test", client=client)
            value = await tool.search(input_value or tool_input(), request_id=REQUEST_ID, remaining_seconds=remaining_seconds)
            self.assertFalse(client.is_closed, "An injected client remains owned by its caller.")
            return value

    async def assert_error(self, result, code="retrieval_invalid_response", **kwargs):
        with self.assertRaises(WorkflowError) as caught:
            await self.run_response(result, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.phase, "retrieval")
        self.assertEqual(caught.exception.retryable, code in {"retrieval_unavailable", "retrieval_timeout"})

    async def test_only_legacy_fields_and_trace_are_sent_and_order_is_preserved(self):
        payload = self.payload()
        payload["evidence"].append(dict(self.manifest.records[BEDROOM + "-description-01"]))
        payload["metadata"] = {"compatible": True}
        payload["evidence"][0]["score"] = 0.9
        captured = []

        def handle(request):
            captured.append(request)
            return response(payload)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=True) as client:
            tool = RetrievalTool(self.manifest, base_url="http://retrieval.test/", client=client)
            result = await tool.search(tool_input(), request_id=REQUEST_ID)
        request = captured[0]
        self.assertEqual(len(captured), 1)
        self.assertEqual(request.method, "POST")
        self.assertEqual(str(request.url), "http://retrieval.test/retrieve")
        self.assertEqual(request.headers["X-Request-ID"], REQUEST_ID)
        self.assertEqual(request.headers["Accept-Encoding"], "identity")
        sent = json.loads(request.content)
        self.assertEqual(set(sent), {"artwork_id", "dataset_version", "query", "top_k"})
        self.assertEqual(sent["top_k"], 2)
        self.assertEqual(result["retrieval_strategy_version"], RETRIEVAL_STRATEGY_VERSION)
        self.assertEqual([item["chunk_id"] for item in result["candidates"]], [item["chunk_id"] for item in payload["evidence"]])
        self.assertNotIn("score", result["candidates"][0])

    async def test_valid_empty_lookup_is_a_successful_empty_candidate_list(self):
        result = await self.run_response(response({"dataset_version": DATASET_VERSION, "evidence": []}))
        self.assertEqual(result["candidates"], [])

    async def test_injected_client_can_return_a_preloaded_json_response(self):
        result = await self.run_response(httpx.Response(200, json=self.payload(), headers={"X-Request-ID": REQUEST_ID}))
        self.assertEqual(result["candidates"][0]["chunk_id"], BEDROOM + "-medium")

    async def test_caller_mutation_during_io_cannot_relabel_a_tool_result(self):
        original = tool_input()

        def handle(request):
            original.update(query_id="q2", artwork_id=TOILETTE, dataset_version="changed")
            original["request_ids"].append("r2")
            return response(self.payload())

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            tool = RetrievalTool(self.manifest, base_url="http://retrieval.test", client=client)
            result = await tool.search(original, request_id=REQUEST_ID)
        self.assertEqual(result["query_id"], "q1")
        self.assertEqual(result["artwork_id"], BEDROOM)
        self.assertEqual(result["dataset_version"], DATASET_VERSION)

    async def test_wrong_scope_version_forgery_duplicates_and_count_are_rejected(self):
        wrong_version = dict(self.payload(), dataset_version="different-release")
        forged = self.payload()
        forged["evidence"][0]["excerpt"] = "This text did not come from the release."
        duplicated = self.payload()
        duplicated["evidence"] *= 2
        excessive = self.payload()
        excessive["evidence"] *= 3
        for payload in (self.payload(TOILETTE), wrong_version, forged, duplicated, excessive):
            with self.subTest(payload=payload):
                await self.assert_error(response(payload))

    async def test_missing_or_malformed_schema_fields_are_rejected(self):
        for payload in (None, [], {}, {"dataset_version": DATASET_VERSION}, {"dataset_version": DATASET_VERSION, "evidence": {}}):
            with self.subTest(payload=payload):
                await self.assert_error(response(payload))

    async def test_strict_json_rejects_duplicates_nonfinite_invalid_utf8_and_trailing_data(self):
        valid = json.dumps(self.payload()).encode()
        bodies = [
            b'{"dataset_version":"aic-seed-v0.1","evidence":[],"evidence":[]}',
            b'{"dataset_version":"aic-seed-v0.1","evidence":[],"metadata":{"a":1,"a":2}}',
            b'{"dataset_version":"aic-seed-v0.1","evidence":[],"metadata":NaN}',
            b'{"dataset_version":"aic-seed-v0.1","evidence":[],"metadata":Infinity}',
            b'{"dataset_version":"aic-seed-v0.1","evidence":[],"metadata":1e999}',
            valid + b' {}', valid + b'\xff', b'\xef\xbb\xbf' + valid,
        ]
        for body in bodies:
            with self.subTest(body=body[:80]):
                await self.assert_error(response(raw=body))

    async def test_deep_metadata_returns_a_typed_protocol_error(self):
        # Exercise the explicit validation bound, the former recursion-boundary
        # failure, and nesting that the JSON decoder itself cannot consume.
        for depth in (65, 990, 2000):
            with self.subTest(depth=depth):
                body = (
                    b'{"dataset_version":"aic-seed-v0.1","evidence":[],"metadata":'
                    + b"[" * depth + b"0" + b"]" * depth + b"}"
                )
                await self.assert_error(response(raw=body))

    async def test_header_validation_rejects_wrong_trace_compression_type_and_duplicates(self):
        headers = [
            [("Content-Type", "text/plain"), ("X-Request-ID", REQUEST_ID)],
            [("Content-Type", "application/json; charset=iso-8859-1"), ("X-Request-ID", REQUEST_ID)],
            [("Content-Type", "application/json; charset=utf-8; charset=utf-8"), ("X-Request-ID", REQUEST_ID)],
            [("Content-Type", "application/json; version=1"), ("X-Request-ID", REQUEST_ID)],
            [("Content-Type", "application/json")],
            [("Content-Type", "application/json"), ("X-Request-ID", "other-request")],
            [("Content-Type", "application/json"), ("X-Request-ID", REQUEST_ID), ("X-Request-ID", REQUEST_ID)],
            [("Content-Type", "application/json"), ("Content-Type", "application/json"), ("X-Request-ID", REQUEST_ID)],
            [("Content-Type", "application/json"), ("Content-Encoding", "gzip"), ("X-Request-ID", REQUEST_ID)],
            [("Content-Type", "application/json"), ("Content-Length", str(MAX_RESPONSE_BYTES + 1)), ("X-Request-ID", REQUEST_ID)],
            [("Content-Type", "application/json"), ("Content-Length", "1"), ("X-Request-ID", REQUEST_ID)],
            [("Content-Type", "application/json"), ("Content-Length", "-1"), ("X-Request-ID", REQUEST_ID)],
            [("Content-Type", "application/json"), ("Content-Length", "1, 1"), ("X-Request-ID", REQUEST_ID)],
        ]
        for value in headers:
            with self.subTest(headers=value):
                await self.assert_error(response(self.payload(), headers=value))

    async def test_json_content_type_allows_only_absent_or_utf8_charset(self):
        for content_type in ("application/json", "application/json; charset=utf-8", 'Application/JSON; Charset = "UTF-8"'):
            with self.subTest(content_type=content_type):
                result = await self.run_response(response(self.payload(), headers=[("Content-Type", content_type), ("X-Request-ID", REQUEST_ID)]))
                self.assertEqual(len(result["candidates"]), 1)

    async def test_streamed_body_limit_applies_without_content_length(self):
        stream = Chunks(b" " * MAX_RESPONSE_BYTES, b" ")
        await self.assert_error(response(stream=stream))
        self.assertTrue(stream.closed)

    async def test_status_mapping_and_no_redirect_follow_or_retry(self):
        for status in (201, 204, 301, 307, 400, 401, 404, 409, 429, 500, 503):
            calls = []

            def handle(request):
                calls.append(request)
                return response({}, status=status, headers=[("Location", "http://other.test/secret")])

            with self.subTest(status=status):
                async with httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=True) as client:
                    tool = RetrievalTool(self.manifest, base_url="http://retrieval.test", client=client)
                    with self.assertRaises(WorkflowError) as caught:
                        await tool.search(tool_input(), request_id=REQUEST_ID)
                    expected = "retrieval_unavailable" if status >= 500 else "retrieval_invalid_response"
                    self.assertEqual(caught.exception.code, expected)
                    self.assertEqual(len(calls), 1)

    async def test_transport_failures_are_typed_without_retry(self):
        for failure, code in (
            (httpx.ConnectError("secret endpoint"), "retrieval_unavailable"),
            (httpx.ReadTimeout("secret payload"), "retrieval_timeout"),
            (httpx.RemoteProtocolError("invalid header"), "retrieval_invalid_response"),
        ):
            calls = []

            def handle(request):
                calls.append(request)
                raise failure

            with self.subTest(code=code):
                async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                    tool = RetrievalTool(self.manifest, base_url="http://retrieval.test", client=client)
                    with self.assertRaises(WorkflowError) as caught:
                        await tool.search(tool_input(), request_id=REQUEST_ID)
                    self.assertEqual(caught.exception.code, code)
                    self.assertNotIn("secret", str(caught.exception))
                    self.assertEqual(len(calls), 1)

    async def test_remaining_deadline_bounds_entire_stream_not_individual_reads(self):
        stream = Chunks(*[b" " for _ in range(10)], delay=0.015)
        started = time.monotonic()
        await self.assert_error(response(stream=stream), "retrieval_timeout", remaining_seconds=0.04)
        self.assertLess(time.monotonic() - started, 0.3)
        self.assertTrue(stream.closed)

    async def test_preparation_and_validation_share_the_whole_call_deadline(self):
        for delayed_stage in ("ToolInput", "ToolResult"):
            with self.subTest(delayed_stage=delayed_stage):
                now = [100.0]
                calls = []

                def handle(request):
                    calls.append(request)
                    return response(self.payload())

                async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                    tool = RetrievalTool(self.manifest, base_url="http://retrieval.test", client=client)
                    validate = tool.validator.validate

                    def delayed_validation(name, payload):
                        validate(name, payload)
                        if name == delayed_stage:
                            now[0] += 3.1

                    with patch("agent.tools.retrieval.monotonic", side_effect=lambda: now[0]):
                        with patch.object(tool.validator, "validate", side_effect=delayed_validation):
                            with self.assertRaises(WorkflowError) as caught:
                                await tool.search(tool_input(), request_id=REQUEST_ID)
                    self.assertEqual(caught.exception.code, "retrieval_timeout")
                    self.assertTrue(caught.exception.retryable)
                    self.assertEqual(len(calls), 0 if delayed_stage == "ToolInput" else 1)

    async def test_call_timeout_is_capped_and_invalid_input_never_sends(self):
        captured = []

        def handle(request):
            captured.append(request)
            return response(self.payload())

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            tool = RetrievalTool(self.manifest, base_url="http://retrieval.test", client=client)
            await tool.search(tool_input(), request_id=REQUEST_ID, remaining_seconds=20)
            self.assertTrue(all(0 < value <= 3 for value in captured[0].extensions["timeout"].values()))
            for request_id in ("", "a\nb", "é", "x" * 129):
                with self.subTest(request_id=request_id):
                    with self.assertRaises(WorkflowError):
                        await tool.search(tool_input(), request_id=request_id)
            for remaining in (0, -1, float("nan"), float("inf"), True):
                with self.subTest(remaining=remaining):
                    with self.assertRaises(WorkflowError):
                        await tool.search(tool_input(), request_id=REQUEST_ID, remaining_seconds=remaining)
            for change in ({"top_k": 1}, {"top_k": 2.0}, {"query": "  support?"}, {"request_ids": ["r1", "r1"]}, {"artwork_id": "unknown"}, {"url": "http://untrusted.test"}):
                with self.subTest(change=change):
                    with self.assertRaises(WorkflowError):
                        await tool.search(dict(tool_input(), **change), request_id=REQUEST_ID)
            self.assertEqual(len(captured), 1)

    async def test_cancellation_propagates_and_closes_stream(self):
        stream = Chunks(b" ", delay=10)
        task = asyncio.create_task(self.run_response(response(stream=stream)))
        await asyncio.sleep(0.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(stream.closed)

    def test_tool_origin_does_not_accept_credentials_or_model_chosen_paths(self):
        for origin in ("ftp://host", "http://user:password@host", "http://host/retrieve", "http://host?query=x", "http://host#fragment", "http://", "http://host:invalid", "http://host:99999"):
            with self.subTest(origin=origin):
                with self.assertRaises(ValueError):
                    RetrievalTool(self.manifest, base_url=origin)


class RealRetrievalIntegrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = EvidenceManifest.load()
        cls.server = create_server("127.0.0.1", 0, seed_path=DEFAULT_SEED_PATH)
        cls.addClassCleanup(cls.server.server_close)
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        cls.thread.start()
        cls.addClassCleanup(cls.thread.join, 5)
        cls.addClassCleanup(cls.server.shutdown)
        cls.base_url = "http://%s:%d" % cls.server.server_address

    async def test_real_http_medium_retrieval_and_scope_for_both_artworks(self):
        tool = RetrievalTool(self.manifest, base_url=self.base_url)
        for artwork_id, medium in ((BEDROOM, "Oil on canvas"), (TOILETTE, "Oil on canvas")):
            with self.subTest(artwork_id=artwork_id):
                result = await tool.search(tool_input(artwork_id), request_id=REQUEST_ID)
                self.assertEqual([item["chunk_id"] for item in result["candidates"]], [artwork_id + "-medium"])
                self.assertEqual(result["candidates"][0]["excerpt"], medium)
                self.assertEqual(result["artwork_id"], artwork_id)

    async def test_real_http_brushwork_empty_lookup_and_complete_scope(self):
        tool = RetrievalTool(self.manifest, base_url=self.base_url)
        for artwork_id in (BEDROOM, TOILETTE):
            with self.subTest(artwork_id=artwork_id):
                medium = await tool.search(tool_input(artwork_id), request_id=REQUEST_ID)
                brushwork = await tool.search(tool_input(artwork_id, "How does the museum describe the brushwork of this artwork?"), request_id=REQUEST_ID)
                self.assertEqual([item["chunk_id"] for item in brushwork["candidates"]], [artwork_id + "-description-01"])
                union = self.manifest.merge(artwork_id, DATASET_VERSION, medium["candidates"], brushwork["candidates"])
                self.assertTrue(self.manifest.complete_scope(artwork_id, DATASET_VERSION, union))
                empty = await tool.search(tool_input(artwork_id, "What is the artist's favorite breakfast?"), request_id=REQUEST_ID)
                self.assertEqual(empty["candidates"], [])


if __name__ == "__main__":
    unittest.main()
