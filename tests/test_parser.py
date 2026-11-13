import json
import tempfile
import unittest

from kora_doctor.parser import InputError, load_records


BASE = {
    "spec_version": "1.0.0",
    "record_id": "01KDTEST000000000000000001",
    "emitter": {"component": "router", "name": "test", "version": "0.1.0"},
    "timing": {"event_time": "2026-10-07T00:00:00Z"},
    "resource": {"provider": "openai", "type": "model", "name": "gpt-5-mini", "operation": "generation"},
    "usage": {"llm": {"input_tokens": 10, "output_tokens": 2, "requests": 1}},
    "run": {"run_id": "run-1", "span_id": "span-1"},
    "attribution": {"environment": "development"},
}


class ParserTests(unittest.TestCase):
    def write(self, text):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        f.write(text)
        f.close()
        return f.name

    def test_single_json(self):
        path = self.write(json.dumps(BASE))
        self.assertEqual(len(load_records(path)), 1)

    def test_json_array(self):
        path = self.write(json.dumps([BASE, dict(BASE, record_id="01KDTEST000000000000000002")]))
        self.assertEqual(len(load_records(path)), 2)

    def test_jsonl(self):
        second = dict(BASE, record_id="01KDTEST000000000000000002")
        path = self.write(json.dumps(BASE) + "\n" + json.dumps(second) + "\n")
        self.assertEqual(len(load_records(path)), 2)

    def test_duplicate_record_ids_fail_closed_in_json_and_jsonl(self):
        # A second trace can reuse an ID. A single-ID savings index then
        # attributes the first call's finding to the second call's cost.
        first = dict(BASE)
        second = dict(BASE, run={"run_id": "other-run", "span_id": "other-span",
                                 "trace_id": "another-trace"})
        with tempfile.TemporaryDirectory() as directory:
            from pathlib import Path
            path = Path(directory) / "duplicate.jsonl"
            for fmt, data in (
                ("array", json.dumps([first, second])),
                ("jsonl", json.dumps(first) + "\n" + json.dumps(second) + "\n"),
            ):
                with self.subTest(format=fmt):
                    path.write_text(data)
                    with self.assertRaisesRegex(
                        InputError, r"record 2: duplicate record_id.*record 1"
                    ):
                        load_records(str(path))

    def test_run_and_record_identifiers_require_nonempty_strings(self):
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad-identities.json"
            for field in ("record_id", "run_id", "span_id", "trace_id"):
                for invalid in (1, True, None, "", "   "):
                    with self.subTest(field=field, invalid=repr(invalid)):
                        record = json.loads(json.dumps(BASE))
                        if field == "record_id":
                            record[field] = invalid
                        else:
                            record["run"][field] = invalid
                        path.write_text(json.dumps(record))
                        with self.assertRaisesRegex(InputError, "must be a non-empty string"):
                            load_records(str(path))

    def test_trace_identifier_surrounding_whitespace_fails_closed(self):
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace-identity.json"
            for trace_id in (" trace-a", "trace-a ", "\ttrace-a"):
                with self.subTest(kind=repr(trace_id)):
                    record = json.loads(json.dumps(BASE))
                    record["run"]["trace_id"] = trace_id
                    path.write_text(json.dumps(record))
                    with self.assertRaisesRegex(InputError, r"run[.]trace_id.*non-empty string"):
                        load_records(str(path))

    def test_optional_step_accepts_zero_and_rejects_unorderable_values(self):
        # Mixed step types can otherwise crash the CLI during sorted analysis.
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "step-shape.json"
            for good in (None, 0, 1):
                with self.subTest(good=good):
                    record = json.loads(json.dumps(BASE))
                    record["run"]["step"] = good
                    path.write_text(json.dumps(record))
                    self.assertEqual(load_records(str(path))[0]["run"]["step"], good)
            for bad in (True, -1, 1.5, "1", [], {}):
                with self.subTest(bad=repr(bad)):
                    record = json.loads(json.dumps(BASE))
                    record["run"]["step"] = bad
                    path.write_text(json.dumps(record))
                    with self.assertRaisesRegex(InputError, r"run[.]step must be a non-negative integer"):
                        load_records(str(path))

    def test_missing_optional_cost_is_allowed(self):
        path = self.write(json.dumps(BASE))
        self.assertNotIn("cost", load_records(path)[0])

    def test_missing_required_field_fails(self):
        bad = dict(BASE)
        bad.pop("run")
        path = self.write(json.dumps(bad))
        with self.assertRaises(InputError):
            load_records(path)

    def test_required_metadata_objects_and_labels_are_validated(self):
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "malformed-shape.json"
            for key in ("emitter", "timing", "attribution"):
                with self.subTest(key=key):
                    record = dict(BASE, **{key: ["unexpected", "array"]})
                    path.write_text(json.dumps(record))
                    with self.assertRaisesRegex(
                        InputError, f"record 1: {key} must be an object"
                    ):
                        load_records(str(path))

            with self.subTest(key="attribution.labels"):
                record = dict(BASE, attribution={"environment": "development",
                                                 "labels": ["not", "a", "map"]})
                path.write_text(json.dumps(record))
                with self.assertRaisesRegex(
                    InputError, r"record 1: attribution[.]labels must be an object"
                ):
                    load_records(str(path))

    def test_rejects_nonstandard_and_overflow_json_numbers(self):
        from pathlib import Path

        valid = json.dumps(BASE)
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "bad-numbers.json"
            for bad in ("NaN", "Infinity", "-Infinity", "1e999", "-1e999"):
                with self.subTest(value=bad):
                    p.write_text(valid.replace('"input_tokens": 10', '"input_tokens": ' + bad))
                    with self.assertRaises(InputError):
                        load_records(str(p))

            # A bad value in a later JSONL record must not be ignored.
            p.write_text(valid + "\n" + valid.replace(
                '"input_tokens": 10', '"input_tokens": NaN') + "\n")
            with self.assertRaisesRegex(
                InputError, r"invalid JSONL at line 2: .*non-finite"
            ):
                load_records(str(p))

    def test_analysis_identity_and_usage_shapes_fail_closed(self):
        # Malformed nested values otherwise reach dictionary-key grouping and
        # crash with an unhandled TypeError or AttributeError.
        from pathlib import Path

        cases = (
            ("usage.llm", "usage", "llm", ["wrong"]),
            ("usage.tool", "usage", "tool", ["wrong"]),
            ("usage.llm.input_tokens", "llm_counter", "input_tokens", []),
            ("usage.llm.output_tokens", "llm_counter", "output_tokens", {}),
            ("usage.tool.type", "tool_counter", "type", []),
            ("resource.provider", "resource", "provider", []),
            ("resource.operation", "resource", "operation", {}),
            ("resource.modality", "resource", "modality", []),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad-audr-shape.json"
            for field, section, name, value in cases:
                with self.subTest(field=field):
                    record = json.loads(json.dumps(BASE))
                    if section == "llm_counter":
                        record["usage"]["llm"][name] = value
                    elif section == "tool_counter":
                        record["usage"] = {"tool": {name: value}}
                    else:
                        record[section][name] = value
                    path.write_text(json.dumps(record))
                    with self.assertRaisesRegex(InputError, field.replace(".", "[.]")):
                        load_records(str(path))

            # Missing optional telemetry remains valid, including scalar
            # boolean counters that the analyzer treats as missing evidence.
            record = json.loads(json.dumps(BASE))
            record["usage"]["llm"]["input_tokens"] = True
            path.write_text(json.dumps(record))
            self.assertEqual(len(load_records(str(path))), 1)

    def test_malformed_json_fails(self):
        path = self.write('{"spec_version":')
        with self.assertRaises(InputError):
            load_records(path)


if __name__ == "__main__":
    unittest.main()
