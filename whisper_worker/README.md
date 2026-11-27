# Whisper worker

Private local speech recognition for Talk with Vincent. Go forwards each recording to `POST /recognize`; `GET /ready` reports when the English model has loaded. The worker binds to loopback, keeps clips in memory only and never logs transcripts.

```bash
python3 -m venv .venv-whisper
.venv-whisper/bin/pip install -r whisper_worker/requirements.txt
.venv-whisper/bin/python -m whisper_worker          # from the repository root
python3 -m unittest discover -s whisper_worker/tests -t .
```

The tests use a stand-in recognizer, so they run without the model. See [Vincent voice setup](../docs/vincent-voice-setup.md) for configuration and how Go connects to it.
