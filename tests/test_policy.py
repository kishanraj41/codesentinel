import unittest
from pathlib import Path

from codesentinel.nemotron.mock_reasoner import (
    apply_guideline_gate,
    needs_escalation,
    safe_physician_query,
)


class PolicyTests(unittest.TestCase):
    def test_missing_guideline_blocks_accept(self):
        decision, rationale = apply_guideline_gate("accept", "combination", [], "Model accepted.")
        self.assertEqual(decision, "review")
        self.assertIn("Guideline evidence was missing", rationale)

        decision, rationale = apply_guideline_gate(
            "accept", "specificity_upgrade", [], "Model accepted."
        )
        self.assertEqual(decision, "review")

    def test_citation_keeps_accept(self):
        decision, _rationale = apply_guideline_gate(
            "accept", "combination", [{"url": "https://www.cms.gov/icd"}], "Supported."
        )
        self.assertEqual(decision, "accept")

    def test_missing_codes_do_not_need_a_guideline(self):
        decision, _rationale = apply_guideline_gate("accept", "missing", [], "Supported.")
        self.assertEqual(decision, "accept")

    def test_escalation_rules(self):
        self.assertTrue(
            needs_escalation(
                assertion_status="present",
                super_decision="reject",
                gap_type="missing",
                raf=0.1,
                super_called=True,
            )
        )
        self.assertTrue(
            needs_escalation(
                assertion_status="present",
                super_decision="accept",
                gap_type="combination",
                raf=0.302,
                super_called=True,
            )
        )
        self.assertFalse(
            needs_escalation(
                assertion_status="present",
                super_decision="accept",
                gap_type="missing",
                raf=0.9,
                super_called=True,
            )
        )

    def test_leading_query_is_replaced(self):
        text = safe_physician_query(
            "Please add code E11.65 to the problem list.",
            "E11.65",
            "Type 2 DM with hyperglycemia",
            ["HbA1c risen from 7.8% to 8.4%."],
        )
        self.assertNotIn("please add", text.lower())
        self.assertIn("does not ask you to add", text.lower())


class ProductionBoundaryTests(unittest.TestCase):
    def test_cascade_does_not_import_eval(self):
        root = Path(__file__).resolve().parents[1] / "codesentinel"
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("import eval", text, path.name)
            self.assertNotIn("from eval", text, path.name)

    def test_eval_reexports_the_production_check(self):
        from codesentinel.policy import unsupported_existing_items as production
        from eval.policy import unsupported_existing_items as reexported

        self.assertIs(reexported, production)
        profile = {
            "clinical_notes": [{"text": "COPD was entered on the problem list in error. Please remove it."}],
            "observations": [],
            "medications": [{"name": "Metformin 500mg"}],
            "conditions": [
                {"icd10_code": "J44.1", "display": "COPD"},
                {"icd10_code": "E11.9", "display": "Type 2 diabetes"},
            ],
        }
        found = {item.code: item for item in production(profile)}
        self.assertEqual(set(found), {"J44.1"})
        self.assertEqual(found["J44.1"].assertion_status, "negated")
        self.assertIn("in error", found["J44.1"].quote.lower())


if __name__ == "__main__":
    unittest.main()
