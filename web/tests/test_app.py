"""Judge-path tests: cache contract, PHI guard, concurrency, budget fallback."""

import asyncio
import json
import re
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.catalog import PRICES, cost_usd
from app.charts import CACHE, CARDS, validate_cache
from app.main import create_app
from app.phi_guard import scan_phi
from app.service import Settings
from app.upstream import UpstreamError

WEB = Path(__file__).resolve().parents[1]
ICD = re.compile(r"\b[A-Z]\d{2}(?:\.[0-9A-Z]+)?\b")


def _client(settings=None):
    return TestClient(create_app(settings or Settings()))


def test_cache_invariants():
    validate_cache()
    margaret = CACHE["margaret-whitmore"]["audit"]
    assert margaret["contract_version"] == "1.0.0"
    assert margaret["summary"]["worklist_count"] == 4
    assert margaret["summary"]["dropped_count"] == 17
    assert margaret["summary"]["candidates_generated"] == 21
    assert "4 codes to review" in margaret["summary"]["headline"]
    assert [step["step"] for step in margaret["timeline"]] == [
        "rules", "triage", "verifier", "guidelines", "adjudication", "escalation", "physician_query",
    ]
    assert CACHE["pt-009"]["audit"]["worklist"] == []
    for bundle in CACHE.values():
        for item in bundle["audit"]["worklist"]:
            query = item.get("physician_query") or ""
            assert not ICD.search(query)
            for citation in item["guideline_citations"]:
                assert "not a live" in citation["quote"]


def test_receipt_uses_catalog_prices():
    receipt = CACHE["margaret-whitmore"]["audit"]["receipt"]
    total = 0.0
    for step in receipt["steps"]:
        prices = receipt["catalog_prices_per_million"]
        if step["model"]:
            pin = prices[step["model"]]["input"]
            pout = prices[step["model"]]["output"]
            assert (pin, pout) == PRICES[step["model"]]
        expected = cost_usd(step["model"], step["input_tokens"], step["output_tokens"])
        assert step["cost_usd"] == expected
        total += expected
    assert receipt["totals"]["cost_usd"] == round(total, 6)
    assert "not a live Token Factory bill" in receipt["disclaimer"].lower() or "not a Token Factory bill" in receipt["disclaimer"]


def test_phi_guard_kinds():
    assert scan_phi("SSN 123-45-6789") == ["ssn"]
    assert "phone" in scan_phi("Call 512-555-0199 after lunch")
    assert "email" in scan_phi("reach dana@example.com")
    assert "mrn" in scan_phi("MRN: 004821")
    assert "dob" in scan_phi("DOB: 03/14/1958")
    assert scan_phi(CACHE["margaret-whitmore"]["chart"]["note"]) == []
    assert scan_phi("HbA1c 8.9% on 2026-03-12. She denies chest pain.") == []


def test_cached_audit_and_pages():
    client = _client()
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["service"] == "codesentinel-web"
    patients = client.get("/api/patients").json()["patients"]
    assert [row["id"] for row in patients] == [card["id"] for card in CARDS]
    audit = client.post("/api/audit", json={"patient_id": "margaret-whitmore", "mode": "auto"})
    assert audit.status_code == 200
    body = audit.json()
    assert body["delivery"]["source"] == "upstream"
    assert body["audit"]["contract_version"] == "1.0.0"
    assert body["audit"]["mode"] == "mock"
    assert body["audit"]["patient_id"] == "margaret-whitmore"
    assert body["audit"]["patient_name"] == "Margaret Whitmore"
    assert [step["step"] for step in body["audit"]["timeline"]] == [
        "rules", "triage", "verifier", "guidelines", "adjudication", "escalation", "physician_query",
    ]
    assert isinstance(body["audit"]["worklist"], list)
    assert "She denies chest pain." in body["chart"]["note"]
    explicit = client.post("/api/audit", json={"patient_id": "pt-005", "mode": "cached"})
    assert explicit.json()["delivery"]["source"] == "cache"
    assert explicit.json()["delivery"]["fallback_reason"] is None
    assert explicit.json()["audit"]["summary"]["abstain_count"] == 1
    missing = client.post("/api/audit", json={"patient_id": "pt-002", "mode": "cached"})
    assert missing.status_code == 404


def test_phi_rejected_and_clean_paste():
    client = _client()
    dirty = client.post("/api/audit", json={
        "profile": {"clinical_notes": [{"text": "Patient SSN 123-45-6789 lives nearby."}]},
    })
    assert dirty.status_code == 422
    assert "123-45-6789" not in dirty.text
    assert "ssn" in dirty.json()["kinds"]
    clean = client.post("/api/audit", json={
        "mode": "auto",
        "profile": {"clinical_notes": [{"text": "She denies chest pain. HbA1c 8.9% on 2026-03-12."}]},
    })
    assert clean.status_code == 200
    body = clean.json()
    assert body["delivery"]["source"] == "upstream"
    assert body["audit"]["mode"] == "mock"
    assert isinstance(body["audit"]["worklist"], list)
    assert "HbA1c 8.9%" in body["chart"]["note"]
    assert "nothing was stored" not in body["delivery"]["message"].lower()


