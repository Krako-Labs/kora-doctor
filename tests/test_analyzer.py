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

    def test_savings_do_not_exceed_observed_cost(self):
        report = analyze(load_records(str(ROOT / "samples/inefficient_agent.jsonl")))
        self.assertLessEqual(report.potential_savings["USD"], report.observed_costs["USD"])


if __name__ == "__main__":
    unittest.main()
