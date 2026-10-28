"""Current-year citations: Tavily finds the passage, the official PDF text verifies it.

Everything here is mocked. No network.
"""

import json
import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from unittest import mock

from codesentinel.guidelines import anchor_for, excerpt_official_text, excerpt_pages
from codesentinel.guidelines.editions import EDITIONS, current_edition, fiscal_year_for
from codesentinel.guidelines.official_text import OfficialText
from codesentinel.nemotron.mock_reasoner import apply_guideline_gate
from codesentinel.tavily.client import GuidelineClient, live_query, reset_pdf_confirmation

FY27 = EDITIONS[2027]
FY27_URL = FY27.pdf_url
FY26_URL = EDITIONS[2026].pdf_url
NOW = datetime(2026, 10, 27, 22, 0, tzinfo=timezone.utc)

I12_LIVE = (
    "be assigned. Sequence according to the circumstances of the admission/encounter. "
    "2) Hypertensive Chronic Kidney Disease Assign codes from category I12, Hypertensive chronic "
    "kidney disease, when both hypertension and a condition classifiable to category N18, Chronic "
    "kidney disease (CKD), are present. CKD should not be coded as hypertensive if the provider "
    "indicates the CKD is not related to the hypertension. The appropriate code from category N18 "
    "should be used as a secondary code with a code from category I12 to identify the stage of "
    "chronic kidney disease. See Section I.C.14. Chronic kidney disease. ICD-10-CM Official "
    "Guidelines for Coding and Reporting FY 2027 Page 51 of 121 If a patient h"
)


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _search(results, calls):
    def transport(url, json=None, timeout=None):
        calls.append({"url": url, "body": json or {}})
        if url.endswith("/extract"):
            return _Response({"results": [], "usage": {"credits": 1}})
        return _Response({"results": results, "usage": {"credits": 2}})

    return transport


def _client(tmp, transport, text=None):
    return GuidelineClient(
        api_key="test-key",
        use_fixtures=False,
        cache_dir=tmp,
        transport=transport,
        now=NOW,
        official_text=text or excerpt_official_text(),
    )


def _superseded_text():
    """The FY 2027 excerpt with the I.C.9.a.2 sentence reworded, as if CMS had revised it."""
    pages = excerpt_pages()
    page = pages[50]
    old = "when both hypertension and a condition classifiable to category N18"
    assert old in " ".join(page.split()), "fixture drifted"
    reworded = " ".join(page.split()).replace(old, "when hypertension and one or more conditions classifiable to category N18")
    pages[50] = reworded
    ordered = [pages.get(n, "") for n in range(1, FY27.pages + 1)]
    return OfficialText(FY27, ordered, "test-sha", FY27_URL)


class EditionTests(unittest.TestCase):
    def test_fiscal_year_turns_over_on_october_first(self):
        self.assertEqual(fiscal_year_for(date(2026, 9, 30)), 2026)
        self.assertEqual(fiscal_year_for(date(2026, 10, 1)), 2027)
        self.assertEqual(current_edition(date(2026, 10, 27)).label, "FY 2027")
        self.assertEqual(current_edition(date(2026, 3, 1)).label, "FY 2026")

    def test_override_and_unknown_years(self):
        with mock.patch.dict(os.environ, {"GUIDELINE_FISCAL_YEAR": "FY2026"}):
            self.assertEqual(current_edition(date(2026, 10, 27)).fiscal_year, 2026)
        with mock.patch.dict(os.environ, {"GUIDELINE_FISCAL_YEAR": ""}):
            self.assertEqual(current_edition(date(2030, 1, 1)).fiscal_year, 2027)

    def test_edition_url_matching(self):
        self.assertTrue(FY27.matches_url(FY27_URL))
        self.assertTrue(FY27.matches_url(FY27.mirror_urls[0]))
        self.assertFalse(FY27.matches_url(FY26_URL))


