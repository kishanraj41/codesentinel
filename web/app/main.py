"""CodeSentinel web demo.

The judge path is: open the page, choose Margaret Whitmore, click Audit chart.
That click POSTs to the real /audit route. With no AUDIT_API_BASE, the call
stays in this process and the cascade runs in mock mode. Cached mode is still
available. A remote AUDIT_API_BASE replaces the in-process route.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.budget import Budget, RateLimiter
from app.charts import CACHE, CARDS, CONTRACT
from app.eval_data import load_eval
from app.local_audit import LocalUpstream
from app.phi_guard import phi_detail, scan_phi
from app.service import AuditError, AuditService, Settings
from app.upstream import Upstream

STATIC = Path(__file__).resolve().parent.parent / "static"
MAX_BODY = 100_000


def settings_from_env() -> Settings:
    def num(name: str, default: float) -> float:
        raw = os.environ.get(name, "").strip()
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    rate = os.environ.get("LIVE_RATE_PER_HOUR", "").strip()
    try:
        per_hour = int(rate) if rate else 10
    except ValueError:
        per_hour = 10
    try:
        concurrent = int(os.environ.get("LIVE_MAX_CONCURRENT", "").strip() or 3)
    except ValueError:
        concurrent = 3
    return Settings(
        audit_api_base=os.environ.get("AUDIT_API_BASE", "").strip(),
        audit_api_key=os.environ.get("AUDIT_API_KEY", "").strip(),
        max_daily_usd=num("MAX_DAILY_USD", 3.0),
        estimated_live_usd=num("ESTIMATED_LIVE_USD", 0.03),
        live_rate_per_hour=per_hour,
        live_max_concurrent=concurrent,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or settings_from_env()
    app = FastAPI(title="CodeSentinel", version="1.0.0")
    app.state.settings = settings
    app.state.budget = Budget(settings.max_daily_usd)
    app.state.limiter = RateLimiter(max(0, settings.live_rate_per_hour))
    if settings.audit_api_base:
        upstream = Upstream(settings.audit_api_base, settings.audit_api_key)
    elif settings.local_audit:
        upstream = LocalUpstream()
    else:
        upstream = Upstream("")
    app.state.upstream = upstream
    app.state.feedback = []
    app.state.service = AuditService(
        settings, app.state.budget, app.state.limiter, app.state.upstream
    )

    @app.middleware("http")
    async def reject_phi(request: Request, call_next):
        if request.method == "POST" and request.url.path.startswith("/api/"):
            body = await request.body()
            if len(body) > MAX_BODY:
                return JSONResponse({"detail": "That note is too long for this demo."}, status_code=413)
            kinds = scan_phi(body.decode("utf-8", errors="replace"))
            if kinds:
                return JSONResponse(
                    {"detail": phi_detail(kinds), "kinds": kinds},
                    status_code=422,
                )
        return await call_next(request)

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "service": "codesentinel-web",
            "contract_version": CONTRACT,
        }

    @app.get("/api/status")
    async def status():
        return {
            "service": "codesentinel-web",
            "contract_version": CONTRACT,
            "audit_api_configured": app.state.upstream.configured,
            "budget": app.state.budget.snapshot(),
            "live_rate_per_hour": settings.live_rate_per_hour,
            "live_max_concurrent": settings.live_max_concurrent,
        }

    @app.get("/api/patients")
    async def patients():
        rows = []
        for card in CARDS:
            chart = CACHE[card["id"]]["chart"]
            rows.append({**card, "coded": chart["coded"]})
        return {"patients": rows}

    @app.get("/api/eval")
    async def eval_tab():
        return load_eval()

    @app.post("/api/audit")
    async def audit(request: Request):
        try:
            result = await _run(request)
        except AuditError as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status)
        return result

    @app.post("/api/audit/stream")
    async def audit_stream(request: Request):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"detail": "Request body must be JSON."}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"detail": "Request body must be a JSON object."}, status_code=400)
        pace = _pace(body)

        async def frames():
            try:
                if body.get("mode", "auto") in ("live", "auto") and app.state.upstream.configured:
                    yield _frame("status", {"message": "Running the live audit…"})
                result = await _run(request, body)
            except AuditError as exc:
                yield _frame("error", {"detail": exc.detail, "status": exc.status})
                return
            audit_body = result.get("audit") or {}
            for step in audit_body.get("timeline") or []:
                yield _frame("step", step)
                if pace:
                    await asyncio.sleep(pace / 1000)
            yield _frame("result", result)

        return StreamingResponse(
            frames(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/feedback")
    async def feedback(request: Request):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"detail": "Request body must be JSON."}, status_code=400)
        action = body.get("action") if isinstance(body, dict) else None
        item_id = body.get("item_id") if isinstance(body, dict) else None
        audit_id = body.get("audit_id") if isinstance(body, dict) else None
        if action not in ("accept", "dismiss", "query") or not isinstance(item_id, str):
            return JSONResponse(
                {"detail": "Send action (accept, dismiss, or query) and item_id."},
                status_code=400,
            )
        app.state.feedback.append(
            {"audit_id": audit_id if isinstance(audit_id, str) else "", "item_id": item_id, "action": action}
        )
        del app.state.feedback[:-1000]
        return {"ok": True, "action": action, "item_id": item_id}

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def _pace(body: dict) -> int:
    if "pace_ms" not in body:
        return 35
    try:
        pace = int(body.get("pace_ms") or 0)
    except (TypeError, ValueError):
        return 35
    return max(0, min(pace, 200))


def _frame(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


async def _run(request: Request, body: dict | None = None) -> dict:
    if body is None:
        try:
            body = await request.json()
        except Exception as exc:
            raise AuditError(400, "Request body must be JSON.") from exc
    if not isinstance(body, dict):
        raise AuditError(400, "Request body must be a JSON object.")
    patient_id = body.get("patient_id")
    mode = body.get("mode") or "auto"
    profile = body.get("profile")
    if patient_id is not None and not isinstance(patient_id, str):
        raise AuditError(400, "Patient id must be a string.")
    if not isinstance(mode, str):
        raise AuditError(400, "Mode must be a string.")
    if profile is not None and not isinstance(profile, dict):
        raise AuditError(400, "Profile must be an object.")
    return await request.app.state.service.perform(
        patient_id=patient_id,
        mode=mode,
        profile=profile,
        client_ip=_client_ip(request),
    )


def _client_ip(request: Request) -> str:
    """Rate-limit key for a visitor.

    The left of X-Forwarded-For is whatever the visitor sent, so it can be
    forged to dodge the per-IP limit. Each trusted proxy appends the address it
    saw, so count TRUSTED_PROXY_HOPS entries from the right (default 1: the
    host's edge proxy). 0 ignores the header.
    """
    try:
        hops = int(os.environ.get("TRUSTED_PROXY_HOPS", "1").strip() or 1)
    except ValueError:
        hops = 1
    forwarded = request.headers.get("x-forwarded-for", "")
    if hops > 0 and forwarded:
        parts = [part.strip() for part in forwarded.split(",") if part.strip()]
        if parts:
            return parts[-min(hops, len(parts))]
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


app = create_app()
