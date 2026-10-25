"""Choose a live audit or a labeled cached one.

Live calls are per request. Nothing here takes a global "someone else is
running" lock.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

from app.budget import Budget, RateLimiter
from app.charts import CACHE, get_cached, margaret_profile
from app.upstream import Upstream, UpstreamError


class AuditError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(detail)


@dataclass
class Settings:
    audit_api_base: str = ""
    audit_api_key: str = ""
    max_daily_usd: float = 3.0
    estimated_live_usd: float = 0.03
    live_rate_per_hour: int = 10
    # When no remote base is set, Audit chart POSTs to the in-process /audit route.
    local_audit: bool = True


CACHE_MESSAGE = "Cached audit, recorded for the demo. Not a live Token Factory call."
FALLBACK_MESSAGES = {
    "api_unconfigured": "Live audit is not connected. Showing the cached run.",
    "api_error": "The audit service did not respond. Showing the cached run.",
    "budget": "Live quota reached, showing cached run.",
    "rate_limit": "Live run limit reached for this network. Showing the cached run.",
}
PASTE_OFFLINE = (
    "No identifiers detected. Nothing was stored. "
    "The live audit service is not connected, so this note was not coded."
)


def upstream_message(audit: dict) -> str:
    mode = audit.get("mode")
    guideline = audit.get("guideline_mode")
    if mode == "live":
        if guideline == "live":
            return "Live audit. Token Factory and Tavily ran for this chart."
        if guideline == "cache":
            return "Live audit. Token Factory ran. Guideline citations came from cache."
        if guideline == "mixed":
            return "Live audit. Token Factory ran. Guideline citations are a mix of live and cache."
        return "Live audit. Token Factory was called for this chart."
    if mode == "degraded":
        return "Degraded audit. A model step failed and that step used the recorded client."
    if mode == "mock":
        return "The audit service ran in mock mode. No Token Factory key is set on that service."
    return "The audit service responded."


def _note_text(profile: dict | None) -> str:
    if not profile:
        return ""
    parts: list[str] = []
    for note in profile.get("clinical_notes") or []:
        if isinstance(note, dict) and isinstance(note.get("text"), str):
            parts.append(note["text"])
        elif isinstance(note, str):
            parts.append(note)
    return "\n".join(parts).strip()


def _chart(patient_id: str | None, profile: dict | None) -> dict:
    cached = CACHE.get(patient_id) if patient_id else None
    if profile:
        text = _note_text(profile)
        coded = []
        for condition in profile.get("conditions") or []:
            if isinstance(condition, dict) and condition.get("icd10_code"):
                coded.append(
                    {
                        "code": condition["icd10_code"],
                        "display": condition.get("display") or "",
                    }
                )
        if text:
            return {"note": text, "coded": coded}
    if cached:
        return copy.deepcopy(cached["chart"])
    return {"note": "", "coded": []}


def _receipt_cost(audit: dict) -> float:
    receipt = audit.get("receipt") or {}
    totals = receipt.get("totals") or {}
    try:
        return max(0.0, float(totals.get("cost_usd") or 0.0))
    except (TypeError, ValueError):
        return 0.0


class AuditService:
    def __init__(self, settings: Settings, budget: Budget, limiter: RateLimiter, upstream: Upstream) -> None:
        self.settings = settings
        self.budget = budget
        self.limiter = limiter
        self.upstream = upstream

    async def perform(
        self,
        *,
        patient_id: str | None,
        mode: str,
        profile: dict | None,
        client_ip: str,
    ) -> dict:
        if mode not in ("auto", "cached", "live"):
            raise AuditError(400, "Mode must be auto, cached, or live.")
        patient_id = (patient_id or "").strip() or None
        if patient_id == "pasted-note":
            patient_id = None
        note = _note_text(profile)
        if profile is not None and not note and patient_id is None:
            raise AuditError(400, "Paste a synthetic note first.")
        if len(note) > 20_000:
            raise AuditError(400, "That note is too long for this demo.")
        known = patient_id in CACHE if patient_id else False
        if patient_id is None and profile is None:
            raise AuditError(400, "Choose a patient or paste a synthetic note.")

        if mode == "cached" or (mode == "auto" and not self.upstream.configured):
            reason = None if mode == "cached" else "api_unconfigured"
            if known:
                return self._from_cache(patient_id, reason)
            if patient_id and not note:
                raise AuditError(404, "No cached audit for that patient.")
            if mode == "cached":
                raise AuditError(400, "Pasted notes have no cached audit. Use Re-run live once the audit service is connected.")
            return self._paste_offline()

        if not self.upstream.configured:
            if known:
                return self._from_cache(patient_id, "api_unconfigured")
            if patient_id and not note:
                raise AuditError(404, "No cached audit for that patient.")
            return self._paste_offline()

        reserved = self.settings.estimated_live_usd
        if not await self.budget.try_reserve(reserved):
            if known:
                return self._from_cache(patient_id, "budget")
            raise AuditError(429, "Live quota reached, showing cached run.")

        if not await self.limiter.allow(client_ip or "unknown"):
            await self.budget.refund(reserved)
            if known:
                return self._from_cache(patient_id, "rate_limit")
            raise AuditError(429, "Live run limit reached for this network. Try a bundled chart for the cached audit.")

        try:
            audit = await self.upstream.audit(self._body(patient_id, profile if not known else None))
        except UpstreamError:
            await self.budget.refund(reserved)
            if known:
                return self._from_cache(patient_id, "api_error")
            raise AuditError(502, "The audit service did not respond. Nothing from this note was stored.")

        await self.budget.reconcile(reserved, _receipt_cost(audit))
        return {
            "delivery": {
                "source": "upstream",
                "fallback_reason": None,
                "message": upstream_message(audit),
                "recorded_at": None,
            },
            "chart": _chart(patient_id, None if known else profile),
            "audit": audit,
        }

    def _body(self, patient_id: str | None, profile: dict | None) -> dict:
        body: dict = {"include_rules_only": True}
        if patient_id:
            body["patient_id"] = patient_id
        if patient_id == "margaret-whitmore":
            body["profile"] = margaret_profile()
        elif profile:
            body["profile"] = profile
            body.setdefault("patient_id", "pasted-note")
        return body

    def _from_cache(self, patient_id: str, reason: str | None) -> dict:
        bundle = get_cached(patient_id)
        if bundle is None:
            raise AuditError(404, "No cached audit for that patient.")
        message = CACHE_MESSAGE if reason is None else FALLBACK_MESSAGES[reason]
        return {
            "delivery": {
                "source": "cache",
                "fallback_reason": reason,
                "message": message,
                "recorded_at": bundle["recorded_at"],
            },
            "chart": bundle["chart"],
            "audit": bundle["audit"],
        }

    def _paste_offline(self) -> dict:
        return {
            "delivery": {
                "source": "not_sent",
                "fallback_reason": "api_unconfigured",
                "message": PASTE_OFFLINE,
                "recorded_at": None,
            },
            "chart": {"note": "", "coded": []},
            "audit": None,
        }
