import http.client
import json
import socket
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from knowledge import retrieve
from knowledge.http_service import create_server


ROOT = Path(__file__).resolve().parents[2]
VERSION = "aic-seed-v0.1"
SEED_PATH = ROOT / "knowledge" / "data" / f"{VERSION}.json"
EVAL_PATH = ROOT / "eval" / f"{VERSION}.tests.json"
BEDROOM = "aic-28560"
TOILETTE = "aic-11723"


def encode_json(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


class RetrievalHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.evaluation = json.loads(EVAL_PATH.read_text(encoding="utf-8"))
        cls.server = create_server("127.0.0.1", 0, seed_path=SEED_PATH)
        cls.addClassCleanup(cls.server.server_close)
        cls.thread = threading.Thread(
            target=cls.server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        cls.thread.start()
        cls.addClassCleanup(cls.thread.join, 5)
        cls.addClassCleanup(cls.server.shutdown)

    def request(self, method, path, *, body=None, headers=()):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        try:
            connection.putrequest(method, path)
            for name, value in headers:
                connection.putheader(name, value)
            connection.endheaders(body)
            response = connection.getresponse()
            data = response.read()
            self.assertEqual(response.getheader("Cache-Control"), "no-store")
            self.assertEqual(response.getheader("Connection"), "close")
            self.assertEqual(response.getheader("Content-Type").split(";")[0], "application/json")
            request_id = response.getheader("X-Request-ID")
            self.assertTrue(request_id)
            self.assertLessEqual(len(request_id), 128)
            self.assertTrue(all(" " <= char <= "~" for char in request_id))
            if method == "HEAD":
                self.assertEqual(data, b"")
                payload = None
            else:
                self.assertEqual(int(response.getheader("Content-Length")), len(data))
                payload = json.loads(data.decode("utf-8"))
            return response.status, payload, request_id, response.getheader("Allow")
        finally:
            connection.close()

    def post(self, payload=None, *, body=None, path="/retrieve", content_type="application/json",
             headers=()):
        if body is None:
            body = encode_json(payload)
        return self.request(
            "POST",
            path,
            body=body,
            headers=(("Content-Type", content_type), ("Content-Length", str(len(body))), *headers),
        )

    def evidence(self, artwork_id, query, **fields):
        return self.post(
            {"artwork_id": artwork_id, "query": query, "dataset_version": VERSION, **fields},
        )

    def assert_error(self, result, status, code, *, retryable=None):
        actual_status, payload, request_id, _ = result
        self.assertEqual(actual_status, status)
        self.assertEqual(set(payload), {"request_id", "error"})
        self.assertEqual(payload["request_id"], request_id)
        error = payload["error"]
        self.assertEqual(set(error), {"code", "message", "retryable"})
        self.assertEqual(error["code"], code)
        self.assertIsInstance(error["message"], str)
        self.assertTrue(error["message"])
        self.assertIsInstance(error["retryable"], bool)
        if retryable is not None:
            self.assertIs(error["retryable"], retryable)

    def assert_evidence(self, result, artwork_id, *, limit=2):
        status, payload, _, _ = result
        self.assertEqual(status, 200)
        self.assertEqual(set(payload), {"dataset_version", "evidence"})
        self.assertEqual(payload["dataset_version"], VERSION)
        self.assertLessEqual(len(payload["evidence"]), limit)
        for hit in payload["evidence"]:
            self.assertEqual(hit["artwork_id"], artwork_id)
        ids = [hit["chunk_id"] for hit in payload["evidence"]]
        self.assertEqual(len(ids), len(set(ids)))
        return ids

    def test_health_and_readiness_for_loaded_service(self):
        for path in ("/healthz", "/readyz"):
            with self.subTest(path=path):
                status, payload, _, _ = self.request("GET", path)
                self.assertEqual(status, 200)
                self.assertEqual(payload, {"status": "ok", "dataset_version": VERSION})

    def test_liveness_remains_available_without_a_dataset(self):
        dataset = self.server.dataset
        self.server.dataset = None
        try:
            status, payload, _, _ = self.request("GET", "/healthz")
            self.assertEqual(status, 200)
            self.assertEqual(payload, {"status": "ok", "dataset_version": None})
            self.assert_error(
                self.request("GET", "/readyz"), 503, "service_not_ready", retryable=True,
            )
            self.assert_error(
                self.evidence(BEDROOM, "material"), 503, "retrieval_unavailable", retryable=True,
            )
        finally:
            self.server.dataset = dataset

    def test_frozen_questions_round_trip_over_http(self):
        for case in self.evaluation["cases"]:
            with self.subTest(case=case["case_id"]):
                artwork = case["artwork_id"]
                result = self.evidence(artwork, case["question"])
                ids = self.assert_evidence(result, artwork)
                # Gold support is distinct from the raw keyword matches.
                self.assertTrue(set(case["expected_chunk_ids"]).issubset(ids))

    def test_same_brushwork_query_returns_different_artwork_evidence(self):
        check = self.evaluation["cross_artwork_checks"][0]
        for variant in check["variants"]:
            artwork = variant["artwork_id"]
            with self.subTest(artwork=artwork):
                result = self.evidence(artwork, check["query"], top_k=1)
                self.assertEqual(
                    self.assert_evidence(result, artwork, limit=1),
                    [variant["expected_first_chunk_id"]],
                )
                evidence = result[1]["evidence"][0]
                self.assertEqual(evidence["license"], "CC-BY-4.0")
                self.assertEqual(evidence["source_language"], "en")

    def test_valid_utf8_body_and_empty_retrieval_are_successes(self):
        body = json.dumps(
            {"artwork_id": TOILETTE, "query": "笔触", "dataset_version": VERSION},
            ensure_ascii=False,
        ).encode("utf-8")
        result = self.post(body=body, content_type='Application/JSON; charset="UTF-8"')
        self.assertEqual(self.assert_evidence(result, TOILETTE), [f"{TOILETTE}-description-01"])
        self.assertEqual(self.assert_evidence(self.evidence(BEDROOM, "wallpaper"), BEDROOM), [])

    def test_methods_and_unknown_routes_use_json_errors(self):
        for method, path, allowed in (("GET", "/retrieve", "POST"), ("POST", "/healthz", "GET")):
            with self.subTest(method=method, path=path):
                result = self.request(method, path, headers=(("Content-Length", "0"),))
                self.assert_error(result, 405, "method_not_allowed", retryable=False)
                self.assertEqual(result[3], allowed)
        status, payload, _, allowed = self.request("HEAD", "/readyz")
        self.assertEqual((status, payload, allowed), (405, None, "GET"))
        self.assert_error(self.request("GET", "/unknown"), 404, "route_not_found")

    def test_query_parameters_are_rejected_on_known_routes(self):
        for path in ("/healthz", "/readyz"):
            with self.subTest(path=path):
                self.assert_error(self.request("GET", path + "?probe=1"), 400, "invalid_request")
        payload = {"artwork_id": BEDROOM, "query": "material", "dataset_version": VERSION}
        self.assert_error(self.post(payload, path="/retrieve?top_k=1"), 400, "invalid_request")

    def test_retrieval_domain_errors_map_to_distinct_statuses(self):
        self.assert_error(self.evidence("aic-00000", "material"), 404, "artwork_not_found")
        self.assert_error(
            self.evidence(BEDROOM, "material", dataset_version="aic-seed-v0.2"),
            409,
            "dataset_version_mismatch",
        )

    def test_invalid_request_fields_are_rejected(self):
        payload = {"artwork_id": BEDROOM, "query": "material", "dataset_version": VERSION}
        for key in payload:
            with self.subTest(missing=key):
                incomplete = {field: value for field, value in payload.items() if field != key}
                self.assert_error(self.post(incomplete), 400, "invalid_request")
        for updates in (
            {"artwork_id": None},
            {"dataset_version": 2},
            {"conversation_id": "unexpected"},
            {"query": "\t \n"},
            {"query": 7},
            {"query": "x" * 1001},
            {"query": "material\x00canvas"},
            {"query": "\ud800"},
            {"top_k": True},
            {"top_k": 1.0},
            {"top_k": "2"},
            {"top_k": 0},
            {"top_k": 6},
        ):
            with self.subTest(updates=updates):
                self.assert_error(self.post({**payload, **updates}), 400, "invalid_request")

    def test_invalid_json_encodings_and_shapes_are_rejected(self):
        valid = encode_json({"artwork_id": BEDROOM, "query": "material", "dataset_version": VERSION})
        for body in (
            b"",
            b"[]",
            b"null",
            b"{",
            valid + b" {}",
            b"\xef\xbb\xbf" + valid,
            valid.replace(b"material", b"\xff"),
            valid[:-1] + b',"query":"brushwork"}',
            valid[:-1] + b',"top_k":NaN}',
            valid[:-1] + b',"top_k":Infinity}',
        ):
            with self.subTest(body=body):
                self.assert_error(self.post(body=body), 400, "invalid_request")

    def test_content_type_and_charset_are_validated(self):
        payload = {"artwork_id": BEDROOM, "query": "material", "dataset_version": VERSION}
        for content_type in (
            "text/plain",
            "application/json; charset=latin-1",
            "application/json; unknown=1",
        ):
            with self.subTest(content_type=content_type):
                self.assert_error(
                    self.post(payload, content_type=content_type), 415, "unsupported_media_type",
                )
        self.assert_error(
            self.post(payload, headers=(("Content-Type", "application/json"),)),
            415,
            "unsupported_media_type",
        )
        self.assert_error(
            self.request("POST", "/retrieve", headers=(("Content-Length", "0"),)),
            415,
            "unsupported_media_type",
        )

    def test_content_length_and_transfer_encoding_are_validated(self):
        for headers in (
            (),
            (("Content-Length", "invalid"),),
            (("Content-Length", "-1"),),
            (("Content-Length", "+1"),),
            (("Content-Length", "0"), ("Content-Length", "1")),
            (("Transfer-Encoding", "chunked"),),
            (("Transfer-Encoding", "chunked"), ("Content-Length", "0")),
        ):
            with self.subTest(headers=headers):
                result = self.request(
                    "POST", "/retrieve", headers=(("Content-Type", "application/json"), *headers),
                )
                self.assert_error(result, 400, "invalid_request")

    def test_request_body_limit_counts_utf8_bytes(self):
        payload = {"artwork_id": BEDROOM, "query": "material", "dataset_version": VERSION}
        normal_body = encode_json(payload)
        at_limit = normal_body.ljust(8192, b" ")
        self.assertEqual(self.assert_evidence(self.post(body=at_limit), BEDROOM), [f"{BEDROOM}-medium"])
        self.assert_error(self.post(body=at_limit + b" "), 413, "payload_too_large")

        oversized_text = json.dumps({**payload, "query": "🎨" * 2100}, ensure_ascii=False)
        oversized_body = oversized_text.encode("utf-8")
        self.assertLess(len(oversized_text), 8192)
        self.assertGreater(len(oversized_body), 8192)
        self.assert_error(self.post(body=oversized_body), 413, "payload_too_large")

    def test_single_valid_request_id_is_echoed_on_success_and_error(self):
        chosen_id = "r" * 128
        status, _, request_id, _ = self.request(
            "GET", "/healthz", headers=(("X-Request-ID", chosen_id),),
        )
        self.assertEqual((status, request_id), (200, chosen_id))
        error = self.post({}, headers=(("x-request-id", "request-from-go"),))
        self.assert_error(error, 400, "invalid_request")
        self.assertEqual(error[2], "request-from-go")

    def test_invalid_or_duplicate_request_ids_are_replaced(self):
        for headers in (
            (("X-Request-ID", "x" * 129),),
            (("X-Request-ID", "caf\xe9"),),
            (("X-Request-ID", ""),),
            (("X-Request-ID", "first"), ("x-request-id", "second")),
        ):
            with self.subTest(headers=headers):
                status, _, request_id, _ = self.request("GET", "/healthz", headers=headers)
                self.assertEqual(status, 200)
                self.assertNotIn(request_id, [value for _, value in headers])
        self.assertNotEqual(self.request("GET", "/healthz")[2], self.request("GET", "/healthz")[2])

    def test_unexpected_retriever_exception_returns_safe_generic_error(self):
        secret = "private traceback and source path"
        query = "visitor text that must not be echoed"
        with self.assertLogs("musebridge.retrieval", level="INFO") as captured:
            with patch("knowledge.http_service.retrieve", side_effect=RuntimeError(secret)):
                result = self.evidence(BEDROOM, query)
        self.assertNotIn(secret, " ".join(captured.output))
        self.assertNotIn(query, " ".join(captured.output))
        self.assert_error(result, 500, "internal_error")
        error_json = json.dumps(result[1])
        self.assertNotIn(secret, error_json)
        self.assertNotIn(query, error_json)
        self.assertEqual(self.assert_evidence(self.evidence(BEDROOM, "material"), BEDROOM),
                         [f"{BEDROOM}-medium"])


    def raw_request(self, raw, *, finish=False):
        with socket.create_connection(self.server.server_address, timeout=3) as connection:
            connection.sendall(raw)
            if finish:
                connection.shutdown(socket.SHUT_WR)
            response = http.client.HTTPResponse(connection)
            response.begin()
            payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(response.getheader("Connection"), "close")
            return (
                response.status, payload,
                response.getheader("X-Request-ID"), response.getheader("Allow"),
            )

    def test_malformed_request_target_returns_client_error(self):
        result = self.raw_request(
            b"GET http://[ HTTP/1.1\r\nHost: localhost\r\n\r\n"
        )
        self.assert_error(result, 400, "invalid_request", retryable=False)

    def test_unknown_http_verb_uses_contract_method_error(self):
        result = self.request("MUSEBRIDGE", "/retrieve")
        self.assert_error(result, 405, "method_not_allowed", retryable=False)
        self.assertEqual(result[3], "POST")

    def test_truncated_body_is_rejected_without_waiting_for_more_bytes(self):
        result = self.raw_request(
            b"POST /retrieve HTTP/1.1\r\nHost: localhost\r\n"
            b"Content-Type: application/json\r\nContent-Length: 10\r\n\r\n{}",
            finish=True,
        )
        self.assert_error(result, 400, "invalid_request", retryable=False)

    def test_stalled_body_times_out_and_health_remains_available(self):
        with patch("knowledge.http_service.READ_TIMEOUT_SECONDS", 0.1):
            result = self.raw_request(
                b"POST /retrieve HTTP/1.1\r\nHost: localhost\r\n"
                b"Content-Type: application/json\r\nContent-Length: 10\r\n\r\n"
            )
        self.assert_error(result, 400, "invalid_request", retryable=False)
        self.assertEqual(self.request("GET", "/healthz")[0], 200)

    def test_concurrent_requests_keep_evidence_and_request_ids_separate(self):
        entered = threading.Event()
        release = threading.Event()

        def blocked_retrieval(dataset, **fields):
            if fields["artwork_id"] == BEDROOM:
                entered.set()
                if not release.wait(timeout=4):
                    raise RuntimeError("test failed to release the first request")
            return retrieve(dataset, **fields)

        def send(artwork, request_id):
            payload = {"artwork_id": artwork, "query": "brushwork", "dataset_version": VERSION}
            return self.post(payload, headers=(("X-Request-ID", request_id),))

        with patch("knowledge.http_service.retrieve", side_effect=blocked_retrieval):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(send, BEDROOM, "bedroom-request")
                try:
                    self.assertTrue(entered.wait(timeout=3))
                    second = pool.submit(send, TOILETTE, "toilette-request").result(timeout=3)
                    self.assertEqual(second[2], "toilette-request")
                    self.assertEqual(
                        self.assert_evidence(second, TOILETTE),
                        [f"{TOILETTE}-description-01"],
                    )
                finally:
                    release.set()
                bedroom = first.result(timeout=3)
                self.assertEqual(bedroom[2], "bedroom-request")
                self.assertEqual(
                    self.assert_evidence(bedroom, BEDROOM),
                    [f"{BEDROOM}-description-01"],
                )


if __name__ == "__main__":
    unittest.main()
