import copy
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

    def test_context_amplification_exposes_local_and_baseline_growth_shape(self):
        report = analyze(load_records(str(ROOT / "samples/harness_waste.jsonl")))
        findings = [
            finding for finding in report.findings
            if finding.category == "context_amplification"
            and finding.title.startswith("Input context amplified")
        ]
        self.assertEqual(len(findings), 1)
        reason = findings[0].reason
        self.assertIn("vs the first reported call", reason)
        self.assertIn("largest step-to-step delta", reason)
        self.assertIn("sharp local jump", reason)
        self.assertIn("not proof that a particular payload was unnecessary", reason)
        self.assertEqual(findings[0].saving_ratio, 0.0)
        self.assertEqual(findings[0].confidence, "low")

    def test_context_amplification_surfaces_steady_cumulative_growth_without_local_jump(self):
        base = load_records(str(ROOT / "samples/harness_waste.jsonl"))
        records = [
            copy.deepcopy(record)
            for record in base
            if record.get("resource", {}).get("type") == "model"
        ]
        tokens = [800, 1100, 1400, 1700, 2000]
        for index, (record, input_tokens) in enumerate(zip(records, tokens), start=1):
            record["run"]["run_id"] = "steady-context-growth"
            record["run"]["step"] = index
            record["usage"]["llm"]["input_tokens"] = input_tokens
            record["record_id"] = f"steady-{index}"

        report = analyze(records)
        findings = [
            finding for finding in report.findings
            if finding.category == "context_amplification"
            and finding.title == "Input context accumulated across the run"
        ]
        self.assertEqual(len(findings), 1)
        reason = findings[0].reason
        self.assertIn("no single transition", reason)
        self.assertIn("steady cumulative growth", reason)
        self.assertIn("largest step-to-step delta was 300 tokens", reason)
        self.assertIn("not proof that a particular payload was unnecessary", reason)
        self.assertEqual(findings[0].confidence, "low")
        self.assertEqual(findings[0].saving_ratio, 0.0)

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

    def test_repeated_tool_is_scoped_to_matching_trace_identity(self):
        source = load_records(str(ROOT / "samples/tool_fingerprints.jsonl"))[:2]
        for first_trace, second_trace, expected in (
            ("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", 0),
            ("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", None, 0),
            ("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", 1),
        ):
            with self.subTest(first_trace=first_trace, second_trace=second_trace):
                records = copy.deepcopy(source)
                records[0]["run"]["trace_id"] = first_trace
                if second_trace is not None:
                    records[1]["run"]["trace_id"] = second_trace
                report = analyze(records)
                self.assertEqual(report.category_counts["repeated_tool_retrieval"], expected)

    def test_distinct_argument_fingerprints_are_not_called_duplicates(self):
        report = analyze(load_records(str(ROOT / "tests/fixtures/tool_fingerprints_distinct.jsonl")))
        findings = [
            finding for finding in report.findings
            if finding.category == "repeated_tool_retrieval"
        ]
        self.assertEqual(findings, [])

    def test_mixed_fingerprints_do_not_flag_known_distinct_reads(self):
        records = copy.deepcopy(load_records(str(ROOT / "tests/fixtures/tool_fingerprints_distinct.jsonl")))
        records[2]["attribution"]["labels"].pop("tool_args_hash")
        findings = [f for f in analyze(records).findings if f.category == "repeated_tool_retrieval"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].record_ids, [records[2]["record_id"]])
        self.assertEqual(findings[0].confidence, "low")
        self.assertEqual(findings[0].saving_ratio, 0.0)

    def test_mixed_fingerprints_with_missing_middle_preserves_distinct_last(self):
        records = copy.deepcopy(load_records(str(ROOT / "tests/fixtures/tool_fingerprints_distinct.jsonl")))
        records[1]["attribution"]["labels"].pop("tool_args_hash")
        findings = [f for f in analyze(records).findings if f.category == "repeated_tool_retrieval"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].record_ids, [records[1]["record_id"]])

    def test_explicit_retry_of_successful_retrieval_is_candidate_not_proof(self):
        records = copy.deepcopy(load_records(str(ROOT / "samples/retry_lineage.jsonl")))
        records[0]["attribution"]["labels"]["operation_status"] = "success"
        report = analyze(records)
        findings = [f for f in report.findings if f.category == "retry_overhead"]
        self.assertEqual(len(findings), 1)
        self.assertIn("previously successful", findings[0].title)
        self.assertEqual(findings[0].confidence, "medium")
        self.assertEqual(findings[0].saving_ratio, 0.0)

    def test_span_id_collision_prefers_retried_record_in_same_run(self):
        records = copy.deepcopy(load_records(str(ROOT / "samples/retry_lineage.jsonl")))
        records[0]["attribution"]["labels"]["operation_status"] = "success"
        records[1]["attribution"]["labels"]["retry_of"] = records[0]["run"]["span_id"]
        unrelated = copy.deepcopy(records[0])
        unrelated["record_id"] = "unrelated-colliding-span"
        unrelated["run"]["run_id"] = "different-run"
        unrelated["attribution"]["labels"]["operation_status"] = "timeout"
        records.append(unrelated)
        report = analyze(records)
        findings = [f for f in report.findings if f.category == "retry_overhead"]
        self.assertEqual(len(findings), 1)
        self.assertIn("previously successful", findings[0].title)

    def _rate_limit_attempts(self):
        base = load_records(str(ROOT / "samples/retry_lineage.jsonl"))[0]
        records = []
        for i, status in enumerate([429, 429, 429, 200]):
            event = copy.deepcopy(base)
            event["record_id"] = f"rate-limit-attempt-{i + 1}"
            event["run"]["span_id"] = f"rate-limit-span-{i + 1}"
            event["run"]["step"] = i + 1
            event["timing"]["event_time"] = f"2026-10-08T02:00:0{i + 1}.000Z"
            labels = event["attribution"]["labels"]
            labels["http_status_code"] = str(status)
            labels["operation_status"] = "failed" if status == 429 else "success"
            if i > 0:
                labels["retry_of"] = records[i - 1]["record_id"]
                labels["retry_attempt"] = str(i + 1)
            records.append(event)
        return records

    def test_http_429_chain_counts_one_operation_not_four_failures(self):
        records = self._rate_limit_attempts()
        report = analyze(records)
        findings = [f for f in report.findings if f.category == "rate_limit_retry_chain"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(report.category_counts["rate_limit_retry_chain"], 1)
        self.assertEqual(findings[0].record_ids, [r["record_id"] for r in records])
        self.assertIn("4 attempts, 3 rate-limit", findings[0].title)
        self.assertIn("eventual HTTP success", findings[0].reason)
        self.assertIn("does not prove a header conflict", findings[0].reason)
        self.assertEqual(findings[0].saving_ratio, 0.0)
        self.assertEqual(findings[0].confidence, "medium")

    def test_http_429_retries_are_not_counted_as_redundant_tool_reads(self):
        records = self._rate_limit_attempts()
        report = analyze(records)
        self.assertEqual(report.category_counts["rate_limit_retry_chain"], 1)
        self.assertEqual(report.category_counts["repeated_tool_retrieval"], 0)
        self.assertFalse([
            finding for finding in report.findings
            if finding.category == "repeated_tool_retrieval"
        ])

    def test_http_429_chain_preserves_unrelated_same_argument_repeat(self):
        records = self._rate_limit_attempts()
        extra = copy.deepcopy(records[-1])
        extra["record_id"] = "independent-follow-up"
        extra["run"]["span_id"] = "independent-follow-up-span"
        extra["run"]["step"] = 5
        extra["timing"]["event_time"] = "2026-10-08T02:00:05.000Z"
        extra["attribution"]["labels"].pop("retry_of")
        extra["attribution"]["labels"].pop("retry_attempt")
        records.append(extra)

        report = analyze(records)
        repeats = [f for f in report.findings if f.category == "repeated_tool_retrieval"]
        self.assertEqual(report.category_counts["rate_limit_retry_chain"], 1)
        self.assertEqual(report.category_counts["repeated_tool_retrieval"], 1)
        self.assertEqual(len(repeats), 1)
        self.assertEqual(repeats[0].record_ids, ["independent-follow-up"])
        self.assertIn("1 same-argument", repeats[0].title)
        self.assertEqual(repeats[0].saving_ratio, 0)

    def test_429_without_explicit_retry_chain_retains_low_confidence_repeat_signal(self):
        records = self._rate_limit_attempts()
        for record in records[1:]:
            record["attribution"]["labels"].pop("retry_of")
            record["attribution"]["labels"].pop("retry_attempt")
        report = analyze(records)
        self.assertEqual(report.category_counts["rate_limit_retry_chain"], 0)
        self.assertEqual(report.category_counts["repeated_tool_retrieval"], 3)

    def test_http_429_conflicting_reported_success_is_low_confidence(self):
        records = self._rate_limit_attempts()
        records[1]["attribution"]["labels"]["operation_status"] = "success"
        findings = [f for f in analyze(records).findings if f.category == "rate_limit_retry_chain"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].confidence, "low")
        self.assertIn("conflicting HTTP and operation statuses", findings[0].reason)
        self.assertIn("final outcome not established", findings[0].reason)
        self.assertEqual(findings[0].saving_ratio, 0.0)

    def test_http_200_with_reported_failure_does_not_certify_success(self):
        records = self._rate_limit_attempts()
        records[-1]["attribution"]["labels"]["operation_status"] = "failed"
        findings = [f for f in analyze(records).findings if f.category == "rate_limit_retry_chain"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].confidence, "low")
        self.assertIn("final outcome not established", findings[0].reason)
        self.assertNotIn("eventual HTTP success", findings[0].reason)

    def test_http_429_without_explicit_retry_links_is_not_a_chain(self):
        records = self._rate_limit_attempts()
        for record in records[1:]:
            record["attribution"]["labels"].pop("retry_of")
            record["attribution"]["labels"].pop("retry_attempt")
        report = analyze(records)
        self.assertEqual(report.category_counts["rate_limit_retry_chain"], 0)

    def test_http_429_branch_is_not_assumed_to_be_one_serial_operation(self):
        records = self._rate_limit_attempts()
        records[2]["attribution"]["labels"]["retry_of"] = records[0]["record_id"]
        report = analyze(records)
        self.assertEqual(report.category_counts["rate_limit_retry_chain"], 0)

    def test_http_429_cross_run_link_is_not_merged(self):
        records = self._rate_limit_attempts()[:2]
        records[0]["run"]["run_id"] = "different-run"
        self.assertEqual(analyze(records).category_counts["rate_limit_retry_chain"], 0)

    def test_http_429_chain_missing_last_status_keeps_outcome_conservative(self):
        records = self._rate_limit_attempts()
        records[-1]["attribution"]["labels"].pop("http_status_code")
        records[-1]["attribution"]["labels"].pop("operation_status")
        findings = [f for f in analyze(records).findings if f.category == "rate_limit_retry_chain"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].confidence, "low")
        self.assertIn("final outcome not established", findings[0].reason)

    def test_explicit_retry_rejects_different_trace_with_reused_run_id(self):
        records = copy.deepcopy(load_records(str(ROOT / "samples/retry_lineage.jsonl")))
        records[0]["run"]["trace_id"] = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        records[1]["run"]["trace_id"] = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        findings = [f for f in analyze(records).findings if f.category == "retry_overhead"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].confidence, "low")
        self.assertIn("without resolvable prior", findings[0].title)

    def test_explicit_retry_rejects_mixed_known_and_missing_trace_id(self):
        records = copy.deepcopy(load_records(str(ROOT / "samples/retry_lineage.jsonl")))
        records[0]["run"]["trace_id"] = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        findings = [f for f in analyze(records).findings if f.category == "retry_overhead"]
        self.assertEqual(len(findings), 1)
        self.assertIn("without resolvable prior", findings[0].title)

    def test_explicit_retry_accepts_matching_trace_ids(self):
        records = copy.deepcopy(load_records(str(ROOT / "samples/retry_lineage.jsonl")))
        for record in records[:2]:
            record["run"]["trace_id"] = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        findings = [f for f in analyze(records).findings if f.category == "retry_overhead"]
        self.assertEqual(len(findings), 1)
        self.assertIn("same failed/unknown tool call", findings[0].title)

    def test_retry_record_id_from_different_run_is_unresolved(self):
        records = copy.deepcopy(load_records(str(ROOT / "samples/retry_lineage.jsonl")))
        records[0]["run"]["run_id"] = "other-run"
        # The retry points to a real record ID but not one in its run.
        findings = [f for f in analyze(records).findings if f.category == "retry_overhead"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].confidence, "low")
        self.assertIn("without resolvable prior", findings[0].title)
        self.assertEqual(findings[0].saving_ratio, 0.0)

    def test_retry_span_id_from_different_run_is_unresolved(self):
        records = copy.deepcopy(load_records(str(ROOT / "samples/retry_lineage.jsonl")))
        records[1]["attribution"]["labels"]["retry_of"] = records[0]["run"]["span_id"]
        records[0]["run"]["run_id"] = "other-run"
        findings = [f for f in analyze(records).findings if f.category == "retry_overhead"]
        self.assertEqual(len(findings), 1)
        self.assertIn("without resolvable prior", findings[0].title)

    def test_duplicate_prior_record_id_is_not_arbitrarily_resolved(self):
        records = copy.deepcopy(load_records(str(ROOT / "samples/retry_lineage.jsonl")))
        other = copy.deepcopy(records[0])
        other["run"]["span_id"] = "other-span"
        records.append(other)
        findings = [f for f in analyze(records).findings if f.category == "retry_overhead"]
        self.assertEqual(len(findings), 1)
        self.assertIn("without resolvable prior", findings[0].title)

    def test_retry_reference_to_future_record_is_unresolved(self):
        records = copy.deepcopy(load_records(str(ROOT / "samples/retry_lineage.jsonl")))
        records[0]["timing"]["event_time"] = "2026-10-08T02:00:04.000Z"
        findings = [f for f in analyze(records).findings if f.category == "retry_overhead"]
        self.assertEqual(len(findings), 1)
        self.assertIn("without resolvable prior", findings[0].title)

    def test_record_id_from_another_run_does_not_match_same_run_span(self):
        records = copy.deepcopy(load_records(str(ROOT / "samples/retry_lineage.jsonl")))
        cross_run = copy.deepcopy(records[0])
        cross_run["record_id"] = "ambiguous-link"
        cross_run["run"]["run_id"] = "other-run"
        records[0]["run"]["span_id"] = "ambiguous-link"
        records[1]["attribution"]["labels"]["retry_of"] = "ambiguous-link"
        records.append(cross_run)
        findings = [f for f in analyze(records).findings if f.category == "retry_overhead"]
        self.assertEqual(len(findings), 1)
        self.assertIn("without resolvable prior", findings[0].title)

    def test_fingerprint_findings_do_not_claim_savings(self):
        report = analyze(load_records(str(ROOT / "samples/tool_fingerprints.jsonl")))
        findings = [
            finding for finding in report.findings
            if finding.category == "repeated_tool_retrieval"
        ]
        self.assertTrue(findings)
        self.assertTrue(all(finding.saving_ratio == 0.0 for finding in findings))


    def test_downstream_failure_alone_does_not_prove_chain_retry(self):
        records = copy.deepcopy(load_records(str(ROOT / "samples/retry_lineage.jsonl")))
        records[0]["attribution"]["labels"]["operation_status"] = "success"
        records[1]["resource"]["name"] = "downstream_action"
        records[1]["attribution"]["labels"]["operation_status"] = "failed"
        records[1]["attribution"]["labels"].pop("retry_of")
        records[1]["attribution"]["labels"].pop("retry_attempt")
        records[2]["attribution"]["labels"]["tool_args_hash"] = records[0]["attribution"]["labels"]["tool_args_hash"]
        report = analyze(records)
        self.assertTrue([f for f in report.findings if f.category == "repeated_tool_retrieval"])
        self.assertFalse([f for f in report.findings if f.category == "retry_overhead"])

    def test_span_id_ambiguous_across_runs_is_unresolved(self):
        records = copy.deepcopy(load_records(str(ROOT / "samples/retry_lineage.jsonl")))
        records[0]["run"]["run_id"] = "other-run-a"
        records[1]["attribution"]["labels"]["retry_of"] = records[0]["run"]["span_id"]
        unrelated = copy.deepcopy(records[0])
        unrelated["record_id"] = "other-run-b-same-span"
        unrelated["run"]["run_id"] = "other-run-b"
        records.append(unrelated)
        findings = [f for f in analyze(records).findings if f.category == "retry_overhead"]
        self.assertEqual(len(findings), 1)
        self.assertIn("without resolvable prior", findings[0].title)
        self.assertEqual(findings[0].confidence, "low")

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

    def test_freshness_flags_only_observed_consecutive_same_snapshots(self):
        records = load_records(str(ROOT / "samples/freshness_refresh.jsonl"))
        report = analyze(records)
        findings = [f for f in report.findings if f.category == "freshness_refresh"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(len(findings[0].record_ids), 3)
        self.assertEqual(findings[0].confidence, "low")
        self.assertEqual(findings[0].saving_ratio, 0)
        self.assertEqual(report.category_counts["freshness_refresh"], 3)

    def test_freshness_rejects_missing_evidence_and_distinct_source(self):
        import copy
        records = load_records(str(ROOT / "samples/freshness_refresh.jsonl"))
        records[1]["attribution"]["labels"]["source_id_hash"] = "other-source"
        records[2]["attribution"]["labels"].pop("source_snapshot_hash")
        report = analyze(records)
        self.assertEqual(report.category_counts["freshness_refresh"], 0)

    def test_freshness_is_not_model_cost_savings(self):
        records = load_records(str(ROOT / "samples/freshness_refresh.jsonl"))
        report = analyze(records)
        for finding in report.findings:
            if finding.category == "freshness_refresh":
                self.assertEqual(finding.saving_ratio, 0)

    def test_reused_run_id_different_traces_does_not_invent_model_duplicate_savings(self):
        base = load_records(str(ROOT / "samples/simple.jsonl"))[0]
        records = []
        for idx, trace in enumerate(("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"), 1):
            record = copy.deepcopy(base)
            record["record_id"] = f"model-scope-{idx}"
            record["run"]["span_id"] = f"model-span-{idx}"
            record["run"]["trace_id"] = trace
            record["run"]["step"] = idx
            records.append(record)
        report = analyze(records)
        self.assertEqual(report.runs, 2)
        self.assertEqual(report.category_counts["duplicate_repeated"], 0)
        self.assertEqual(report.potential_savings.get("USD", 0), 0)

        records[1]["run"]["trace_id"] = records[0]["run"]["trace_id"]
        same_trace = analyze(records)
        self.assertEqual(same_trace.runs, 1)
        self.assertEqual(same_trace.category_counts["duplicate_repeated"], 1)
        self.assertAlmostEqual(same_trace.potential_savings["USD"], 0.002)

        # One known and one unknown trace cannot prove a shared execution.
        records[1]["run"].pop("trace_id")
        incomplete_trace = analyze(records)
        self.assertEqual(incomplete_trace.runs, 2)
        self.assertEqual(incomplete_trace.category_counts["duplicate_repeated"], 0)
        self.assertEqual(incomplete_trace.potential_savings.get("USD", 0), 0)

    def test_cross_trace_model_context_is_not_combined(self):
        base = load_records(str(ROOT / "samples/simple.jsonl"))[0]
        records = []
        for idx, (trace, tokens) in enumerate((
            ("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", 600),
            ("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", 2600),
        ), 1):
            record = copy.deepcopy(base)
            record["record_id"] = f"context-scope-{idx}"
            record["run"]["span_id"] = f"context-span-{idx}"
            record["run"]["trace_id"] = trace
            record["run"]["step"] = idx
            record["usage"]["llm"]["input_tokens"] = tokens
            records.append(record)
        self.assertEqual(analyze(records).category_counts["context_amplification"], 0)
        records[1]["run"]["trace_id"] = records[0]["run"]["trace_id"]
        self.assertGreater(analyze(records).category_counts["context_amplification"], 0)

    def test_cross_trace_model_call_count_is_not_orchestration_overhead(self):
        base = load_records(str(ROOT / "samples/simple.jsonl"))[0]
        records = []
        for idx in range(6):
            record = copy.deepcopy(base)
            record["record_id"] = f"orchestration-scope-{idx}"
            record["run"]["span_id"] = f"orchestration-span-{idx}"
            record["run"]["step"] = idx + 1
            record["run"]["trace_id"] = ("a" if idx < 3 else "b") * 32
            records.append(record)
        self.assertEqual(analyze(records).category_counts["orchestration_overhead"], 0)
        for record in records:
            record["run"]["trace_id"] = "a" * 32
        self.assertEqual(analyze(records).category_counts["orchestration_overhead"], 2)

    def test_cross_trace_high_context_does_not_invent_cache_persistence(self):
        records = copy.deepcopy(load_records(str(ROOT / "samples/cache_instability.jsonl")))
        for idx, record in enumerate(records):
            record["run"]["trace_id"] = f"{idx + 1:032x}"
        report = analyze(records)
        self.assertEqual(report.runs, 3)
        self.assertEqual(report.category_counts["context_amplification"], 0)
        for record in records:
            record["run"]["trace_id"] = "a" * 32
        self.assertGreater(analyze(records).category_counts["context_amplification"], 0)

    def test_savings_do_not_exceed_observed_cost(self):
        report = analyze(load_records(str(ROOT / "samples/inefficient_agent.jsonl")))
        self.assertLessEqual(report.potential_savings["USD"], report.observed_costs["USD"])


if __name__ == "__main__":
    unittest.main()