def test_stream_includes_steps_and_result():
    client = _client()
    response = client.post("/api/audit/stream", json={
        "patient_id": "margaret-whitmore", "mode": "cached", "pace_ms": 0,
    })
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    text = response.text
    assert "event: step" in text
    assert "event: result" in text
    assert "I20.9" in text
    assert "Dropped" not in text or "I20.9" in text


def test_budget_and_rate_limit_fall_back_to_cache():
    app = create_app(Settings(
        audit_api_base="http://audit.test",
        max_daily_usd=0,
        estimated_live_usd=0.03,
        live_rate_per_hour=10,
    ))
    calls = {"n": 0}

    async def explode(_body):
        calls["n"] += 1
        return {"worklist": [], "mode": "live"}

    app.state.upstream.audit = explode
    client = TestClient(app)
    response = client.post("/api/audit", json={"patient_id": "pt-009", "mode": "live"})
    assert response.status_code == 200
    assert response.json()["delivery"]["message"] == "Live quota reached, showing cached run."
    assert calls["n"] == 0

    limited = create_app(Settings(
        audit_api_base="http://audit.test",
        max_daily_usd=5,
        estimated_live_usd=0.01,
        live_rate_per_hour=1,
    ))

    async def ok(body):
        calls["n"] += 1
        return {
            "worklist": [],
            "dropped": [],
            "mode": "live",
            "patient_id": body.get("patient_id"),
            "timeline": [],
            "receipt": {"totals": {"cost_usd": 0.004}},
        }

    calls["n"] = 0
    limited.state.upstream.audit = ok
    client = TestClient(limited)
    first = client.post("/api/audit", json={"patient_id": "pt-001", "mode": "live"})
    second = client.post("/api/audit", json={"patient_id": "pt-001", "mode": "live"})
    assert first.status_code == 200
    assert first.json()["delivery"]["source"] == "upstream"
    assert second.json()["delivery"]["fallback_reason"] == "rate_limit"
    assert second.json()["audit"]["patient_id"] == "pt-001"
    assert calls["n"] == 1


def test_upstream_error_uses_cache_and_live_success_is_labeled():
    app = create_app(Settings(audit_api_base="http://audit.test", max_daily_usd=3, estimated_live_usd=0.02))

    async def down(_body):
        raise UpstreamError("down")

    app.state.upstream.audit = down
    client = TestClient(app)
    failed = client.post("/api/audit", json={"patient_id": "pt-004", "mode": "live"})
    assert failed.json()["delivery"]["fallback_reason"] == "api_error"
    assert "cached run" in failed.json()["delivery"]["message"].lower()

    async def up(body):
        return {
            "contract_version": "1.0.0",
            "patient_id": body["patient_id"],
            "patient_name": "Robert Williams",
            "mode": "live",
            "guideline_mode": "live",
            "worklist": [{
                "id": "pt-004:E11.22",
                "icd10_code": "E11.22",
                "code_display": "Type 2 diabetes with CKD",
                "gap_type": "combination",
                "decision": "accept",
                "confidence": 0.8,
                "rationale": "Live row.",
                "verified_quotes": ["Diabetic nephropathy progressing."],
                "decided_by": "nvidia/Nemotron-3-Ultra-550b-a55b",
                "guideline_citations": [],
                "meat": {},
            }],
            "dropped": [],
            "timeline": [{"step": "rules", "label": "Rules", "model": None, "status": "ok", "latency_ms": 5, "detail": "live", "mock": False}],
            "summary": {"worklist_count": 1, "dropped_count": 0, "candidates_generated": 1, "headline": "1 code to review"},
            "receipt": {"steps": [], "totals": {"cost_usd": 0.01, "input_tokens": 10, "output_tokens": 2, "latency_ms": 5, "tavily_credits": 2, "tavily_cache_hits": 0, "calls": 1}},
        }

    app.state.upstream.audit = up
    app.state.budget.spent = 0
    live = client.post("/api/audit", json={"patient_id": "pt-004", "mode": "live"})
    body = live.json()
    assert body["delivery"]["source"] == "upstream"
    assert "Token Factory" in body["delivery"]["message"]
    assert body["audit"]["worklist"][0]["icd10_code"] == "E11.22"


