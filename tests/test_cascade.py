import os
import unittest

from codesentinel.nemotron.cascade import run_cascade
from codesentinel.nemotron.client import Completion
from codesentinel.nemotron.schemas import AdjudicationResult, TriageResult
from codesentinel.tavily.client import Citation


class _EmptyGuidelines:
    use_fixtures = False
    credits = 0
    cache_hits = 0
    live_requests = 0
    fixture_hits = 0
    degraded_reasons = []

    def lookup(self, family, issue):
        return []


class _Scripted:
    """Returns a present assertion with a quote the caller chooses."""

    def __init__(self, quote, confidence=0.92):
        self.mock = True
        self.used_fallback = False
        self.degraded_reasons = []
        self.quote = quote
        self.confidence = confidence
        self.calls = []

    def ensure_models(self):
        return None

    def select_triage_model(self):
        return "nvidia/Nemotron-3_5-Lightning"

    def complete_json(self, **kwargs):
        schema_name = kwargs["schema_name"]
        self.calls.append(schema_name)
        model = kwargs["model"]
        if schema_name == "triage":
            data = TriageResult(
                assertion_status="present",
                supporting_quotes=[self.quote],
                section="assessment_plan",
                confidence=self.confidence,
            )
        else:
            data = AdjudicationResult(
                decision="accept",
                icd10_code="E11.22",
                code_display="Type 2 DM with diabetic CKD",
                rationale="The scripted model accepted this code.",
                physician_query_needed=False,
            )
        return Completion(
            data=data,
            raw="{}",
            input_tokens=20,
            output_tokens=10,
            latency_ms=1,
            mock=True,
            usage_source="estimated",
            model=model,
        )


class CascadeTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("NEBIUS_API_KEY", None)
        os.environ.pop("TAVILY_API_KEY", None)

    def test_ungrounded_quote_is_dropped(self):
        os.environ.pop("NEBIUS_API_KEY", None)
        profile = {
            "fhir_patient_id": "inline",
            "clinical_notes": [{"text": "ASSESSMENT AND PLAN:\nLungs are clear. Blood pressure is controlled."}],
            "observations": [],
            "medications": [],
        }
        candidate = {
            "tier": "TIER_3_NOTE_NER",
            "icd10_code": "I50.9",
            "condition_name": "Heart failure",
            "evidence_sources": ["imagined"],
            "evidence_count": 1,
            "hcc_category": "HCC 85",
            "raf_value": 0.323,
            "annual_revenue_impact": 100,
        }
        rules = {"approved": [candidate], "review": [], "rejected": []}
        client = _Scripted("severe congestive heart failure on exam")
        result = run_cascade(profile, rules, nemotron_client=client, guideline_client=_EmptyGuidelines())
        self.assertEqual(len(result["worklist"]), 0)
        self.assertEqual(result["dropped"][0]["reason"], "unsupported")
        self.assertEqual(result["dropped"][0]["dropped_by"], "verifier")

    def test_specificity_without_guidelines_cannot_accept(self):
        profile = _diabetes_profile()
        quote = "Type 2 diabetes mellitus with hyperglycemia."
        candidate = {
            "tier": "SPECIFICITY_UPGRADE",
            "icd10_code": "E11.65",
            "condition_name": "Type 2 DM with hyperglycemia",
            "upgrade_from": "E11.9",
            "evidence_sources": [quote],
            "hcc_category": "HCC 37",
            "raf_value": 0.166,
            "annual_revenue_impact": 1826,
        }
        rules = {"approved": [candidate], "review": [], "rejected": []}
        client = _Scripted(quote)
        result = run_cascade(profile, rules, nemotron_client=client, guideline_client=_EmptyGuidelines())
        self.assertEqual(result["worklist"][0]["decision"], "review")
        self.assertIn("Guideline evidence was missing", result["worklist"][0]["rationale"])
        self.assertEqual(result["worklist"][0]["guideline_citations"], [])
        self.assertTrue(result["worklist"][0]["physician_query"])
        self.assertNotIn("please add", result["worklist"][0]["physician_query"].lower())

    def test_guideline_citation_allows_accept(self):
        profile = _diabetes_profile()
        quote = "Type 2 diabetes mellitus with hyperglycemia."
        candidate = {
            "tier": "SPECIFICITY_UPGRADE",
            "icd10_code": "E11.65",
            "condition_name": "Type 2 DM with hyperglycemia",
            "upgrade_from": "E11.9",
            "evidence_sources": [quote],
            "hcc_category": "HCC 37",
            "raf_value": 0.166,
            "annual_revenue_impact": 1826,
        }
        rules = {"approved": [candidate], "review": [], "rejected": []}

        class _OneGuideline(_EmptyGuidelines):
            def lookup(self, family, issue):
                return [
                    Citation(
                        url="https://www.cms.gov/medicare/coding-billing/icd-10-codes",
                        quote="Recorded fixture excerpt for this test.",
                        source="cms.gov",
                        title="ICD-10-CM",
                        retrieved="fixture",
                        cached_at="2026-10-24T00:00:00+00:00",
                        code_family=family,
                        issue=issue,
                    )
                ]

        result = run_cascade(profile, rules, nemotron_client=_Scripted(quote), guideline_client=_OneGuideline())
        item = result["worklist"][0]
        self.assertEqual(item["decision"], "accept")
        self.assertEqual(item["guideline_citations"][0]["source"], "cms.gov")
        self.assertEqual(result["guideline_mode"], "fixture")
        self.assertEqual(result["citations"][0]["issue"], "specificity-hyperglycemia")

    def test_pt004_guideline_changes_combination_decision(self):
        from codesentinel.nemotron.client import NemotronClient
        from codesentinel.tavily.client import GuidelineClient
        from gap_pipeline import run_gap_pipeline
        from test_dataset import get_patient_profile

        profile = get_patient_profile("pt-004")
        demographics = profile["demographics"]
        guided = run_gap_pipeline(
            profile,
            demographics,
            nemotron_client=NemotronClient(api_key=""),
            guideline_client=GuidelineClient(api_key="", use_fixtures=True),
        )
        unguided = run_gap_pipeline(
            profile,
            demographics,
            nemotron_client=NemotronClient(api_key=""),
            guideline_client=_EmptyGuidelines(),
        )
        with_citation = _item(guided, "E11.22")
        without_citation = _item(unguided, "E11.22")
        self.assertIsNotNone(with_citation)
        self.assertEqual(with_citation["decision"], "accept")
        self.assertEqual(with_citation["decided_by"], "nvidia/Nemotron-3-Ultra-550b-a55b")
        self.assertEqual(with_citation["guideline_citations"][0]["retrieved"], "anchor")
        self.assertEqual(with_citation["guideline_citations"][0]["source_tier"], "official")
        self.assertEqual(with_citation["guideline_citations"][0]["section"], "I.A.15")
        self.assertEqual(without_citation["decision"], "review")
        self.assertIn("Guideline evidence was missing", without_citation["rationale"])
        self.assertEqual(without_citation["guideline_citations"], [])


