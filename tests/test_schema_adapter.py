"""Transport-only compatibility regression, preserving independent local constraints."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import openrouter_smoke as smoke
from test_openrouter_smoke import FakeAPI, SENTINEL, output_draft


class SchemaAdapterTests(unittest.TestCase):
    def test_outbound_schema_omits_unique_items_without_mutating_offline_schema(self):
        packet = smoke.offline_eval.build_prompt("E01")
        original = copy.deepcopy(packet)
        with patch.object(smoke.offline_eval, "build_prompt", return_value=packet):
            body = smoke.build_request()
        self.assertNotIn('"uniqueItems"', json.dumps(body["response_format"]))
        self.assertEqual(packet, original)
        self.assertIn('"uniqueItems": true', json.dumps(original["response_schema"]))

    def test_only_thirteen_declared_array_constraints_differ(self):
        original = smoke.offline_eval.build_prompt("E01")["response_schema"]
        actual = smoke.build_request()["response_format"]["json_schema"]["schema"]
        removed = []

        def compare(before, after, path=()):
            if isinstance(before, dict):
                self.assertIsInstance(after, dict)
                self.assertFalse(set(after) - set(before))
                for key, value in before.items():
                    if key not in after:
                        self.assertEqual(key, "uniqueItems")
                        self.assertIs(value, True)
                        removed.append(path + (key,))
                    else:
                        compare(value, after[key], path + (key,))
            else:
                self.assertEqual(after, before)

        compare(original, actual)
        fields = ("subject_parts", "requester_candidates", "input_min", "input_max",
                  "output_voltage", "output_current", "output_power", "topology",
                  "qualification", "continuous_current", "pcb_changes_allowed")
        expected = [("properties", "fields", "properties", field, "properties", "evidence_ids", "uniqueItems")
                    for field in fields]
        expected += [("properties", "fields", "properties", field, "properties", "value", "uniqueItems")
                     for field in ("subject_parts", "requester_candidates")]
        self.assertCountEqual(removed, expected)

    def test_duplicate_part_or_evidence_never_creates_review(self):
        for kind in ("part", "evidence"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(prefix="bom-duplicate-") as directory:
                api = FakeAPI()
                draft = output_draft()
                if kind == "part":
                    values = draft["fields"]["subject_parts"]["value"]
                else:
                    values = draft["fields"]["input_min"]["evidence_ids"]
                self.assertTrue(values)
                values.append(values[0])
                api.response["choices"][0]["message"]["content"] = json.dumps(draft)
                result = smoke.run_once(SENTINEL, directory, api)
                self.assertFalse(result["review_created"])
                self.assertFalse(Path(directory, "review-0.json").exists())
                self.assertEqual(api.posts, 1)
                self.assertNotEqual(result["status"], "pending_review")


if __name__ == "__main__":
    unittest.main()
