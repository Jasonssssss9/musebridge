"""Bounded non-streaming Ollama transport with strict outer/inner validation."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from time import monotonic
from urllib.parse import urlsplit

import httpx
from jsonschema import FormatChecker
from jsonschema.exceptions import ValidationError, SchemaError

from agent.providers.grammar import grammar_schema
from agent.providers.profile import OllamaProfile
from agent.runtime.service_errors import failure
from agent.runtime.validation import ContractViolation, StrictValidator, TRACE_ID, strict_json


@dataclass(frozen=True)
class OllamaCompletion:
    data: dict
    model_provider: str
    model_name: str
    model_digest: str
    done_reason: str
    prompt_tokens: int
    output_tokens: int
    estimated_prompt_tokens: int
    duration_ms: int
    server_timings_ns: dict


class OllamaModel:
    def __init__(self, *, base_url, sizer, profile=None, transport=None, observer=None):
        parsed = urlsplit(base_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {"", "/"}):
            raise ValueError("Ollama requires a configured HTTP(S) origin without credentials or a path.")
        self.profile = profile or OllamaProfile()
        if self.profile != OllamaProfile():
            raise ValueError("A different model or budget requires an explicitly revised profile.")
        self.sizer, self.observer = sizer, observer
        self.client = httpx.AsyncClient(base_url=base_url.rstrip("/"), transport=transport,
            follow_redirects=False, trust_env=False, headers={"Accept-Encoding": "identity"},
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2))
        self.verified = False
        self.metadata_fingerprint = None

    async def aclose(self):
        self.verified = False
        await self.client.aclose()

    async def _json(self, method, path, *, deadline, body=None, limit=256*1024, trace=None, phase="admission"):
        headers = {"X-Request-ID": trace} if trace else {}
        try:
            async with asyncio.timeout_at(deadline):
                async with self.client.stream(method, path, json=body, headers=headers,
                                              timeout=max(0.001, deadline-monotonic())) as response:
                    if response.status_code != 200:
                        code = "generation_invalid_response" if 200 <= response.status_code < 500 and response.status_code not in {404, 408, 429} else "generation_unavailable"
                        raise failure(code, phase)
                    media = response.headers.get("content-type", "").lower().split(";")[0].strip()
                    if media != "application/json" or response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise failure("generation_invalid_response", phase)
                    lengths = response.headers.get_list("content-length")
                    if lengths and (len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit() or int(lengths[0]) > limit):
                        raise failure("generation_invalid_response", phase)
                    raw = bytearray()
                    async for chunk in response.aiter_raw():
                        if len(raw) + len(chunk) > limit:
                            raise failure("generation_invalid_response", phase)
                        raw.extend(chunk)
                    if lengths and int(lengths[0]) != len(raw):
                        raise failure("generation_invalid_response", phase)
                    data = strict_json(bytes(raw), max_bytes=limit)
                    if type(data) is not dict:
                        raise failure("generation_invalid_response", phase)
                    if monotonic() >= deadline:
                        raise failure("generation_timeout", phase)
                    return data
        except (TimeoutError, httpx.TimeoutException) as exc:
            self.verified = False
            raise failure("generation_timeout", phase) from exc
        except httpx.RequestError as exc:
            self.verified = False
            raise failure("generation_unavailable", phase) from exc
        except (ContractViolation, UnicodeError, ValueError) as exc:
            raise failure("generation_invalid_response", phase) from exc

    async def check_identity(self, *, deadline, trace=None):
        try:
            version = await self._json("GET", "/api/version", deadline=deadline, trace=trace)
            tags = await self._json("GET", "/api/tags", deadline=deadline, trace=trace)
            models = tags.get("models")
            matches = [item for item in models if type(item) is dict and item.get("name") == self.profile.model_name] if type(models) is list else []
            if (version.get("version") != self.profile.server_version or len(matches) != 1
                    or matches[0].get("digest", "").removeprefix("sha256:") != self.profile.model_digest
                    or matches[0].get("remote_model") or matches[0].get("remote_host")):
                raise failure("service_not_ready", "admission")
        except Exception:
            self.verified = False
            raise

    async def verify(self, *, deadline):
        self.verified = False
        await self.check_identity(deadline=deadline)
        show = await self._json("POST", "/api/show", deadline=deadline,
            body={"model": self.profile.model_name, "verbose": True}, limit=32*1024*1024)
        try:
            if (show.get("renderer") != "qwen3.5" or show.get("parser") != "qwen3.5"
                    or show.get("template_source") != "renderer_parser"
                    or show.get("system") or show.get("messages")
                    or show.get("details", {}).get("quantization_level") != "Q4_K_M"
                    or "completion" not in show.get("capabilities", [])):
                raise ValueError("Unsupported model metadata.")
            self.sizer.verify_model(show["model_info"])
            selected = {key: show.get(key) for key in ("renderer", "parser", "template_source", "template", "parameters", "thinking", "details", "model_info", "system", "messages")}
            self.metadata_fingerprint = hashlib.sha256(json.dumps(selected, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        except (KeyError, TypeError, ValueError) as exc:
            self.verified = False
            raise failure("service_not_ready", "admission") from exc
        self.verified = True

    async def warm(self, *, deadline):
        await self.check_identity(deadline=deadline)
        await self._json("POST", "/api/generate", deadline=deadline, body={
            "model": self.profile.model_name, "prompt": "", "stream": False,
            "keep_alive": self.profile.keep_alive, "options": {"num_ctx": self.profile.context_tokens}})
        state = await self._json("GET", "/api/ps", deadline=deadline)
        models = state.get("models", [])
        if not any(item.get("name") == self.profile.model_name
                   and item.get("digest", "").removeprefix("sha256:") == self.profile.model_digest
                   and item.get("context_length") == self.profile.context_tokens
                   for item in models):
            raise failure("service_not_ready", "admission")
        return state

    async def complete_structured(self, *, operation, messages, output_schema, profile, deadline, trace_context):
        phase = {"plan": "planning", "assess": "assessment", "final": "final_generation"}.get(operation)
        if phase is None or profile != self.profile or not self.verified:
            raise failure("service_not_ready", "admission")
        trace = trace_context.get("request_id")
        if type(trace) is not str or not TRACE_ID.fullmatch(trace):
            raise failure("invalid_request")
        started = monotonic()
        output_tokens = profile.plan_output_tokens if operation == "plan" else profile.answer_output_tokens
        estimated = self.sizer(messages, output_schema)
        if estimated + output_tokens + profile.safety_tokens > profile.context_tokens:
            raise failure("agent_context_limit", "context")
        try:
            async with asyncio.timeout_at(deadline):
                await self.check_identity(deadline=deadline, trace=trace)
                raw = json.dumps({"model": profile.model_name, "messages": deepcopy(messages),
                    "format": grammar_schema(output_schema), "stream": False, "think": profile.think,
                    "truncate": False, "keep_alive": profile.keep_alive,
                    "options": {"num_ctx": profile.context_tokens, "num_predict": output_tokens,
                                "temperature": profile.temperature, "presence_penalty": profile.presence_penalty}}, ensure_ascii=False, allow_nan=False)
                if len(raw.encode()) > 256*1024:
                    raise failure("agent_context_limit", "context")
                reply = await self._json("POST", "/api/chat", body=json.loads(raw), deadline=deadline, trace=trace, phase=phase)
                message = reply.get("message")
                if (reply.get("model") != profile.model_name or reply.get("done") is not True
                        or reply.get("done_reason") != "stop" or type(message) is not dict
                        or message.get("role") != "assistant" or message.get("thinking")
                        or message.get("tool_calls") or reply.get("error")
                        or reply.get("remote_host") or reply.get("remote_model")):
                    raise failure("generation_invalid_response", phase)
                counts = [reply.get("prompt_eval_count"), reply.get("eval_count")]
                if (any(type(value) is not int or value <= 0 for value in counts)
                        or counts[0] > estimated or counts[1] >= output_tokens):
                    raise failure("generation_invalid_response", phase)
                timings = {key: reply[key] for key in ("total_duration", "load_duration", "prompt_eval_duration", "eval_duration") if key in reply}
                if any(type(value) is not int or value < 0 for value in timings.values()):
                    raise failure("generation_invalid_response", phase)
                data = strict_json(message.get("content"))
                if type(data) is not dict:
                    raise failure("generation_invalid_response", phase)
                StrictValidator(output_schema, format_checker=FormatChecker()).validate(data)
                await self.check_identity(deadline=deadline, trace=trace)
                if monotonic() >= deadline:
                    raise failure("generation_timeout", phase)
                completion = OllamaCompletion(deepcopy(data), "ollama", profile.model_name,
                    profile.model_digest, "stop", counts[0], counts[1], estimated,
                    int((monotonic()-started)*1000), timings)
                if self.observer is not None:
                    self.observer(operation, trace, completion)
                return completion
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise failure("generation_timeout", phase) from exc
        except (ContractViolation, ValidationError, SchemaError, TypeError, ValueError, KeyError) as exc:
            raise failure("generation_invalid_response", phase) from exc
