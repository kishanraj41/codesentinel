"""Client for the audit service described in docs/api.md (contract 1.0.0)."""

from __future__ import annotations

import httpx


class UpstreamError(Exception):
    pass


class Upstream:
    def __init__(self, base_url: str, api_key: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    async def audit(self, body: dict) -> dict:
        if not self.configured:
            raise UpstreamError("Audit API is not configured")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=5.0)) as client:
                response = await client.post(f"{self.base_url}/audit", json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise UpstreamError("Audit service unreachable") from exc
        if response.status_code >= 400:
            detail = _detail(response)
            raise UpstreamError(detail or f"Audit service returned {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise UpstreamError("Audit service returned an unreadable response") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("worklist"), list):
            raise UpstreamError("Audit service response is missing a worklist")
        return payload


def _detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return ""
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str):
            return detail
    return ""