class OfficialTextTests(unittest.TestCase):
    def test_find_ignores_whitespace_quotes_and_the_running_header(self):
        text = excerpt_official_text()
        row = anchor_for("E11", "combination-code-diabetes-ckd")
        straight = row["quote"].replace("“", '"').replace("”", '"').replace(" ", "  ")
        match = text.find(straight)
        self.assertIsNotNone(match)
        self.assertEqual(match.page, 11)
        self.assertEqual(match.section, "I.A.15")
        self.assertIn("“with”", match.text)

    def test_reworded_text_is_not_found(self):
        text = excerpt_official_text()
        self.assertIsNone(text.find("Assign codes from category I12 whenever hypertension and CKD are both present in the chart."))
        self.assertIsNone(text.find("too short"))

    def test_every_anchor_is_found_on_its_page_and_section(self):
        text = excerpt_official_text()
        for key in (
            ("E11", "combination-code-diabetes-ckd"),
            ("E11", "specificity-hyperglycemia"),
            ("I12", "combination-code-hypertensive-ckd"),
            ("I13", "combination-code-hypertensive-ckd"),
            ("I50", "specificity-upgrade"),
            ("N18", "specificity-upgrade"),
        ):
            row = anchor_for(*key)
            match = text.find(row["quote"])
            self.assertIsNotNone(match, key)
            self.assertEqual(match.page, row["page"], key)
            self.assertEqual(match.section, row["section"], key)
            self.assertEqual(row["verified_in"]["FY 2027"], row["page"])


