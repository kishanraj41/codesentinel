"""Degraded model calls are recorded on the chart and summarized without the prompt."""

import unittest

from eval.adapter import _audit_to_eval
from eval.run import degraded_report, format_degraded_section


class DegradedReasonTests(unittest.TestCase):
    def test_chart_record_keeps_the_reason(self):
        reason = "stage=triage model=nvidia/Nemotron-3_5-Lightning status=500 error=HTTPError"
        payload = _audit_to_eval(
            {
                "worklist": [],
                "dropped": [],
                "receipt": {"totals": {}},
                "degraded_reasons": [reason],
            },
            "degraded",
        )
        self.assertEqual(payload["mode"], "degraded")
        self.assertEqual(payload["degraded_reasons"], [reason])
        self.assertNotIn("prompt", reason)

    def test_summary_counts_charts_and_unique_reasons(self):
        reason = "stage=escalation model=nvidia/Nemotron-3-Ultra status=429 error=HTTPError"
        report = degraded_report(
            {
                "C4": {
                    "charts": [
                        {"patient_id": "a", "mode": "degraded", "degraded_reasons": [reason]},
                        {"patient_id": "b", "mode": "live", "degraded_reasons": []},
                        {"patient_id": "c", "mode": "degraded", "degraded_reasons": [reason]},
                    ]
                }
            }
        )
        self.assertEqual(report["charts"], 2)
        self.assertEqual(report["reasons"], [reason])
        self.assertEqual(report["by_config"]["C4"], 2)
        text = "\n".join(format_degraded_section(report))
        self.assertIn("Charts that fell back to the recorded client: 2.", text)
        self.assertIn(reason, text)

    def test_empty_summary(self):
        text = "\n".join(format_degraded_section(degraded_report({"C4": {"charts": []}})))
        self.assertIn("Charts that fell back to the recorded client: 0.", text)
        self.assertIn("No degraded reasons recorded.", text)


if __name__ == "__main__":
    unittest.main()
