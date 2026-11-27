"""Private local speech recognition for Talk with Vincent (docs/vincent-voice.md).

Go forwards one raw recording to ``POST /recognize`` and receives
``{"text": ..., "language": "en"}``. ``GET /ready`` returns 200 only after the
English model has loaded. The worker binds to loopback, keeps clips in memory,
discards them after each request and never logs audio or transcripts.
"""

import json
import logging
import threading
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from time import monotonic

LOGGER = logging.getLogger("musebridge.whisper")

MAX_BYTES = 3 * 1024 * 1024
MAX_SECONDS = 20.0
# Recorders stop at 20 s; allow a little container padding before calling it over-duration.
DURATION_TOLERANCE = 0.5
MAX_TEXT = 500
READ_TIMEOUT_SECONDS = 25


class WorkerError(Exception):
    """A private failure with its HTTP status and contract code."""

    def __init__(self, status, code):
        super().__init__(code)
        self.status = status
        self.code = code


def _media(content_type):
    """Validate the declared type. Returns 'mp4' or 'webm' or raises 415."""
    if not content_type:
        raise WorkerError(415, "unsupported_media_type")
    message = Message()
    message["content-type"] = content_type
    media_type = message.get_content_type()
    params = dict(message.get_params()[1:]) if message.get_params() else {}
    if media_type in ("audio/mp4", "video/mp4"):
        container = "mp4"
    elif media_type == "audio/webm":
        container = "webm"
    else:
        raise WorkerError(415, "unsupported_media_type")
    for name, value in params.items():
        if name != "codecs":
            raise WorkerError(415, "unsupported_media_type")
        for codec in value.split(","):
            codec = codec.strip().lower()
            if codec == "opus" or (container == "mp4" and codec.startswith("mp4a.")):
                continue
            raise WorkerError(415, "unsupported_media_type")
    return container


def _sniff(body):
    if len(body) >= 12 and body[4:8] == b"ftyp":
        return "mp4"
    if body[:4] == b"\x1aE\xdf\xa3":
        head = body[:64]
        if b"webm" in head:
            return "webm"
        if b"matroska" in head:
            return "matroska"
    return ""


def _has_video(container, body):
    if container == "mp4":
        start = body.find(b"hdlr")
        while start >= 0:
            if body[start + 12:start + 16] == b"vide":
                return True
            start = body.find(b"hdlr", start + 4)
        return False
    return any(codec in body for codec in (b"V_VP8", b"V_VP9", b"V_AV1", b"V_MPEG4"))


def check_container(content_type, body):
    """Match the declaration against the bytes, as Go does before forwarding."""
    declared = _media(content_type)
    if not body:
        raise WorkerError(422, "audio_invalid")
    sniffed = _sniff(body)
    if not sniffed:
        raise WorkerError(422, "audio_invalid")
    if sniffed != declared or _has_video(sniffed, body):
        raise WorkerError(415, "unsupported_media_type")


def clean_transcript(text):
    """Apply the message text rules without truncating."""
    text = (text or "").strip()
    if not text:
        raise WorkerError(422, "no_speech")
    if len(text) > MAX_TEXT:
        raise WorkerError(422, "transcript_too_long")
    if any((ord(c) < 32 and c not in "\t\n\r") or 127 <= ord(c) <= 159 for c in text):
        raise WorkerError(422, "audio_invalid")
    return text


class FasterWhisperRecognizer:
    """English recognition with faster-whisper. Decoding uses its bundled PyAV."""

    def __init__(self, model="small.en", device="cpu", compute_type="int8"):
        self.model_name, self.device, self.compute_type = model, device, compute_type
        self._model = None

    def load(self):
        from faster_whisper import WhisperModel  # imported here so tests need no model

        self._model = WhisperModel(self.model_name, device=self.device, compute_type=self.compute_type)

    def decode(self, body):
        """Return 16 kHz mono samples and their duration in seconds, or raise 422."""
        from faster_whisper import decode_audio

        try:
            samples = decode_audio(BytesIO(body), sampling_rate=16000)
        except Exception as error:  # PyAV raises several error types for broken input
            raise WorkerError(422, "audio_invalid") from error
        return samples, len(samples) / 16000.0

    def transcribe(self, samples):
        segments, _ = self._model.transcribe(
            samples, language="en", beam_size=5, vad_filter=True, condition_on_previous_text=False
        )
        return " ".join(segment.text.strip() for segment in segments)


class WhisperServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, recognizer):
        super().__init__(address, Handler)
        self.recognizer = recognizer
        self.ready = threading.Event()
        self.slot = threading.Lock()  # one recognition at a time on the local model

    def load_model(self):
        try:
            self.recognizer.load()
        except Exception:  # pragma: no cover - reported through /ready staying 503
            LOGGER.exception("model_load_failed")
            return
        self.ready.set()
        LOGGER.info("model_ready")


class Handler(BaseHTTPRequestHandler):
    server_version = "MuseBridgeWhisper"
    sys_version = ""
    timeout = READ_TIMEOUT_SECONDS

    def log_message(self, *args):  # the default would log paths with timing only; keep it quiet
        return

    def _send(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _discard(self, length, limit=4 * MAX_BYTES):
        """Read and drop an oversized body so the client receives the 413 instead of a reset."""
        remaining = min(length, limit)
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 64 * 1024))
            if not chunk:
                break
            remaining -= len(chunk)
        if length > limit:
            self.close_connection = True

    def do_GET(self):
        if self.path != "/ready":
            self._send(404, {"error": "route_not_found"})
        elif self.server.ready.is_set():
            self._send(200, {"ready": True})
        else:
            self._send(503, {"error": "transcription_unavailable"})

    def do_POST(self):
        started = monotonic()
        trace = (self.headers.get("X-Request-ID") or "")[:128]
        try:
            status, payload = 200, {"text": self._recognize(), "language": "en"}
            code = "ok"
        except WorkerError as error:
            status, code = error.status, error.code
            payload = {"error": error.code}
        except Exception:
            status = 503
            code = "transcription_unavailable"
            payload = {"error": code}
            LOGGER.exception("recognition_failed")
        # Log before replying. No transcript, clip bytes or answer text in logs.
        LOGGER.info(json.dumps({"event": "recognize", "request_id": trace, "status": status, "code": code,
                                "duration_ms": round((monotonic() - started) * 1000, 1)}))
        self._send(status, payload)

    def _recognize(self):
        if self.path != "/recognize":
            raise WorkerError(404, "route_not_found")
        if not self.server.ready.is_set():
            raise WorkerError(503, "transcription_unavailable")
        encoding = self.headers.get("Content-Encoding", "identity").lower()
        if encoding != "identity":
            raise WorkerError(415, "unsupported_media_type")
        length = self.headers.get("Content-Length")
        if length is None or not length.isdigit():
            raise WorkerError(422, "audio_invalid")
        if int(length) > MAX_BYTES:
            self._discard(int(length))
            raise WorkerError(413, "payload_too_large")
        body = self.rfile.read(int(length))
        check_container(self.headers.get("Content-Type"), body)
        if not self.server.slot.acquire(blocking=False):
            raise WorkerError(429, "voice_busy")
        try:
            samples, seconds = self.server.recognizer.decode(body)
            del body
            if seconds > MAX_SECONDS + DURATION_TOLERANCE:
                raise WorkerError(422, "audio_invalid")
            if seconds <= 0:
                raise WorkerError(422, "no_speech")
            return clean_transcript(self.server.recognizer.transcribe(samples))
        finally:
            self.server.slot.release()
