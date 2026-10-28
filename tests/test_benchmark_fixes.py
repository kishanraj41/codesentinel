"""Fixes found by the independent Synthea benchmark (dev split only).

1. Medication or lab evidence is kept for review when the note is silent template text.
2. Triage sees the structured lines relevant to the candidate, not the first 40.
3. The unsupported-existing check counts structured evidence and Condition display terms.
4. LOINC 2339-0 is blood glucose, not glycosuria.
"""

import unittest

from codesentinel.nemotron.mock_reasoner import reconcile_triage, triage_structured_lines
from codesentinel.nemotron.schemas import TriageResult
from codesentinel.nemotron.verifier import lab_line, medication_line
from codesentinel.policy import unsupported_existing_items
from gap_pipeline import LOINC_ICD10_MAP

TEMPLATE_NOTE = (
    "# Chief Complaint\nNo complaints.\n\n# History of Present Illness\n"
    "The patient is a 70 year-old male. Patient has a history of dental caries, stress (finding).\n\n"
    "# Assessment and Plan\nPatient is presenting with medication review due (situation)."
)


def _triage(status="copied_forward_unclear"):
    return TriageResult(assertion_status=status, supporting_quotes=[], section="unknown", confidence=0.4)


def _metformin_profile(**extra):
    profile = {
        "medications": [{"medication_id": "m1", "name": "Metformin 500 MG Oral Tablet", "status": "active"}],
        "observations": [],
        "conditions": [],
        "claims_codes": set(),
    }
    profile.update(extra)
    return profile


class MedicationRescueTests(unittest.TestCase):
    def test_specific_drug_with_template_note_goes_to_review(self):
        result = reconcile_triage(
            _triage(), code="E11.9", condition="Type 2 diabetes mellitus", notes=TEMPLATE_NOTE,
            profile=_metformin_profile(),
        )
        self.assertEqual(result.assertion_status, "medication_supported")

    def test_family_history_sentence_still_wins(self):
        notes = TEMPLATE_NOTE + "\nHis mother has type 2 diabetes."
        result = reconcile_triage(
            _triage("family_history"), code="E11.9", condition="Type 2 diabetes mellitus", notes=notes,
            profile=_metformin_profile(),
        )
        self.assertEqual(result.assertion_status, "family_history")

    def test_nonspecific_drug_is_not_rescued(self):
        profile = {"medications": [{"medication_id": "m1", "name": "Lisinopril 10 MG", "status": "active"}]}
        result = reconcile_triage(
            _triage(), code="N18.9", condition="Chronic kidney disease", notes=TEMPLATE_NOTE, profile=profile,
        )
        self.assertEqual(result.assertion_status, "copied_forward_unclear")

    def test_drug_already_explained_by_billed_code(self):
        profile = {
            "medications": [{"medication_id": "m1", "name": "Lisinopril 10 MG", "status": "active"}],
            "claims_codes": {"I12.9"},
        }
        result = reconcile_triage(
            _triage("medication_supported"), code="I10", condition="Essential hypertension",
            notes=TEMPLATE_NOTE, profile=profile,
        )
        self.assertEqual(result.assertion_status, "copied_forward_unclear")

    def test_lab_with_silent_note_goes_to_review(self):
        profile = {
            "medications": [],
            "observations": [{"observation_id": "o1", "name": "HbA1c", "loinc": "4548-4", "value": 8.1, "unit": "%"}],
        }
        result = reconcile_triage(
            _triage(), code="E11.65", condition="Type 2 diabetes mellitus with hyperglycemia",
            notes=TEMPLATE_NOTE, profile=profile, gap={"icd10_code": "E11.65", "lab_loinc": "4548-4"},
        )
        self.assertEqual(result.assertion_status, "lab_only")


    def test_lab_panel_name_is_not_a_condition_mention(self):
        notes = TEMPLATE_NOTE + "\nThe following lab reports were completed:\n- lipid panel with direct ldl - serum or plasma"
        profile = {"medications": [{"medication_id": "m1", "name": "Simvastatin 10 MG Oral Tablet", "status": "active"}]}
        result = reconcile_triage(
            _triage(), code="E78.5", condition="Hyperlipidemia", notes=notes, profile=profile,
        )
        self.assertEqual(result.assertion_status, "medication_supported")


class TriageLinesTests(unittest.TestCase):
    def _long_profile(self):
        observations = [
            {"observation_id": f"o{i}", "name": f"Lab {i}", "loinc": "2947-0", "value": 140 + i, "unit": "mmol/L",
             "date": f"2026-0{1 + i % 9}-01"}
            for i in range(80)
        ]
        medications = [{"medication_id": "m1", "name": "Metformin 500 MG Oral Tablet", "status": "active"}]
        return {"observations": observations, "medications": medications}

    def test_short_chart_is_sent_whole_in_order(self):
        profile = _metformin_profile()
        lines = [medication_line(m) for m in profile["medications"]]
        self.assertEqual(triage_structured_lines({"icd10_code": "E11.9"}, profile, lines), lines)

    def test_long_chart_keeps_candidate_medication(self):
        profile = self._long_profile()
        all_lines = [lab_line(o) for o in profile["observations"]] + [medication_line(profile["medications"][0])]
        picked = triage_structured_lines({"icd10_code": "E11.9"}, profile, all_lines)
        self.assertEqual(picked[0], medication_line(profile["medications"][0]))
        self.assertLessEqual(len(picked), 60)
        self.assertTrue(set(picked) <= set(all_lines))


