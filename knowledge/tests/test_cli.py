"""Exercise the documented local command as a separate process."""

import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]


class CliTests(unittest.TestCase):
    def run_cli(self, *arguments):
        return subprocess.run(
            [sys.executable, "-m", "knowledge", *arguments],
            cwd=ROOT, capture_output=True, text=True, timeout=10,
        )

    def test_material_query_returns_json_and_original_source(self):
        result = self.run_cli("--artwork-id", "aic-28560", "--query", "这件作品用了什么材料？")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        response = json.loads(result.stdout)
        self.assertEqual(response["dataset_version"], "aic-seed-v0.1")
        self.assertEqual([hit["chunk_id"] for hit in response["evidence"]], ["aic-28560-medium"])
        self.assertEqual(response["evidence"][0]["excerpt"], "Oil on canvas")

    def test_empty_result_is_success(self):
        result = self.run_cli("--artwork-id", "aic-11723", "--query", "What is the woman's real name?")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["evidence"], [])

    def test_invalid_request_produces_stderr_and_nonzero_exit(self):
        result = self.run_cli("--artwork-id", "aic-28560", "--query", "material", "--top-k", "0")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(json.loads(result.stderr)["error"]["code"], "invalid_request")

    def test_missing_seed_has_no_silent_fallback(self):
        result = self.run_cli(
            "--seed", str(ROOT / "knowledge/data/does-not-exist.json"),
            "--artwork-id", "aic-28560", "--query", "material",
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(json.loads(result.stderr)["error"]["code"], "invalid_dataset")

    def test_version_pin_rejects_mismatched_seed(self):
        result = self.run_cli(
            "--artwork-id", "aic-28560", "--query", "material",
            "--dataset-version", "aic-seed-v9",
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)["error"]["code"], "invalid_dataset")

    def test_argparse_rejects_missing_required_options(self):
        result = self.run_cli("--artwork-id", "aic-28560")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--query", result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
