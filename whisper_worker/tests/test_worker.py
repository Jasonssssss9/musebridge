import json
import threading
import time
import unittest
import urllib.error
import urllib.request

from whisper_worker.worker import MAX_BYTES, WhisperServer, WorkerError

MP4 = b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00isomM4A " + b"\x01" * 64
WEBM = b"\x1aE\xdf\xa3\x9fB\x82\x84webm....A_OPUS" + b"\x02" * 64


class FakeRecognizer:
    def __init__(self, text="  Why do they look blue today?  ", seconds=3.0, decode_error=False):
        self.text, self.seconds, self.decode_error = text, seconds, decode_error
        self.gate = None
        self.calls = 0

    def load(self):
        pass

    def decode(self, body):
        if self.decode_error:
            raise WorkerError(422, "audio_invalid")
        return b"samples", self.seconds

    def transcribe(self, samples):
        self.calls += 1
        if self.gate:
            self.gate.wait(5)
        return self.text


class WorkerTest(unittest.TestCase):
    def start(self, recognizer, ready=True):
        server = WhisperServer(("127.0.0.1", 0), recognizer)
        if ready:
            server.ready.set()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def post(self, base, body, content_type="audio/mp4", headers=None):
        request = urllib.request.Request(base + "/recognize", data=body, method="POST")
        if content_type:
            request.add_header("Content-Type", content_type)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_success_trims_and_reports_english(self):
        base = self.start(FakeRecognizer())
        self.assertEqual(self.post(base, MP4, "audio/mp4; codecs=mp4a.40.2"),
                         (200, {"text": "Why do they look blue today?", "language": "en"}))
        self.assertEqual(self.post(base, WEBM, "audio/webm;codecs=opus")[0], 200)

    def test_ready_only_after_model_load(self):
        base = self.start(FakeRecognizer(), ready=False)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(base + "/ready", timeout=5)
        self.assertEqual(caught.exception.code, 503)
        self.assertEqual(self.post(base, MP4), (503, {"error": "transcription_unavailable"}))

    def test_contract_errors(self):
        cases = [
            (FakeRecognizer(), MP4, "application/json", 415, "unsupported_media_type"),
            (FakeRecognizer(), WEBM, "audio/mp4", 415, "unsupported_media_type"),
            (FakeRecognizer(), MP4 + b"\x00\x00\x00\x21hdlr\x00\x00\x00\x00\x00\x00\x00\x00vide", "video/mp4", 415, "unsupported_media_type"),
            (FakeRecognizer(), b"not audio at all", "audio/webm", 422, "audio_invalid"),
            (FakeRecognizer(decode_error=True), MP4, "audio/mp4", 422, "audio_invalid"),
            (FakeRecognizer(seconds=21.0), MP4, "audio/mp4", 422, "audio_invalid"),
            (FakeRecognizer(text="   "), MP4, "audio/mp4", 422, "no_speech"),
            (FakeRecognizer(text="a" * 501), MP4, "audio/mp4", 422, "transcript_too_long"),
        ]
        for recognizer, body, content_type, status, code in cases:
            with self.subTest(code=code, content_type=content_type):
                base = self.start(recognizer)
                self.assertEqual(self.post(base, body, content_type), (status, {"error": code}))

    def test_size_limit_and_twenty_seconds(self):
        base = self.start(FakeRecognizer(seconds=20.3))
        self.assertEqual(self.post(base, MP4)[0], 200)
        self.assertEqual(self.post(base, MP4 + b"\x00" * MAX_BYTES), (413, {"error": "payload_too_large"}))

    def test_single_recognition_slot(self):
        recognizer = FakeRecognizer()
        recognizer.gate = threading.Event()
        base = self.start(recognizer)
        first = {}
        thread = threading.Thread(target=lambda: first.update(result=self.post(base, MP4)))
        thread.start()
        deadline = time.time() + 5
        while recognizer.calls == 0 and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.post(base, MP4), (429, {"error": "voice_busy"}))
        recognizer.gate.set()
        thread.join(5)
        self.assertEqual(first["result"][0], 200)

    def test_logs_carry_no_transcript(self):
        base = self.start(FakeRecognizer(text="my private question"))
        with self.assertLogs("musebridge.whisper", level="INFO") as logs:
            self.post(base, MP4, headers={"X-Request-ID": "req-test"})
        joined = "\n".join(logs.output)
        self.assertIn("req-test", joined)
        self.assertNotIn("private question", joined)


if __name__ == "__main__":
    unittest.main()
