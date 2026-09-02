import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openai_otp_fallback import (
    OpenAIConfig,
    OpenAIFallbackClient,
    response_schema,
    sanitize_text,
)
from otp_bge_openai_gate import (
    ROUTE_BGE_DIRECT,
    ROUTE_OPENAI_FALLBACK,
    build_candidate_evidence,
    route_with_bge,
)


def records_for(text, values_and_patterns):
    records = []
    search_from = 0
    for index, (raw, normalized, pattern) in enumerate(values_and_patterns):
        start = text.index(raw, search_from)
        records.append(
            {
                "candidate_id": f"C{index}",
                "raw": raw,
                "code": normalized,
                "pattern": pattern,
                "start": start,
                "end": start + len(raw),
            }
        )
        search_from = start + len(raw)
    return records


class BGEOpenAIGateTests(unittest.TestCase):
    def gate(self, text, values_and_patterns, scores, bge_top3):
        records = records_for(text, values_and_patterns)
        evidence = build_candidate_evidence(text, records, scores)
        return route_with_bge(text, evidence, bge_top3)

    def test_confident_single_candidate_stays_with_bge(self):
        decision = self.gate(
            "Your verification code is 853152.",
            [("853152", "853152", "bounded_alnum")],
            {"C0": (0.71, 0.31)},
            ["853152"],
        )
        self.assertEqual(decision.route, ROUTE_BGE_DIRECT)
        self.assertEqual(decision.reasons, ["BGE_CONFIDENT"])

    def test_gate_never_selects_a_candidate(self):
        decision = self.gate(
            "Your verification code is 853152.",
            [("853152", "853152", "bounded_alnum")],
            {"C0": (0.71, 0.31)},
            ["853152"],
        )
        self.assertFalse(hasattr(decision, "selected_candidate_id"))

    def test_multiple_candidates_call_openai(self):
        decision = self.gate(
            "Your code is 853152. Copyright 2026.",
            [
                ("853152", "853152", "bounded_alnum"),
                ("2026", "2026", "bounded_alnum"),
            ],
            {"C0": (0.64, 0.20), "C1": (0.61, 0.18)},
            ["853152", "2026"],
        )
        self.assertEqual(decision.route, ROUTE_OPENAI_FALLBACK)
        self.assertIn("MULTIPLE_CANDIDATES", decision.reasons)
        self.assertIn("STRUCTURAL_YEAR", decision.reasons)
        self.assertIn("TOP1_TOP2_CLOSE", decision.reasons)

    def test_low_bge_with_code_cue_calls_openai(self):
        decision = self.gate(
            "1692 is your Uber code.",
            [("1692", "1692", "bounded_alnum")],
            {"C0": (0.4563, 0.22)},
            [],
        )
        self.assertEqual(decision.route, ROUTE_OPENAI_FALLBACK)
        self.assertIn("LOW_BGE_SCORE_WITH_CODE_CUE", decision.reasons)
        self.assertIn("BGE_CANDIDATE_VS_NO_OTP_UNCERTAIN", decision.reasons)

    def test_notification_competing_with_otp_calls_openai(self):
        decision = self.gate(
            "Your two-factor authentication method ending in 3788 was changed.",
            [("3788", "3788", "bounded_alnum")],
            {"C0": (0.56, 0.58)},
            ["3788"],
        )
        self.assertEqual(decision.route, ROUTE_OPENAI_FALLBACK)
        self.assertIn("BGE_MIXED_OTP_NOTIFICATION", decision.reasons)
        self.assertIn("STRUCTURAL_PHONE_SUFFIX", decision.reasons)

    def test_uppercase_candidate_calls_openai(self):
        decision = self.gate(
            "Your verification code is GQQCTJ.",
            [("GQQCTJ", "GQQCTJ", "uppercase_alpha")],
            {"C0": (0.68, 0.24)},
            ["GQQCTJ"],
        )
        self.assertEqual(decision.route, ROUTE_OPENAI_FALLBACK)
        self.assertIn("UPPERCASE_ALPHA_CANDIDATE", decision.reasons)

    def test_no_candidate_stays_with_empty_bge_result(self):
        decision = route_with_bge("Security notice only.", [], [])
        self.assertEqual(decision.route, ROUTE_BGE_DIRECT)
        self.assertEqual(decision.reasons, ["NO_REGEX_CANDIDATE"])

    def test_structured_output_schema_only_allows_candidate_ids(self):
        schema = response_schema(["C0", "C1"])
        self.assertEqual(
            schema["properties"]["selected_candidate_id"]["enum"],
            ["C0", "C1", None],
        )

    def test_sanitization_removes_email_and_url(self):
        cleaned = sanitize_text("Send to user@example.com via https://example.com/token")
        self.assertNotIn("user@example.com", cleaned)
        self.assertNotIn("https://", cleaned)

    def test_mock_api_decision_and_usage(self):
        captured = {}

        class MockResponses:
            def create(self, **kwargs):
                captured.update(kwargs)
                return SimpleNamespace(
                    id="resp_test",
                    model="gpt-5.6-luna",
                    output_text=json.dumps(
                        {
                            "message_type": "OTP",
                            "selected_candidate_id": "C1",
                            "ranked_candidate_ids": ["C1", "C0"],
                            "reason_code": "OTP_WITH_DISTRACTORS",
                        }
                    ),
                    usage=SimpleNamespace(
                        input_tokens=100,
                        output_tokens=20,
                        total_tokens=120,
                        input_tokens_details=SimpleNamespace(
                            cached_tokens=10,
                            cache_write_tokens=5,
                        ),
                        output_tokens_details=SimpleNamespace(reasoning_tokens=3),
                    ),
                )

        client = OpenAIFallbackClient.__new__(OpenAIFallbackClient)
        client.config = OpenAIConfig(api_key="test")
        client.client = SimpleNamespace(responses=MockResponses())
        candidates = [
            {
                "candidate_id": "C0",
                "value": "2026",
                "pattern": "bounded_alnum",
                "context": "Copyright 2026",
                "bge_otp_score": 0.61,
                "bge_notification_score": 0.18,
                "bge_rank": 2,
                "structural_flags": ["YEAR"],
            },
            {
                "candidate_id": "C1",
                "value": "853152",
                "pattern": "bounded_alnum",
                "context": "Your verification code is 853152",
                "bge_otp_score": 0.64,
                "bge_notification_score": 0.20,
                "bge_rank": 1,
                "structural_flags": [],
            },
        ]
        decision = client.decide(
            "Security code",
            "message",
            candidates,
            ["MULTIPLE_CANDIDATES"],
            ["853152", "2026"],
        )

        self.assertEqual(decision.selected_candidate_id, "C1")
        self.assertEqual(decision.ranked_candidate_ids, ["C1", "C0"])
        self.assertEqual(decision.usage.input_tokens, 100)
        self.assertEqual(decision.usage.cached_input_tokens, 10)
        self.assertGreater(decision.usage.estimated_cost_usd, 0)
        self.assertFalse(captured["store"])
        self.assertEqual(captured["text"]["format"]["type"], "json_schema")
        payload = json.loads(captured["input"])
        self.assertEqual(payload["bge_top3_values"], ["853152", "2026"])
        self.assertNotIn("ground_truth", payload)


if __name__ == "__main__":
    unittest.main()
