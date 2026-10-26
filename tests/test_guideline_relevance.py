"""Citation quotes have to mention the code, category, or issue. Off-topic passages are dropped."""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from codesentinel.tavily.client import GuidelineClient, passage_score, relevant_quote


CHEST_PAIN = (
    "Chest pain and related codes include R07.9 Chest pain unspecified, "
    "R07.1 Chest pain on breathing, I20.0 Unstable angina, and I21 Acute myocardial infarction."
)
CODE_LENGTH = (
    "Diagnosis codes are to be used and reported at their highest number of characters available "
    "and to the highest level of specificity documented in the medical record. "
    "ICD-10-CM codes contain 3, 4, 5, 6, or 7 characters."
)
TRANSPLANT = (
    "Kidney transplant status is reported with Z94.0. Chronic kidney disease after transplant "
    "may require an additional code. Do not assume the transplant alone is a complication."
)
HYPERGLYCEMIA = (
    "Type 2 diabetes mellitus with hyperglycemia is coded E11.65 rather than E11.9 "
    "when hyperglycemia is documented."
)
COMBINATION = (
    "The with convention presumes a causal relationship. Type 2 diabetes mellitus with "
    "chronic kidney disease is reported with combination code E11.22."
)
HEART_FAILURE = (
    "Heart failure codes in category I50 are selected to the type documented, "
    "including systolic heart failure I50.22."
)


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class RelevanceTests(unittest.TestCase):
    def test_off_topic_passages_score_zero(self):
        self.assertEqual(passage_score(CHEST_PAIN, "E11", "specificity-hyperglycemia"), 0)
        self.assertIsNone(relevant_quote(CHEST_PAIN, "E11", "specificity-hyperglycemia"))
        self.assertEqual(passage_score(CODE_LENGTH, "I50", "specificity-upgrade"), 0)
        self.assertIsNone(relevant_quote(CODE_LENGTH, "I50", "specificity-upgrade"))
        self.assertEqual(passage_score(TRANSPLANT, "E11", "combination-code-diabetes-ckd"), 0)
        self.assertIsNone(relevant_quote(TRANSPLANT, "E11", "combination-code-diabetes-ckd"))

    def test_relevant_sentence_wins_over_a_junk_lead(self):
        mixed_hyper = CHEST_PAIN + "\n" + HYPERGLYCEMIA
        quote = relevant_quote(mixed_hyper, "E11", "specificity-hyperglycemia")
        self.assertIn("E11.65", quote)
        self.assertNotIn("Chest pain", quote)

        mixed_combo = TRANSPLANT + "\n" + COMBINATION
        combo = relevant_quote(mixed_combo, "E11", "combination-code-diabetes-ckd")
        self.assertIn("E11.22", combo)
        self.assertNotIn("Z94.0", combo)

        mixed_hf = CODE_LENGTH + "\n" + HEART_FAILURE
        heart = relevant_quote(mixed_hf, "I50", "specificity-upgrade")
        self.assertIn("I50", heart)
        self.assertNotIn("7 characters", heart)

    def test_live_lookup_drops_junk_and_keeps_the_matching_passage(self):
        calls = []

        def transport(url, json=None, timeout=None):
            calls.append(url)
            if url.endswith("/extract"):
                return _Response({
                    "results": [{
                        "raw_content": CHEST_PAIN + "\n" + HYPERGLYCEMIA,
                    }],
                    "usage": {"credits": 1},
                })
            return _Response({
                "results": [{
                    "url": "https://www.cms.gov/medicare/coding-billing/icd-10-codes",
                    "title": "ICD-10-CM",
                    "content": CHEST_PAIN,
                }],
                "usage": {"credits": 2},
            })

        with tempfile.TemporaryDirectory() as tmp:
            client = GuidelineClient(api_key="test-key", use_fixtures=False, cache_dir=tmp, transport=transport)
            found = client.lookup("E11", "specificity-hyperglycemia")
        self.assertEqual(len(found), 1)
        self.assertIn("E11.65", found[0].quote)
        self.assertNotIn("Chest pain", found[0].quote)
        self.assertTrue(any(url.endswith("/extract") for url in calls))

    def test_live_lookup_marks_low_confidence_when_nothing_matches(self):
        def transport(url, json=None, timeout=None):
            if url.endswith("/extract"):
                return _Response({"results": [{"raw_content": CODE_LENGTH}], "usage": {"credits": 1}})
            return _Response({
                "results": [{
                    "url": "https://www.cms.gov/medicare/coding-billing/icd-10-codes",
                    "title": "ICD-10-CM",
                    "content": CODE_LENGTH,
                }],
                "usage": {"credits": 2},
            })

        with tempfile.TemporaryDirectory() as tmp:
            client = GuidelineClient(api_key="test-key", use_fixtures=False, cache_dir=tmp, transport=transport)
            found = client.lookup("I50", "specificity-upgrade")
        self.assertEqual(found, [])
        self.assertTrue(any("low-confidence" in reason for reason in client.degraded_reasons))

    def test_cached_junk_is_not_served(self):
        def transport(url, json=None, timeout=None):
            if url.endswith("/extract"):
                return _Response({"results": [{"raw_content": HEART_FAILURE}], "usage": {"credits": 1}})
            return _Response({
                "results": [{
                    "url": "https://www.cdc.gov/nchs/icd/icd-10-cm.htm",
                    "title": "ICD-10-CM",
                    "content": CODE_LENGTH,
                }],
                "usage": {"credits": 2},
            })

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "I50__specificity-upgrade.json"
            cache.write_text(json.dumps({
                "cached_at": datetime.now(timezone.utc).isoformat(),
                "citations": [{
                    "url": "https://www.cms.gov/medicare/coding-billing/icd-10-codes",
                    "quote": CODE_LENGTH,
                    "source": "cms.gov",
                    "title": "ICD-10-CM",
                }],
            }), encoding="utf-8")
            client = GuidelineClient(api_key="test-key", use_fixtures=False, cache_dir=tmp, transport=transport)
            found = client.lookup("I50", "specificity-upgrade")
        self.assertEqual(len(found), 1)
        self.assertIn("heart failure", found[0].quote.lower())
        self.assertTrue(any("cached citation" in reason for reason in client.degraded_reasons))

    def test_fixtures_are_not_run_through_the_relevance_filter(self):
        client = GuidelineClient(api_key="", use_fixtures=True)
        found = client.lookup("I50", "specificity-upgrade")
        self.assertTrue(found)
        self.assertIn("Not a live official excerpt", found[0].quote)


if __name__ == "__main__":
    unittest.main()
