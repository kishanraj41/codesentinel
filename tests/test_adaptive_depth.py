"""Fix B: adaptive depth (config C4a).

C4a runs C1 (Lightning triage + quote verifier) and escalates the chart to the full
C4 path (Super, Tavily, Ultra) only when a combination or specificity candidate
survives triage. C1-C4 are unchanged. The audit records which depth ran.
"""

import os
import unittest

from codesentinel.nemotron.cascade import (
    ADAPTIVE_CONFIG,
    DEFAULT_SERVICE_CONFIG,
    needs_deep_cascade,
    run_cascade,
)
from codesentinel.nemotron.catalog import SUPER, ULTRA
from codesentinel.nemotron.client import Completion
from codesentinel.nemotron.schemas import AdjudicationResult, TriageResult

NOTE = (
    "Assessment and plan: Hyperlipidemia, continue atorvastatin. "
    "Type 2 diabetes with diabetic chronic kidney disease stage 3, eGFR stable over a year."
)


class _Client:
    def __init__(self, status="present", confidence=0.95):
        self.mock = True
        self.used_fallback = False
        self.degraded_reasons = []
        self.status = status
        self.confidence = confidence
        self.calls = []
        self.models = []

    def ensure_models(self):
        return None

    def select_triage_model(self):
        return "nvidia/Nemotron-3_5-Lightning"

    def complete_json(self, **kwargs):
        name = kwargs["schema_name"]
        self.calls.append(name)
        self.models.append(kwargs["model"])
        if name == "triage":
            data = TriageResult(
                assertion_status=self.status,
                supporting_quotes=[NOTE.split(". ")[0] if "E78" in str(kwargs) else NOTE],
                section="assessment_plan",
                confidence=self.confidence,
            )
        else:
            data = AdjudicationResult(
                decision="accept", icd10_code="E11.22", code_display="Type 2 DM with diabetic CKD",
                rationale="Scripted accept.", physician_query_needed=False,
            )
        return Completion(data=data, raw="{}", input_tokens=10, output_tokens=5, latency_ms=1,
                          mock=True, usage_source="estimated", model=kwargs["model"])


class _Guidelines:
    use_fixtures = False
    credits = 0
    cache_hits = 0
    live_requests = 0
    degraded_reasons = []

    def __init__(self):
        self.lookups = []

    def lookup(self, family, issue):
        self.lookups.append((family, issue))
        return []


MISSING = {"tier": "TIER_3_NOTE_NER", "icd10_code": "E78.5", "condition_name": "Hyperlipidemia",
           "evidence_sources": ["Assessment and plan: Hyperlipidemia, continue atorvastatin"]}
COMBO = {"tier": "COMBINATION_CODE", "icd10_code": "E11.22", "condition_name": "Type 2 DM with diabetic CKD",
         "evidence_sources": [NOTE], "raf_value": 0.3}


def _profile():
    return {"fhir_patient_id": "adaptive", "clinical_notes": [{"text": NOTE}], "observations": [],
            "medications": [], "conditions": [], "encounters": []}


def _run(gaps, config, client=None, guidelines=None):
    client = client or _Client()
    guidelines = guidelines or _Guidelines()
    result = run_cascade(_profile(), {"approved": gaps, "review": [], "rejected": []},
                         nemotron_client=client, guideline_client=guidelines, config=config)
    return result, client, guidelines


class AdaptiveDepthTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("NEBIUS_API_KEY", None)

    def test_missing_only_chart_stays_at_c1(self):
        result, client, guidelines = _run([MISSING], "C4a")
        self.assertEqual(result["depth"]["config"], "C4a")
        self.assertTrue(result["depth"]["adaptive"])
        self.assertEqual(result["depth"]["depth_used"], "C1")
        self.assertFalse(result["depth"]["escalated"])
        self.assertEqual(client.calls, ["triage"])
        self.assertEqual(guidelines.lookups, [])
        steps = {step["step"]: step for step in result["timeline"]}
        for name in ("guidelines", "adjudication", "escalation"):
            self.assertEqual(steps[name]["status"], "skipped")
            self.assertIn("adaptive", steps[name]["detail"])

    def test_surviving_combination_escalates_to_c4(self):
        result, client, guidelines = _run([MISSING, COMBO], "C4a")
        self.assertEqual(result["depth"]["depth_used"], "C4")
        self.assertTrue(result["depth"]["escalated"])
        self.assertEqual(result["depth"]["deep_candidates"], ["E11.22"])
        self.assertIn(SUPER, client.models)
        self.assertIn(ULTRA, client.models)
        self.assertTrue(guidelines.lookups)

    def test_adaptive_matches_c4_when_escalated(self):
        a, _c, _g = _run([MISSING, COMBO], "C4a")
        b, _c2, _g2 = _run([MISSING, COMBO], "C4")
        strip = lambda rows: [(r["icd10_code"], r["decision"]) for r in rows]
        self.assertEqual(strip(a["worklist"]), strip(b["worklist"]))

    def test_adaptive_matches_c1_when_not_escalated(self):
        a, _c, _g = _run([MISSING], "C4a")
        b, _c2, _g2 = _run([MISSING], "C1")
        strip = lambda rows: [(r["icd10_code"], r["decision"]) for r in rows]
        self.assertEqual(strip(a["worklist"]), strip(b["worklist"]))

    def test_combination_dropped_at_triage_does_not_escalate(self):
        result, client, _g = _run([COMBO], "C4a", client=_Client(status="negated"))
        self.assertEqual(result["depth"]["depth_used"], "C1")
        self.assertEqual(client.calls, ["triage"])

    def test_fixed_configs_unchanged(self):
        for config in ("C1", "C2", "C3", "C4"):
            with self.subTest(config=config):
                result, client, _g = _run([MISSING], config)
                self.assertEqual(result["depth"]["depth_used"], config)
                self.assertFalse(result["depth"]["adaptive"])
        # Full C4 still runs guideline lookup and Super on a combination chart, as before.
        result, client, guidelines = _run([COMBO], "C4")
        self.assertIn(SUPER, client.models)
        self.assertTrue(guidelines.lookups)

    def test_needs_deep_cascade_uses_rules_tiers(self):
        rows = [{"gap": {"tier": "SPECIFICITY_UPGRADE", "icd10_code": "E11.65"}},
                {"gap": {"tier": "TIER_1_LAB", "icd10_code": "N18.3"}},
                {"gap": {"tier": "COMBINATION_CODE", "icd10_code": "I12.9"}}]
        self.assertEqual(needs_deep_cascade(rows), ["E11.65", "I12.9"])
        self.assertEqual(needs_deep_cascade([]), [])


class ServiceDefaultTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("NEBIUS_API_KEY", None)
        os.environ.pop("TAVILY_API_KEY", None)
        os.environ.pop("CODESENTINEL_CASCADE_CONFIG", None)

    def tearDown(self):
        os.environ.pop("CODESENTINEL_CASCADE_CONFIG", None)

    def test_service_default_is_adaptive(self):
        self.assertEqual(DEFAULT_SERVICE_CONFIG, ADAPTIVE_CONFIG)
        from gap_pipeline import run_gap_pipeline

        result = run_gap_pipeline(_profile(), {}, nemotron_client=_Client(), guideline_client=_Guidelines())
        self.assertEqual(result["audit"]["depth"]["config"], "C4a")

    def test_env_override_selects_full_cascade(self):
        os.environ["CODESENTINEL_CASCADE_CONFIG"] = "C4"
        from gap_pipeline import run_gap_pipeline

        result = run_gap_pipeline(_profile(), {}, nemotron_client=_Client(), guideline_client=_Guidelines())
        self.assertEqual(result["audit"]["depth"]["config"], "C4")


if __name__ == "__main__":
    unittest.main()
