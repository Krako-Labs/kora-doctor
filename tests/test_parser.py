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

    def test_malformed_json_fails(self):
        path = self.write('{"spec_version":')
        with self.assertRaises(InputError):
            load_records(path)


if __name__ == "__main__":
    unittest.main()
