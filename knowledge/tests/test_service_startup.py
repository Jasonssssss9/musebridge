"""Verify C-service startup fails closed and honors deployment configuration."""

import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from knowledge import DatasetValidationError
from knowledge.http_service import create_server


ROOT = Path(__file__).resolve().parents[2]
SEED_PATH = ROOT / "knowledge" / "data" / "aic-seed-v0.1.json"
VERSION = "aic-seed-v0.1"
CONFIG_KEYS = ("HOST", "PORT", "SEED_PATH", "DATASET_VERSION")


class ServiceStartupTests(unittest.TestCase):
    def environment(self, **overrides):
        environment = os.environ.copy()
        for key in CONFIG_KEYS:
            environment.pop(key, None)
        environment.update(overrides)
        return environment

    def test_invalid_seed_or_version_is_rejected_before_binding(self):
        # An occupied port would raise OSError if the server bound before validation.
        with tempfile.TemporaryDirectory() as directory, socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            port = occupied.getsockname()[1]
            missing = Path(directory) / "missing.json"
            malformed = Path(directory) / "malformed.json"
            malformed.write_text("{}", encoding="utf-8")
            cases = (
                (missing, VERSION),
                (malformed, VERSION),
                (SEED_PATH, "aic-seed-v9"),
            )
            for seed_path, version in cases:
                with self.subTest(seed_path=seed_path, version=version):
                    with self.assertRaises(DatasetValidationError):
                        create_server(
                            host="127.0.0.1",
                            port=port,
                            seed_path=seed_path,
                            dataset_version=version,
                        )

    def test_invalid_environment_configuration_exits_without_serving(self):
        cases = (
            {"SEED_PATH": str(ROOT / "knowledge/data/missing-seed.json")},
            {"DATASET_VERSION": "aic-seed-v9"},
            {"PORT": "not-a-port"},
            {"PORT": "0"},
            {"PORT": "65536"},
        )
        for configuration in cases:
            with self.subTest(configuration=configuration):
                result = subprocess.run(
                    [sys.executable, "-m", "knowledge.http_service"],
                    cwd=ROOT,
                    env=self.environment(**configuration),
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertTrue(result.stderr.strip())
                self.assertNotIn("Traceback (most recent call last)", result.stderr)

    def test_cli_flags_override_environment_and_serve_readiness(self):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]

        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "knowledge.http_service",
                "--host", "127.0.0.1",
                "--port", str(port),
                "--seed", str(SEED_PATH),
                "--dataset-version", VERSION,
            ],
            cwd=ROOT,
            env=self.environment(
                HOST="invalid.invalid",
                PORT="not-a-port",
                SEED_PATH=str(ROOT / "knowledge/data/missing-seed.json"),
                DATASET_VERSION="aic-seed-v9",
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        response_status = None
        response_body = None
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    stdout, stderr = process.communicate()
                    self.fail(
                        f"Service exited before readiness: code={process.returncode}, "
                        f"stdout={stdout!r}, stderr={stderr!r}"
                    )
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.3)
                try:
                    connection.request("GET", "/readyz")
                    response = connection.getresponse()
                    response_status = response.status
                    response_body = response.read()
                    break
                except (ConnectionError, OSError, http.client.HTTPException):
                    time.sleep(0.05)
                finally:
                    connection.close()
            self.assertEqual(response_status, 200, "Service never became ready.")
            self.assertEqual(
                json.loads(response_body),
                {"status": "ok", "dataset_version": VERSION},
            )
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=3)


if __name__ == "__main__":
    unittest.main()