def _item(result, code):
    for item in result["audit"]["worklist"]:
        if item["icd10_code"] == code:
            return item
    return None


class ChartEvidenceTests(unittest.TestCase):
    def test_combination_proposed_when_ckd_is_only_in_the_note(self):
        from codesentinel.nemotron.client import NemotronClient
        from codesentinel.tavily.client import GuidelineClient
        from gap_pipeline import run_gap_pipeline

        profile = {
            "fhir_patient_id": "combo-chart",
            "demographics": {"full_name": "Synthetic"},
            "clinical_notes": [{
                "text": (
                    "ASSESSMENT AND PLAN:\n"
                    "Type 2 diabetes mellitus with hyperglycemia. HbA1c is 8.7 percent. "
                    "Chronic kidney disease stage 3 is active. Diabetic nephropathy is active."
                ),
            }],
            "observations": [
                {"loinc": "4548-4", "name": "HbA1c", "value": 8.7, "unit": "%", "observation_id": "a1c"},
                {"loinc": "33914-3", "name": "eGFR", "value": 44, "unit": "mL/min/1.73m2", "observation_id": "egfr"},
            ],
            "medications": [{"name": "Metformin 1000mg", "status": "active", "medication_id": "m1"}],
            "conditions": [{"icd10_code": "E11.9", "display": "Type 2 diabetes mellitus without complications"}],
            "encounters": [{"encounter_id": "e1"}],
        }
        rules = run_gap_pipeline(profile, profile["demographics"], verify=False)
        proposed = {gap["icd10_code"] for gap in rules["approved"] + rules["review"]}
        self.assertIn("E11.22", proposed)
        audited = run_gap_pipeline(
            profile,
            profile["demographics"],
            nemotron_client=NemotronClient(api_key=""),
            guideline_client=GuidelineClient(api_key="", use_fixtures=True),
        )
        item = _item(audited, "E11.22")
        self.assertIsNotNone(item)
        self.assertIn(item["decision"], {"accept", "review"})

    def test_next_sentence_retraction_drops_heart_failure(self):
        from codesentinel.nemotron.client import NemotronClient
        from codesentinel.tavily.client import GuidelineClient
        from gap_pipeline import run_gap_pipeline

        profile = {
            "fhir_patient_id": "retraction",
            "demographics": {"full_name": "Synthetic"},
            "clinical_notes": [{
                "text": (
                    "ASSESSMENT AND PLAN:\n"
                    "Congestive heart failure is listed below. "
                    "The prior diagnosis was retracted after a normal echocardiogram and the list is wrong. "
                    "Hypothyroidism is active. TSH is 8.1. Continue levothyroxine."
                ),
            }],
            "observations": [
                {"loinc": "3016-3", "name": "TSH", "value": 8.1, "unit": "mIU/L", "observation_id": "tsh"},
            ],
            "medications": [
                {"name": "Furosemide 40mg", "status": "active", "medication_id": "f"},
                {"name": "Levothyroxine 88mcg", "status": "active", "medication_id": "l"},
            ],
            "conditions": [],
            "encounters": [{"encounter_id": "e1"}],
        }
        result = run_gap_pipeline(
            profile,
            profile["demographics"],
            nemotron_client=NemotronClient(api_key=""),
            guideline_client=GuidelineClient(api_key="", use_fixtures=True),
        )
        shown = {item["icd10_code"] for item in result["audit"]["worklist"]}
        dropped = {item["icd10_code"]: item["reason"] for item in result["audit"]["dropped"]}
        self.assertNotIn("I50.9", shown)
        self.assertEqual(dropped.get("I50.9"), "negated")
        self.assertIn("E03.9", shown)

    def test_staged_ckd_drops_unspecified_sibling(self):
        from codesentinel.nemotron.cascade import _suppress_unspecified_siblings

        worklist = [
            _row("N18.3", "CKD stage 3", "accept"),
            _row("N18.9", "CKD unspecified", "accept"),
            _row("E11.65", "Type 2 DM with hyperglycemia", "accept"),
            _row("E11.22", "Type 2 DM with diabetic CKD", "accept", gap_type="combination"),
            _row("E78.5", "Hyperlipidemia", "accept"),
        ]
        kept, removed = _suppress_unspecified_siblings(worklist)
        kept_codes = {item["icd10_code"] for item in kept}
        self.assertNotIn("N18.9", kept_codes)
        self.assertEqual(kept_codes, {"N18.3", "E11.65", "E11.22", "E78.5"})
        self.assertEqual(removed[0]["reason"], "rejected")
        self.assertEqual(removed[0]["dropped_by"], "adjudicator")
        self.assertIsNone(removed[0]["model"])
        self.assertIn("Unspecified sibling", removed[0]["reason_detail"])

    def test_review_only_specific_code_keeps_unspecified_sibling(self):
        from codesentinel.nemotron.cascade import _suppress_unspecified_siblings

        kept, removed = _suppress_unspecified_siblings([
            _row("N18.3", "CKD stage 3", "review"),
            _row("N18.9", "CKD unspecified", "accept"),
        ])
        self.assertEqual(removed, [])
        self.assertEqual({item["icd10_code"] for item in kept}, {"N18.3", "N18.9"})

    def test_reduced_ef_proposes_upgrade_and_drops_unspecified(self):
        from codesentinel.nemotron.client import NemotronClient
        from codesentinel.tavily.client import GuidelineClient
        from gap_pipeline import run_gap_pipeline

        profile = {
            "fhir_patient_id": "hf-spec",
            "demographics": {"full_name": "Synthetic"},
            "clinical_notes": [{
                "text": (
                    "ASSESSMENT AND PLAN:\n"
                    "Heart failure with reduced ejection fraction is active. "
                    "The ejection fraction is 35 percent."
                ),
            }],
            "observations": [
                {"loinc": "42637-9", "name": "BNP", "value": 560, "unit": "pg/mL", "observation_id": "bnp"},
            ],
            "medications": [{"name": "Furosemide 40mg", "status": "active", "medication_id": "f"}],
            "conditions": [],
            "encounters": [{"encounter_id": "e1"}],
        }
        rules = run_gap_pipeline(profile, profile["demographics"], verify=False)
        proposed = {gap["icd10_code"] for gap in rules["approved"] + rules["review"]}
        self.assertIn("I50.22", proposed)
        self.assertIn("I50.9", proposed)
        audited = run_gap_pipeline(
            profile,
            profile["demographics"],
            nemotron_client=NemotronClient(api_key=""),
            guideline_client=GuidelineClient(api_key="", use_fixtures=True),
        )
        shown = {item["icd10_code"] for item in audited["audit"]["worklist"]}
        self.assertIn("I50.22", shown)
        self.assertNotIn("I50.9", shown)
        upgrade = _item(audited, "I50.22")
        self.assertEqual(upgrade["decision"], "accept")
        self.assertTrue(upgrade["guideline_citations"])
        dropped = {item["icd10_code"]: item for item in audited["audit"]["dropped"]}
        self.assertEqual(dropped["I50.9"]["reason"], "rejected")
        self.assertIn("Unspecified sibling", dropped["I50.9"]["reason_detail"])

    def test_single_kidney_lab_without_ckd_does_not_yield_n18(self):
        from codesentinel.nemotron.client import NemotronClient
        from codesentinel.nemotron.mock_reasoner import kidney_disease_is_established
        from codesentinel.tavily.client import GuidelineClient
        from gap_pipeline import run_gap_pipeline

        acute = {
            "fhir_patient_id": "acute-kidney-lab",
            "demographics": {"full_name": "Synthetic"},
            "clinical_notes": [{
                "text": (
                    "ASSESSMENT AND PLAN:\n"
                    "Hyperlipidemia is active. LDL is 170. Continue atorvastatin. "
                    "Creatinine is up after a few days of poor intake. This reading is acute."
                ),
            }],
            "observations": [
                {"loinc": "2089-1", "name": "LDL", "value": 170, "unit": "mg/dL", "date": "2026-03-01", "observation_id": "ldl"},
                {"loinc": "33914-3", "name": "eGFR", "value": 54, "unit": "mL/min/1.73m2", "date": "2026-03-01", "observation_id": "egfr"},
            ],
            "medications": [{"name": "Atorvastatin 20mg", "status": "active", "medication_id": "a"}],
            "conditions": [],
            "encounters": [{"encounter_id": "e1"}],
        }
        rules = run_gap_pipeline(acute, acute["demographics"], verify=False)
        proposed = {gap["icd10_code"] for gap in rules["approved"] + rules["review"]}
        self.assertTrue(any(code.startswith("N18") for code in proposed))
        audited = run_gap_pipeline(
            acute,
            acute["demographics"],
            nemotron_client=NemotronClient(api_key=""),
            guideline_client=GuidelineClient(api_key="", use_fixtures=True),
        )
        shown = {item["icd10_code"] for item in audited["audit"]["worklist"]}
        self.assertFalse(any(code.startswith("N18") for code in shown))
        self.assertTrue(any(code.startswith("E78") for code in shown))
        short = "The eGFR has been low for 2 months."
        self.assertFalse(kidney_disease_is_established(short, acute["observations"]))
        denied = "No history of chronic kidney disease. The eGFR is 54."
        self.assertFalse(kidney_disease_is_established(denied, acute["observations"]))

    def test_documented_ckd_or_three_month_chronicity_keeps_n18(self):
        from codesentinel.nemotron.client import NemotronClient
        from codesentinel.nemotron.mock_reasoner import kidney_disease_is_established
        from codesentinel.tavily.client import GuidelineClient
        from gap_pipeline import run_gap_pipeline

        diagnosed = {
            "fhir_patient_id": "ckd-diagnosed",
            "demographics": {"full_name": "Synthetic"},
            "clinical_notes": [{
                "text": "ASSESSMENT AND PLAN:\nChronic kidney disease stage 3 is active. The eGFR is 48.",
            }],
            "observations": [
                {"loinc": "33914-3", "name": "eGFR", "value": 48, "unit": "mL/min/1.73m2", "date": "2026-03-01", "observation_id": "egfr"},
            ],
            "medications": [],
            "conditions": [],
            "encounters": [{"encounter_id": "e1"}],
        }
        audited = run_gap_pipeline(
            diagnosed,
            diagnosed["demographics"],
            nemotron_client=NemotronClient(api_key=""),
            guideline_client=GuidelineClient(api_key="", use_fixtures=True),
        )
        shown = {item["icd10_code"] for item in audited["audit"]["worklist"]}
        self.assertTrue(any(code.startswith("N18") for code in shown))

        lasting = "The eGFR has been below 60 for 4 months."
        self.assertTrue(kidney_disease_is_established(lasting, diagnosed["observations"]))
        spanned = [
            {"loinc": "33914-3", "name": "eGFR", "value": 52, "unit": "mL/min/1.73m2", "date": "2026-01-01"},
            {"loinc": "33914-3", "name": "eGFR", "value": 50, "unit": "mL/min/1.73m2", "date": "2026-05-01"},
        ]
        self.assertTrue(kidney_disease_is_established("Lipids are stable.", spanned))
        one_day = [
            {"loinc": "2160-0", "name": "Creatinine", "value": 1.8, "unit": "mg/dL", "date": "2026-04-02"},
        ]
        self.assertFalse(kidney_disease_is_established("Creatinine is up. This reading is acute.", one_day))

    def test_do_not_use_names_the_combination(self):
        from codesentinel.nemotron.mock_reasoner import mock_triage

        note = (
            "ASSESSMENT AND PLAN:\n"
            "Hypertensive chronic kidney disease is active. "
            "Do not use a diabetic kidney combination."
        )
        diabetic = mock_triage(_triage_context("E11.22", "Type 2 DM with diabetic CKD", note))
        hypertensive = mock_triage(_triage_context("I12.9", "Hypertensive CKD", note))
        self.assertEqual(diabetic.assertion_status, "negated")
        self.assertEqual(hypertensive.assertion_status, "present")

    def test_recorded_client_uses_chart_text_not_labels(self):
        from codesentinel.nemotron.mock_reasoner import mock_adjudicate, mock_triage

        denied = mock_triage(_triage_context(
            "I50.9", "Heart failure",
            "ASSESSMENT AND PLAN:\nHeart failure is listed. The patient does not have heart failure.",
        ))
        self.assertEqual(denied.assertion_status, "negated")
        family = mock_triage(_triage_context(
            "I50.9", "Heart failure",
            "Mother previously treated for congestive heart failure.",
        ))
        self.assertEqual(family.assertion_status, "family_history")
        screened = mock_triage(_triage_context(
            "F32.9", "Major depression",
            "Major depression was a routine item. No treatment indicated.",
        ))
        self.assertEqual(screened.assertion_status, "screening_or_rule_out")
        kidney = mock_triage(_triage_context(
            "N18.9", "Chronic kidney disease",
            "Medications were refilled. No new complaints were raised at this visit.",
            medications=[{"name": "Lisinopril 10mg", "status": "active", "medication_id": "l"}],
        ))
        self.assertEqual(kidney.assertion_status, "copied_forward_unclear")
        diabetes = mock_triage(_triage_context(
            "E11.9", "Type 2 diabetes mellitus",
            "Medications were refilled. No new complaints were raised at this visit.",
            medications=[{"name": "Metformin 500mg", "status": "active", "medication_id": "m"}],
        ))
        self.assertEqual(diabetes.assertion_status, "medication_supported")
        self.assertLess(diabetes.confidence, 0.6)
        held = mock_adjudicate({
            "gap_type": "missing",
            "citations": [],
            "confidence": diabetes.confidence,
            "icd10_code": "E11.9",
            "code_display": "Type 2 diabetes mellitus",
            "assertion_status": diabetes.assertion_status,
        })
        self.assertEqual(held.decision, "abstain")

    def test_c1_does_not_call_super_or_guidelines(self):
        profile = _diabetes_profile()
        quote = "Type 2 diabetes mellitus with hyperglycemia."
        candidate = {
            "tier": "SPECIFICITY_UPGRADE",
            "icd10_code": "E11.65",
            "condition_name": "Type 2 DM with hyperglycemia",
            "upgrade_from": "E11.9",
            "evidence_sources": [quote],
            "raf_value": 0.166,
        }
        rules = {"approved": [candidate], "review": [], "rejected": []}

        class _Counting(_EmptyGuidelines):
            def lookup(self, family, issue):
                raise AssertionError("C1 must not look up guidelines")

        client = _Scripted(quote, confidence=0.9)
        result = run_cascade(
            profile, rules, nemotron_client=client, guideline_client=_Counting(), config="C1"
        )
        self.assertEqual(client.calls, ["triage"])
        self.assertEqual(result["worklist"][0]["decision"], "review")
        self.assertIn("stops before Super", result["worklist"][0]["rationale"])
        self.assertEqual(result["timeline"][3]["status"], "skipped")
        self.assertEqual(result["timeline"][4]["status"], "skipped")


