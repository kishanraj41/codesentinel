"""Fix A: unsupported-existing gating on charts whose notes name no condition.

A billed code with no evidence is review work only when the notes discuss
conditions. When the chart window's notes name no condition at all, the row is
informational (decision "info"): kept for context, outside review items and scoring.
"""

import os
import unittest

from codesentinel.nemotron.cascade import run_cascade
from codesentinel.policy import (
    INFO_DECISION,
    INFO_DROP_REASON,
    condition_mentions,
    notes_mention_conditions,
    unsupported_existing_items,
)
from eval.adapter import _audit_to_eval
from eval.score import aggregate, score_chart

SILENT_NOTE = (
    "# Chief Complaint\nNo complaints.\n\n# History of Present Illness\n"
    "The patient is a 70 year-old male. Patient has a history of dental caries, stress (finding).\n\n"
    "# Assessment and Plan\nPatient is presenting with medication review due (situation)."
)


def _profile(notes, conditions, medications=(), observations=()):
    return {
        "fhir_patient_id": "gate",
        "clinical_notes": [{"text": notes}] if notes is not None else [],
        "conditions": list(conditions),
        "medications": list(medications),
        "encounters": [],
        "observations": list(observations),
    }


ANEMIA = {"icd10_code": "D64.9", "display": "Anemia, unspecified"}
OBS = [{"loinc": "2947-0", "value": 140}]


class ConditionMentionTests(unittest.TestCase):
    def test_template_note_without_condition_names_is_silent(self):
        profile = _profile(SILENT_NOTE, [ANEMIA], observations=OBS)
        self.assertEqual(condition_mentions(profile), [])
        self.assertFalse(notes_mention_conditions(profile))

    def test_lexicon_condition_counts_as_mention(self):
        profile = _profile(SILENT_NOTE + "\nHypertension is stable.", [ANEMIA], observations=OBS)
        self.assertTrue(notes_mention_conditions(profile))

    def test_billed_condition_display_counts_as_mention(self):
        profile = _profile(
            "Patient has a history of gingivitis (disorder).",
            [ANEMIA, {"icd10_code": "K05.10", "display": "Gingivitis"}],
            observations=OBS,
        )
        self.assertTrue(notes_mention_conditions(profile))

    def test_negated_and_family_mentions_still_count(self):
        for sentence in ("The patient denies diabetes.", "Mother had heart failure."):
            with self.subTest(sentence=sentence):
                self.assertTrue(notes_mention_conditions(_profile(sentence, [ANEMIA], observations=OBS)))

    def test_section_header_alone_is_not_a_mention(self):
        profile = _profile("# Assessment and Plan\n\n## Plan\nFollow up in one year.", [ANEMIA], observations=OBS)
        self.assertFalse(notes_mention_conditions(profile))


class GateTests(unittest.TestCase):
    def test_silent_chart_makes_unsupported_code_info(self):
        items = unsupported_existing_items(_profile(SILENT_NOTE, [ANEMIA], observations=OBS))
        self.assertEqual([item.code for item in items], ["D64.9"])
        self.assertEqual(items[0].decision, INFO_DECISION)
        self.assertEqual(items[0].drop_reason, INFO_DROP_REASON)
        self.assertFalse(items[0].shown_to_coder)

    def test_chart_with_condition_mentions_keeps_review(self):
        profile = _profile(SILENT_NOTE + "\nPatient is presenting with hyperlipidemia (disorder).", [ANEMIA], observations=OBS)
        items = unsupported_existing_items(profile)
        self.assertEqual([item.code for item in items], ["D64.9"])
        self.assertEqual(items[0].decision, "review")
        self.assertEqual(items[0].drop_reason, "no_meat")
        self.assertTrue(items[0].shown_to_coder)

    def test_contradicted_code_is_never_info(self):
        profile = _profile("COPD was entered on the problem list in error.", [{"icd10_code": "J44.1", "display": "COPD"}], observations=OBS)
        items = unsupported_existing_items(profile)
        self.assertEqual([(item.code, item.assertion_status) for item in items], [("J44.1", "negated")])
        self.assertNotEqual(items[0].decision, INFO_DECISION)

    def test_no_notes_with_structured_data_is_info(self):
        items = unsupported_existing_items(_profile(None, [ANEMIA], medications=[{"name": "Acetaminophen 325 MG"}]))
        self.assertEqual([item.decision for item in items], [INFO_DECISION])

    def test_empty_chart_still_flags_nothing(self):
        self.assertEqual(unsupported_existing_items(_profile(None, [ANEMIA])), [])

    def test_supported_code_on_silent_chart_is_not_listed(self):
        profile = _profile(SILENT_NOTE, [ANEMIA], observations=[{"loinc": "718-7", "value": 10.5}])
        self.assertEqual(unsupported_existing_items(profile), [])