class LiveCitationTests(unittest.TestCase):
    def setUp(self):
        reset_pdf_confirmation()

    def test_live_passage_is_verified_and_carries_provenance(self):
        calls = []
        results = [
            {"url": FY27_URL, "title": "FY 2027 ICD-10-CM Coding Guidelines", "content": I12_LIVE},
            {"url": "https://example.com/coding-blog", "title": "blog", "content": I12_LIVE},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            client = _client(tmp, _search(results, calls))
            found = client.lookup("I12", "combination-code-hypertensive-ckd")
            again = client.lookup("I12", "combination-code-hypertensive-ckd")
            self.assertIs(found, again)
            self.assertEqual(len(calls), 1)
            body = calls[0]["body"]
            self.assertEqual(body["include_domains"], ["cms.gov", "cdc.gov"])
            self.assertIn("FY 2027", body["query"])
            self.assertIn("ICD-10-CM Official Guidelines for Coding and Reporting", body["query"])
            self.assertEqual(client.credits, 2)

            citation = found[0]
            self.assertEqual(citation.provenance, "tavily_live")
            self.assertEqual(citation.retrieved, "live")
            self.assertEqual(citation.fiscal_year, "FY 2027")
            self.assertEqual(citation.url, FY27_URL)
            self.assertEqual(citation.section, "I.C.9.a.2")
            self.assertEqual(citation.page, 50)
            self.assertTrue(citation.verified_verbatim)
            self.assertEqual(citation.anchor_status, "verified")
            self.assertTrue(citation.quote.startswith("Assign codes from category I12"))
            self.assertIn("to identify the stage of chronic kidney disease.", citation.quote)
            self.assertNotIn("Page 51 of 121", citation.quote)
            self.assertNotIn("If a patient h", citation.quote)

            # A second client in the same run reads the per-run cache instead of searching.
            later_calls = []
            later = _client(tmp, _search(results, later_calls))
            cached = later.lookup("I12", "combination-code-hypertensive-ckd")
            self.assertEqual(later_calls, [])
            self.assertEqual(cached[0].provenance, "tavily_live")
            self.assertEqual(cached[0].retrieved, "cache")
            self.assertEqual(cached[0].quote, citation.quote)
            self.assertEqual(later.credits, 0)

    def test_paraphrased_passage_falls_back_to_the_curated_anchor(self):
        calls = []
        paraphrase = (
            "Assign codes from category I12 whenever hypertension and chronic kidney disease are both "
            "documented in the record. Use an N18 code for the stage."
        )
        results = [{"url": FY27_URL, "title": "FY 2027 ICD-10-CM Coding Guidelines", "content": paraphrase}]
        with tempfile.TemporaryDirectory() as tmp:
            client = _client(tmp, _search(results, calls))
            found = client.lookup("I12", "combination-code-hypertensive-ckd")
        self.assertEqual(len(calls), 1)
        self.assertEqual(found[0].provenance, "curated_fallback")
        self.assertEqual(found[0].retrieved, "anchor")
        self.assertEqual(found[0].fiscal_year, "FY 2027")
        self.assertTrue(found[0].verified_verbatim)
        self.assertEqual(found[0].anchor_status, "verified")
        self.assertEqual(client.degraded_reasons, [])
        self.assertTrue(any("matched the official PDF verbatim" in note for note in client.notes))

    def test_prior_year_pdf_is_not_cited_as_current(self):
        calls = []
        results = [{"url": FY26_URL, "title": "FY 2026 ICD-10-CM Coding Guidelines", "content": I12_LIVE.replace("FY 2027", "FY 2026")}]
        with tempfile.TemporaryDirectory() as tmp:
            client = _client(tmp, _search(results, calls))
            found = client.lookup("I12", "combination-code-hypertensive-ckd")
        self.assertEqual(found[0].provenance, "curated_fallback")
        self.assertNotIn("fy-2026", found[0].url)

    def test_tavily_error_falls_back_without_leaking_the_key(self):
        def transport(url, json=None, timeout=None):
            raise RuntimeError("Authorization: Bearer test-key")

        with tempfile.TemporaryDirectory() as tmp:
            client = _client(tmp, transport)
            found = client.lookup("N18", "specificity-upgrade")
        self.assertEqual(found[0].provenance, "curated_fallback")
        self.assertTrue(client.degraded_reasons)
        self.assertNotIn("test-key", " ".join(client.degraded_reasons))


class SupersededAnchorTests(unittest.TestCase):
    def setUp(self):
        reset_pdf_confirmation()

    def test_superseded_anchor_is_replaced_by_the_live_citation(self):
        calls = []
        revised = I12_LIVE.replace(
            "when both hypertension and a condition classifiable to category N18",
            "when hypertension and one or more conditions classifiable to category N18",
        )
        results = [{"url": FY27_URL, "title": "FY 2027 ICD-10-CM Coding Guidelines", "content": revised}]
        with tempfile.TemporaryDirectory() as tmp:
            client = _client(tmp, _search(results, calls), text=_superseded_text())
            found = client.lookup("I12", "combination-code-hypertensive-ckd")
        citation = found[0]
        self.assertEqual(citation.provenance, "tavily_live")
        self.assertEqual(citation.anchor_status, "superseded")
        self.assertIn("one or more conditions", citation.quote)
        self.assertEqual(citation.supersedes["section"], "I.C.9.a.2")
        self.assertIn("when both hypertension", citation.supersedes["quote"])
        self.assertEqual(client.superseded_anchors, ["I12|combination-code-hypertensive-ckd"])

        decision, rationale = apply_guideline_gate("accept", "combination", [citation.as_dict()], "Chart supports I12.9.")
        self.assertEqual(decision, "accept")
        self.assertIn("FY 2027 guideline text", rationale)
        self.assertIn("superseded", rationale)

    def test_superseded_anchor_without_a_live_passage_holds_the_row(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            client = _client(tmp, _search([], calls), text=_superseded_text())
            found = client.lookup("I12", "combination-code-hypertensive-ckd")
        self.assertEqual(found, [])
        self.assertTrue(any("not in the FY 2027 guidelines" in reason for reason in client.degraded_reasons))
        decision, rationale = apply_guideline_gate("accept", "combination", found, "Chart supports I12.9.")
        self.assertEqual(decision, "review")
        self.assertIn("Guideline evidence was missing", rationale)

    def test_cascade_re_decides_on_the_live_citation(self):
        from codesentinel.nemotron.client import NemotronClient
        from gap_pipeline import run_gap_pipeline
        from test_dataset import get_patient_profile

        row = anchor_for("E11", "combination-code-diabetes-ckd")
        pages = excerpt_pages()
        old = "These conditions should be coded as related even in the absence of provider documentation explicitly linking them"
        new = "These conditions should be coded as related even when provider documentation does not explicitly link them"
        self.assertIn(old, row["quote"])
        pages[11] = " ".join(pages[11].split()).replace(old, new)
        text = OfficialText(FY27, [pages.get(n, "") for n in range(1, FY27.pages + 1)], "test-sha", FY27_URL)
        live = row["quote"].replace(old, new)
        calls = []
        results = [{"url": FY27_URL, "title": "FY 2027 ICD-10-CM Coding Guidelines", "content": live}]
        profile = get_patient_profile("pt-004")
        with tempfile.TemporaryDirectory() as tmp:
            guidelines = _client(tmp, _search(results, calls), text=text)
            result = run_gap_pipeline(
                profile,
                profile["demographics"],
                nemotron_client=NemotronClient(api_key=""),
                guideline_client=guidelines,
            )
        audit = result["audit"]
        item = next(row for row in audit["worklist"] if row["icd10_code"] == "E11.22")
        citation = item["guideline_citations"][0]
        self.assertEqual(citation["provenance"], "tavily_live")
        self.assertEqual(citation["anchor_status"], "superseded")
        self.assertIn("does not explicitly link", citation["quote"])
        self.assertIn("superseded", item["rationale"])
        self.assertEqual(audit["guideline_provenance"]["target_fiscal_year"], "FY 2027")
        self.assertIn("E11|combination-code-diabetes-ckd", audit["guideline_provenance"]["superseded_anchors"])
        guideline_step = next(step for step in audit["timeline"] if step["step"] == "guidelines")
        self.assertIn("superseded", guideline_step["detail"])


if __name__ == "__main__":
    unittest.main()