class _StatusScript:
    """Triage returns one assertion. Later steps accept unless reject_on_adjudicate is set."""

    def __init__(self, status, *, reject=False):
        self.mock = True
        self.used_fallback = False
        self.degraded_reasons = []
        self.status = status
        self.reject = reject

    def ensure_models(self):
        return None

    def select_triage_model(self):
        return "nvidia/Nemotron-3_5-Lightning"

    def complete_json(self, **kwargs):
        schema_name = kwargs["schema_name"]
        model = kwargs["model"]
        if schema_name == "triage":
            data = TriageResult(
                assertion_status=self.status,
                supporting_quotes=["model quote that is not in the chart"],
                section="unknown",
                confidence=0.4,
            )
        else:
            data = AdjudicationResult(
                decision="reject" if self.reject else "accept",
                icd10_code="E11.9",
                code_display="Type 2 diabetes",
                rationale="Scripted adjudicator.",
                physician_query_needed=False,
            )
        return Completion(
            data=data, raw="{}", input_tokens=10, output_tokens=5, latency_ms=1,
            mock=True, usage_source="estimated", model=model,
        )


class LivePathFixTests(unittest.TestCase):
    def test_refill_medication_list_is_not_dropped(self):
        note = "See medication list. Refills were sent today. No new complaints were raised at this visit."
        profile = {
            "fhir_patient_id": "gb-med",
            "clinical_notes": [{"text": note}],
            "observations": [],
            "medications": [{"name": "Metformin 500mg", "status": "active", "medication_id": "m1"}],
            "conditions": [],
        }
        gap = {
            "tier": "TIER_2_PHENOTYPE",
            "icd10_code": "E11.9",
            "condition_name": "Type 2 diabetes mellitus",
            "evidence_sources": ["Medication: metformin"],
        }
        result = run_cascade(
            profile, {"approved": [gap], "review": [], "rejected": []},
            nemotron_client=_StatusScript("copied_forward_unclear", reject=True),
            guideline_client=_EmptyGuidelines(),
            config="C4",
        )
        self.assertEqual(result["dropped"], [])
        item = result["worklist"][0]
        self.assertEqual(item["assertion_status"], "medication_supported")
        self.assertEqual(item["decision"], "abstain")
        self.assertTrue(item["verified_quotes"])

    def test_explicit_negation_still_drops_a_medication(self):
        note = (
            "See medication list. The patient does not have hypertension. "
            "Amlodipine is for Raynaud phenomenon."
        )
        profile = {
            "fhir_patient_id": "med-fp",
            "clinical_notes": [{"text": note}],
            "observations": [],
            "medications": [{"name": "Amlodipine 5mg", "status": "active", "medication_id": "a1"}],
            "conditions": [],
        }
        gap = {
            "tier": "TIER_2_PHENOTYPE",
            "icd10_code": "I10",
            "condition_name": "Essential hypertension",
            "evidence_sources": ["Medication: amlodipine"],
        }
        result = run_cascade(
            profile, {"approved": [gap], "review": [], "rejected": []},
            nemotron_client=_StatusScript("copied_forward_unclear"),
            guideline_client=_EmptyGuidelines(),
            config="C1",
        )
        self.assertEqual(result["worklist"], [])
        self.assertEqual(result["dropped"][0]["reason"], "copied_forward_unclear")

    def test_low_specificity_drug_stays_dropped(self):
        note = "Refills were sent today. No new complaints were raised at this visit."
        profile = {
            "fhir_patient_id": "ace",
            "clinical_notes": [{"text": note}],
            "observations": [],
            "medications": [{"name": "Lisinopril 10mg", "status": "active", "medication_id": "l1"}],
            "conditions": [],
        }
        gap = {
            "tier": "TIER_2_PHENOTYPE_WEAK",
            "icd10_code": "N18.9",
            "condition_name": "Chronic kidney disease",
            "evidence_sources": ["Medication: lisinopril"],
        }
        result = run_cascade(
            profile, {"approved": [gap], "review": [], "rejected": []},
            nemotron_client=_StatusScript("copied_forward_unclear"),
            guideline_client=_EmptyGuidelines(),
            config="C1",
        )
        self.assertEqual(result["worklist"], [])
        self.assertEqual(result["dropped"][0]["reason"], "copied_forward_unclear")

    def test_empty_note_lab_is_not_negated(self):
        profile = {
            "fhir_patient_id": "pt-007",
            "clinical_notes": [],
            "observations": [{
                "observation_id": "a1c",
                "loinc": "4548-4",
                "name": "HbA1c",
                "value": 7.2,
                "unit": "%",
            }],
            "medications": [],
            "conditions": [],
        }
        gap = {
            "tier": "TIER_1_STRUCTURED",
            "icd10_code": "E11.65",
            "condition_name": "Type 2 diabetes with hyperglycemia",
            "lab_loinc": "4548-4",
            "lab_value": 7.2,
            "evidence_sources": ["Lab: HbA1c = 7.2"],
        }
        result = run_cascade(
            profile, {"approved": [gap], "review": [], "rejected": []},
            nemotron_client=_StatusScript("negated", reject=True),
            guideline_client=_EmptyGuidelines(),
            config="C2",
        )
        self.assertEqual(result["dropped"], [])
        item = result["worklist"][0]
        self.assertEqual(item["assertion_status"], "lab_only")
        self.assertEqual(item["decision"], "review")
        self.assertIn("HbA1c", item["verified_quotes"][0])

    def test_negated_note_is_not_relabeled_lab_only(self):
        profile = {
            "fhir_patient_id": "neg-lab",
            "clinical_notes": [{"text": "The patient does not have diabetes. HbA1c is 5.2."}],
            "observations": [{
                "observation_id": "a1c",
                "loinc": "4548-4",
                "name": "HbA1c",
                "value": 5.2,
                "unit": "%",
            }],
            "medications": [],
            "conditions": [],
        }
        gap = {
            "tier": "TIER_3_NOTE_NER",
            "icd10_code": "E11.65",
            "condition_name": "Type 2 diabetes with hyperglycemia",
            "evidence_sources": ["The patient does not have diabetes."],
        }
        result = run_cascade(
            profile, {"approved": [gap], "review": [], "rejected": []},
            nemotron_client=_StatusScript("negated"),
            guideline_client=_EmptyGuidelines(),
            config="C1",
        )
        self.assertEqual(result["worklist"], [])
        self.assertEqual(result["dropped"][0]["reason"], "negated")

    def test_unsupported_existing_runs_on_the_cascade(self):
        profile = {
            "fhir_patient_id": "coded",
            "clinical_notes": [{"text": "COPD was entered on the problem list in error. Please remove it."}],
            "observations": [],
            "medications": [
                {"name": "Lisinopril 10mg", "status": "active", "medication_id": "l1"},
                {"name": "Metformin 500mg", "status": "active", "medication_id": "m1"},
            ],
            "conditions": [
                {"icd10_code": "J44.1", "display": "COPD"},
                {"icd10_code": "E11.9", "display": "Type 2 diabetes"},
            ],
        }
        result = run_cascade(
            profile, {"approved": [], "review": [], "rejected": []},
            nemotron_client=_StatusScript("present"),
            guideline_client=_EmptyGuidelines(),
            config="C1",
        )
        flagged = {
            item["icd10_code"]: item
            for item in result["worklist"]
            if item["gap_type"] == "unsupported_existing"
        }
        self.assertIn("J44.1", flagged)
        self.assertEqual(flagged["J44.1"]["decision"], "review")
        self.assertNotIn("E11.9", flagged)


def _row(code, display, decision, gap_type="missing"):
    return {
        "id": f"p:{code}",
        "icd10_code": code,
        "code_display": display,
        "gap_type": gap_type,
        "tier": "",
        "decision": decision,
        "verified_quotes": [display],
        "supporting_quotes": [display],
    }


def _triage_context(code, name, note, medications=None):
    return {
        "candidate": {"icd10_code": code, "condition_name": name},
        "profile": {
            "observations": [],
            "medications": medications or [],
            "clinical_notes": [{"text": note}],
        },
        "notes": note,
    }


def _diabetes_profile():
    return {
        "fhir_patient_id": "inline",
        "clinical_notes": [
            {"text": "ASSESSMENT AND PLAN:\nType 2 diabetes mellitus with hyperglycemia."}
        ],
        "observations": [],
        "medications": [],
    }


if __name__ == "__main__":
    unittest.main()
