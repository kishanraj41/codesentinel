import unittest

from codesentinel.nemotron.verifier import build_corpus, lab_line, medication_line


class VerifierTests(unittest.TestCase):
    def test_note_quote_must_be_a_real_substring(self):
        profile = {
            "clinical_notes": [
                {"text": "ASSESSMENT AND PLAN:\nType 2 diabetes mellitus with hyperglycemia."}
            ],
            "observations": [],
            "medications": [],
        }
        corpus = build_corpus(profile)
        self.assertTrue(corpus.grounded("Type 2 diabetes mellitus with hyperglycemia."))
        self.assertTrue(corpus.grounded("Type 2 diabetes\tmellitus with hyperglycemia."))
        self.assertFalse(corpus.grounded("severe congestive heart failure on exam"))
        self.assertFalse(corpus.grounded("diabetes"))

    def test_structured_lab_line_and_record_id(self):
        observation = {
            "observation_id": "obs-1",
            "loinc": "4548-4",
            "name": "HbA1c",
            "value": 8.4,
            "unit": "%",
        }
        medication = {"medication_id": "med-1", "name": "Metformin 1000mg"}
        profile = {
            "clinical_notes": [],
            "observations": [observation],
            "medications": [medication],
        }
        corpus = build_corpus(profile)
        self.assertTrue(corpus.grounded(lab_line(observation)))
        self.assertTrue(corpus.grounded(medication_line(medication)))
        self.assertTrue(corpus.grounded("obs-1"))
        self.assertFalse(corpus.grounded("HbA1c 8.4"))
        self.assertFalse(corpus.grounded("Lab HbA1c"))


if __name__ == "__main__":
    unittest.main()
