import unittest
from pathlib import Path

from kora_doctor.analyzer import analyze
from kora_doctor.otel import enrich_with_otel
from kora_doctor.parser import load_records


ROOT = Path(__file__).resolve().parents[1]


class OtelTests(unittest.TestCase):
    def test_otel_sidecar_hashes_payloads_and_infers_retry(self):
        records = load_records(str(ROOT / "samples/otel_audr.jsonl"))
        enriched, stats = enrich_with_otel(records, str(ROOT / "samples/otel_sidecar.json"))

        self.assertEqual(stats["matched"], 2)
        self.assertEqual(stats["args_hashed"], 2)
        self.assertEqual(stats["results_hashed"], 2)
        self.assertEqual(stats["statuses"], 2)
        self.assertEqual(stats["inferred_retries"], 1)

        first_labels = enriched[0]["attribution"]["labels"]
        second_labels = enriched[1]["attribution"]["labels"]
        self.assertEqual(first_labels["tool_args_hash"], second_labels["tool_args_hash"])
        self.assertEqual(first_labels["tool_result_hash"], second_labels["tool_result_hash"])
        self.assertEqual(first_labels["operation_status"], "failed")
        self.assertEqual(second_labels["operation_status"], "success")
        self.assertEqual(second_labels["retry_of"], enriched[0]["record_id"])

        report = analyze(enriched)
        retry = [f for f in report.findings if f.category == "retry_overhead"]
        self.assertEqual(len(retry), 1)
        self.assertEqual(retry[0].confidence, "medium")

    def test_otel_conditional_consumer_metadata_is_preserved(self):
        import json
        import tempfile
        records = load_records(str(ROOT / "samples/otel_audr.jsonl"))
        span = {
            "spanId": records[0]["run"]["span_id"],
            "traceId": records[0]["run"].get("trace_id"),
            "attributes": [
                {"key": "gen_ai.output.consumed", "value": {"boolValue": False}},
                {"key": "gen_ai.output.conditional_consumer_exists", "value": {"boolValue": True}},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            sidecar = Path(directory) / "otel.json"
            sidecar.write_text(json.dumps({"spans": [span]}))
            enriched, stats = enrich_with_otel(records, str(sidecar))
        self.assertEqual(stats["matched"], 1)
        self.assertEqual(enriched[0]["attribution"]["labels"]["conditional_consumer_exists"], "true")
        findings = [f for f in analyze(enriched).findings if f.category == "unused_work"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].confidence, "low")

    def test_otel_replanning_labels(self):
        import json
        import tempfile
        records = load_records(str(ROOT / "samples/otel_audr.jsonl"))
        attrs = {
            "plan_before_hash": "action-digest",
            "plan_after_hash": "action-digest",
            "next_action_before_hash": "next-digest",
            "next_action_after_hash": "next-digest",
            "nonempty_result": True,
            "expected_repeat": False,
        }
        span = {"spanId": records[0]["run"]["span_id"], "attributes": attrs}
        with tempfile.TemporaryDirectory() as directory:
            sidecar = Path(directory) / "otel.json"
            sidecar.write_text(json.dumps({"spans": [span]}))
            enriched, _ = enrich_with_otel(records, str(sidecar))
        findings = [f for f in analyze(enriched).findings if f.category == "replanning_loop"]
        self.assertEqual(len(findings), 1)

    def test_otel_sidecar_does_not_persist_raw_payloads_in_labels(self):
        records = load_records(str(ROOT / "samples/otel_audr.jsonl"))
        enriched, _ = enrich_with_otel(records, str(ROOT / "samples/otel_sidecar.json"))
        serialized = str(enriched)
        self.assertNotIn('"customer_id":42', serialized)
        self.assertNotIn('"name":"Ada"', serialized)


if __name__ == "__main__":
    unittest.main()
