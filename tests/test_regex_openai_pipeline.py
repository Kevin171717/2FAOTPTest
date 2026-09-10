import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from openai_otp_fallback import OpenAIConfig, OpenAIFallbackClient
from regex_v2_candidates import extract_candidates
from run_regex_openai_experiment import subject_proxy_from_note


class RegexOpenAIPipelineTests(unittest.TestCase):
    def test_regex_candidate_recall_is_100_percent_for_otp_rows(self):
        dataset = pd.read_excel(
            ROOT / "otp_dataset_dedup.xlsx",
            sheet_name="dataset_dedup",
            dtype=str,
            keep_default_na=False,
        )
        for zero_index, row in dataset.iterrows():
            sequence = zero_index + 1
            ground_truth = str(row["ground_truth"]).strip()
            if not ground_truth or ground_truth.lower() in {"none", "null", "nan", "n/a", "na"}:
                continue
            if ground_truth.startswith(("http://", "https://")):
                continue
            subject = subject_proxy_from_note(row.get("note", ""))
            text = f"{subject}\n{row['text']}" if subject else row["text"]
            actual = [candidate["value"] for candidate in extract_candidates(text)]
            self.assertIn(ground_truth, actual, f"Regex recall miss at row {sequence}")

    def test_url_token_is_not_sent_as_otp_candidate(self):
        text = "025060 is your verification code. https://goo.gl/UERgF7"
        self.assertEqual([row["value"] for row in extract_candidates(text)], ["025060"])

    def test_mock_regex_only_api_contains_no_bge_or_ground_truth(self):
        captured = {}

        class MockResponses:
            def create(self, **kwargs):
                captured.update(kwargs)
                return SimpleNamespace(
                    id="resp_regex_test",
                    model="gpt-4o",
                    output_text=json.dumps(
                        {
                            "message_type": "OTP",
                            "selected_candidate_id": "C0",
                            "ranked_candidate_ids": ["C0"],
                            "reason_code": "EXPLICIT_ACTIONABLE_OTP",
                        }
                    ),
                    usage=SimpleNamespace(
                        input_tokens=80,
                        output_tokens=15,
                        total_tokens=95,
                        input_tokens_details=SimpleNamespace(
                            cached_tokens=0,
                            cache_write_tokens=0,
                        ),
                        output_tokens_details=SimpleNamespace(reasoning_tokens=0),
                    ),
                )

        client = OpenAIFallbackClient.__new__(OpenAIFallbackClient)
        client.config = OpenAIConfig(api_key="test")
        client.client = SimpleNamespace(responses=MockResponses())
        candidates = extract_candidates("Your verification code is 853152.")
        decision = client.decide_regex_only("Security code", "Your code is 853152.", candidates)

        self.assertEqual(decision.selected_candidate_id, "C0")
        payload = json.loads(captured["input"])
        serialized = json.dumps(payload)
        self.assertNotIn("bge", serialized.lower())
        self.assertNotIn("ground_truth", serialized)
        self.assertEqual(captured["prompt_cache_key"], "otp-regex-openai-v1")
        self.assertFalse(captured["store"])
        self.assertNotIn("verbosity", captured["text"])
        self.assertNotIn("reasoning", captured)

    def test_raw_api_receives_the_unmodified_message_without_candidates(self):
        captured = {}
        raw_message = "  User@example.com\nCode: 12 34 56\nhttps://example.com/a?x=1  "

        class MockResponses:
            def create(self, **kwargs):
                captured.update(kwargs)
                return SimpleNamespace(
                    id="resp_raw_test",
                    model="gpt-4o",
                    output_text=json.dumps(
                        {
                            "message_type": "OTP_AND_LINK",
                            "otp_codes": ["12 34 56"],
                            "verification_url": "https://example.com/a?x=1",
                            "reason_code": "OTP_AND_VERIFICATION_LINK",
                        }
                    ),
                    usage=SimpleNamespace(
                        input_tokens=90,
                        output_tokens=20,
                        total_tokens=110,
                        input_tokens_details=SimpleNamespace(cached_tokens=0, cache_write_tokens=0),
                        output_tokens_details=SimpleNamespace(reasoning_tokens=0),
                    ),
                )

        client = OpenAIFallbackClient.__new__(OpenAIFallbackClient)
        client.config = OpenAIConfig(api_key="test")
        client.client = SimpleNamespace(responses=MockResponses())
        decision = client.decide_raw_message(raw_message)

        payload = json.loads(captured["input"])
        self.assertEqual(payload, {"message": raw_message})
        self.assertNotIn("candidates", payload)
        self.assertEqual(decision.otp_codes, ["12 34 56"])
        self.assertEqual(decision.verification_url, "https://example.com/a?x=1")
        self.assertEqual(captured["prompt_cache_key"], "otp-raw-openai-v1")
        self.assertEqual(captured["max_output_tokens"], 1200)
        self.assertFalse(captured["store"])


if __name__ == "__main__":
    unittest.main()
