import unittest
from pathlib import Path

from kora_doctor.analyzer import analyze
from kora_doctor.parser import load_records


ROOT = Path(__file__).resolve().parents[1]


class AnalyzerTests(unittest.TestCase):
    def test_simple_sample_runs(self):
        report = analyze(load_records(str(ROOT / "samples/simple.jsonl")))
        self.assertEqual(report.records, 2)
        self.assertEqual(report.model_calls, 1)
        self.assertEqual(report.tool_calls, 1)
        self.assertEqual(report.category_counts["smaller_model_candidate"], 0)
        self.assertEqual(report.category_counts["deterministic_candidate"], 0)

    def test_inefficient_sample_surfaces_core_categories(self):
        report = analyze(load_records(str(ROOT / "samples/inefficient_agent.jsonl")))
        self.assertGreaterEqual(report.category_counts["duplicate_repeated"], 3)
        self.assertGreaterEqual(report.category_counts["deterministic_candidate"], 1)
        self.assertGreaterEqual(report.category_counts["orchestration_overhead"], 1)
        self.assertGreater(report.potential_savings["USD"], 0)


    def test_harness_waste_sample_surfaces_user_requested_signals(self):
        report = analyze(load_records(str(ROOT / "samples/harness_waste.jsonl")))
        self.assertGreaterEqual(report.category_counts["repeated_tool_retrieval"], 1)
        self.assertGreaterEqual(report.category_counts["context_amplification"], 1)
        self.assertGreaterEqual(report.category_counts["deterministic_candidate"], 1)

        titles = [finding.title for finding in report.findings]
        self.assertTrue(any("repeated tool/read" in title for title in titles))
        self.assertIn("LLM used for deterministic validation", titles)
        self.assertTrue(any("Input context amplified" in title for title in titles))

    def test_repeated_tool_is_candidate_not_high_confidence(self):
        report = analyze(load_records(str(ROOT / "samples/harness_waste.jsonl")))
        findings = [
            finding for finding in report.findings
            if finding.category == "repeated_tool_retrieval"
        ]
        self.assertTrue(findings)
        self.assertTrue(all(finding.confidence == "low" for finding in findings))

    def test_savings_do_not_exceed_observed_cost(self):
        report = analyze(load_records(str(ROOT / "samples/inefficient_agent.jsonl")))
        self.assertLessEqual(report.potential_savings["USD"], report.observed_costs["USD"])


if __name__ == "__main__":
    unittest.main()
