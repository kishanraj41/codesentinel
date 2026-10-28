"""Offline tests for the Synthea benchmark builder and runner (no Synthea output needed)."""

import base64
import unittest

from eval.synthea import build
from eval.synthea.run import BundleFHIRClient, _normalize


def _cond(sct, display, cid=None):
    return {
        "resourceType": "Condition",
        "id": cid or f"c-{sct}",
        "clinicalStatus": {"coding": [{"code": "active"}]},
        "code": {"coding": [{"system": "http://snomed.info/sct", "code": sct, "display": display}]},
    }


class TruthCodeRules(unittest.TestCase):
    def setUp(self):
        self.mapping = build.load_map()

    def _truth(self, *scts):
        active = {s: _cond(s, s) for s in scts}
        codes, rules = build.truth_codes(active, self.mapping)
        return set(codes), rules

    def test_map_has_sources(self):
        for row in self.mapping.values():
            self.assertTrue(row["source"])
            self.assertTrue(row["icd10cm"])

    def test_diabetes_with_ckd_and_hypertension(self):
        codes, rules = self._truth("44054006", "433144002", "59621000")
        self.assertEqual(codes, {"E11.22", "N18.30", "I12.9"})
        self.assertIn("R1", rules)
        self.assertIn("R5", rules)

    def test_one_ckd_stage_and_esrd(self):
        codes, rules = self._truth("431855005", "431856006", "46177005", "59621000")
        self.assertEqual(codes, {"N18.6", "I12.0"})
        self.assertIn("R8", rules)

    def test_hypertension_heart_failure_ckd(self):
        codes, _ = self._truth("59621000", "88805009", "433144002")
        self.assertEqual(codes, {"I13.0", "I50.9", "N18.30"})

    def test_hyperglycemia_and_prediabetes_excludes(self):
        codes, rules = self._truth("44054006", "80394007", "714628002")
        self.assertEqual(codes, {"E11.65"})
        self.assertIn("R2", rules)
        self.assertIn("R3", rules)

    def test_plain_hypertension(self):
        codes, _ = self._truth("59621000", "55822004")
        self.assertEqual(codes, {"I10", "E78.5"})


class Support(unittest.TestCase):
    def test_lab_criteria(self):
        def obs(loinc, value, when):
            return {"code": {"coding": [{"code": loinc}]}, "valueQuantity": {"value": value}, "effectiveDateTime": when}
        self.assertTrue(build._lab_support("diabetes", [obs("4548-4", 7.1, "2026-05-01")], "male"))
        self.assertFalse(build._lab_support("diabetes", [obs("4548-4", 5.9, "2026-05-01")], "male"))
        egfr = [obs("33914-3", 52, "2026-01-01"), obs("33914-3", 48, "2026-06-01")]
        self.assertTrue(build._lab_support("ckd3", egfr, "female"))
        self.assertFalse(build._lab_support("ckd3", egfr[:1], "female"))
        self.assertTrue(build._lab_support("anemia", [obs("718-7", 12.5, "2026-06-01")], "male"))
        self.assertFalse(build._lab_support("anemia", [obs("718-7", 12.5, "2026-06-01")], "female"))