class GoldPlantedStillCaughtTests(unittest.TestCase):
    """Gold-B's planted unsupported codes (and Gold-A's) must stay review work."""

    def test_gold_b_planted_unsupported_are_review(self):
        from eval.charts import iter_gold_b

        planted = 0
        for label, profile in iter_gold_b(None):
            codes = {u["code"] for u in label.get("unsupported_existing") or []}
            if not codes:
                continue
            shown = {item.code for item in unsupported_existing_items(profile) if item.decision != INFO_DECISION}
            for code in codes:
                planted += 1
                self.assertIn(code, shown, f"{label['patient_id']} {code} must stay a review item")
        self.assertGreaterEqual(planted, 3)

    def test_gold_a_planted_unsupported_are_review(self):
        from eval.charts import load_gold_a

        for label, profile in load_gold_a():
            codes = {u["code"] for u in label.get("unsupported_existing") or []}
            shown = {item.code for item in unsupported_existing_items(profile) if item.decision != INFO_DECISION}
            for code in codes:
                self.assertIn(code, shown, f"{label['patient_id']} {code}")


class _NoCandidatesClient:
    mock = True
    used_fallback = False
    degraded_reasons = []

    def ensure_models(self):
        return None

    def select_triage_model(self):
        return "nvidia/Nemotron-3_5-Lightning"


class _NoGuidelines:
    use_fixtures = False
    credits = 0
    cache_hits = 0
    live_requests = 0
    degraded_reasons = []

    def lookup(self, family, issue):
        return []


class CascadeInfoRowTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("NEBIUS_API_KEY", None)

    def _run(self, notes):
        profile = _profile(notes, [ANEMIA], observations=OBS)
        return run_cascade(
            profile, {"approved": [], "review": [], "rejected": []},
            nemotron_client=_NoCandidatesClient(), guideline_client=_NoGuidelines(), config="C1",
        )

    def test_info_rows_are_outside_the_worklist(self):
        result = self._run(SILENT_NOTE)
        self.assertEqual(result["worklist"], [])
        self.assertEqual([row["icd10_code"] for row in result["info"]], ["D64.9"])
        self.assertEqual(result["info"][0]["decision"], "info")
        self.assertEqual(result["legacy"]["review"], [])

    def test_review_rows_stay_in_the_worklist(self):
        result = self._run(SILENT_NOTE + "\nHypertension is stable.")
        self.assertEqual(result["info"], [])
        self.assertEqual([row["decision"] for row in result["worklist"]], ["review"])

    def test_eval_excludes_info_from_review_items_and_scoring(self):
        result = self._run(SILENT_NOTE)
        payload = _audit_to_eval(result, "mock", "C1")
        label = {"patient_id": "gate", "expected_gaps": [], "unsupported_existing": [{"code": "D64.9"}]}
        scored = score_chart(label, payload["items"])
        self.assertEqual(scored["review_items"], 0)
        self.assertEqual(scored["info_rows"], 1)
        self.assertEqual(scored["unsupported_fn"], ["D64.9"])
        self.assertEqual(scored["unsupported_info"], ["D64.9"])
        agg = aggregate([scored], costs=[0], latencies=[0], tavily_credits=[0], tavily_cache_hits=[0],
                        tavily_uncached=[0], guideline_changes=0)
        self.assertEqual(agg["review_burden_per_chart"], 0)
        self.assertEqual(agg["info_rows_per_chart"], 1)
        self.assertEqual(agg["unsupported_existing"]["planted_shown_as_info"], 1)

    def test_audit_response_carries_info(self):
        from codesentinel.audit import apply_verification

        profile = _profile(SILENT_NOTE, [ANEMIA], observations=OBS)
        out = apply_verification(
            profile, {}, {"approved": [], "review": [], "rejected": []},
            nemotron_client=_NoCandidatesClient(), guideline_client=_NoGuidelines(), cascade_config="C1",
        )
        audit = out["audit"]
        self.assertEqual(audit["summary"]["worklist_count"], 0)
        self.assertEqual(audit["summary"]["info_count"], 1)
        self.assertEqual(audit["info"][0]["decision"], "info")


if __name__ == "__main__":
    unittest.main()
