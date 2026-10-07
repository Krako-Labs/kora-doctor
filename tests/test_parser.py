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

    def test_missing_optional_cost_is_allowed(self):
        path = self.write(json.dumps(BASE))
        self.assertNotIn("cost", load_records(path)[0])

    def test_missing_required_field_fails(self):
        bad = dict(BASE)
        bad.pop("run")
        path = self.write(json.dumps(bad))
        with self.assertRaises(InputError):
            load_records(path)

    def test_malformed_json_fails(self):
        path = self.write('{"spec_version":')
        with self.assertRaises(InputError):
            load_records(path)


if __name__ == "__main__":
    unittest.main()
