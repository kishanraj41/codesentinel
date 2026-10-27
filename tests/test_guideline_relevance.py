"""Citation quotes have to mention the code, category, or issue. Off-topic passages are dropped."""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from codesentinel.tavily.client import (
    FILTER_VERSION,
    GuidelineClient,
    disk_cache_path,
    guideline_query,
    passage_score,
    relevant_quote,
)


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
HEART_FAILURE_LOOSE = (
    "Chronic systolic heart failure, including HFrEF and acute on chronic diastolic failure, "
    "is reported from the type and acuity documented in the record."
)
OFFICIAL_PDF = "https://www.cms.gov/files/document/fy-2026-icd-10-cm-coding-guidelines.pdf"
OFFICIAL_TITLE = "ICD-10-CM Official Guidelines for Coding and Reporting FY 2026"
MMRR_URL = "https://www.cms.gov/mmrr/Downloads/mmrr201100102a02pdf"
MEARIS_URL = "https://mearis.cms.gov/public/new-technology-add-on-payment"
CMS_PAGE = "https://www.cms.gov/medicare/coding-billing/icd-10-codes"


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

    def test_heart_failure_specificity_does_not_require_the_exact_code(self):
        self.assertGreater(passage_score(HEART_FAILURE_LOOSE, "I50", "specificity-upgrade"), 0)
        quote = relevant_quote(HEART_FAILURE_LOOSE, "I50", "specificity-upgrade")
        self.assertIn("HFrEF", quote)
        self.assertNotIn("I50.22", quote)
        hyphen = (
            "Document acute on chronic systolic heart-failure, or HFpEF, "
            "rather than leaving the type unspecified."
        )
        self.assertGreater(passage_score(hyphen, "I50", "specificity-upgrade"), 0)
        code_only = (
            "Assign I50.22 when the record documents that more specific heart-failure code."
        )
        self.assertGreater(passage_score(code_only, "I50", "specificity-upgrade"), 0)
        blob = ("boilerplate " * 80) + HEART_FAILURE_LOOSE
        window = relevant_quote(blob, "I50", "specificity-upgrade")
        self.assertIn("HFrEF", window)
        self.assertIn("heart failure", window.lower())

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
            cache = disk_cache_path(tmp, "I50", "specificity-upgrade")
            cache.write_text(json.dumps({
                "query": guideline_query("I50", "specificity-upgrade"),
                "filter_version": FILTER_VERSION,
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

    def test_i50_query_names_the_type_and_the_official_guidelines(self):
        query = guideline_query("I50", "specificity-upgrade")
        self.assertIn("heart failure", query.lower())
        self.assertIn("HFrEF", query)
        self.assertIn("acute on chronic", query)
        self.assertIn("ICD-10-CM Official Guidelines for Coding and Reporting", query)
        self.assertIn("FY2026", query)
        diabetes = guideline_query("E11", "combination-code-diabetes-ckd")
        self.assertIn("E11.22", diabetes)
        self.assertIn("FY2026", diabetes)

    def test_official_guidelines_pdf_outranks_secondary_cms_pages(self):
        calls = []

        def transport(url, json=None, timeout=None):
            calls.append((url, json or {}))
            if url.endswith("/extract"):
                target = (json or {}).get("urls", [""])[0]
                text = COMBINATION if "fy-2026" in target else CHEST_PAIN
                return _Response({"results": [{"raw_content": text}], "usage": {"credits": 1}})
            body = json or {}
            self.assertEqual(body.get("include_domains"), ["cms.gov", "cdc.gov"])
            self.assertIn("ICD-10-CM Official Guidelines for Coding and Reporting", body.get("query") or "")
            self.assertIn("FY2026", body.get("query") or "")
            return _Response({
                "results": [
                    {"url": MMRR_URL, "title": "ICD-9 to ICD-10 translation", "content": COMBINATION},
                    {"url": MEARIS_URL, "title": "NTAP new technology add-on", "content": COMBINATION},
                    {"url": OFFICIAL_PDF, "title": OFFICIAL_TITLE, "content": "See the guidelines PDF."},
                ],
                "usage": {"credits": 2},
            })

        with tempfile.TemporaryDirectory() as tmp:
            client = GuidelineClient(api_key="test-key", use_fixtures=False, cache_dir=tmp, transport=transport)
            found = client.lookup("E11", "combination-code-diabetes-ckd")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].url, OFFICIAL_PDF)
        self.assertEqual(found[0].source_tier, "official")
        self.assertIn("E11.22", found[0].quote)
        extracted = [body.get("urls", [""])[0] for url, body in calls if str(url).endswith("/extract")]
        self.assertIn(OFFICIAL_PDF, extracted)
        self.assertNotIn(MMRR_URL, extracted)

    def test_secondary_source_is_used_only_when_the_guidelines_do_not_match(self):
        def transport(url, json=None, timeout=None):
            if url.endswith("/extract"):
                target = (json or {}).get("urls", [""])[0]
                if "fy-2026" in target:
                    return _Response({"results": [{"raw_content": CHEST_PAIN}], "usage": {"credits": 1}})
                return _Response({"results": [{"raw_content": COMBINATION}], "usage": {"credits": 1}})
            return _Response({
                "results": [
                    {"url": OFFICIAL_PDF, "title": OFFICIAL_TITLE, "content": CODE_LENGTH},
                    {"url": MMRR_URL, "title": "ICD-9 to ICD-10 translation", "content": COMBINATION},
                    {"url": CMS_PAGE, "title": "ICD-10-CM codes", "content": COMBINATION},
                ],
                "usage": {"credits": 2},
            })

        with tempfile.TemporaryDirectory() as tmp:
            client = GuidelineClient(api_key="test-key", use_fixtures=False, cache_dir=tmp, transport=transport)
            found = client.lookup("E11", "combination-code-diabetes-ckd")
        self.assertEqual(found[0].url, CMS_PAGE)
        self.assertEqual(found[0].source_tier, "secondary")
        self.assertNotIn("mmrr", found[0].url)

    def test_negative_lookup_is_cached_on_query_and_filter_version(self):
        calls = []

        def transport(url, json=None, timeout=None):
            calls.append(url)
            if url.endswith("/extract"):
                return _Response({"results": [{"raw_content": CODE_LENGTH}], "usage": {"credits": 1}})
            return _Response({
                "results": [{
                    "url": CMS_PAGE,
                    "title": "ICD-10-CM",
                    "content": CODE_LENGTH,
                }],
                "usage": {"credits": 2},
            })

        with tempfile.TemporaryDirectory() as tmp:
            client = GuidelineClient(api_key="test-key", use_fixtures=False, cache_dir=tmp, transport=transport)
            self.assertEqual(client.lookup("I50", "specificity-upgrade"), [])
            first = len(calls)
            self.assertGreater(first, 0)
            self.assertEqual(client.lookup("I50", "specificity-upgrade"), [])
            self.assertEqual(len(calls), first)
            self.assertGreater(client.cache_hits, 0)

            later = GuidelineClient(api_key="test-key", use_fixtures=False, cache_dir=tmp, transport=transport)
            self.assertEqual(later.lookup("I50", "specificity-upgrade"), [])
            self.assertEqual(len(calls), first)
            self.assertEqual(later.live_requests, 0)
            self.assertGreater(later.cache_hits, 0)

            path = disk_cache_path(tmp, "I50", "specificity-upgrade")
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["citations"], [])
            self.assertEqual(payload["filter_version"], FILTER_VERSION)
            self.assertEqual(payload["query"], guideline_query("I50", "specificity-upgrade"))
            payload["filter_version"] = "0"
            path.write_text(json.dumps(payload), encoding="utf-8")
            stale = GuidelineClient(api_key="test-key", use_fixtures=False, cache_dir=tmp, transport=transport)
            self.assertEqual(stale.lookup("I50", "specificity-upgrade"), [])
            self.assertGreater(len(calls), first)


if __name__ == "__main__":
    unittest.main()
