"""Harness invariants. These tests do not threshold the held-out score."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from eval.adapter import AuditAdapter, guardrail_probe, parse_verdict
from eval.charts import bundle_to_profile
from eval.generate import SCENARIOS, build_all, build_chart
from eval.policy import apply_policy
from eval.score import score_chart, wilson

ROOT = Path(__file__).resolve().parents[2]
PHI_PATTERNS = (
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(r"\b\d{3}[-.)]\d{3}[-.]\d{4}\b"),
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
)


def _rules_codes(profile: dict) -> set[str]:
    import logging
    import sys
    logging.getLogger("gapdetect.pipeline").setLevel(logging.ERROR)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from gap_pipeline import run_gap_pipeline
    result = run_gap_pipeline(profile, profile.get("demographics") or {}, verify=False)
    codes = set()
    for bucket in ("approved", "review", "rejected"):
        for gap in result[bucket]:
            if bucket != "rejected":
                codes.add((gap.get("icd10_code") or "").upper())
    return codes


def _shown(audit: dict) -> set[str]:
    return {
        (it.get("code") or "").upper()
        for it in audit["items"]
        if it.get("shown_to_coder") and it.get("gap_type") != "unsupported_existing"
    }


class CorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.charts = build_all()

    def test_size_and_splits(self):
        self.assertEqual(len(self.charts), 90)
        self.assertGreaterEqual(len(self.charts), 60)
        self.assertLessEqual(len(self.charts), 100)
        counts = {}
        for chart in self.charts:
            counts[chart["split"]] = counts.get(chart["split"], 0) + 1
        self.assertEqual(counts, {"train": 12, "dev": 12, "test": 66})
        ids = [c["patient_id"] for c in self.charts]
        self.assertEqual(len(ids), len(set(ids)))
        scenarios_in_test = {c["scenario"] for c in self.charts if c["split"] == "test"}
        self.assertEqual(scenarios_in_test, {name for name, _ in SCENARIOS})

    def test_deterministic(self):
        again = build_all()
        left = [(c["patient_id"], c["split"], c["scenario"], c["note"]) for c in self.charts]
        right = [(c["patient_id"], c["split"], c["scenario"], c["note"]) for c in again]
        self.assertEqual(left, right)

    def test_no_phi_patterns(self):
        for chart in self.charts:
            for pattern in PHI_PATTERNS:
                self.assertIsNone(pattern.search(chart["note"]), chart["patient_id"])

    def test_quotes_and_label_disjointness(self):
        for chart in self.charts:
            note = chart["note"].lower()
            meds = " ".join(
                (res.get("resource") or {}).get("medicationCodeableConcept", {}).get("text", "")
                for res in chart["bundle"]["entry"]
            ).lower()
            expected = set()
            for gap in chart["expected_gaps"]:
                code = gap["code"]
                self.assertNotIn(code, expected)
                expected.add(code)
                quote = gap["evidence_quote"].lower()
                if gap["evidence_kind"] == "medication":
                    self.assertIn(quote, meds, chart["scenario"])
                else:
                    self.assertIn(quote, note, chart["scenario"])
            traps = set()
            for trap in chart["must_not_flag"]:
                self.assertNotIn(trap["code"], expected, chart["scenario"])
                self.assertNotIn(trap["code"], traps, chart["scenario"])
                traps.add(trap["code"])
                quote = trap["evidence_quote"].lower()
                if trap["evidence_kind"] == "medication":
                    self.assertIn(quote, meds, chart["scenario"])
                else:
                    self.assertIn(quote, note, chart["scenario"])
            for item in chart["unsupported_existing"]:
                self.assertNotIn(item["code"], expected)
                self.assertIn(item["evidence_quote"].lower(), note)

    def test_bundle_roundtrip(self):
        chart = self.charts[0]
        profile = bundle_to_profile(chart["bundle"])
        self.assertEqual(profile["fhir_patient_id"], chart["patient_id"])
        self.assertIn("not a real patient", profile["clinical_notes"][0]["text"])
        self.assertTrue(profile["encounters"])

    def test_rules_flags_match_construction(self):
        """Templates that claim the rules fire must actually fire. This is a data check."""
        for chart in self.charts:
            profile = bundle_to_profile(chart["bundle"])
            found = _rules_codes(profile)
            for gap in chart["expected_gaps"]:
                if gap.get("rules_should_find", True):
                    self.assertIn(gap["code"], found, f"{chart['scenario']} missing {gap['code']}: {sorted(found)}")
                else:
                    self.assertNotIn(gap["code"], found, f"{chart['scenario']} unexpectedly found {gap['code']}")
            for trap in chart["must_not_flag"]:
                if trap.get("rules_should_flag", True):
                    self.assertIn(
                        trap["code"], found,
                        f"{chart['scenario']} trap {trap['code']} was not flagged: {sorted(found)}",
                    )


class PolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.by_name = {}
        for index, (name, _) in enumerate(SCENARIOS):
            cls.by_name[name] = build_chart(index, 1, f"unit-{name}")

    def _profile(self, name: str) -> dict:
        return bundle_to_profile(self.by_name[name]["bundle"])

    def test_policy_does_not_read_gold_labels(self):
        text = (ROOT / "eval" / "policy.py").read_text(encoding="utf-8")
        self.assertNotIn("expected_gaps", text)
        self.assertNotIn("must_not_flag", text)

    def test_negation_dropped_and_hypertension_kept(self):
        adapter = AuditAdapter()
        profile = self._profile("negation_hf")
        c0 = _shown(adapter.audit(profile, "C0", mode="mock"))
        c1 = _shown(adapter.audit(profile, "C1", mode="mock"))
        self.assertIn("I50.9", c0)
        self.assertNotIn("I50.9", c1)
        self.assertIn("I10", c1)

    def test_c0_hides_review_only_osa(self):
        adapter = AuditAdapter()
        profile = self._profile("missing_osa")
        self.assertNotIn("G47.33", _shown(adapter.audit(profile, "C0", mode="mock")))
        self.assertIn("G47.33", _shown(adapter.audit(profile, "C0+", mode="mock")))

    def test_systolic_heart_failure_hides_unspecified_sibling(self):
        adapter = AuditAdapter()
        profile = self._profile("hf_specificity")
        c1 = _shown(adapter.audit(profile, "C1", mode="mock"))
        c4 = _shown(adapter.audit(profile, "C4", mode="mock"))
        # Rules propose I50.22 from the reduced-EF sentence. C1 is the offline policy,
        # which shows that candidate and then hides I50.9. C4 is the cascade, which
        # accepts I50.22 only with a quote and a guideline citation, then drops I50.9.
        self.assertIn("I50.22", c1)
        self.assertNotIn("I50.9", c1)
        self.assertIn("I50.22", c4)
        self.assertNotIn("I50.9", c4)

    def test_guideline_step_drops_acute_egfr(self):
        adapter = AuditAdapter()
        profile = self._profile("acute_egfr")
        c1 = _shown(adapter.audit(profile, "C1", mode="mock"))
        c3 = _shown(adapter.audit(profile, "C3", mode="mock"))
        self.assertIn("N18.3", c1)
        self.assertNotIn("N18.3", c3)
        self.assertIn("E78.5", c3)

    def test_c4_cascade_drops_cross_sentence_retraction(self):
        adapter = AuditAdapter()
        profile = self._profile("cross_sentence_retraction")
        shown = _shown(adapter.audit(profile, "C4", mode="mock"))
        self.assertNotIn("I50.9", shown)
        self.assertIn("E03.9", shown)
        # The offline sentence classifier still keeps the code. C4 no longer uses it.
        c1 = _shown(adapter.audit(profile, "C1", mode="mock"))
        self.assertIn("I50.9", c1)

    def test_c4_cascade_proposes_combination_from_chart(self):
        adapter = AuditAdapter()
        profile = self._profile("combo_parent_uncoded")
        shown = _shown(adapter.audit(profile, "C4", mode="mock"))
        self.assertIn("N18.3", shown)
        self.assertIn("E11.65", shown)
        self.assertIn("E11.22", shown)
        self.assertNotIn("N18.9", shown)

    def test_unsupported_existing_starts_at_c1(self):
        adapter = AuditAdapter()
        profile = self._profile("unsupported_copd")
        c0 = adapter.audit(profile, "C0", mode="mock")
        c1 = adapter.audit(profile, "C1", mode="mock")
        def unsupported(audit):
            return {
                it["code"] for it in audit["items"]
                if it.get("gap_type") == "unsupported_existing" and it.get("shown_to_coder")
            }
        self.assertEqual(unsupported(c0), set())
        self.assertIn("J44.1", unsupported(c1))
        c4 = adapter.audit(profile, "C4", mode="mock")
        self.assertIn("J44.1", unsupported(c4))

    def test_guardrail_drops_hallucinated_quote(self):
        probe = guardrail_probe()
        self.assertTrue(probe["passed"], probe)

    def test_family_history_dropped(self):
        adapter = AuditAdapter()
        shown = _shown(adapter.audit(self._profile("family_hf"), "C1", mode="mock"))
        self.assertNotIn("I50.9", shown)
        self.assertIn("E78.5", shown)


class GuidelineCountTests(unittest.TestCase):
    def test_missing_guideline_text_is_not_a_change_without_tavily(self):
        from eval.adapter import _audit_to_eval

        audit = {
            "worklist": [{
                "icd10_code": "E11.65",
                "code_display": "Type 2 DM with hyperglycemia",
                "gap_type": "specificity_upgrade",
                "decision": "review",
                "rationale": (
                    "Guideline evidence was missing, so this combination or specificity "
                    "code cannot be accepted. Triage held this candidate."
                ),
            }],
            "dropped": [],
            "receipt": {"totals": {}},
        }
        self.assertEqual(_audit_to_eval(audit, "live", "C1")["guideline_decision_changes"], 0)
        self.assertEqual(_audit_to_eval(audit, "live", "C2")["guideline_decision_changes"], 0)
        self.assertEqual(_audit_to_eval(audit, "live", "C3")["guideline_decision_changes"], 1)
        self.assertEqual(_audit_to_eval(audit, "mock", "C4")["guideline_decision_changes"], 1)


class LiveSwitchTests(unittest.TestCase):
    def test_live_mode_refuses_without_both_keys(self):
        import os
        from eval.run import run_eval

        saved = {name: os.environ.pop(name, None) for name in ("NEBIUS_API_KEY", "TAVILY_API_KEY")}
        try:
            with self.assertRaises(SystemExit) as caught:
                run_eval(mode="live", skip_gold_a=True, skip_mtsamples=True)
        finally:
            for name, value in saved.items():
                if value is not None:
                    os.environ[name] = value
        self.assertIn("will not relabel a mock run", str(caught.exception))


class ScoreTests(unittest.TestCase):
    def test_confusion(self):
        label = {
            "patient_id": "x",
            "expected_gaps": [{"code": "E11.65"}, {"code": "I10"}],
            "must_not_flag": [{"code": "I50.9", "category": "negation"}],
            "unsupported_existing": [{"code": "J44.1"}],
        }
        items = [
            {"code": "E11.65", "shown_to_coder": True, "gap_type": "missing", "decision": "accept"},
            {"code": "I50.9", "shown_to_coder": True, "gap_type": "missing", "decision": "accept"},
            {"code": "J44.1", "shown_to_coder": True, "gap_type": "unsupported_existing", "decision": "accept"},
            {"code": "K21.0", "shown_to_coder": False, "gap_type": "missing", "decision": "reject",
             "drop_reason": "unsupported_claim"},
        ]
        scored = score_chart(label, items)
        self.assertEqual(scored["tp"], ["E11.65"])
        self.assertEqual(scored["fp"], ["I50.9"])
        self.assertEqual(scored["fn"], ["I10"])
        self.assertEqual(scored["hard_pass"], 0)
        self.assertEqual(scored["unsupported_tp"], ["J44.1"])
        self.assertEqual(scored["unsupported_claim_drops"], 1)
        # The unsupported flag is sent to a human and counts as review burden.
        self.assertEqual(scored["review_items"], 3)

    def test_wilson_empty(self):
        self.assertEqual(wilson(0, 0), (None, None))

    def test_parse_verdict(self):
        payload = {"choices": [{"message": {"content": json.dumps({
            "decision": "reject",
            "assertion_status": "negated",
            "supporting_quotes": ["denies"],
            "rationale": "negated",
        })}}]}
        parsed = parse_verdict(payload)
        self.assertEqual(parsed["decision"], "reject")
        with self.assertRaises(RuntimeError):
            parse_verdict({"choices": [{"message": {"content": "not json"}}]})


class ApplyPolicyDirectTest(unittest.TestCase):
    def test_problem_list_only_is_copy_forward(self):
        profile = {
            "fhir_patient_id": "copy",
            "demographics": {},
            "clinical_notes": [{"text": "PROBLEM LIST:\nCOPD\n\nASSESSMENT AND PLAN:\nHypertension is active.\n"}],
            "observations": [],
            "medications": [],
            "conditions": [],
            "encounters": [],
            "claims_codes": set(),
        }
        gap = {
            "icd10_code": "J44.1",
            "condition_name": "COPD",
            "tier": "TIER_3_NOTE_NER",
            "evidence_sources": ["Clinical note [assessment_plan]: ...COPD..."],
            "evidence_count": 1,
        }
        trace = apply_policy(profile, [gap], "C1")
        copd = next(it for it in trace.items if it.code == "J44.1" and it.gap_type != "unsupported_existing")
        self.assertEqual(copd.assertion_status, "copied_forward_unclear")
        self.assertFalse(copd.shown_to_coder)


if __name__ == "__main__":
    unittest.main()


class AdaptiveConfigTests(unittest.TestCase):
    def test_mock_c4a_records_depth_per_chart(self):
        from eval.charts import iter_gold_b
        from eval.run import _run_split

        rows = iter_gold_b("test")[:8]
        per_config = _run_split(AuditAdapter(), rows, mode="mock")
        c4a = per_config["C4a"]
        depths = [chart["depth_used"] for chart in c4a["charts"]]
        self.assertTrue(set(depths) <= {"C1", "C4"})
        self.assertEqual(c4a["summary"]["escalated_charts"], depths.count("C4"))
        self.assertEqual(per_config["C1"]["summary"]["escalated_fraction"], 0)
        self.assertEqual(per_config["C4"]["summary"]["escalated_fraction"], 1)
        self.assertIn("info_rows_per_chart", c4a["summary"])