class UnsupportedExistingTests(unittest.TestCase):
    def _profile(self, notes, conditions, medications=(), encounters=(), observations=()):
        return {
            "clinical_notes": [{"text": notes}],
            "conditions": list(conditions),
            "medications": list(medications),
            "encounters": list(encounters),
            "observations": list(observations),
        }

    def test_display_term_in_note_counts_as_mention(self):
        profile = self._profile(
            "Patient has a history of gingivitis (disorder).",
            [{"icd10_code": "K05.10", "display": "Gingivitis"}],
            observations=[{"loinc": "2947-0", "value": 140}],
        )
        self.assertEqual(unsupported_existing_items(profile), [])

    def test_linked_medication_reason_supports_code(self):
        profile = self._profile(
            TEMPLATE_NOTE,
            [{"icd10_code": "I10", "display": "Essential hypertension"}],
            medications=[{"name": "Metoprolol 25 MG", "reasons": ["Essential hypertension (disorder)"]}],
        )
        self.assertEqual(unsupported_existing_items(profile), [])

    def test_family_drug_supports_ischemic_heart_disease(self):
        profile = self._profile(
            TEMPLATE_NOTE,
            [{"icd10_code": "I25.9", "display": "Chronic ischemic heart disease, unspecified"}],
            medications=[{"name": "Nitroglycerin 0.4 MG/ACTUAT Mucosal Spray"}],
        )
        self.assertEqual(unsupported_existing_items(profile), [])

    def test_nonspecific_drugs_do_not_excuse_heart_failure(self):
        # Found on the Synthea held-out run: carvedilol excused a planted unsupported I50.9.
        for drug in ("Carvedilol 25 MG Oral Tablet", "Furosemide 40 MG Oral Tablet"):
            with self.subTest(drug=drug):
                profile = self._profile(
                    TEMPLATE_NOTE,
                    [{"icd10_code": "I50.9", "display": "Heart failure, unspecified"}],
                    medications=[{"name": drug}],
                )
                items = unsupported_existing_items(profile)
                self.assertEqual([item.code for item in items], ["I50.9"])
                self.assertEqual(items[0].decision, "review")
                self.assertEqual(items[0].drop_reason, "no_meat")

    def test_nonspecific_drugs_do_not_excuse_hypertensive_heart_ckd(self):
        for drug in ("Carvedilol 25 MG Oral Tablet", "Furosemide 40 MG Oral Tablet"):
            with self.subTest(drug=drug):
                profile = self._profile(
                    TEMPLATE_NOTE,
                    [{"icd10_code": "I13.0", "display": "Hypertensive heart and chronic kidney disease with heart failure"}],
                    medications=[{"name": drug}],
                )
                items = unsupported_existing_items(profile)
                self.assertEqual([item.code for item in items], ["I13.0"])
                self.assertEqual(items[0].decision, "review")
                self.assertEqual(items[0].drop_reason, "no_meat")

    def test_family_drug_still_supports_i13(self):
        profile = self._profile(
            TEMPLATE_NOTE,
            [{"icd10_code": "I13.0", "display": "Hypertensive heart and chronic kidney disease with heart failure"}],
            medications=[{"name": "Lisinopril 10 MG Oral Tablet"}, {"name": "Furosemide 40 MG"}],
        )
        self.assertEqual(unsupported_existing_items(profile), [])

    def test_heart_failure_specific_drug_still_supports_i50(self):
        profile = self._profile(
            TEMPLATE_NOTE,
            [{"icd10_code": "I50.9", "display": "Heart failure, unspecified"}],
            medications=[{"name": "Sacubitril 97 MG / Valsartan 103 MG Oral Tablet"}, {"name": "Carvedilol 25 MG"}],
        )
        self.assertEqual(unsupported_existing_items(profile), [])

    def test_qualifying_lab_supports_prediabetes(self):
        profile = self._profile(
            TEMPLATE_NOTE,
            [{"icd10_code": "R73.03", "display": "Prediabetes"}],
            observations=[{"loinc": "4548-4", "value": 6.0}],
        )
        self.assertEqual(unsupported_existing_items(profile), [])

    def test_code_without_any_evidence_is_still_flagged(self):
        profile = self._profile(
            TEMPLATE_NOTE,
            [{"icd10_code": "J44.9", "display": "Chronic obstructive pulmonary disease, unspecified"}],
            medications=[{"name": "Simvastatin 20 MG", "reasons": ["Hyperlipidemia"]}],
            observations=[{"loinc": "2947-0", "value": 140}],
        )
        items = unsupported_existing_items(profile)
        self.assertEqual([item.code for item in items], ["J44.9"])
        self.assertEqual(items[0].drop_reason, "no_meat")


class PrediabetesExcludesTests(unittest.TestCase):
    def test_no_r73_candidate_when_diabetes_is_coded(self):
        import logging

        from gap_pipeline import run_gap_pipeline

        logging.getLogger("gapdetect.pipeline").setLevel(logging.ERROR)
        profile = {
            "clinical_notes": [],
            "observations": [{"observation_id": "o1", "name": "HbA1c", "loinc": "4548-4", "value": 6.0, "unit": "%",
                              "date": "2026-09-01"}],
            "medications": [],
            "conditions": [{"icd10_code": "E11.9", "display": "Type 2 diabetes mellitus"}],
            "claims_codes": {"E11.9"},
        }
        out = run_gap_pipeline(profile, {}, verify=False)
        codes = {g.get("icd10_code") for bucket in ("approved", "review", "rejected") for g in out[bucket]}
        self.assertFalse(any(str(c).startswith("R73") for c in codes))


class LoincMapTests(unittest.TestCase):
    def test_blood_glucose_does_not_raise_glycosuria(self):
        codes = {t["icd10"] for t in LOINC_ICD10_MAP["2339-0"]["thresholds"]}
        self.assertNotIn("R81", codes)
        self.assertIn("R81", {t["icd10"] for t in LOINC_ICD10_MAP["5792-7"]["thresholds"]})


if __name__ == "__main__":
    unittest.main()
