"""Private HTTP transport for the local MuseBridge retrieval service."""

import argparse
import json
import logging
import os
from pathlib import Path
import re
import signal
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from time import monotonic
from urllib.parse import urlsplit
from uuid import uuid4

from . import DATASET_VERSION, DatasetValidationError, RetrievalError, load_dataset, retrieve

DEFAULT_SEED = Path(__file__).resolve().parent / "data" / f"{DATASET_VERSION}.json"
MAX_BODY_BYTES = 8192
READ_TIMEOUT_SECONDS = 5
LOGGER = logging.getLogger("musebridge.retrieval")
ROUTES = {"/retrieve": "POST", "/healthz": "GET", "/readyz": "GET"}
RETRIEVAL_ERRORS = {
    "invalid_request": (400, False),
    "artwork_not_found": (404, False),
    "dataset_version_mismatch": (409, False),
}


class _HTTPError(Exception):
    def __init__(self, status, code, message, *, retryable=False, allow=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.retryable = retryable
        self.allow = allow


def _invalid(message="The request is invalid."):
    return _HTTPError(400, "invalid_request", message)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON property.")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("Nonfinite JSON number.")


class RetrievalHTTPServer(ThreadingHTTPServer):
    """A concurrent local service sharing an immutable, loaded Dataset."""

    daemon_threads = True

    def __init__(self, address, dataset):
        self.dataset = dataset
        super().__init__(address, _Handler)

    def handle_error(self, request, client_address):
        # Do not let socketserver log request content through an exception.
        LOGGER.error(json.dumps({
            "event": "connection_failed",
            "service": "retrieval",
            "error_type": sys.exc_info()[0].__name__,
        }))


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "MuseBridgeRetrieval"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(READ_TIMEOUT_SECONDS)
        self._request_id = None
        self._started_at = monotonic()
        self._artwork_id = None
        self._chunk_ids = []

    def __getattr__(self, name):
        # BaseHTTPRequestHandler otherwise emits an HTML 501 for unknown verbs.
        if name.startswith("do_"):
            return self._dispatch
        raise AttributeError(name)

    def log_message(self, format, *args):
        # The default access log includes the raw URL, which may contain user text.
        pass

    def _ensure_request_id(self):
        if self._request_id is not None:
            return
        headers = getattr(self, "headers", None)
        supplied = headers.get_all("X-Request-ID", []) if headers else []
        if (
            len(supplied) == 1
            and 1 <= len(supplied[0]) <= 128
            and supplied[0].strip()
            and all(32 <= ord(char) <= 126 for char in supplied[0])
        ):
            self._request_id = supplied[0]
        else:
            self._request_id = "req-" + uuid4().hex

    def _send_json(self, status, payload, *, code="ok", allow=None):
        self._ensure_request_id()
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.close_connection = True
        dataset = self.server.dataset
        LOGGER.info(json.dumps({
            "event": "request_completed",
            "service": "retrieval",
            "request_id": self._request_id,
            "artwork_id": self._artwork_id,
            "dataset_version": dataset.dataset_version if dataset is not None else None,
            "status": status,
            "code": code,
            "duration_ms": round((monotonic() - self._started_at) * 1000, 3),
            "chunk_ids": self._chunk_ids,
        }))
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("X-Request-ID", self._request_id)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            if allow is not None:
                self.send_header("Allow", allow)
            self.end_headers()
            if getattr(self, "command", None) != "HEAD":
                self.wfile.write(encoded)
        except OSError:
            # A disconnected client must not turn a finished request into a 500.
            self.close_connection = True

    def _send_failure(self, error):
        self._ensure_request_id()
        self._send_json(error.status, {
            "request_id": self._request_id,
            "error": {
                "code": error.code,
                "message": str(error),
                "retryable": error.retryable,
            },
        }, code=error.code, allow=error.allow)

    def send_error(self, code, message=None, explain=None):
        # Protocol parsing failures also use the safe JSON envelope.
        self._send_failure(_invalid("The HTTP request is malformed."))

    def _read_payload(self):
        types = self.headers.get_all("Content-Type", [])
        if len(types) != 1 or re.fullmatch(
            r'application/json(?:\s*;\s*charset\s*=\s*(?:"utf-8"|utf-8))?',
            types[0].strip(), re.IGNORECASE,
        ) is None:
            raise _HTTPError(
                415, "unsupported_media_type",
                "Use application/json with UTF-8 encoding.",
            )
        encodings = self.headers.get_all("Content-Encoding", [])
        if encodings and (len(encodings) != 1 or encodings[0].strip().lower() != "identity"):
            raise _HTTPError(415, "unsupported_media_type", "Content encoding is not supported.")
        if self.headers.get_all("Transfer-Encoding", []):
            raise _invalid("Use a single Content-Length header without transfer encoding.")
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or re.fullmatch(r"[0-9]+", lengths[0].strip()) is None:
            raise _invalid("A single valid Content-Length header is required.")
        digits = lengths[0].strip().lstrip("0") or "0"
        if len(digits) > 4 or int(digits) > MAX_BODY_BYTES:
            raise _HTTPError(413, "payload_too_large", "The request body exceeds 8192 bytes.")
        try:
            raw = self.rfile.read(int(digits))
        except (OSError, TimeoutError) as exc:
            raise _invalid("The request body could not be read completely.") from exc
        if len(raw) != int(digits):
            raise _invalid("The request body is incomplete.")
        try:
            payload = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise _invalid("The body must be one valid UTF-8 JSON object.") from exc
        required = {"artwork_id", "query", "dataset_version"}
        if (
            not isinstance(payload, dict)
            or not required.issubset(payload)
            or set(payload) - required - {"top_k"}
        ):
            raise _invalid("Provide artwork_id, query, dataset_version and optional top_k only.")
        for field in required:
            value = payload[field]
            if isinstance(value, str) and any(0xD800 <= ord(char) <= 0xDFFF for char in value):
                raise _invalid("JSON strings must contain valid Unicode characters.")
        return payload

    def _dispatch(self):
        self._ensure_request_id()
        try:
            try:
                target = urlsplit(self.path)
            except ValueError as exc:
                raise _invalid("The HTTP request target is malformed.") from exc
            if target.scheme or target.netloc or target.fragment or "?" in self.path:
                raise _invalid("Use an endpoint path without query parameters or fragments.")
            method = ROUTES.get(target.path)
            if method is None:
                raise _HTTPError(404, "route_not_found", "The requested route does not exist.")
            if self.command != method:
                raise _HTTPError(
                    405, "method_not_allowed",
                    "The HTTP method is not supported for this route.", allow=method,
                )
            dataset = self.server.dataset
            if target.path == "/healthz":
                self._send_json(200, {
                    "status": "ok",
                    "dataset_version": dataset.dataset_version if dataset is not None else None,
                })
                return
            if target.path == "/readyz":
                if dataset is None:
                    raise _HTTPError(
                        503, "service_not_ready",
                        "The evidence service is not ready.", retryable=True,
                    )
                self._send_json(200, {"status": "ok", "dataset_version": dataset.dataset_version})
                return
            payload = self._read_payload()
            if dataset is None:
                raise _HTTPError(
                    503, "retrieval_unavailable",
                    "The evidence service is temporarily unavailable.", retryable=True,
                )
            candidate = payload["artwork_id"]
            if isinstance(candidate, str) and candidate in dataset.artworks:
                self._artwork_id = candidate
            response = retrieve(dataset, **payload)
            self._chunk_ids = [item["chunk_id"] for item in response["evidence"]]
            self._send_json(200, response)
        except _HTTPError as exc:
            self._send_failure(exc)
        except RetrievalError as exc:
            status, retryable = RETRIEVAL_ERRORS.get(exc.code, (500, True))
            if status == 500:
                self._send_failure(_HTTPError(
                    500, "internal_error", "The evidence service could not complete the request.",
                    retryable=True,
                ))
            else:
                self._send_failure(_HTTPError(status, exc.code, str(exc), retryable=retryable))
        except Exception:
            # Exception messages may contain user input or local paths.
            self._send_failure(_HTTPError(
                500, "internal_error", "The evidence service could not complete the request.",
                retryable=True,
            ))


def create_server(
    host="127.0.0.1", port=0, *,
    seed_path=DEFAULT_SEED, dataset_version=DATASET_VERSION,
):
    """Validate the seed before binding; port zero is useful for isolated tests."""
    if not isinstance(host, str) or not host.strip():
        raise ValueError("host must be a nonempty string.")
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("port must be an integer from 0 to 65535.")
    dataset = load_dataset(seed_path, expected_version=dataset_version)
    return RetrievalHTTPServer((host, port), dataset)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=os.environ.get("PORT", "8001"))
    parser.add_argument("--seed", type=Path, default=os.environ.get("SEED_PATH", str(DEFAULT_SEED)))
    parser.add_argument("--dataset-version", default=os.environ.get("DATASET_VERSION", DATASET_VERSION))
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be an integer from 1 to 65535.")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        server = create_server(
            args.host, args.port, seed_path=args.seed, dataset_version=args.dataset_version,
        )
    except (DatasetValidationError, OSError, ValueError) as exc:
        LOGGER.error(json.dumps({
            "event": "startup_failed", "service": "retrieval", "message": str(exc),
        }))
        return 2

    def stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        with server:
            LOGGER.info(json.dumps({
                "event": "service_started",
                "service": "retrieval",
                "host": args.host,
                "port": server.server_port,
                "dataset_version": server.dataset.dataset_version,
            }))
            server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
