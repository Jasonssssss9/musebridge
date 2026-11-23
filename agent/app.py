"""Private v2 FastAPI run service. Default startup has no ready model provider."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hmac
import os
import re
import secrets

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect
from starlette.responses import Response

from agent.contracts.codec import ContractCodec, PRIVATE_BODY_LIMIT
from agent.runtime.errors import WorkflowError
from agent.runtime.runs import RunRegistry, validate_run_shape
from agent.runtime.service_errors import failure, private_status, safe_failure
from agent.runtime.validation import ContractViolation, strict_json

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_TRACE = re.compile(r"[\x21-\x7e]{1,128}\Z")
_CODEC = ContractCodec()


def _reply(definition: str, body: dict, status: int = 200, headers=None) -> Response:
    return Response(_CODEC.encode(definition, body), status_code=status,
                    media_type="application/json", headers=headers)


def _error(error: BaseException, request_id: str, run_id=None, headers=None) -> Response:
    safe = safe_failure(error)
    return _reply("ErrorEnvelope", {"request_id": request_id, "run_id": run_id,
                                   "error": safe.as_dict()}, private_status(safe.code), headers)


class PrivateBoundary:
    """Pure ASGI boundary: raw identity headers precede routing and body reads."""

    def __init__(self, app, *, token: str):
        self.app, self.token = app, token.encode("ascii")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        values = {}
        for key, value in scope.get("headers", []):
            values.setdefault(key.lower(), []).append(value)
        trace_headers = values.get(b"x-request-id", [])
        trace = None
        if len(trace_headers) == 1:
            try:
                candidate = trace_headers[0].decode("ascii")
                if _TRACE.fullmatch(candidate):
                    trace = candidate
            except UnicodeError:
                pass
        request_id = trace or "req-" + secrets.token_hex(16)
        scope.setdefault("state", {})["request_id"] = request_id
        started = False

        async def respond(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = [(key, value) for key, value in message.get("headers", [])
                           if key.lower() not in {b"x-request-id", b"cache-control"}]
                headers += [(b"x-request-id", request_id.encode("ascii")), (b"cache-control", b"no-store")]
                message = {**message, "headers": headers}
            await send(message)

        infrastructure = scope["path"] in {"/healthz", "/readyz"}
        try:
            if not infrastructure:
                authorization = values.get(b"authorization", [])
                valid = False
                if len(authorization) == 1:
                    scheme, separator, credential = authorization[0].partition(b" ")
                    valid = bool(separator) and scheme.lower() == b"bearer" and hmac.compare_digest(credential, self.token)
                if not valid:
                    raise failure("agent_unauthorized")
            if (not infrastructure and trace is None) or (trace_headers and trace is None):
                raise failure("invalid_request")
            if scope.get("query_string"):
                raise failure("invalid_request")
            await self.app(scope, receive, respond)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if started:
                raise
            await _error(exc, request_id, scope["state"].get("run_id"))(scope, receive, respond)


def _content_type(headers: list[bytes]) -> bool:
    if len(headers) != 1:
        return False
    try:
        parts = [part.strip().lower() for part in headers[0].decode("ascii").split(";")]
    except UnicodeError:
        return False
    if parts[0] != "application/json" or len(parts) > 2:
        return False
    if len(parts) == 1:
        return True
    name, separator, value = parts[1].partition("=")
    return bool(separator) and name.strip() == "charset" and value.strip() in {"utf-8", '"utf-8"'}


async def _body(request: Request) -> bytes:
    headers = {}
    for key, value in request.scope.get("headers", []):
        headers.setdefault(key.lower(), []).append(value)
    if not _content_type(headers.get(b"content-type", [])) or headers.get(b"content-encoding"):
        raise failure("unsupported_media_type")
    lengths = headers.get(b"content-length", [])
    if lengths and (len(lengths) != 1 or not lengths[0].isdigit() or len(lengths[0]) > 10):
        raise failure("invalid_request")
    if lengths and int(lengths[0]) > PRIVATE_BODY_LIMIT:
        raise failure("payload_too_large")
    content = bytearray()
    try:
        async with asyncio.timeout(10):
            async for chunk in request.stream():
                if len(content) + len(chunk) > PRIVATE_BODY_LIMIT:
                    raise failure("payload_too_large")
                content.extend(chunk)
    except TimeoutError as exc:
        raise failure("request_timeout") from exc
    except ClientDisconnect as exc:
        raise failure("invalid_request") from exc
    if lengths and len(content) != int(lengths[0]):
        raise failure("invalid_request")
    return bytes(content)


def create_app(runtime: RunRegistry | None = None, *, service_token: str | None = None) -> FastAPI:
    """Uvicorn factory; real inference requires explicit provider configuration."""
    token = service_token if service_token is not None else os.environ.get("AGENT_SERVICE_TOKEN", "")
    try:
        valid_token = (type(token) is str and len(token.encode("ascii")) >= 43
                       and re.fullmatch(r"[\x21-\x7e]+", token))
    except UnicodeError:
        valid_token = False
    if not valid_token:
        raise ValueError("AGENT_SERVICE_TOKEN must be a visible ASCII token generated from at least 32 random bytes.")
    injected = runtime is not None
    provider = "disabled" if injected else os.environ.get("AGENT_PROVIDER", "disabled")
    if provider not in {"disabled", "ollama"}:
        raise ValueError("AGENT_PROVIDER must be disabled or ollama.")
    runtime = runtime if runtime is not None else RunRegistry()

    @asynccontextmanager
    async def lifespan(_app):
        nonlocal runtime
        close = runtime.close
        if provider == "ollama":
            from agent.providers.bootstrap import start_ollama_runtime
            runtime, close = await start_ollama_runtime()
            _app.state.runtime = runtime
        try:
            yield
        finally:
            await close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None,
                  telemetry={"tracing": False, "metrics": False, "logs": False, "auto_configure": False},
                  redirect_slashes=False, lifespan=lifespan)
    app.state.runtime = runtime
    app.add_middleware(PrivateBoundary, token=token)

    @app.exception_handler(WorkflowError)
    async def workflow_error(request: Request, error: WorkflowError):
        return _error(error, request.state.request_id, getattr(request.state, "run_id", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _error_value):
        return _error(failure("invalid_request"), request.state.request_id)

    @app.exception_handler(HTTPException)
    async def route_error(request: Request, error: HTTPException):
        code = "method_not_allowed" if error.status_code == 405 else "route_not_found"
        headers = {"Allow": error.headers["Allow"]} if error.headers and "Allow" in error.headers else None
        return _error(failure(code), request.state.request_id, headers=headers)

    @app.get("/healthz")
    async def health():
        return _reply("HealthResponse", {"status": "ok"})

    @app.get("/readyz")
    async def ready(request: Request):
        if not request.app.state.runtime.is_ready():
            raise failure("service_not_ready", "capacity")
        strategy = getattr(request.app.state.runtime, "retrieval_strategy_version", None)
        return _reply("ReadinessResponse", {
            "status": "ok", "contract_version": "agent-run-v2", "workflow_id": "museum_qa_v2",
            "profile_id": "local-ollama-v2", "dataset_version": "aic-seed-v0.1",
            "state_version": "conversation-state-v1", "supported_languages": ["en", "es", "zh-CN", "ko"],
            "history_turn_limit": 5,
        }, headers={"X-Retrieval-Strategy": strategy} if strategy else None)

    @app.post("/internal/v2/agent/runs")
    async def create_run(request: Request):
        try:
            value = strict_json(await _body(request), max_bytes=PRIVATE_BODY_LIMIT)
            run = validate_run_shape(value, _CODEC)
        except ContractViolation as exc:
            raise failure("invalid_request") from exc
        request.state.run_id = run["run_id"]
        snapshot = await request.app.state.runtime.submit(run, request_id=request.state.request_id)
        return _reply("RunSnapshot", snapshot)

    @app.get("/internal/v2/agent/runs/{run_id}")
    async def get_run(run_id: str, request: Request):
        if _UUID.fullmatch(run_id) is None:
            raise failure("invalid_request")
        request.state.run_id = run_id
        return _reply("RunSnapshot", await request.app.state.runtime.get(run_id))

    return app
