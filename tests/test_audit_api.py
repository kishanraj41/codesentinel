import os
import unittest

from fastapi.testclient import TestClient

os.environ.pop("NEBIUS_API_KEY", None)
os.environ.pop("TAVILY_API_KEY", None)

from mcp_server import app  # noqa: E402


class AuditApiTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("NEBIUS_API_KEY", None)
        os.environ.pop("TAVILY_API_KEY", None)
        self.client = TestClient(app)

    def test_health_and_patients(self):
        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        body = health.json()
        self.assertEqual(body["service"], "codesentinel")
        self.assertEqual(body["contract_version"], "1.0.0")
        self.assertEqual(body["nemotron"], "mock")
        self.assertEqual(body["tavily"], "mock")

        patients = self.client.get("/patients").json()["patients"]
        self.assertEqual(len(patients), 10)
        self.assertEqual(patients[0]["id"], "pt-001")

    def test_audit_pt001_keyless(self):
        response = self.client.post("/audit", json={"patient_id": "pt-001"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["contract_version"], "1.0.0")
        self.assertEqual(body["mode"], "mock")
        self.assertEqual(body["patient_id"], "pt-001")
        self.assertEqual(
            [step["step"] for step in body["timeline"]],
            ["rules", "triage", "verifier", "guidelines", "adjudication", "escalation", "physician_query"],
        )
        self.assertGreater(body["summary"]["candidates_generated"], 0)
        self.assertEqual(body["summary"]["worklist_count"], len(body["worklist"]))
        self.assertEqual(body["summary"]["dropped_count"], len(body["dropped"]))
        self.assertGreater(body["receipt"]["totals"]["cost_usd"], 0)
        self.assertIn("nvidia/Nemotron-3_5-Lightning", body["receipt"]["models_used"])
        codes = {item["icd10_code"] for item in body["worklist"]}
        self.assertIn("E11.65", codes)
        upgrade = next(item for item in body["worklist"] if item["icd10_code"] == "E11.65")
        self.assertEqual(upgrade["gap_type"], "specificity_upgrade")
        self.assertTrue(upgrade["verified_quotes"])
        citation = upgrade["guideline_citations"][0]
        self.assertEqual(citation["retrieved"], "anchor")
        self.assertEqual(citation["source_tier"], "official")
        self.assertEqual(citation["section"], "I.C.4.a")
        self.assertEqual(citation["page"], 39)
        self.assertNotIn("[...]", citation["quote"])
        self.assertNotIn("Not a live official excerpt", citation["quote"])
        self.assertIsInstance(body["rules_only"], dict)
        self.assertIn("approved", body["rules_only"])

    def test_unknown_patient_and_phi_and_ssrf(self):
        missing = self.client.post("/audit", json={"patient_id": "no-such-patient"})
        self.assertEqual(missing.status_code, 404)

        phi = self.client.post(
            "/audit",
            json={"patient_id": "pt-001", "profile": {"clinical_notes": [{"text": "SSN 123-45-6789"}]}},
        )
        self.assertEqual(phi.status_code, 422)
        self.assertIn("synthetic data only", phi.json()["detail"].lower())

        blocked = self.client.post(
            "/audit",
            json={"patient_id": "abc", "fhir_server_url": "http://169.254.169.254/latest/meta-data"},
        )
        self.assertEqual(blocked.status_code, 403)

        debug = self.client.get("/debug/patient/pt-001")
        self.assertEqual(debug.status_code, 404)

    def test_include_rules_only_false(self):
        response = self.client.post("/audit", json={"patient_id": "pt-005", "include_rules_only": False})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIsNone(body["rules_only"])
        accepted = [item for item in body["worklist"] if item["decision"] == "accept"]
        self.assertEqual(accepted, [])


if __name__ == "__main__":
    unittest.main()
