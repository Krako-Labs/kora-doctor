import unittest
from pathlib import Path

from kora_doctor.analyzer import analyze
from kora_doctor.otel import enrich_with_otel
from kora_doctor.parser import load_records


ROOT = Path(__file__).resolve().parents[1]


class OtelTests(unittest.TestCase):
    def test_otel_conflicting_identifier_aliases_fail_closed_even_unmatched(self):
        import json
        import tempfile
        from kora_doctor.parser import InputError

        records = load_records(str(ROOT / "samples/otel_audr.jsonl"))[:1]
        for label, override in (
            ("matched_span", {"span_id": "private-other-span"}),
            ("matched_trace", {"trace_id": "private-other-trace"}),
            ("unmatched_span", {"spanId": "unmatched-span", "span_id": "private-other-span"}),
        ):
            with self.subTest(case=label):
                span = {
                    "spanId": records[0]["run"]["span_id"],
                    "traceId": records[0]["run"]["trace_id"],
                    "attributes": {"gen_ai.tool.call.arguments": "private-payload"},
                }
                span.update(override)
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "sidecar.json"
                    path.write_text(json.dumps({"spans": [span]}))
                    with self.assertRaisesRegex(InputError, "conflicting .* ID aliases") as caught:
                        enrich_with_otel(records, str(path))
                self.assertNotIn("private", str(caught.exception))

    def test_otel_equivalent_canonical_identifier_aliases_match(self):
        import copy
        import json
        import tempfile

        records = copy.deepcopy(load_records(str(ROOT / "samples/otel_audr.jsonl"))[:1])
        records[0]["run"]["span_id"] = "AABBCCDDEEFF0011"
        records[0]["run"]["trace_id"] = "01234567-89AB-CDEF-0123-456789ABCDEF"
        span = {
            "spanId": "AABBCCDDEEFF0011",
            "span_id": "aabbccddeeff0011",
            "traceId": "0123456789abcdef0123456789abcdef",
            "trace_id": "01234567-89ab-cdef-0123-456789abcdef",
            "attributes": {"gen_ai.tool.call.arguments": "private-payload"},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sidecar.json"
            path.write_text(json.dumps({"spans": [span]}))
            enriched, stats = enrich_with_otel(records, str(path))
        self.assertEqual(stats["matched"], 1)
        self.assertEqual(stats["args_hashed"], 1)
        self.assertNotIn("private-payload", str(enriched))

    def test_otel_rejects_nonstring_span_trace_identity(self):
        import copy
        import json
        import tempfile
        from kora_doctor.parser import InputError

        source = load_records(str(ROOT / "samples/otel_audr.jsonl"))[:1]
        for field, value in (
            ("spanId", 123), ("spanId", True), ("spanId", 0),
            ("span_id", 123), ("traceId", 123), ("traceId", True),
            ("trace_id", 123),
        ):
            with self.subTest(field=field, value_type=type(value).__name__):
                records = copy.deepcopy(source)
                span = {
                    "spanId": records[0]["run"]["span_id"],
                    "traceId": records[0]["run"]["trace_id"],
                    "attributes": {"operation_status": "success"},
                }
                if field == "span_id":
                    span.pop("spanId")
                if field == "trace_id":
                    span.pop("traceId")
                span[field] = value
                with tempfile.TemporaryDirectory() as directory:
                    sidecar = Path(directory) / "sidecar.json"
                    sidecar.write_text(json.dumps({"spans": [span]}))
                    with self.assertRaisesRegex(InputError, "invalid OTel structure") as caught:
                        enrich_with_otel(records, str(sidecar))
                self.assertNotIn("private", str(caught.exception))

    def test_otel_requires_bilateral_trace_evidence(self):
        import copy
        import json
        import tempfile

        source = load_records(str(ROOT / "samples/otel_audr.jsonl"))[:1]
        for name, audr_trace, sidecar_trace, should_match in (
            ("sidecar_missing", "opaque-A", None, False),
            ("audr_missing", None, "opaque-A", False),
            ("both_missing", None, None, True),
            ("both_matching", "opaque-A", "opaque-A", True),
        ):
            with self.subTest(case=name):
                records = copy.deepcopy(source)
                if audr_trace is None:
                    records[0]["run"].pop("trace_id", None)
                else:
                    records[0]["run"]["trace_id"] = audr_trace
                span = {"spanId": records[0]["run"]["span_id"],
                        "attributes": [{"key": "gen_ai.tool.call.arguments", "value": {"stringValue": "private-customer"}}]}
                if sidecar_trace is not None:
                    span["traceId"] = sidecar_trace
                with tempfile.TemporaryDirectory() as directory:
                    sidecar = Path(directory) / "sidecar.json"
                    sidecar.write_text(json.dumps({"spans": [span]}))
                    enriched, stats = enrich_with_otel(records, str(sidecar))
                self.assertEqual(stats["matched"], int(should_match))
                self.assertEqual("tool_args_hash" in enriched[0]["attribution"]["labels"], should_match)
                self.assertNotIn("private-customer", str(enriched))

    def test_otel_does_not_merge_opaque_trace_or_span_ids(self):
        import copy
        import json
        import tempfile

        base = load_records(str(ROOT / "samples/otel_audr.jsonl"))[:1]
        for name, audr_trace, sidecar_trace, audr_span, sidecar_span, should_match in (
            ("case_distinct", "Trace-A", "trace-a", "span-1", "span-1", False),
            ("punctuation_distinct", "ab-cd", "abcd", "span-1", "span-1", False),
            ("span_case_distinct", "trace-a", "trace-a", "Span-A", "span-a", False),
            ("canonical_uuid", "01234567-89AB-CDEF-0123-456789ABCDEF", "0123456789abcdef0123456789abcdef", "AABBCCDDEEFF0011", "aabbccddeeff0011", True),
        ):
            with self.subTest(case=name):
                records = copy.deepcopy(base)
                records[0]["run"].update(trace_id=audr_trace, span_id=audr_span)
                sidecar = {"spans": [{
                    "traceId": sidecar_trace,
                    "spanId": sidecar_span,
                    "attributes": [{"key": "gen_ai.tool.call.arguments", "value": {"stringValue": "private-customer"}}],
                }]}
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "sidecar.json"
                    path.write_text(json.dumps(sidecar))
                    enriched, stats = enrich_with_otel(records, str(path))
                self.assertEqual(stats["matched"], int(should_match))
                self.assertEqual("tool_args_hash" in enriched[0]["attribution"]["labels"], should_match)
                self.assertNotIn("private-customer", str(enriched))

    def test_otel_rejects_wrong_scalar_anyvalue_types(self):
        import json
        import tempfile

        from kora_doctor.parser import InputError

        records = load_records(str(ROOT / "samples/otel_audr.jsonl"))[:1]
        bad_scalars = {
            "string_object": {"stringValue": {"secret": "private"}},
            "string_boolean": {"stringValue": True},
            "bool_string": {"boolValue": "true"},
            "bool_integer": {"boolValue": 1},
            "int_boolean": {"intValue": True},
            "int_fractional": {"intValue": 1.2},
            "int_nonnumeric": {"intValue": "private"},
            "int_out_of_range": {"intValue": "9223372036854775808"},
            "double_boolean": {"doubleValue": True},
            "double_object": {"doubleValue": {"secret": "private"}},
            "double_nonnumeric": {"doubleValue": "private"},
            "bytes_integer": {"bytesValue": 123},
            "bytes_bad_base64": {"bytesValue": "private !"},
        }
        for name, value in bad_scalars.items():
            for matched in (True, False):
                with self.subTest(case=name, matched=matched):
                    span = {
                        "spanId": records[0]["run"]["span_id"] if matched else "ffffffffffffffff",
                        "attributes": [
                            {"key": "gen_ai.tool.call.arguments", "value": value}
                        ],
                    }
                    with tempfile.TemporaryDirectory() as directory:
                        path = Path(directory) / "invalid-otel-scalar.json"
                        path.write_text(json.dumps({"spans": [span]}))
                        with self.assertRaisesRegex(InputError, "invalid OTel attributes") as caught:
                            enrich_with_otel(records, str(path))
                    self.assertNotIn("private", str(caught.exception))

    def test_otel_equivalent_protojson_scalars_have_same_fingerprint(self):
        import json
        import tempfile

        records = load_records(str(ROOT / "samples/otel_audr.jsonl"))[:1]
        pairs = (
            ("int64", {"intValue": "42"}, {"intValue": 42.0}),
            ("double", {"doubleValue": "2.5"}, {"doubleValue": 2.5}),
            ("bytes_padding", {"bytesValue": "AQIDBA"}, {"bytesValue": "AQIDBA=="}),
            ("bytes_urlsafe", {"bytesValue": "-___"}, {"bytesValue": "+///"}),
        )
        for name, first, second in pairs:
            with self.subTest(case=name):
                hashes = []
                for value in (first, second):
                    span = {
                        "spanId": records[0]["run"]["span_id"],
                    "traceId": records[0]["run"]["trace_id"],
                        "attributes": [
                            {"key": "gen_ai.tool.call.arguments", "value": value}
                        ],
                    }
                    with tempfile.TemporaryDirectory() as directory:
                        path = Path(directory) / "otel-equivalent.json"
                        path.write_text(json.dumps({"spans": [span]}))
                        enriched, stats = enrich_with_otel(records, str(path))
                    self.assertEqual(stats["matched"], 1)
                    hashes.append(enriched[0]["attribution"]["labels"]["tool_args_hash"])
                self.assertEqual(hashes[0], hashes[1])

    def test_otel_accepts_protojson_scalar_values(self):
        import json
        import tempfile

        records = load_records(str(ROOT / "samples/otel_audr.jsonl"))[:1]
        values = (
            {"stringValue": "private customer"},
            {"boolValue": False},
            {"intValue": "9223372036854775807"},
            {"intValue": 1.0},
            {"doubleValue": 1.5},
            {"doubleValue": "NaN"},
            {"bytesValue": "AQID"},
            {"bytesValue": "AQIDBA"},
        )
        for value in values:
            with self.subTest(value_type=next(iter(value))):
                span = {
                    "spanId": records[0]["run"]["span_id"],
                    "traceId": records[0]["run"]["trace_id"],
                    "attributes": [
                        {"key": "gen_ai.tool.call.arguments", "value": value}
                    ],
                }
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "valid-otel-scalar.json"
                    path.write_text(json.dumps({"spans": [span]}))
                    enriched, stats = enrich_with_otel(records, str(path))
                self.assertEqual(stats["matched"], 1)
                self.assertEqual(stats["args_hashed"], 1)
                self.assertNotIn("private customer", str(enriched))

    def test_otel_rejects_excessive_json_and_anyvalue_nesting(self):
        import json
        import tempfile

        from kora_doctor.parser import InputError

        records = load_records(str(ROOT / "samples/otel_audr.jsonl"))[:1]
        value = {"stringValue": "private-secret"}
        for _ in range(60):
            value = {"arrayValue": {"values": [value]}}
        payload = {"spans": [{"spanId": records[0]["run"]["span_id"],
                              "attributes": [{"key": "gen_ai.tool.call.arguments", "value": value}]}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "deep-anyvalue.json"
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(InputError, "invalid OTel") as caught:
                enrich_with_otel(records, str(path))
            self.assertNotIn("private-secret", str(caught.exception))

            # Plain JSON maps (without AnyValue wrappers) must be bounded too.
            raw = {"secret": "private-secret"}
            for _ in range(180):
                raw = {"nested": raw}
            payload["spans"][0]["attributes"] = {"gen_ai.tool.call.arguments": raw}
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(InputError, "invalid OTel") as caught:
                enrich_with_otel(records, str(path))
            self.assertNotIn("private-secret", str(caught.exception))

            # A file deeper than Python's JSON decoder recursion limit must
            # result in an input error, not a Python traceback.
            path.write_text('{"spans":[' + ("[" * 1300) + "0" + ("]" * 1300) + "]}")
            with self.assertRaisesRegex(InputError, "invalid OTel"):
                enrich_with_otel(records, str(path))

    def test_otel_rejects_malformed_attributes_and_nested_any_values(self):
        import json
        import tempfile

        from kora_doctor.parser import InputError

        records = load_records(str(ROOT / "samples/otel_audr.jsonl"))[:1]
        bad = {
            "attributes_scalar": 7,
            "attributes_string": "private-customer",
            "attributes_null": None,
            "list_entry_scalar": [5],
            "list_entry_missing_key": [{"value": {"stringValue": "private-customer"}}],
            "list_entry_nonstring_key": [{"key": 7, "value": {"stringValue": "value"}}],
            "list_entry_missing_value": [{"key": "gen_ai.tool.call.arguments"}],
            "duplicate_list_keys": [
                {"key": "gen_ai.tool.call.arguments", "value": {"stringValue": "first"}},
                {"key": "gen_ai.tool.call.arguments", "value": {"stringValue": "second"}},
            ],
            "array_values_string": {
                "gen_ai.tool.call.arguments": {"arrayValue": {"values": "private-customer"}}
            },
            "array_values_scalar": {
                "gen_ai.tool.call.arguments": {"arrayValue": {"values": 123}}
            },
            "array_item_not_anyvalue": {
                "gen_ai.tool.call.arguments": {"arrayValue": {"values": [12]}}
            },
            "kvlist_values_scalar": {
                "gen_ai.tool.call.arguments": {"kvlistValue": {"values": 12}}
            },
            "kvlist_entry_not_object": {
                "gen_ai.tool.call.arguments": {"kvlistValue": {"values": [12]}}
            },
            "kvlist_entry_missing_key": {
                "gen_ai.tool.call.arguments": {"kvlistValue": {
                    "values": [{"value": {"stringValue": "private-customer"}}]}}
            },
            "kvlist_entry_nonstring_key": {
                "gen_ai.tool.call.arguments": {"kvlistValue": {
                    "values": [{"key": 12, "value": {"stringValue": "private-customer"}}]}}
            },
            "kvlist_item_not_anyvalue": {
                "gen_ai.tool.call.arguments": {"kvlistValue": {
                    "values": [{"key": "customer", "value": "private-customer"}]}}
            },
            "multiple_anyvalue_kinds": {
                "gen_ai.tool.call.arguments": {"stringValue": "first", "intValue": "99"}
            },
        }
        for name, attributes in bad.items():
            with self.subTest(name=name):
                span = {
                    "spanId": records[0]["run"]["span_id"],
                    "traceId": records[0]["run"]["trace_id"],
                    "attributes": attributes,
                }
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "invalid-attributes.json"
                    path.write_text(json.dumps({"spans": [span]}))
                    with self.assertRaisesRegex(InputError, "invalid OTel attributes") as caught:
                        enrich_with_otel(records, str(path))
                self.assertNotIn("private-customer", str(caught.exception))

    def test_otel_rejects_malformed_attributes_in_unmatched_span(self):
        import json
        import tempfile

        from kora_doctor.parser import InputError

        records = load_records(str(ROOT / "samples/otel_audr.jsonl"))[:1]
        unmatched = {
            "spanId": "ffffffffffffffff",
            "attributes": {"gen_ai.tool.call.arguments": {
                "arrayValue": {"values": "customer-secret"}}},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid-unmatched.json"
            path.write_text(json.dumps({"spans": [unmatched]}))
            with self.assertRaisesRegex(InputError, "invalid OTel attributes") as caught:
                enrich_with_otel(records, str(path))
        self.assertNotIn("customer-secret", str(caught.exception))

    def test_otel_valid_nested_anyvalue_is_hashed_without_raw_payload(self):
        import json
        import tempfile

        records = load_records(str(ROOT / "samples/otel_audr.jsonl"))[:1]
        span = {
            "spanId": records[0]["run"]["span_id"],
            "traceId": records[0]["run"]["trace_id"],
            "attributes": [
                {"key": "gen_ai.tool.call.arguments", "value": {"kvlistValue": {
                    "values": [
                        {"key": "payload", "value": {"arrayValue": {"values": [
                            {"stringValue": "private-customer"},
                            {"intValue": "42"},
                        ]}}},
                    ]}}},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "valid-nested-attributes.json"
            path.write_text(json.dumps({"spans": [span]}))
            enriched, stats = enrich_with_otel(records, str(path))
        self.assertEqual(stats["matched"], 1)
        self.assertEqual(stats["args_hashed"], 1)
        self.assertTrue(enriched[0]["attribution"]["labels"]["tool_args_hash"].startswith("sha256:"))
        self.assertNotIn("private-customer", str(enriched))

    def test_otel_rejects_malformed_span_containers(self):
        import json
        import tempfile

        from kora_doctor.parser import InputError

        records = load_records(str(ROOT / "samples/otel_audr.jsonl"))
        invalid = {
            "unknown_root": {"unexpected_field": "private payload"},
            "nonlist_resource_spans": {"resourceSpans": {"scopeSpans": []}},
            "nonobject_resource_group": {"resourceSpans": [7]},
            "nonlist_scope_groups": {"resourceSpans": [{"scopeSpans": 7}]},
            "nonobject_scope_group": {"resourceSpans": [{"scopeSpans": [False]}]},
            "nonlist_nested_spans": {"resourceSpans": [{"scopeSpans": [{"spans": 3}]}]},
            "nonobject_nested_span": {"resourceSpans": [{"scopeSpans": [{"spans": [None]}]}]},
            "nonlist_simple_spans": {"spans": {"spanId": "private"}},
            "nonobject_simple_span": {"spans": [None]},
            "nonobject_root_array_span": [None],
        }
        for name, payload in invalid.items():
            with self.subTest(case=name):
                with tempfile.TemporaryDirectory() as directory:
                    sidecar = Path(directory) / "untrusted-sidecar.json"
                    sidecar.write_text(json.dumps(payload))
                    with self.assertRaisesRegex(InputError, "invalid OTel structure"):
                        enrich_with_otel(records, str(sidecar))

    def test_otel_legacy_scope_fallback_remains_supported(self):
        import json
        import tempfile

        records = load_records(str(ROOT / "samples/otel_audr.jsonl"))
        span = {
            "spanId": records[0]["run"]["span_id"],
            "traceId": records[0]["run"]["trace_id"],
            "attributes": {"http.response.status_code": 429},
        }
        payload = {
            "resourceSpans": [{
                "scopeSpans": [],
                "instrumentationLibrarySpans": [{"spans": [span]}],
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            sidecar = Path(directory) / "legacy-otel.json"
            sidecar.write_text(json.dumps(payload))
            enriched, stats = enrich_with_otel(records, str(sidecar))
        self.assertEqual(stats["matched"], 1)
        self.assertEqual(enriched[0]["attribution"]["labels"]["http_status_code"], "429")

    def test_otel_accepts_valid_empty_span_containers(self):
        import json
        import tempfile

        records = load_records(str(ROOT / "samples/otel_audr.jsonl"))
        for payload in ([], {"spans": []}, {"resourceSpans": []},
                        {"resourceSpans": [{"scopeSpans": []}]}):
            with self.subTest(payload=payload):
                with tempfile.TemporaryDirectory() as directory:
                    sidecar = Path(directory) / "empty-sidecar.json"
                    sidecar.write_text(json.dumps(payload))
                    enriched, stats = enrich_with_otel(records, str(sidecar))
                self.assertEqual(stats["spans"], 0)
                self.assertEqual(enriched, records)

    def test_otel_raw_text_whitespace_does_not_certify_equal_payloads(self):
        import json
        import tempfile

        # OTel stringValue may hold raw tool text, where leading whitespace
        # is meaningful. Only JSON payloads should be structurally normalized.
        source = load_records(str(ROOT / "samples/otel_audr.jsonl"))
        cases = (
            ("arguments", "  private-request", "private-request",
             "same-result", "same-result", False, True),
            ("results", "same-request", "same-request",
             "  private-result", "private-result", True, False),
        )
        for name, first_arg, second_arg, first_result, second_result, args_equal, results_equal in cases:
            with self.subTest(variant=name):
                spans = []
                for record, arguments, result in zip(
                    source, (first_arg, second_arg), (first_result, second_result)
                ):
                    spans.append({
                        "spanId": record["run"]["span_id"],
                        "traceId": record["run"]["trace_id"],
                        "attributes": {
                            "gen_ai.tool.call.arguments": arguments,
                            "gen_ai.tool.call.result": result,
                            "operation_status": "success",
                        },
                    })
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "otel-sidecar.json"
                    path.write_text(json.dumps({"spans": spans}))
                    enriched, stats = enrich_with_otel(source, str(path))
                self.assertEqual(stats["matched"], 2)
                left = enriched[0]["attribution"]["labels"]
                right = enriched[1]["attribution"]["labels"]
                self.assertEqual(left["tool_args_hash"] == right["tool_args_hash"], args_equal)
                self.assertEqual(left["tool_result_hash"] == right["tool_result_hash"], results_equal)
                self.assertEqual(stats["inferred_retries"], 0)
                exact = [f for f in analyze(enriched).findings
                         if f.category == "repeated_tool_retrieval" and f.confidence == "medium"]
                self.assertEqual(exact, [])
                self.assertNotIn("private-request", str(enriched))
                self.assertNotIn("private-result", str(enriched))

    def test_otel_json_scalar_looking_tool_text_cannot_certify_exact_repeat(self):
        import json
        import tempfile

        # A tool can return text that happens to be valid JSON *scalar* text.
        # Do not collapse distinct raw strings or coerce stringValue into an
        # integer/bool AnyValue when deciding if two calls were identical.
        source = load_records(str(ROOT / "samples/otel_audr.jsonl"))
        cases = (
            ("number_whitespace", " 42 ", "42"),
            ("boolean_whitespace", "true", " true "),
            ("null_whitespace", "null", "null\n"),
            ("json_string_whitespace", '\"x\"', ' \"x\" '),
            ("string_versus_integer", {"stringValue": "42"}, {"intValue": "42"}),
            ("huge_number_like_text", "9" * 5000, "9" * 4999),
        )
        for name, first, second in cases:
            with self.subTest(case=name):
                spans = []
                for record, value in zip(source, (first, second)):
                    spans.append({
                        "spanId": record["run"]["span_id"],
                        "traceId": record["run"]["trace_id"],
                        "attributes": [{
                            "key": "gen_ai.tool.call.arguments",
                            "value": value if isinstance(value, dict) else {"stringValue": value},
                        }],
                    })
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "otel-sidecar.json"
                    path.write_text(json.dumps({"spans": spans}))
                    enriched, stats = enrich_with_otel(source, str(path))
                self.assertEqual(stats["matched"], 2)
                hashes = [r["attribution"]["labels"]["tool_args_hash"] for r in enriched]
                self.assertNotEqual(*hashes)
                self.assertEqual(stats["inferred_retries"], 0)
                medium_repeats = [f for f in analyze(enriched).findings
                                  if f.category == "repeated_tool_retrieval"
                                  and f.confidence == "medium"]
                self.assertEqual(medium_repeats, [])

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
        self.assertEqual(second_labels["retry_inferred_by_otel"], "true")

        report = analyze(enriched)
        retry = [f for f in report.findings if f.category == "retry_overhead"]
        self.assertEqual(len(retry), 1)
        self.assertEqual(retry[0].confidence, "low")
        self.assertIn("inferred", retry[0].reason.lower())

    def test_otel_inferred_retry_rejects_ambiguous_order_and_identity(self):
        import copy
        import json
        import tempfile

        source = load_records(str(ROOT / "samples/otel_audr.jsonl"))
        source_spans = json.loads((ROOT / "samples/otel_sidecar.json").read_text())[
            "resourceSpans"
        ][0]["scopeSpans"][0]["spans"]

        def backwards_time(records, spans):
            records[1]["timing"]["event_time"] = "2026-10-08T04:00:00Z"

        def same_time(records, spans):
            records[1]["timing"]["event_time"] = records[0]["timing"]["event_time"]

        def missing_time(records, spans):
            records[0]["timing"].pop("event_time")

        def naive_time(records, spans):
            records[0]["timing"]["event_time"] = "2026-10-08T04:00:01"

        def different_trace(records, spans):
            trace = "ffffffffffffffffffffffffffffffff"
            records[1]["run"]["trace_id"] = trace
            spans[1]["traceId"] = trace

        def duplicate_record_id(records, spans):
            records[1]["record_id"] = records[0]["record_id"]

        def reversed_steps(records, spans):
            records[0]["run"]["step"] = 2
            records[1]["run"]["step"] = 1

        for name, mutate in (
            ("backwards_time", backwards_time),
            ("same_time", same_time),
            ("missing_time", missing_time),
            ("naive_time", naive_time),
            ("different_trace", different_trace),
            ("duplicate_record_id", duplicate_record_id),
            ("reversed_steps", reversed_steps),
        ):
            with self.subTest(name=name):
                records = copy.deepcopy(source)
                spans = copy.deepcopy(source_spans)
                mutate(records, spans)
                with tempfile.TemporaryDirectory() as directory:
                    sidecar = Path(directory) / "otel.json"
                    sidecar.write_text(json.dumps({"spans": spans}))
                    enriched, stats = enrich_with_otel(records, str(sidecar))
                self.assertEqual(stats["matched"], 2)
                self.assertEqual(stats["inferred_retries"], 0)
                self.assertNotIn("retry_of", enriched[1]["attribution"]["labels"])
                self.assertEqual(
                    [f for f in analyze(enriched).findings if f.category == "retry_overhead"],
                    [],
                )

    def test_otel_retry_order_respects_timezone_offsets(self):
        import copy
        import json
        import tempfile

        records = copy.deepcopy(load_records(str(ROOT / "samples/otel_audr.jsonl")))
        records[1]["timing"]["event_time"] = "2026-10-08T06:00:02+02:00"
        spans = json.loads((ROOT / "samples/otel_sidecar.json").read_text())[
            "resourceSpans"
        ][0]["scopeSpans"][0]["spans"]
        with tempfile.TemporaryDirectory() as directory:
            sidecar = Path(directory) / "otel.json"
            sidecar.write_text(json.dumps({"spans": spans}))
            enriched, stats = enrich_with_otel(records, str(sidecar))
        self.assertEqual(stats["inferred_retries"], 1)
        self.assertEqual(enriched[1]["attribution"]["labels"]["retry_of"], records[0]["record_id"])

    def test_otel_inferred_retry_cannot_certify_one_http_429_operation(self):
        import json
        import tempfile

        records = load_records(str(ROOT / "samples/otel_audr.jsonl"))
        spans = json.loads((ROOT / "samples/otel_sidecar.json").read_text())[
            "resourceSpans"
        ][0]["scopeSpans"][0]["spans"]
        spans[0]["attributes"].append({
            "key": "http.response.status_code", "value": {"intValue": "429"},
        })
        spans[1]["attributes"].append({
            "key": "http.response.status_code", "value": {"intValue": "200"},
        })
        with tempfile.TemporaryDirectory() as directory:
            sidecar = Path(directory) / "otel.json"
            sidecar.write_text(json.dumps({"spans": spans}))
            enriched, stats = enrich_with_otel(records, str(sidecar))
        self.assertEqual(stats["inferred_retries"], 1)
        report = analyze(enriched)
        self.assertEqual(report.category_counts["rate_limit_retry_chain"], 0)
        retries = [f for f in report.findings if f.category == "retry_overhead"]
        self.assertEqual(len(retries), 1)
        self.assertEqual(retries[0].confidence, "low")
        self.assertEqual(retries[0].saving_ratio, 0.0)

    def test_otel_inference_requires_sidecar_argument_and_problem_status_evidence(self):
        import copy
        import json
        import tempfile

        source = load_records(str(ROOT / "samples/otel_audr.jsonl"))
        baseline, _ = enrich_with_otel(source, str(ROOT / "samples/otel_sidecar.json"))
        original_hash = baseline[0]["attribution"]["labels"]["tool_args_hash"]
        for record, status in zip(source, ("failed", "success")):
            record["attribution"]["labels"].update({
                "tool_args_hash": original_hash,
                "operation_status": status,
            })
        original_spans = json.loads((ROOT / "samples/otel_sidecar.json").read_text())[
            "resourceSpans"
        ][0]["scopeSpans"][0]["spans"]

        for case, first_attrs, second_attrs in (
            ("no_otel_evidence", [], []),
            ("args_only_on_first", ["gen_ai.tool.call.arguments"], []),
            ("args_only_on_second", [], ["gen_ai.tool.call.arguments"]),
            ("no_otel_prior_failure", ["gen_ai.tool.call.arguments"], ["gen_ai.tool.call.arguments"]),
        ):
            with self.subTest(case=case):
                records = copy.deepcopy(source)
                spans = copy.deepcopy(original_spans)
                for span, keys in zip(spans, (first_attrs, second_attrs)):
                    span["attributes"] = [
                        attr for attr in span["attributes"] if attr["key"] in keys
                    ]
                if case in ("no_otel_evidence", "no_otel_prior_failure"):
                    spans[0].pop("status", None)
                with tempfile.TemporaryDirectory() as directory:
                    sidecar = Path(directory) / "otel.json"
                    sidecar.write_text(json.dumps({"spans": spans}))
                    enriched, stats = enrich_with_otel(records, str(sidecar))
                self.assertEqual(stats["inferred_retries"], 0)
                self.assertNotIn("retry_inferred_by_otel", enriched[1]["attribution"]["labels"])

    def test_otel_inference_requires_both_calls_to_match_sidecar(self):
        import copy
        import json
        import tempfile

        baseline = load_records(str(ROOT / "samples/otel_audr.jsonl"))
        for record, status in zip(baseline, ("failed", "success")):
            record["attribution"]["labels"].update({
                "tool_args_hash": "sha256:already-present",
                "operation_status": status,
            })

        first_span = copy.deepcopy(json.loads(
            (ROOT / "samples/otel_sidecar.json").read_text()
        )["resourceSpans"][0]["scopeSpans"][0]["spans"][0])
        first_span["attributes"] = []
        for spans in ([], [first_span]):
            with self.subTest(matched_span_count=len(spans)):
                records = copy.deepcopy(baseline)
                with tempfile.TemporaryDirectory() as directory:
                    sidecar = Path(directory) / "otel.json"
                    sidecar.write_text(json.dumps({"spans": spans}))
                    enriched, stats = enrich_with_otel(records, str(sidecar))
                self.assertEqual(stats["matched"], len(spans))
                self.assertEqual(stats["inferred_retries"], 0)
                self.assertNotIn("retry_of", enriched[1]["attribution"]["labels"])
                self.assertNotIn("retry_inferred_by_otel", enriched[1]["attribution"]["labels"])

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

    def test_otel_http_429_status_is_transported_without_raw_headers(self):
        import json
        import tempfile
        records = load_records(str(ROOT / "samples/otel_audr.jsonl"))
        span = {
            "spanId": records[0]["run"]["span_id"],
                    "traceId": records[0]["run"]["trace_id"],
            "attributes": [
                {"key": "http.response.status_code", "value": {"intValue": "429"}},
                {"key": "http.response.header.retry-after", "value": {"stringValue": "private value"}},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            sidecar = Path(directory) / "otel-http-status.json"
            sidecar.write_text(json.dumps({"spans": [span]}))
            enriched, stats = enrich_with_otel(records, str(sidecar))
        self.assertEqual(stats["matched"], 1)
        labels = enriched[0]["attribution"]["labels"]
        self.assertEqual(labels["http_status_code"], "429")
        self.assertNotIn("http.response.header.retry-after", labels)
        self.assertNotIn("private value", str(enriched))

    def test_otel_conflicting_audr_labels_do_not_override_source(self):
        import copy
        import json
        import tempfile

        base = load_records(str(ROOT / "samples/otel_audr.jsonl"))[:1]
        span_id = base[0]["run"]["span_id"]
        trace_id = base[0]["run"]["trace_id"]
        scenarios = (
            ("operation_status", {"operation_status": "success"},
             {"operation_status": "failed"}),
            ("http_status_alias", {"http.response.status_code": "200"},
             {"http.response.status_code": 429}),
            ("retry_parent_alias", {"kora.retry_of": "real-parent"},
             {"kora.retry_of": "other-parent"}),
            ("args_fingerprint", {"tool_args_hash": "sha256:existing"},
             {"gen_ai.tool.call.arguments": '{"call":42}'}),
        )
        for name, existing, attributes in scenarios:
            with self.subTest(name=name):
                records = copy.deepcopy(base)
                records[0]["attribution"]["labels"].update(existing)
                span = {"spanId": span_id, "traceId": trace_id, "attributes": attributes}
                with tempfile.TemporaryDirectory() as directory:
                    sidecar = Path(directory) / "conflicting-sidecar.json"
                    sidecar.write_text(json.dumps({"spans": [span]}))
                    enriched, stats = enrich_with_otel(records, str(sidecar))
                self.assertEqual(stats["matched"], 0)
                self.assertEqual(stats["conflicts"], 1)
                self.assertEqual(stats["inferred_retries"], 0)
                self.assertEqual(enriched, records)

    def test_otel_matching_existing_aliases_are_preserved(self):
        import copy
        import json
        import tempfile

        records = copy.deepcopy(load_records(str(ROOT / "samples/otel_audr.jsonl"))[:1])
        labels = records[0]["attribution"]["labels"]
        labels.update({
            "kora.operation_status": "FAILED",
            "http.response.status_code": "429",
            "sr.tenant_hmac": "tenant-opaque",
        })
        span = {
            "spanId": records[0]["run"]["span_id"],
            "traceId": records[0]["run"]["trace_id"],
            "attributes": {
                "operation_status": "failed",
                "http.response.status_code": 429,
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            sidecar = Path(directory) / "matching-sidecar.json"
            sidecar.write_text(json.dumps({"spans": [span]}))
            enriched, stats = enrich_with_otel(records, str(sidecar))
        self.assertEqual(stats["matched"], 1)
        self.assertEqual(stats["conflicts"], 0)
        self.assertEqual(enriched[0]["attribution"]["labels"]["operation_status"], "failed")
        self.assertEqual(enriched[0]["attribution"]["labels"]["http_status_code"], "429")
        self.assertEqual(enriched[0]["attribution"]["labels"]["sr.tenant_hmac"], "tenant-opaque")

    def test_otel_mismatched_explicit_trace_id_does_not_enrich(self):
        import json
        import tempfile
        records = load_records(str(ROOT / "samples/otel_audr.jsonl"))
        span = {
            "spanId": records[0]["run"]["span_id"],
            "traceId": "ffffffffffffffffffffffffffffffff",
            "attributes": {"http.response.status_code": 429, "gen_ai.tool.call.arguments": "private"},
            "status": {"code": "STATUS_CODE_ERROR"},
        }
        with tempfile.TemporaryDirectory() as directory:
            sidecar = Path(directory) / "trace-mismatch.json"
            sidecar.write_text(json.dumps({"spans": [span]}))
            enriched, stats = enrich_with_otel(records, str(sidecar))
        self.assertEqual(stats["matched"], 0)
        self.assertEqual(stats["args_hashed"], 0)
        self.assertEqual(enriched, records)

    def test_otel_ambiguous_span_id_without_trace_id_is_skipped(self):
        import copy
        import json
        import tempfile
        records = load_records(str(ROOT / "samples/otel_audr.jsonl"))[:1]
        second = copy.deepcopy(records[0])
        second["record_id"] = "second-run-colliding-span"
        second["run"]["run_id"] = "another-run"
        second["run"]["trace_id"] = "ffffffffffffffffffffffffffffffff"
        records.append(second)
        span = {
            "spanId": records[0]["run"]["span_id"],
            "attributes": {"http.response.status_code": 429},
        }
        with tempfile.TemporaryDirectory() as directory:
            sidecar = Path(directory) / "ambiguous.json"
            sidecar.write_text(json.dumps({"spans": [span]}))
            enriched, stats = enrich_with_otel(records, str(sidecar))
        self.assertEqual(stats["matched"], 0)
        self.assertEqual(enriched, records)

    def test_otel_shared_span_id_matches_unique_explicit_trace_id(self):
        import copy
        import json
        import tempfile
        records = load_records(str(ROOT / "samples/otel_audr.jsonl"))[:1]
        second = copy.deepcopy(records[0])
        second["record_id"] = "second-run-same-span"
        second["run"]["run_id"] = "another-run"
        second["run"]["trace_id"] = "ffffffffffffffffffffffffffffffff"
        records.append(second)
        span = {
            "spanId": second["run"]["span_id"],
            "traceId": second["run"]["trace_id"],
            "attributes": {"http.response.status_code": 429},
        }
        with tempfile.TemporaryDirectory() as directory:
            sidecar = Path(directory) / "two-traces.json"
            sidecar.write_text(json.dumps({"spans": [span]}))
            enriched, stats = enrich_with_otel(records, str(sidecar))
        self.assertEqual(stats["matched"], 1)
        self.assertNotIn("http_status_code", enriched[0]["attribution"]["labels"])
        self.assertEqual(enriched[1]["attribution"]["labels"]["http_status_code"], "429")

    def test_otel_unique_span_id_without_audr_trace_id_is_rejected_when_sidecar_has_trace(self):
        import copy
        import json
        import tempfile
        records = copy.deepcopy(load_records(str(ROOT / "samples/otel_audr.jsonl"))[:1])
        records[0]["run"].pop("trace_id", None)
        span = {
            "spanId": records[0]["run"]["span_id"],
            "traceId": "ffffffffffffffffffffffffffffffff",
            "attributes": {"http.response.status_code": 429},
        }
        with tempfile.TemporaryDirectory() as directory:
            sidecar = Path(directory) / "single-no-trace.json"
            sidecar.write_text(json.dumps({"spans": [span]}))
            enriched, stats = enrich_with_otel(records, str(sidecar))
        self.assertEqual(stats["matched"], 0)
        self.assertEqual(enriched, records)

    def test_otel_enrichment_preserves_optional_seenrelay_labels(self):
        import copy
        import json
        import tempfile
        records = copy.deepcopy(load_records(str(ROOT / "samples/otel_audr.jsonl"))[:1])
        optional_labels = {
            "sr.tenant_hmac": "hmac-tenant-a",
            "sr.auth_scope_hmac": "hmac-scope-a",
            "sr.dependencies_hash": "digest-deps",
            "sr.environment_hash": "digest-env",
            "sr.freshness_policy": "policy-opaque",
            "sr.hard_valid_until": "2026-10-08T05:00:00Z",
        }
        records[0]["attribution"]["labels"].update(optional_labels)
        span = {
            "spanId": records[0]["run"]["span_id"],
            "traceId": records[0]["run"]["trace_id"],
            "attributes": {"http.response.status_code": 429},
        }
        with tempfile.TemporaryDirectory() as directory:
            sidecar = Path(directory) / "enrich-preserves-optional.json"
            sidecar.write_text(json.dumps({"spans": [span]}))
            enriched, stats = enrich_with_otel(records, str(sidecar))
        self.assertEqual(stats["matched"], 1)
        labels = enriched[0]["attribution"]["labels"]
        for name, value in optional_labels.items():
            self.assertEqual(labels[name], value)
        self.assertEqual(labels["http_status_code"], "429")
        self.assertEqual(records[0]["attribution"]["labels"], optional_labels | {"feature": "otel-sidecar"})

    def test_otel_conflicting_duplicate_sidecar_spans_are_not_applied(self):
        import json
        import tempfile
        records = load_records(str(ROOT / "samples/otel_audr.jsonl"))[:1]
        span_id = records[0]["run"]["span_id"]
        trace_id = records[0]["run"]["trace_id"]
        spans = [
            {"spanId": span_id, "traceId": trace_id,
             "attributes": {"http.response.status_code": 429}},
            {"spanId": span_id, "traceId": trace_id,
             "attributes": {"http.response.status_code": 200}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            sidecar = Path(directory) / "duplicate-spans.json"
            sidecar.write_text(json.dumps({"spans": spans}))
            enriched, stats = enrich_with_otel(records, str(sidecar))
        self.assertEqual(stats["spans"], 2)
        self.assertEqual(stats["matched"], 0)
        self.assertEqual(enriched, records)

    def test_otel_same_span_id_across_distinct_explicit_traces_enriches_both(self):
        import copy
        import json
        import tempfile
        records = load_records(str(ROOT / "samples/otel_audr.jsonl"))[:1]
        second = copy.deepcopy(records[0])
        second["record_id"] = "second-otel-trace-span"
        second["run"]["run_id"] = "separate-agent-run"
        second["run"]["trace_id"] = "ffffffffffffffffffffffffffffffff"
        records.append(second)
        spans = [
            {"spanId": records[0]["run"]["span_id"],
             "traceId": records[0]["run"]["trace_id"],
             "attributes": {"http.response.status_code": 429}},
            {"spanId": second["run"]["span_id"],
             "traceId": second["run"]["trace_id"],
             "attributes": {"http.response.status_code": 200}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            sidecar = Path(directory) / "two-distinct-traces.json"
            sidecar.write_text(json.dumps({"spans": spans}))
            enriched, stats = enrich_with_otel(records, str(sidecar))
        self.assertEqual(stats["matched"], 2)
        self.assertEqual(enriched[0]["attribution"]["labels"]["http_status_code"], "429")
        self.assertEqual(enriched[1]["attribution"]["labels"]["http_status_code"], "200")

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
        span = {"spanId": records[0]["run"]["span_id"], "traceId": records[0]["run"]["trace_id"], "attributes": attrs}
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


    def test_otel_oversized_raw_json_integer_fails_closed_on_all_python_versions(self):
        import tempfile
        from kora_doctor.parser import InputError

        records = load_records(str(ROOT / "samples/otel_audr.jsonl"))[:1]
        # Raw numeric OTel metadata can exceed Python 3.13's int conversion
        # limit. Python 3.9 accepts it, so enforce the same safe input bound.
        for digits in (129, 5000):
            with self.subTest(digits=digits), tempfile.TemporaryDirectory() as directory:
                sidecar = Path(directory) / "synthetic-oversized-otel.json"
                sidecar.write_text('{"spans":[{"spanId":"unmatched","attributes":{'
                                   '"private_integer":' + ('8' * digits) + '}}]}')
                with self.assertRaisesRegex(InputError, "invalid OTel JSON number") as caught:
                    enrich_with_otel(records, str(sidecar))
                self.assertNotIn('8' * 30, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
