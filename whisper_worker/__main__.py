"""Run the private Whisper worker: ``python3 -m whisper_worker``.

Environment (all optional):
    WHISPER_HOST          loopback address to bind (default 127.0.0.1)
    WHISPER_PORT          default 8003
    WHISPER_MODEL         faster-whisper model name (default small.en)
    WHISPER_DEVICE        cpu | auto (default cpu, so the Qwen GPU is never used)
    WHISPER_COMPUTE_TYPE  default int8
"""

import ipaddress
import logging
import os
import signal
import sys
import threading

from .worker import FasterWhisperRecognizer, WhisperServer


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    host = os.environ.get("WHISPER_HOST", "127.0.0.1")
    if host != "localhost":
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            sys.exit("WHISPER_HOST must be a loopback address; keep the worker off the public network.")
    port = int(os.environ.get("WHISPER_PORT", "8003"))
    recognizer = FasterWhisperRecognizer(
        model=os.environ.get("WHISPER_MODEL", "small.en"),
        device=os.environ.get("WHISPER_DEVICE", "cpu"),
        compute_type=os.environ.get("WHISPER_COMPUTE_TYPE", "int8"),
    )
    server = WhisperServer((host, port), recognizer)
    threading.Thread(target=server.load_model, daemon=True).start()
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown).start())
    logging.getLogger("musebridge.whisper").info(
        f'{{"event":"started","address":"{host}:{port}","model":"{recognizer.model_name}"}}'
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
