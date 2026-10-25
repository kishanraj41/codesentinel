"""POST /audit against the in-process CodeSentinel service.

This is a real HTTP call through the ASGI stack, to the same route mcp_server
exposes. It is how the demo reaches the cascade when AUDIT_API_BASE is unset.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

from app.upstream import UpstreamError

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class LocalUpstream:
    def __init__(self) -> None:
        from mcp_server import app as audit_app

        self._app = audit_app
        self.base_url = "http://codesentinel.local"
        self.api_key = ""

    @property
    def configured(self) -> bool:
        return True

    async def audit(self, body: dict) -> dict:
        transport = httpx.ASGITransport(app=self._app)
        try:
            async with httpx.AsyncClient(transport=transport, base_url=self.base_url) as client:
                response = await client.post("/audit", json=body)
        except httpx.HTTPError as exc:
            raise UpstreamError("Audit service unreachable") from exc
        if response.status_code >= 400:
            detail = ""
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if isinstance(payload, dict) and isinstance(payload.get("detail"), str):
                detail = payload["detail"]
            raise UpstreamError(detail or f"Audit service returned {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise UpstreamError("Audit service returned an unreadable response") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("worklist"), list):
            raise UpstreamError("Audit service response is missing a worklist")
        return payload
