import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def test_cli_text(self):
        result = subprocess.run(
            [sys.executable, "-m", "kora_doctor", "audit", str(ROOT / "samples/inefficient_agent.jsonl")],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("KORA Doctor", result.stdout)
        self.assertIn("Potentially avoidable", result.stdout)
        self.assertIn("heuristic candidates", result.stdout)

    def test_cli_malformed(self):
        result = subprocess.run(
            [sys.executable, "-m", "kora_doctor", "audit", str(ROOT / "tests/fixtures/malformed.jsonl")],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("error:", result.stderr)


if __name__ == "__main__":
    unittest.main()