def test_two_visitors_at_once():
    app = create_app(Settings(audit_api_base="http://audit.test", max_daily_usd=3, estimated_live_usd=0.01, live_rate_per_hour=10))

    async def slow(body):
        await asyncio.sleep(0.15)
        return {
            "worklist": [],
            "dropped": [],
            "mode": "live",
            "patient_id": body.get("patient_id"),
            "timeline": [],
            "receipt": {"totals": {"cost_usd": 0.002}},
        }

    app.state.upstream.audit = slow

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            left, right = await asyncio.gather(
                client.post("/api/audit", json={"patient_id": "margaret-whitmore", "mode": "live"}),
                client.post("/api/audit", json={"patient_id": "pt-009", "mode": "live"}),
            )
            return left, right

    left, right = asyncio.run(go())
    assert left.status_code == 200
    assert right.status_code == 200
    assert left.json()["audit"]["patient_id"] == "margaret-whitmore"
    assert right.json()["audit"]["patient_id"] == "pt-009"
    assert left.json()["delivery"]["source"] == "upstream"
    assert right.json()["delivery"]["source"] == "upstream"


def test_feedback_stores_action_only():
    client = _client()
    response = client.post("/api/feedback", json={
        "audit_id": "cache-pt-001",
        "item_id": "pt-001:E11.65",
        "action": "accept",
        "note": "She denies chest pain.",
    })
    assert response.status_code == 200
    stored = client.app.state.feedback[-1]
    assert stored == {"audit_id": "cache-pt-001", "item_id": "pt-001:E11.65", "action": "accept"}
    assert "note" not in stored


def test_eval_tab_reads_summary_json():
    client = _client()
    payload = client.get("/api/eval").json()
    assert payload["baseline"]["review"] == 1188
    assert payload["baseline"]["approved"] == 279
    assert payload["baseline"]["notes"] == 2227
    assert "not precision" in payload["baseline"]["warning"].lower()
    assert payload["status"] == "loaded"
    assert payload["source"].replace("\\", "/").endswith("eval/results/live/summary.json")
    assert payload["files"] == [payload["source"]]
    ids = [row["id"] for row in payload["configs"]]
    assert ids[0] == "C0"
    assert "C4" in ids
    c4 = next(row for row in payload["configs"] if row["id"] == "C4")
    assert c4["review_burden"] == c4["review_burden_per_chart"]
    assert c4["usd_per_chart"] == c4["cost_per_chart_usd"]
    assert isinstance(c4["precision"], float)
    titles = [row["title"] for row in payload["known_failure_classes"]]
    assert "Negation" in titles
    assert "Screening" in titles


def test_pages_name_the_judge_path():
    html = (WEB / "static" / "index.html").read_text(encoding="utf-8")
    script = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    assert "Synthetic data only. Do not paste PHI." in html
    for card in CARDS:
        assert f'data-audit="{card["id"]}"' in html
        assert card["name"] in html
        assert card["hook"] in html
    assert "Audit chart" in html
    assert "Dropped by Nemotron" in script
    assert "Draft physician query" in script
    assert "annual_revenue" not in script
    client = _client()
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/app.css").status_code == 200


def test_forged_forwarded_for_does_not_reset_the_ip_limit():
    app = create_app(Settings(
        audit_api_base="http://audit.test",
        max_daily_usd=5,
        estimated_live_usd=0.01,
        live_rate_per_hour=1,
    ))
    calls = {"n": 0}

    async def ok(_body):
        calls["n"] += 1
        return {"worklist": [], "mode": "live"}

    app.state.upstream.audit = ok
    client = TestClient(app)
    first = client.post(
        "/api/audit",
        json={"patient_id": "pt-009", "mode": "live"},
        headers={"X-Forwarded-For": "1.1.1.1, 203.0.113.7"},
    )
    forged = client.post(
        "/api/audit",
        json={"patient_id": "pt-009", "mode": "live"},
        headers={"X-Forwarded-For": "9.9.9.9, 203.0.113.7"},
    )
    assert first.json()["delivery"]["source"] == "upstream"
    assert forged.json()["delivery"]["fallback_reason"] == "rate_limit"
    assert calls["n"] == 1


def test_live_concurrency_cap_falls_back_to_cache():
    app = create_app(Settings(
        audit_api_base="http://audit.test",
        max_daily_usd=5,
        estimated_live_usd=0.01,
        live_rate_per_hour=100,
        live_max_concurrent=1,
    ))
    release = asyncio.Event()

    async def slow(_body):
        await release.wait()
        return {"worklist": [], "mode": "live"}

    app.state.upstream.audit = slow

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            first = asyncio.create_task(
                client.post("/api/audit", json={"patient_id": "pt-009", "mode": "live"})
            )
            await asyncio.sleep(0.05)
            second = await client.post(
                "/api/audit",
                json={"patient_id": "pt-009", "mode": "live"},
                headers={"X-Forwarded-For": "198.51.100.2"},
            )
            release.set()
            return (await first).json(), second.json()

    first, second = asyncio.run(run())
    assert first["delivery"]["source"] == "upstream"
    assert second["delivery"]["fallback_reason"] == "busy"
    assert app.state.budget.snapshot()["spent_usd"] == 0.0
