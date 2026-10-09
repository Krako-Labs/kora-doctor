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


    def test_cache_metrics_are_reported_from_audr_counters(self):
        report = analyze(load_records(str(ROOT / "samples/cache_instability.jsonl")))
        self.assertEqual(report.cache_metrics["reported_calls"], 3)
        self.assertEqual(report.cache_metrics["uncached_input_tokens"], 9600)
        self.assertEqual(report.cache_metrics["cache_read_tokens"], 0)
        self.assertEqual(report.cache_metrics["cache_write_tokens"], 3000)
        self.assertEqual(report.cache_metrics["read_share_percent"], 0.0)

    def test_zero_cache_reads_on_high_context_run_is_flagged_conservatively(self):
        report = analyze(load_records(str(ROOT / "samples/cache_instability.jsonl")))
        findings = [
            finding for finding in report.findings
            if finding.title == "No prompt-cache reads reported across high-context run"
        ]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].confidence, "low")
        self.assertEqual(findings[0].saving_ratio, 0.0)


    def test_matching_tool_fingerprints_raise_repeat_confidence(self):
        report = analyze(load_records(str(ROOT / "samples/tool_fingerprints.jsonl")))
        findings = [
            finding for finding in report.findings
            if finding.category == "repeated_tool_retrieval"
        ]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].confidence, "medium")
        self.assertIn("exact repeated tool call", findings[0].title)
        self.assertEqual(len(findings[0].record_ids), 1)

    def test_distinct_argument_fingerprints_are_not_called_duplicates(self):
        report = analyze(load_records(str(ROOT / "tests/fixtures/tool_fingerprints_distinct.jsonl")))
        findings = [
            finding for finding in report.findings
            if finding.category == "repeated_tool_retrieval"
        ]
        self.assertEqual(findings, [])

    def test_fingerprint_findings_do_not_claim_savings(self):
        report = analyze(load_records(str(ROOT / "samples/tool_fingerprints.jsonl")))
        findings = [
            finding for finding in report.findings
            if finding.category == "repeated_tool_retrieval"
        ]
        self.assertTrue(findings)
        self.assertTrue(all(finding.saving_ratio == 0.0 for finding in findings))


    def test_explicit_retry_lineage_with_same_args_is_detected(self):
        report = analyze(load_records(str(ROOT / "samples/retry_lineage.jsonl")))
        findings = [
            finding for finding in report.findings
            if finding.category == "retry_overhead"
        ]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].confidence, "medium")
        self.assertIn("same failed/unknown tool call", findings[0].title)
        self.assertEqual(findings[0].saving_ratio, 0.0)

    def test_retry_lineage_is_separate_from_plain_repeat_detection(self):
        report = analyze(load_records(str(ROOT / "samples/retry_lineage.jsonl")))
        retry_ids = {
            rid
            for finding in report.findings
            if finding.category == "retry_overhead"
            for rid in finding.record_ids
        }
        self.assertIn("01KD6000000000000000000002", retry_ids)


    def test_explicit_dead_planning_and_unconsumed_output_are_detected(self):
        report = analyze(load_records(str(ROOT / "samples/unused_work.jsonl")))
        findings = [
            finding for finding in report.findings
            if finding.category == "unused_work"
        ]
        self.assertEqual(len(findings), 2)
        titles = {finding.title for finding in findings}
        self.assertIn("Planner produced 3 unexecuted step(s)", titles)
        self.assertIn("Output explicitly marked unconsumed", titles)
        self.assertEqual({f.confidence for f in findings}, {"medium", "low"})
        self.assertTrue(all(finding.saving_ratio == 0.0 for finding in findings))

    def test_conditional_consumer_is_not_proven_dead_work(self):
        import copy
        base = load_records(str(ROOT / "samples/unused_work.jsonl"))
        conditional = copy.deepcopy(base[1])
        conditional["attribution"]["labels"]["conditional_consumer_exists"] = "true"
        report = analyze([conditional])
        finding = next(f for f in report.findings if f.category == "unused_work")
        self.assertEqual(finding.confidence, "low")
        self.assertIn("Conditional", finding.title)
        self.assertIn("not proof of dead work", finding.reason)
        self.assertEqual(finding.saving_ratio, 0)

    def test_explicit_no_consumer_requires_evidence_for_medium_confidence(self):
        import copy
        base = load_records(str(ROOT / "samples/unused_work.jsonl"))
        uncertain = analyze([base[1]])
        self.assertEqual(next(f for f in uncertain.findings if f.category == "unused_work").confidence, "low")
        proven = copy.deepcopy(base[1])
        proven["attribution"]["labels"]["no_downstream_consumer"] = "true"
        report = analyze([proven])
        finding = next(f for f in report.findings if f.category == "unused_work")
        self.assertEqual(finding.confidence, "medium")
        self.assertIn("no downstream consumer", finding.title)

    def test_conditional_consumers_sample_is_actionable(self):
        report = analyze(load_records(str(ROOT / "samples/conditional_consumers.jsonl")))
        findings = [f for f in report.findings if f.category == "unused_work"]
        self.assertEqual(len(findings), 3)
        self.assertEqual(sum("Conditional" in f.title for f in findings), 1)
        self.assertEqual(sum(f.confidence == "medium" for f in findings), 1)
        self.assertTrue(all(f.saving_ratio == 0 for f in findings))

    def test_replanning_requires_explicit_exclusion_of_intentional_repeats(self):
        report = analyze(load_records(str(ROOT / "samples/replanning_loops.jsonl")))
        findings = [f for f in report.findings if f.category == "replanning_loop"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].confidence, "medium")
        self.assertEqual(report.category_counts["replanning_loop"], 1)
        self.assertEqual(findings[0].saving_ratio, 0)

    def test_savings_do_not_exceed_observed_cost(self):
        report = analyze(load_records(str(ROOT / "samples/inefficient_agent.jsonl")))
        self.assertLessEqual(report.potential_savings["USD"], report.observed_costs["USD"])


if __name__ == "__main__":
    unittest.main()
