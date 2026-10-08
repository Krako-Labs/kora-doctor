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

    def test_otel_sidecar_does_not_persist_raw_payloads_in_labels(self):
        records = load_records(str(ROOT / "samples/otel_audr.jsonl"))
        enriched, _ = enrich_with_otel(records, str(ROOT / "samples/otel_sidecar.json"))
        serialized = str(enriched)
        self.assertNotIn('"customer_id":42', serialized)
        self.assertNotIn('"name":"Ada"', serialized)


if __name__ == "__main__":
    unittest.main()