class Runner(unittest.TestCase):
    def test_bundle_client_feeds_the_fhir_adapter(self):
        from fhir_adapter import FHIRPatientExtractor

        note = base64.b64encode(b"Patient is on lisinopril for essential hypertension.").decode()
        bundle = {"resourceType": "Bundle", "entry": [{"resource": r} for r in [
            {"resourceType": "Patient", "id": "p-1", "birthDate": "1960-01-01", "gender": "female", "name": [{"given": ["A"], "family": "B"}]},
            build._condition("p-1", "E78.5", "Hyperlipidemia, unspecified"),
            {"resourceType": "Observation", "id": "o1", "category": [{"coding": [{"code": "laboratory"}]}],
             "code": {"coding": [{"system": "http://loinc.org", "code": "4548-4", "display": "HbA1c"}]},
             "valueQuantity": {"value": 7.2, "unit": "%"}, "effectiveDateTime": "2026-06-01"},
            {"resourceType": "Observation", "id": "o2", "category": [{"coding": [{"code": "vital-signs"}]}],
             "code": {"coding": [{"system": "http://loinc.org", "code": "8480-6"}]}, "valueQuantity": {"value": 150}},
            {"resourceType": "MedicationRequest", "id": "m1", "status": "active",
             "medicationCodeableConcept": {"coding": [{"code": "314076", "display": "lisinopril 10 MG Oral Tablet"}]}},
            {"resourceType": "DocumentReference", "id": "d1", "date": "2026-06-01",
             "content": [{"attachment": {"contentType": "text/plain", "data": note}}]},
        ]]}
        profile = FHIRPatientExtractor(BundleFHIRClient(bundle)).extract_patient("p-1")
        self.assertEqual(profile["claims_codes"], {"E78.5"})
        self.assertEqual([o["loinc"] for o in profile["observations"]], ["4548-4"])
        self.assertEqual(profile["medications"][0]["name"].lower(), "lisinopril 10 mg oral tablet")
        self.assertIn("essential hypertension", profile["clinical_notes"][0]["text"])

    def test_normalize_parent_code_and_neutral(self):
        label = {
            "truth_codes": ["N18.30", "E11.22"],
            "expected_gaps": [{"code": "N18.30"}],
            "neutral_codes": [{"code": "D64.9"}],
        }
        items = [
            {"code": "N18.3", "shown_to_coder": True, "gap_type": "missing"},
            {"code": "D64.9", "shown_to_coder": True, "gap_type": "missing"},
            {"code": "D64.9", "shown_to_coder": True, "gap_type": "unsupported_existing"},
            {"code": "I10", "shown_to_coder": True, "gap_type": "missing"},
        ]
        out = _normalize(items, label)
        self.assertEqual([(i["code"], i["gap_type"]) for i in out],
                         [("N18.30", "missing"), ("D64.9", "unsupported_existing"), ("I10", "missing")])
        self.assertEqual(out[0]["emitted_code"], "N18.3")


class SplitTests(unittest.TestCase):
    def test_split_is_seeded_disjoint_and_complete(self):
        import json
        import random

        from eval.synthea.run import SPLIT_FILE, split_ids

        data = json.loads(SPLIT_FILE.read_text(encoding="utf-8"))
        dev, held = split_ids("dev"), split_ids("held_out")
        self.assertEqual((len(dev), len(held)), (100, 300))
        self.assertFalse(dev & held)
        ids = sorted(dev | held)
        random.Random(data["seed"]).shuffle(ids)
        self.assertEqual(set(ids[:100]), dev)
        self.assertIsNone(split_ids("all"))


if __name__ == "__main__":
    unittest.main()


class AdaptiveReportTests(unittest.TestCase):
    """The Synthea report carries the adaptive config, info rows and escalation share."""

    def _summary(self):
        cfg = lambda cid, esc: {
            "id": cid, "label": cid, "precision": 0.5, "recall": 0.5, "f1": 0.5, "tp": 1, "fp": 1, "fn": 1,
            "review_burden_per_chart": 1.0, "gap_rows_per_chart": 0.5, "info_rows_per_chart": 0.25, "info_rows": 1,
            "escalated_fraction": esc, "cost_per_chart_usd": 0.001, "latency_p50_s": 1, "latency_p95_s": 2,
            "precision_ci95": [0.1, 0.9], "recall_ci95": [0.1, 0.9],
            "unsupported_existing": {"tp": 1, "fp": 0, "fn": 0, "planted_shown_as_info": 0},
        }
        return {
            "dataset": {"split": "dev", "charts": 4, "synthea_version": "v4.0.0", "seed": 1, "state": "Texas",
                        "ages": "45-90", "reference_date": "2026-10-27", "remove_fraction": 0.3, "expected_gaps": 1,
                        "charts_with_gaps": 1, "neutral_codes": 0, "unsupported_existing": 1},
            "mode": "mock", "run_label": "dev round 1", "commit": "abc", "generated_local": "now",
            "configs": [cfg("C0", 0), cfg("C0+", 0), cfg("C1", 0), cfg("C4", 1), cfg("C4a", 0.25)],
            "per_code": {"C0+": {}, "C4": {}, "C4a": {"I10": {"tp": 1}}}, "gold_by_code": {"I10": 1},
            "gold_gap_fate": {}, "caveats": [],
        }

    def test_markdown_has_new_columns_and_c4a(self):
        from eval.synthea.run import markdown

        text = markdown(self._summary())
        self.assertIn("Planted unsupported caught", text)
        self.assertIn("Charts past C1", text)
        self.assertIn("| C4a", text)
        self.assertIn("25%", text)
        self.assertIn("dev round 1", text)
        self.assertIn("C4a TP / FP", text)

    def test_configs_include_adaptive(self):
        from eval.lexicon import CONFIG_LABELS, CONFIGS

        self.assertEqual(CONFIGS[:6], ("C0", "C0+", "C1", "C2", "C3", "C4"))
        self.assertIn("C4a", CONFIGS)
        self.assertIn("C4a", CONFIG_LABELS)
