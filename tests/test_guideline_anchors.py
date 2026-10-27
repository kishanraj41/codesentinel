"""Curated quotes are verbatim spans of the current edition, and lookup falls back to them."""

import tempfile
import unittest

import requests

from codesentinel.guidelines import EXCERPT_PATH, anchor_rows, collapse_ws, load_anchors
from codesentinel.tavily.client import GuidelineClient, reset_pdf_confirmation
from scripts.extract_guideline_excerpt import PDF_SHA256 as SCRIPT_SHA256


def _pages(excerpt: str) -> dict[int, str]:
    pages: dict[int, list[str]] = {}
    current = None
    for line in excerpt.splitlines():
        if line.startswith("===== PAGE ") and line.endswith("====="):
            current = int(line.split()[2])
            pages[current] = []
            continue
        if current is not None:
            pages[current].append(line)
    return {number: "\n".join(lines) for number, lines in pages.items()}


class AnchorVerbatimTests(unittest.TestCase):
    def test_each_quote_is_a_contiguous_span_of_its_page(self):
        payload = load_anchors()
        self.assertEqual(payload["pdf_sha256"], SCRIPT_SHA256)
        pages = _pages(EXCERPT_PATH.read_text(encoding="utf-8"))
        rows = anchor_rows()
        keys = {row["key"] for row in rows}
        self.assertTrue(
            {
                "E11|combination-code-diabetes-ckd",
                "E11|specificity-hyperglycemia",
                "I12|combination-code-hypertensive-ckd",
                "I13|combination-code-hypertensive-ckd",
                "I50|specificity-upgrade",
                "N18|specificity-upgrade",
            }.issubset(keys)
        )
        for row in rows:
            quote = row["quote"]
            self.assertNotIn("[...]", quote)
            self.assertNotIn("…", quote)
            self.assertGreater(len(quote.split()), 12)
            page = pages[row["page"]]
            self.assertIn(f"Page {row['page']} of 121", page)
            self.assertIn(collapse_ws(quote), collapse_ws(page))


class AnchorLookupTests(unittest.TestCase):
    def setUp(self):
        reset_pdf_confirmation()

    def test_mock_mode_uses_the_anchor_and_does_not_call_tavily(self):
        calls = []

        def transport(url, json=None, timeout=None):
            calls.append(url)
            raise AssertionError("mock mode should not call Tavily")

        client = GuidelineClient(api_key="", use_fixtures=True, transport=transport)
        found = client.lookup("I50", "specificity-upgrade")
        self.assertEqual(calls, [])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].retrieved, "anchor")
        self.assertEqual(found[0].source_tier, "official")
        self.assertEqual(found[0].source_label, "Official guidelines")
        self.assertEqual(found[0].section, "I.C.9.a.3")
        self.assertEqual(found[0].page, 51)
        self.assertIn("type of heart failure", found[0].quote)

    def test_pdf_failure_keeps_the_anchor_and_logs_the_class(self):
        def transport(url, json=None, timeout=None):
            raise AssertionError("no verified text, so Tavily is not searched")

        def pdf_fetch(url, **kwargs):
            raise requests.ConnectionError("bearer sk-should-not-appear")

        with tempfile.TemporaryDirectory() as tmp:
            client = GuidelineClient(
                api_key="sk-should-not-appear",
                use_fixtures=False,
                cache_dir=tmp,
                transport=transport,
                pdf_fetch=pdf_fetch,
            )
            found = client.lookup("I12", "combination-code-hypertensive-ckd")
            self.assertEqual(found[0].section, "I.C.9.a.2")
            self.assertEqual(found[0].provenance, "curated_fallback")
            self.assertEqual(found[0].anchor_status, "unverified")
            self.assertFalse(found[0].verified_verbatim)
            self.assertIn("N18", found[0].quote)
            self.assertEqual(len(client.degraded_reasons), 1)
            reason = client.degraded_reasons[0]
            self.assertIn("ConnectionError", reason)
            self.assertIn("FY 2027", reason)
            self.assertNotIn("sk-should-not-appear", reason)
            self.assertNotIn("bearer", reason)

            later_calls = {"n": 0}

            def later_fetch(url, **kwargs):
                later_calls["n"] += 1
                raise requests.ConnectionError("again")

            later = GuidelineClient(
                api_key="sk-should-not-appear",
                use_fixtures=False,
                cache_dir=tmp,
                transport=transport,
                pdf_fetch=later_fetch,
            )
            again = later.lookup("E11", "combination-code-diabetes-ckd")
        self.assertEqual(later_calls["n"], 0)
        self.assertEqual(again[0].section, "I.A.15")
        self.assertIn("ConnectionError", later.degraded_reasons[0])


if __name__ == "__main__":
    unittest.main()
