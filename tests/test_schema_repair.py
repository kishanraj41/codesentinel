import json
import unittest

from codesentinel.nemotron.client import NemotronClient
from codesentinel.nemotron.schemas import TRIAGE_SCHEMA, TriageResult


class _Response:
    def __init__(self, status, payload, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


def _triage_body():
    return {
        "assertion_status": "present",
        "supporting_quotes": ["Type 2 diabetes mellitus with hyperglycemia."],
        "section": "assessment_plan",
        "meat": {"monitor": None, "evaluate": None, "assess": "Type 2 diabetes mellitus with hyperglycemia.", "treat": None},
        "confidence": 0.8,
    }


class SchemaRepairTests(unittest.TestCase):
    def test_one_repair_then_valid(self):
        calls = {"n": 0}

        def transport(url, headers=None, json=None, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                content = "not json"
            else:
                content = json_dumps()
            return _Response(
                200,
                {"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 10, "completion_tokens": 8}},
            )

        client = NemotronClient(api_key="test-key", transport=transport, sleep=lambda _seconds: None)
        result = client.complete_json(
            model="nvidia/Nemotron-3_5-Lightning",
            messages=[{"role": "user", "content": "chart"}],
            schema_name="triage",
            schema=TRIAGE_SCHEMA,
            pydantic_model=TriageResult,
            temperature=0,
            reasoning=False,
        )
        self.assertTrue(result.repaired)
        self.assertIsInstance(result.data, TriageResult)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(result.usage_source, "provider")
        self.assertFalse(result.mock)

    def test_second_failure_returns_no_data(self):
        def transport(url, headers=None, json=None, timeout=None):
            return _Response(200, {"choices": [{"message": {"content": "still bad"}}], "usage": {}})

        client = NemotronClient(api_key="test-key", transport=transport, sleep=lambda _seconds: None)
        result = client.complete_json(
            model="nvidia/Nemotron-3_5-Lightning",
            messages=[{"role": "user", "content": "chart"}],
            schema_name="triage",
            schema=TRIAGE_SCHEMA,
            pydantic_model=TriageResult,
            temperature=0,
            reasoning=False,
        )
        self.assertTrue(result.repaired)
        self.assertIsNone(result.data)

    def test_429_retries(self):
        calls = {"n": 0}

        def transport(url, headers=None, json=None, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return _Response(429, {}, headers={"Retry-After": "0"})
            return _Response(
                200,
                {
                    "choices": [{"message": {"content": json_dumps()}}],
                    "usage": {"prompt_tokens": 4, "completion_tokens": 4},
                },
                headers={"x-ratelimit-remaining-requests": "59", "x-ratelimit-remaining-tokens": "1000"},
            )

        client = NemotronClient(api_key="test-key", transport=transport, sleep=lambda _seconds: None)
        result = client.complete_json(
            model="nvidia/Nemotron-3_5-Lightning",
            messages=[{"role": "user", "content": "chart"}],
            schema_name="triage",
            schema=TRIAGE_SCHEMA,
            pydantic_model=TriageResult,
            temperature=0,
            reasoning=False,
        )
        self.assertEqual(calls["n"], 2)
        self.assertEqual(result.data.assertion_status, "present")
        self.assertEqual(client.last_ratelimit_remaining_requests, "59")

    def test_models_list_falls_back_to_nano(self):
        def get_transport(url, headers=None, timeout=None):
            return _Response(200, {"data": [{"id": "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"}]})

        client = NemotronClient(api_key="test-key", get_transport=get_transport)
        self.assertEqual(client.select_triage_model(), "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B")


    def test_models_list_retries_once_after_timeout(self):
        calls = {"n": 0}

        def get_transport(url, headers=None, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("read timed out")
            return _Response(200, {"data": [{"id": "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"}]})

        client = NemotronClient(api_key="test-key", get_transport=get_transport, sleep=lambda _s: None)
        self.assertEqual(client.select_triage_model(), "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B")
        self.assertEqual(calls["n"], 2)
        self.assertFalse(any("models.list failed" in r for r in client.degraded_reasons))

    def test_models_list_reports_failure_after_retries(self):
        def get_transport(url, headers=None, timeout=None):
            raise TimeoutError("read timed out")

        client = NemotronClient(api_key="test-key", get_transport=get_transport, sleep=lambda _s: None)
        client.ensure_models()
        self.assertTrue(any("models.list failed" in r for r in client.degraded_reasons))


class ReplayTests(unittest.TestCase):
    def test_replay_skips_transport(self):
        import tempfile

        calls = {"n": 0}

        def transport(url, headers=None, json=None, timeout=None):
            calls["n"] += 1
            return _Response(
                200,
                {
                    "choices": [{"message": {"content": json_dumps()}}],
                    "usage": {"prompt_tokens": 4, "completion_tokens": 4},
                },
            )

        messages = [{"role": "user", "content": "chart"}]
        with tempfile.TemporaryDirectory() as tmp:
            client = NemotronClient(
                api_key="test-key", transport=transport, sleep=lambda _seconds: None, record_dir=tmp
            )
            first = client.complete_json(
                model="nvidia/Nemotron-3_5-Lightning",
                messages=messages,
                schema_name="triage",
                schema=TRIAGE_SCHEMA,
                pydantic_model=TriageResult,
                temperature=0,
                reasoning=False,
            )
            self.assertEqual(calls["n"], 1)
            self.assertFalse(first.mock)
            self.assertEqual(first.usage_source, "provider")
            replay = NemotronClient(
                api_key="test-key",
                transport=transport,
                sleep=lambda _seconds: None,
                record_dir=tmp,
                replay=True,
            )
            second = replay.complete_json(
                model="nvidia/Nemotron-3_5-Lightning",
                messages=messages,
                schema_name="triage",
                schema=TRIAGE_SCHEMA,
                pydantic_model=TriageResult,
                temperature=0,
                reasoning=False,
            )
        self.assertEqual(calls["n"], 1)
        self.assertIsInstance(second.data, TriageResult)
        self.assertFalse(second.mock)
        self.assertEqual(second.usage_source, "provider")


class RetryBackoffTests(unittest.TestCase):
    def test_server_errors_use_exponential_backoff(self):
        calls = {"n": 0}
        sleeps = []

        def transport(url, headers=None, json=None, timeout=None):
            calls["n"] += 1
            if calls["n"] < 3:
                return _Response(500, {"error": "busy"})
            return _Response(
                200,
                {
                    "choices": [{"message": {"content": json_dumps()}}],
                    "usage": {"prompt_tokens": 4, "completion_tokens": 4},
                },
            )

        client = NemotronClient(
            api_key="test-key",
            transport=transport,
            sleep=sleeps.append,
            rng=lambda: 0.0,
        )
        result = client.complete_json(
            model="nvidia/Nemotron-3_5-Lightning",
            messages=[{"role": "user", "content": "chart"}],
            schema_name="triage",
            schema=TRIAGE_SCHEMA,
            pydantic_model=TriageResult,
            temperature=0,
            reasoning=False,
            stage="adjudication",
        )
        self.assertEqual(calls["n"], 3)
        self.assertEqual(sleeps, [1.0, 2.0])
        self.assertEqual(result.data.assertion_status, "present")
        self.assertEqual(client.degraded_reasons, [])

    def test_timeouts_record_the_stage_and_not_the_prompt(self):
        import requests

        sleeps = []
        prompt = "SECRET-PROMPT-DO-NOT-LOG"

        def transport(url, headers=None, json=None, timeout=None):
            raise requests.Timeout("timed out talking to the provider")

        client = NemotronClient(
            api_key="sk-test-key-do-not-log",
            transport=transport,
            sleep=sleeps.append,
            rng=lambda: 0.0,
        )
        result = client.complete_json(
            model="nvidia/Nemotron-3_5-Lightning",
            messages=[{"role": "user", "content": prompt}],
            schema_name="triage",
            schema=TRIAGE_SCHEMA,
            pydantic_model=TriageResult,
            temperature=0,
            reasoning=False,
            stage="triage",
        )
        self.assertTrue(result.fallback)
        self.assertEqual(len(sleeps), 2)
        self.assertEqual(sleeps, [1.0, 2.0])
        self.assertEqual(len(client.degraded_reasons), 1)
        reason = client.degraded_reasons[0]
        self.assertIn("stage=triage", reason)
        self.assertIn("model=nvidia/Nemotron-3_5-Lightning", reason)
        self.assertIn("status=", reason)
        self.assertIn("error=Timeout", reason)
        self.assertNotIn(prompt, reason)
        self.assertNotIn("sk-test-key-do-not-log", reason)
        self.assertNotIn("timed out", reason)
        self.assertEqual(result.error, reason)


def json_dumps():
    return json.dumps(_triage_body())


if __name__ == "__main__":
    unittest.main()
