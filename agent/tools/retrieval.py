"""Bounded asynchronous HTTP adapter for the existing retrieval service."""

import asyncio
from copy import deepcopy
import json
import math
from time import monotonic
from urllib.parse import urlsplit

import httpx

from agent.runtime.errors import WorkflowError
from agent.runtime.validation import ContractValidator
from .evidence import EvidenceManifest


TOOL_VERSION = "evidence-tool-v1"
RETRIEVAL_STRATEGY_VERSION = "legacy-facet-v0.1"
MAX_REQUEST_BYTES = 8 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
MAX_CALL_SECONDS = 3.0


def _error(code: str, message: str, *, retryable: bool = False) -> WorkflowError:
    return WorkflowError(code, message, phase="retrieval", retryable=retryable)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON property.")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("Nonfinite JSON number.")


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Nonfinite JSON number.")
    return number


def _valid_content_type(value: str) -> bool:
    parts = [part.strip().lower() for part in value.split(";")]
    if parts[0] != "application/json" or len(parts) > 2:
        return False
    if len(parts) == 1:
        return True
    name, separator, charset = parts[1].partition("=")
    return (
        bool(separator)
        and name.strip() == "charset"
        and charset.strip() in {"utf-8", '"utf-8"'}
    )


class RetrievalTool:
    """One registered lookup per call, with no redirects or automatic retries.

    The caller owns an injected AsyncClient; otherwise a short-lived client is
    closed within each lookup. The remaining workflow budget caps the entire
    network exchange, including streamed response reads.
    """

    tool_version = TOOL_VERSION
    retrieval_strategy_version = RETRIEVAL_STRATEGY_VERSION

    def __init__(
        self,
        manifest: EvidenceManifest,
        *,
        base_url: str,
        client: httpx.AsyncClient | None = None,
    ):
        parsed = urlsplit(base_url)
        # Accessing port validates malformed and out-of-range port strings.
        parsed.port
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("The retrieval base URL must be an HTTP(S) service origin.")
        self.manifest = manifest
        self.url = base_url.rstrip("/") + "/retrieve"
        self.client = client
        self.validator = ContractValidator()

    async def search(
        self,
        tool_input: dict,
        *,
        request_id: str,
        remaining_seconds: float = MAX_CALL_SECONDS,
    ) -> dict:
        """Validate injected scope, execute one real lookup, and verify evidence."""
        started = monotonic()
        # Keep response attribution tied to the exact request that was sent.
        # A caller may reuse or mutate its input while this lookup is awaiting IO.
        tool_input = deepcopy(tool_input)
        try:
            self.validator.validate("ToolInput", tool_input)
        except (ValueError, TypeError) as exc:
            raise _error("retrieval_invalid_response", "The retrieval input violates its schema.") from exc
        if tool_input["query"] != tool_input["query"].strip() or type(tool_input["top_k"]) is not int:
            raise _error("retrieval_invalid_response", "The retrieval input is not canonical.")
        self.manifest.artwork(tool_input["artwork_id"], tool_input["dataset_version"])
        if (
            not isinstance(request_id, str)
            or not 1 <= len(request_id) <= 128
            or not request_id.strip()
            or not all(32 <= ord(character) <= 126 for character in request_id)
        ):
            raise _error("retrieval_invalid_response", "The retrieval request ID is invalid.")
        if type(remaining_seconds) not in {int, float} or not math.isfinite(remaining_seconds) or remaining_seconds <= 0:
            raise _error("retrieval_timeout", "The retrieval deadline has expired.", retryable=True)
        deadline = started + min(MAX_CALL_SECONDS, remaining_seconds)
        body = json.dumps(
            {key: tool_input[key] for key in ("artwork_id", "query", "dataset_version", "top_k")},
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(body) > MAX_REQUEST_BYTES:
            raise _error("retrieval_invalid_response", "The retrieval request exceeds its byte limit.")

        timeout = deadline - monotonic()
        if timeout <= 0:
            raise _error("retrieval_timeout", "The retrieval deadline has expired.", retryable=True)
        try:
            async with asyncio.timeout(timeout):
                if self.client is None:
                    async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
                        payload = await self._request(client, body, request_id, timeout)
                else:
                    payload = await self._request(self.client, body, request_id, timeout)
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise _error("retrieval_timeout", "The retrieval deadline has expired.", retryable=True) from exc
        except httpx.RemoteProtocolError as exc:
            raise _error("retrieval_invalid_response", "The retrieval HTTP response is invalid.") from exc
        except httpx.RequestError as exc:
            raise _error("retrieval_unavailable", "The retrieval service is unavailable.", retryable=True) from exc

        try:
            self.validator.validate("LegacyRetrieveResponse", payload)
        except (WorkflowError, ValueError, TypeError) as exc:
            raise _error("retrieval_invalid_response", "The retrieval response violates its schema.") from exc
        candidates = self.manifest.validate_candidates(
            tool_input["artwork_id"], payload["dataset_version"], payload["evidence"],
        )
        result = {
            "tool_version": TOOL_VERSION,
            "query_id": tool_input["query_id"],
            "artwork_id": tool_input["artwork_id"],
            "dataset_version": self.manifest.dataset_version,
            "retrieval_strategy_version": RETRIEVAL_STRATEGY_VERSION,
            "candidates": candidates,
        }
        self.validator.validate("ToolResult", result)
        # JSON/schema/provenance validation is part of the same child deadline.
        # CPU-only validation does not yield to asyncio's timer, so check it
        # explicitly before any accepted evidence can leave this operation.
        if monotonic() >= deadline:
            raise _error("retrieval_timeout", "The retrieval deadline has expired.", retryable=True)
        return result

    async def _request(self, client, body: bytes, request_id: str, timeout: float) -> dict:
        async with client.stream(
            "POST",
            self.url,
            content=body,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "X-Request-ID": request_id,
            },
            follow_redirects=False,
            timeout=httpx.Timeout(timeout),
        ) as response:
            if 500 <= response.status_code <= 599:
                raise _error("retrieval_unavailable", "The retrieval service is unavailable.", retryable=True)
            if response.status_code != 200:
                raise _error("retrieval_invalid_response", "The retrieval service returned an unexpected status.")
            content_types = response.headers.get_list("content-type")
            encodings = response.headers.get_list("content-encoding")
            if (
                len(content_types) != 1
                or not _valid_content_type(content_types[0])
                or (encodings and encodings != ["identity"])
                or response.headers.get_list("x-request-id") != [request_id]
            ):
                raise _error("retrieval_invalid_response", "The retrieval response headers are invalid.")
            lengths = response.headers.get_list("content-length")
            if lengths and (
                len(lengths) != 1
                or not lengths[0].isascii()
                or not lengths[0].isdecimal()
                or len(lengths[0]) > 10
                or int(lengths[0]) > MAX_RESPONSE_BYTES
            ):
                raise _error("retrieval_invalid_response", "The retrieval response length is invalid.")
            chunks = bytearray()
            # Compression has already been rejected. aiter_bytes also supports
            # injected clients whose response hooks have read a bounded body.
            async for chunk in response.aiter_bytes():
                if len(chunks) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise _error("retrieval_invalid_response", "The retrieval response exceeds its byte limit.")
                chunks.extend(chunk)
            if lengths and len(chunks) != int(lengths[0]):
                raise _error("retrieval_invalid_response", "The retrieval response length does not match its header.")
            try:
                return json.loads(
                    chunks.decode("utf-8", errors="strict"),
                    object_pairs_hook=_unique_object,
                    parse_constant=_reject_constant,
                    parse_float=_finite_float,
                )
            except (UnicodeError, ValueError, RecursionError) as exc:
                raise _error("retrieval_invalid_response", "The retrieval response is not valid JSON.") from exc
