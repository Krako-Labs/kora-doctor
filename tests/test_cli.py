import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def test_demo_without_user_trace_is_explicitly_synthetic(self):
        result = subprocess.run(
            [sys.executable, "-m", "kora_doctor", "demo"],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SYNTHETIC DEMO", result.stdout)
        self.assertIn("7 records", result.stdout)
        self.assertIn("not measured", result.stdout)

    def test_demo_json_preserves_synthetic_warning_and_savings_disclaimer(self):
        import json

        result = subprocess.run(
            [sys.executable, "-m", "kora_doctor", "demo", "--json"],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["records"], 7)
        self.assertTrue(any("SYNTHETIC DEMO" in note for note in report["warnings"]))
        self.assertEqual(report["savings_basis"], "illustrative_heuristic_not_measured")

    def test_cli_text(self):
        result = subprocess.run(
            [sys.executable, "-m", "kora_doctor", "audit", str(ROOT / "samples/inefficient_agent.jsonl")],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("KORA Doctor", result.stdout)
        self.assertIn("Illustrative savings", result.stdout)
        self.assertIn("Illustrative remainder", result.stdout)
        self.assertIn("not measured", result.stdout)
        self.assertIn("Do not remove calls", result.stdout)
        self.assertNotIn("Estimated optimized cost", result.stdout)
        self.assertIn("Fix execution waste first", result.stdout)

    def test_cli_observed_run_count_uses_correct_singular_and_plural(self):
        for fixture, expected in (("harness_waste.jsonl", "1 run ·"),
                                  ("inefficient_agent.jsonl", "2 runs ·")):
            with self.subTest(fixture=fixture):
                result = subprocess.run(
                    [sys.executable, "-m", "kora_doctor", "audit",
                     str(ROOT / "samples" / fixture)],
                    cwd=ROOT, text=True, capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(expected, result.stdout)

    def test_json_savings_are_explicitly_hypothetical_without_changing_existing_keys(self):
        import json

        result = subprocess.run(
            [sys.executable, "-m", "kora_doctor", "audit",
             str(ROOT / "samples/harness_waste.jsonl"), "--json"],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertGreater(report["potential_savings"]["USD"], 0)
        self.assertIn("estimated_optimized_costs", report)
        self.assertEqual(report["savings_basis"], "illustrative_heuristic_not_measured")

    def test_cli_reports_otel_conflict_without_overriding_audr(self):
        import json
        import tempfile

        records = [
            json.loads(line)
            for line in (ROOT / "samples/otel_audr.jsonl").read_text().splitlines()
            if line.strip()
        ]
        records[0]["attribution"]["labels"]["operation_status"] = "success"
        span = {
            "spanId": records[0]["run"]["span_id"],
            "traceId": records[0]["run"]["trace_id"],
            "status": {"code": "STATUS_CODE_ERROR"},
            "attributes": {"gen_ai.tool.call.arguments": "private customer payload"},
        }
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "audr.json"
            otel_path = Path(directory) / "otel.json"
            input_path.write_text(json.dumps(records))
            otel_path.write_text(json.dumps({"spans": [span]}))
            result = subprocess.run(
                [sys.executable, "-m", "kora_doctor", "audit",
                 str(input_path), "--otel", str(otel_path), "--json"],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OTel source conflict: 1 record(s) skipped", result.stdout)
        self.assertNotIn("private customer payload", result.stdout)
        report = json.loads(result.stdout)
        self.assertTrue(any("conflict" in warning.lower() for warning in report["warnings"]))

    def test_cli_rejects_malformed_otel_scalars_and_deep_json_privately(self):
        import json
        import tempfile

        records_path = str(ROOT / "samples/otel_audr.jsonl")
        malformed_scalar = {"spans": [{
            "spanId": "ffffffffffffffff",
            "attributes": [{"key": "gen_ai.tool.call.arguments",
                            "value": {"boolValue": "private customer value"}}],
        }]}
        too_deep = {"spans": [{
            "attributes": {"gen_ai.tool.call.arguments": {"private": "depth-private-sentinel"}},
        }]}
        node = too_deep["spans"][0]["attributes"]["gen_ai.tool.call.arguments"]
        for _ in range(150):
            node = {"child": node}
        too_deep["spans"][0]["attributes"]["gen_ai.tool.call.arguments"] = node
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid-otel.json"
            for name, payload in (("scalar", malformed_scalar), ("depth", too_deep)):
                with self.subTest(case=name):
                    path.write_text(json.dumps(payload))
                    completed = subprocess.run(
                        [sys.executable, "-m", "kora_doctor", "audit", records_path,
                         "--otel", str(path), "--json"],
                        cwd=ROOT, capture_output=True, text=True,
                    )
                    self.assertEqual(completed.returncode, 2, completed.stderr)
                    self.assertEqual(completed.stdout, "")
                    self.assertIn("invalid OTel", completed.stderr)
                    self.assertNotIn("private customer value", completed.stderr)
                    self.assertNotIn("depth-private-sentinel", completed.stderr)
                    self.assertNotIn("Traceback", completed.stderr)

    def test_cli_duplicate_record_id_cannot_misattribute_savings(self):
        import json
        import tempfile

        records = [json.loads(line) for line in
                   (ROOT / "samples/harness_waste.jsonl").read_text().splitlines()
                   if line.strip()]
        # The first call has a 90% illustrative saving ratio. Reusing its ID
        # on a later high-cost call previously applied that ratio to $100.
        private_id = "private-customer-record-id-42"
        records[3]["record_id"] = private_id
        records[-1]["record_id"] = private_id
        records[-1]["run"] = dict(records[-1]["run"],
                                  run_id="different-run", trace_id="different-trace")
        records[-1]["cost"] = {"total_cost": 100.0, "currency": "USD"}
        records[-1]["resource"]["name"] = "private-customer-resource"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.jsonl"
            path.write_text("\n".join(json.dumps(record) for record in records))
            result = subprocess.run(
                [sys.executable, "-m", "kora_doctor", "audit", str(path), "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("duplicate record_id", result.stderr)
        self.assertNotIn("private-customer-resource", result.stderr)
        self.assertNotIn(private_id, result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_cli_invalid_utf8_is_a_safe_input_error(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            invalid_audr = Path(directory) / "invalid-audr.jsonl"
            invalid_otel = Path(directory) / "invalid-otel.json"
            invalid_audr.write_bytes(bytes([0xFF, 0xFE, 0x00]))
            invalid_otel.write_bytes(bytes([0xFF, 0xFE, 0x00]))

            for name, args, expected in (
                (
                    "AUDR",
                    ["audit", str(invalid_audr)],
                    "input must be UTF-8 encoded",
                ),
                (
                    "OTel",
                    ["audit", str(ROOT / "samples/otel_audr.jsonl"),
                     "--otel", str(invalid_otel)],
                    "OTel sidecar must be UTF-8 encoded",
                ),
            ):
                with self.subTest(source=name):
                    result = subprocess.run(
                        [sys.executable, "-m", "kora_doctor", *args],
                        cwd=ROOT,
                        text=True,
                        capture_output=True,
                    )
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn(expected, result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertNotIn("UnicodeDecodeError", result.stderr)
                    self.assertEqual(result.stdout, "")

    def test_cli_rejects_invalid_attribution_shape_without_traceback(self):
        import json
        import tempfile

        sample = json.loads(
            (ROOT / "samples/otel_audr.jsonl").read_text().splitlines()[0]
        )
        sample["attribution"] = ["not", "an", "object"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid-shape.json"
            path.write_text(json.dumps(sample))
            result = subprocess.run(
                [sys.executable, "-m", "kora_doctor", "audit", str(path)],
                cwd=ROOT, text=True, capture_output=True,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("attribution must be an object", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_cli_malformed_otel_container_is_a_safe_input_error(self):
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            sidecar = Path(directory) / "malformed-otel.json"
            sidecar.write_text(json.dumps({
                "resourceSpans": [{"scopeSpans": [{"spans": 123}]}],
                "private": "customer-secret",
            }))
            result = subprocess.run(
                [sys.executable, "-m", "kora_doctor", "audit",
                 str(ROOT / "samples/otel_audr.jsonl"),
                 "--otel", str(sidecar), "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("invalid OTel structure", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("customer-secret", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_cli_rejects_malformed_nested_otel_anyvalue_privately(self):
        import json
        import tempfile

        records = (ROOT / "samples/otel_audr.jsonl")
        source = json.loads(records.read_text().splitlines()[0])
        span = {
            "spanId": source["run"]["span_id"],
            "traceId": source["run"]["trace_id"],
            "attributes": [
                {"key": "gen_ai.tool.call.arguments", "value": {
                    "kvlistValue": {"values": {"sensitive": "customer-secret"}}}}
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            otel = Path(directory) / "malformed-anyvalue.json"
            otel.write_text(json.dumps({"spans": [span]}))
            result = subprocess.run(
                [sys.executable, "-m", "kora_doctor", "audit",
                 str(records), "--otel", str(otel), "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("invalid OTel attributes", result.stderr)
        self.assertNotIn("customer-secret", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_cli_rejects_nonfinite_audr_numbers_without_raw_payload(self):
        import json
        import tempfile

        record = json.loads((ROOT / "samples/simple.jsonl").read_text().splitlines()[0])
        # The exporter uses a sensitive resource name. It must not appear in errors.
        record["resource"]["name"] = "private-customer-resource"
        valid = json.dumps(record)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid-audr.json"
            for number in ("NaN", "Infinity", "1e999"):
                with self.subTest(number=number):
                    path.write_text(valid.replace('"input_tokens": 500', '"input_tokens": ' + number))
                    result = subprocess.run(
                        [sys.executable, "-m", "kora_doctor", "audit", str(path), "--json"],
                        cwd=ROOT, text=True, capture_output=True,
                    )
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(result.stdout, "")
                    self.assertIn("non-finite value", result.stderr)
                    self.assertNotIn("private-customer-resource", result.stderr)
                    self.assertNotIn("Traceback", result.stderr)

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
