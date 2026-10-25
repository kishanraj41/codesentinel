import os
import unittest

from codesentinel.fhir_guard import GuardError, assert_same_host, validate_fhir_base_url
from fhir_adapter import FHIRClient


class FHIRGuardTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("FHIR_ALLOW_ANY", None)
        os.environ.pop("FHIR_ALLOWED_HOSTS", None)

    def test_blocks_metadata_private_and_non_http(self):
        for url in (
            "http://169.254.169.254/latest/meta-data",
            "http://10.1.1.1/fhir",
            "file:///etc/passwd",
            "http://user:pass@hapi.fhir.org/fhir",
            "http://metadata.google.internal/",
        ):
            with self.assertRaises(GuardError):
                validate_fhir_base_url(url)

    def test_allows_loopback_and_default_host(self):
        self.assertTrue(validate_fhir_base_url("http://127.0.0.1:9090/fhir").startswith("http://127.0.0.1"))
        self.assertTrue(validate_fhir_base_url("https://hapi.fhir.org/baseR4").startswith("https://hapi.fhir.org"))
        with self.assertRaises(GuardError):
            validate_fhir_base_url("https://evil.example/fhir")

    def test_allow_any_still_blocks_metadata_and_private_dns(self):
        os.environ["FHIR_ALLOW_ANY"] = "1"
        with self.assertRaises(GuardError):
            validate_fhir_base_url("http://169.254.169.254/")

        def resolver(_host, _port):
            return [(None, None, None, None, ("127.0.0.1", 0))]

        with self.assertRaises(GuardError):
            validate_fhir_base_url("http://rebinding.example/fhir", resolver=resolver)

        def public_resolver(_host, _port):
            return [(None, None, None, None, ("93.184.216.34", 0))]

        self.assertIn("public.example", validate_fhir_base_url("http://public.example/fhir", resolver=public_resolver))

    def test_redirect_must_stay_on_host(self):
        base = "http://127.0.0.1:9090/fhir"
        self.assertTrue(assert_same_host(base, "http://127.0.0.1:9090/fhir/Patient/1"))
        with self.assertRaises(GuardError):
            assert_same_host(base, "http://169.254.169.254/latest")

    def test_client_rejects_metadata_before_any_request(self):
        with self.assertRaises(GuardError):
            FHIRClient("http://169.254.169.254/")


if __name__ == "__main__":
    unittest.main()
