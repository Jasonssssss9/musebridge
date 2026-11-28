"""Check the installed decoder without downloading or loading speech weights."""

import importlib.util
from io import BytesIO
import unittest
import wave

from whisper_worker.worker import FasterWhisperRecognizer


@unittest.skipUnless(importlib.util.find_spec("faster_whisper"), "install whisper_worker/requirements.txt for the decoder check")
class RealDecoderTests(unittest.TestCase):
    def test_installed_decoder_reads_pcm_without_a_model(self):
        clip = BytesIO()
        with wave.open(clip, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(b"\x00\x00" * 8000)

        recognizer = FasterWhisperRecognizer(device="cpu", compute_type="int8")
        samples, seconds = recognizer.decode(clip.getvalue())
        self.assertEqual(len(samples), 8000)
        self.assertAlmostEqual(seconds, 0.5)
        self.assertIsNone(recognizer._model)
