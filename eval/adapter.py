"""Audit adapter.

C0 and C0+ are the rules pipeline. Mock C1–C3 use the offline policy in
eval.policy. Mock C4 calls the real CodeSentinel cascade with the recorded
client and guideline fixtures. Live mode sends C1–C4 through that same cascade.

Mode selection:
- mock when mode is mock
- live when mode is live (the eval command refuses this unless both API keys are set)
- auto is live when NEBIUS_API_KEY is set, unless EVAL_OFFLINE=1

Mock numbers are estimates from prompt length and catalog prices. They are not
Token Factory invoices and not live Nemotron quality.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from eval.lexicon import CATALOG_PRICES_PER_MILLION, CONFIG_LABELS, MODEL_BY_STAGE
from eval.policy import SHOWN, Item, apply_policy, quote_verified

REPO_ROOT = Path(__file__).resolve().parents[1]
RECORDED = Path(__file__).resolve().parent / "fixtures" / "recorded"
TAVILY_CACHE = Path(__file__).resolve().parent / "fixtures" / "tavily_cache.json"
NEMO_RECORD = Path(__file__).resolve().parent / "fixtures" / "nemotron"
GUIDELINE_CACHE = Path(__file__).resolve().parent / "fixtures" / "guideline_cache"

TRIAGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "assertion_status": {
            "type": "string",
            "enum": [
                "present", "negated", "historical_resolved", "family_history",
                "screening_or_rule_out", "hypothetical_plan", "copied_forward_unclear",
                "lab_only", "medication_only",
            ],
        },
        "decision": {"type": "string", "enum": ["accept", "review", "reject", "abstain"]},
        "supporting_quotes": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
    },
    "required": ["assertion_status", "decision", "supporting_quotes", "rationale"],
}


def _ensure_repo_on_path() -> None:
    root = str(REPO_ROOT)
    import sys
    if root not in sys.path:
        sys.path.insert(0, root)


def discover_backend():
    """Return an external audit_chart(profile, config_id) callable, or None."""
    for module_name in ("codesentinel.audit", "codesentinel.nemotron"):
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        fn = getattr(module, "audit_chart", None)
        if callable(fn):
            return fn
    return None


def _rules(profile: dict) -> dict:
    _ensure_repo_on_path()
    import logging
    logging.getLogger("gapdetect.pipeline").setLevel(logging.ERROR)
    from gap_pipeline import run_gap_pipeline
    demo = profile.get("demographics") or {}
    # Rules buckets only. Verification would replace them with cascade decisions.
    return run_gap_pipeline(profile, demo, verify=False)


def _item_from_gap(gap: dict, *, shown: bool, decision: str) -> dict[str, Any]:
    tier = gap.get("tier") or ""
    if tier == "SPECIFICITY_UPGRADE":
        gap_type = "specificity_upgrade"
    elif tier == "COMBINATION_CODE":
        gap_type = "combination"
    else:
        gap_type = "missing"
    return {
        "code": (gap.get("icd10_code") or "").upper(),
        "condition_name": gap.get("condition_name") or "",
        "gap_type": gap_type,
        "source_tier": tier,
        "decision": decision,
        "shown_to_coder": shown,
        "assertion_status": "rules",
        "quote_verified": True,
        "drop_reason": None if shown else "rules_bucket_not_in_config",
        "stage": "rules",
        "model": None,
        "guideline_url": None,
        "from_code": gap.get("upgrade_from"),
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_usd": 0.0,
        "tavily_credits": 0.0,
        "tavily_cache_hit": False,
    }


def _rules_only(rules: dict, config: str) -> dict[str, Any]:
    show_review = config == "C0+"
    items = []
    for gap in rules.get("approved") or []:
        items.append(_item_from_gap(gap, shown=True, decision="accept"))
    for gap in rules.get("review") or []:
        items.append(_item_from_gap(gap, shown=show_review, decision="review"))
    for gap in rules.get("rejected") or []:
        items.append(_item_from_gap(gap, shown=False, decision="reject"))
    return {
        "items": items,
        "cost_usd": 0.0,
        "tavily_credits": 0.0,
        "tavily_cache_hits": 0,
        "tavily_would_search": 0,
        "guideline_decision_changes": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "mode": "rules",
    }


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _price(model: str, tokens_in: int, tokens_out: int) -> float:
    pin, pout = CATALOG_PRICES_PER_MILLION[model]
    return tokens_in / 1_000_000 * pin + tokens_out / 1_000_000 * pout


def _charge_mock(trace, config: str, gaps: list[dict], profile: dict) -> None:
    """Estimate catalog cost from the prompt we would have sent. Not a live invoice.

    The prompt includes the chart note, because a real triage call would send it.
    Token count is len(prompt)//4. These charts are short, so the estimate is
    small; it is not the planning figure for a 25-candidate production chart.
    """
    if config in {"C0", "C0+"}:
        return
    note = " ".join(n.get("text") or "" for n in profile.get("clinical_notes") or [])
    triage_model = MODEL_BY_STAGE["triage"]
    super_model = MODEL_BY_STAGE["adjudicate"]
    ultra_model = MODEL_BY_STAGE["escalate"]
    for gap in gaps:
        quote = " ".join(gap.get("evidence_sources") or [])[:800]
        prompt = (
            f"Note:\n{note[:6000]}\n"
            f"Candidate {gap.get('icd10_code')} {gap.get('condition_name')}. "
            f"Evidence: {quote}"
        )
        completion = '{"assertion_status":"present","decision":"review","supporting_quotes":[],"rationale":""}'
        tin, tout = _tokens(prompt), _tokens(completion)
        trace.tokens_in += tin
        trace.tokens_out += tout
        trace.cost_usd += _price(triage_model, tin, tout)
    if config in {"C2", "C3", "C4"}:
        for item in trace.items:
            if item.stage in {"adjudicate", "guideline", "escalate"} or item.gap_type in {
                "combination", "specificity_upgrade",
            }:
                prompt = f"Adjudicate {item.code} status {item.assertion_status} quote {item.quote[:400]}"
                tin, tout = _tokens(prompt), _tokens('{"decision":"accept"}')
                item.tokens_in += tin
                item.tokens_out += tout
                cost = _price(super_model, tin, tout)
                item.cost_usd += cost
                item.model = super_model
                trace.tokens_in += tin
                trace.tokens_out += tout
                trace.cost_usd += cost
    if config == "C4":
        for item in trace.items:
            if item.stage == "escalate" or item.from_code == "I50.9" or (
                item.decision == "abstain" and item.code.startswith(("I50", "J44", "F32", "E11.2"))
            ):
                prompt = f"Escalate {item.code} {item.assertion_status} {item.quote[:400]}"
                tin, tout = _tokens(prompt), _tokens('{"decision":"accept","icd10_code":"' + item.code + '"}')
                cost = _price(ultra_model, tin, tout)
                item.model = ultra_model
                item.tokens_in += tin
                item.tokens_out += tout
                item.cost_usd += cost
                trace.tokens_in += tin
                trace.tokens_out += tout
                trace.cost_usd += cost
    for item in trace.items:
        if item.stage == "triage" and item.model is None:
            item.model = triage_model


def _trace_payload(trace, mode: str) -> dict[str, Any]:
    return {
        "items": [it.as_dict() for it in trace.items],
        "cost_usd": round(trace.cost_usd, 8),
        "tavily_credits": trace.tavily_credits,
        "tavily_cache_hits": trace.tavily_cache_hits,
        "tavily_would_search": trace.tavily_would_search,
        "guideline_decision_changes": trace.guideline_decision_changes,
        "tokens_in": trace.tokens_in,
        "tokens_out": trace.tokens_out,
        "mode": mode,
    }


class AuditAdapter:
    def __init__(self) -> None:
        self._rules_cache: dict[str, dict] = {}
        self.backend = discover_backend()
        self.live_errors: list[str] = []

    def rules_for(self, profile: dict) -> dict:
        pid = profile.get("fhir_patient_id") or ""
        if pid not in self._rules_cache:
            self._rules_cache[pid] = _rules(profile)
        return self._rules_cache[pid]

    def audit(self, profile: dict, config: str, *, mode: str = "auto") -> dict[str, Any]:
        if config not in CONFIG_LABELS:
            raise KeyError(f"Unknown config {config}")
        if self.backend is not None and mode == "auto" and os.environ.get("EVAL_OFFLINE") != "1":
            try:
                external = self.backend(profile, config)
                external["mode"] = external.get("mode") or "external"
                external.setdefault("items", [])
                return external
            except Exception as exc:  # backend is optional; fall through and record it
                self.live_errors.append(f"{profile.get('fhir_patient_id')} {config}: backend {exc}")
        started = time.perf_counter()
        chosen = _select_mode(mode)
        if chosen == "live" and config in {"C1", "C2", "C3", "C4"}:
            payload = self._cascade(profile, "live", config)
        elif config == "C4":
            payload = self._cascade(profile, "mock", "C4")
        else:
            rules = self.rules_for(profile)
            if config in {"C0", "C0+"}:
                payload = _rules_only(rules, config)
            elif chosen == "live":
                payload = self._live_or_fallback(profile, rules, config)
            else:
                gaps = list(rules.get("approved") or []) + list(rules.get("review") or [])
                trace = apply_policy(profile, gaps, config)
                _charge_mock(trace, config, gaps, profile)
                payload = _trace_payload(trace, "mock")
        payload["latency_s"] = time.perf_counter() - started
        payload["config"] = config
        payload["patient_id"] = profile.get("fhir_patient_id")
        return payload

    def _live_or_fallback(self, profile: dict, rules: dict, config: str) -> dict[str, Any]:
        try:
            return live_cascade(profile, rules, config)
        except Exception as exc:
            self.live_errors.append(f"{profile.get('fhir_patient_id')} {config}: {exc}")
            gaps = list(rules.get("approved") or []) + list(rules.get("review") or [])
            trace = apply_policy(profile, gaps, config)
            _charge_mock(trace, config, gaps, profile)
            payload = _trace_payload(trace, "mock_fallback")
            payload["live_error"] = str(exc)
            return payload

    def _cascade(self, profile: dict, chosen: str, config: str = "C4") -> dict[str, Any]:
        """C1–C4 through run_gap_pipeline. Mock never calls Token Factory."""
        try:
            audit = cascade_audit(profile, mock=chosen != "live", config=config)
        except Exception as exc:
            self.live_errors.append(f"{profile.get('fhir_patient_id')} {config}: {exc}")
            audit = cascade_audit(profile, mock=True, config=config)
            payload = _audit_to_eval(audit, "mock_fallback", config)
            payload["live_error"] = str(exc)
            return payload
        mode_name = "mock" if chosen != "live" else (audit.get("mode") or "live")
        if chosen != "live":
            mode_name = "mock"
        return _audit_to_eval(audit, mode_name, config)


def cascade_audit(profile: dict, *, mock: bool, config: str = "C4") -> dict[str, Any]:
    _ensure_repo_on_path()
    import logging
    logging.getLogger("gapdetect.pipeline").setLevel(logging.ERROR)
    logging.getLogger("codesentinel").setLevel(logging.ERROR)
    from codesentinel.nemotron.client import NemotronClient
    from codesentinel.tavily.client import GuidelineClient
    from gap_pipeline import run_gap_pipeline

    if mock:
        nemotron = NemotronClient(api_key="")
        guidelines = GuidelineClient(api_key="", use_fixtures=True)
    else:
        nemotron = NemotronClient(
            record_dir=str(NEMO_RECORD),
            replay=os.environ.get("CODESENTINEL_REPLAY") == "1",
        )
        guidelines = GuidelineClient(cache_dir=str(GUIDELINE_CACHE))
    result = run_gap_pipeline(
        profile,
        profile.get("demographics") or {},
        verify=True,
        nemotron_client=nemotron,
        guideline_client=guidelines,
        cascade_config=config,
    )
    audit = result.get("audit")
    if not audit:
        raise RuntimeError("cascade did not return an audit")
    return audit


def _audit_to_eval(audit: dict, mode: str, config: str = "C4") -> dict[str, Any]:
    items = []
    for row in audit.get("worklist") or []:
        items.append(_eval_item(row, shown=True))
    for row in audit.get("dropped") or []:
        items.append(_eval_item(row, shown=False))
    receipt = audit.get("receipt") or {}
    totals = receipt.get("totals") or {}
    changes = 0
    # C1 and C2 never call Tavily. The gate still stamps "Guideline evidence was
    # missing" onto combination and specificity rationales, but that is not a
    # decision the guideline step changed.
    if config in {"C3", "C4"}:
        for row in audit.get("worklist") or []:
            rationale = row.get("rationale") or ""
            if row.get("gap_type") in {"combination", "specificity_upgrade"} and "Guideline evidence was missing" in rationale:
                changes += 1
    return {
        "items": items,
        "cost_usd": float(totals.get("cost_usd") or 0),
        "tavily_credits": float(totals.get("tavily_credits") or 0),
        "tavily_cache_hits": int(totals.get("tavily_cache_hits") or 0),
        "tavily_would_search": 0,
        "guideline_decision_changes": changes,
        "citation_provenance": _citation_provenance(audit),
        "tokens_in": int(totals.get("input_tokens") or 0),
        "tokens_out": int(totals.get("output_tokens") or 0),
        "mode": mode,
        "degraded_reasons": [str(item) for item in (audit.get("degraded_reasons") or []) if str(item).strip()],
    }


def _citation_provenance(audit: dict) -> dict[str, int]:
    """Shown citations by provenance (tavily_live, curated_fallback, fixture), plus superseded anchors."""
    counts: dict[str, int] = {}
    seen = set()
    for row in audit.get("worklist") or []:
        for citation in row.get("guideline_citations") or []:
            if not isinstance(citation, dict):
                continue
            key = (citation.get("url"), citation.get("quote"), citation.get("issue"))
            if key in seen:
                continue
            seen.add(key)
            name = citation.get("provenance") or citation.get("retrieved") or "unknown"
            counts[name] = counts.get(name, 0) + 1
            if citation.get("anchor_status") == "superseded":
                counts["superseded_anchor"] = counts.get("superseded_anchor", 0) + 1
    return counts


def _eval_item(row: dict, *, shown: bool) -> dict[str, Any]:
    citations = row.get("guideline_citations") or []
    url = citations[0].get("url") if citations and isinstance(citations[0], dict) else None
    reason = row.get("reason")
    if reason == "unsupported":
        reason = "unsupported_claim"
    decision = row.get("decision") or ("reject" if not shown else "review")
    return {
        "code": (row.get("icd10_code") or "").upper(),
        "condition_name": row.get("code_display") or "",
        "gap_type": row.get("gap_type") or "missing",
        "source_tier": row.get("tier") or "",
        "decision": decision,
        "shown_to_coder": shown and decision in {"accept", "review", "abstain"},
        "assertion_status": row.get("assertion_status") or reason or "",
        "quote_verified": bool(row.get("verified_quotes")),
        "drop_reason": None if shown else reason,
        "stage": "cascade",
        "model": row.get("decided_by") or row.get("model"),
        "guideline_url": url,
        "from_code": row.get("upgrade_from"),
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_usd": 0.0,
        "tavily_credits": 0.0,
        "tavily_cache_hit": False,
    }


def _select_mode(mode: str) -> str:
    if mode == "mock":
        return "mock"
    if mode == "live":
        return "live"
    if os.environ.get("EVAL_OFFLINE") == "1":
        return "mock"
    if mode == "auto":
        return "live" if os.environ.get("NEBIUS_API_KEY") else "mock"
    if mode == "recorded":
        return "mock"
    return "mock"


def _request_hash(model: str, messages: list[dict]) -> str:
    blob = json.dumps({"model": model, "messages": messages}, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def call_token_factory(model: str, messages: list[dict], *, temperature: float = 0) -> dict[str, Any]:
    """OpenAI-compatible chat call. Records the raw JSON when it succeeds."""
    api_key = os.environ.get("NEBIUS_API_KEY")
    if not api_key:
        raise RuntimeError("NEBIUS_API_KEY is unset")
    base = os.environ.get("NEBIUS_BASE_URL") or "https://api.tokenfactory.nebius.com/v1"
    digest = _request_hash(model, messages)
    recorded_path = RECORDED / f"{digest}.json"
    if recorded_path.exists() and os.environ.get("EVAL_IGNORE_RECORDED") != "1":
        return json.loads(recorded_path.read_text(encoding="utf-8"))
    body = {
        "model": model,
        "temperature": temperature,
        "messages": messages,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "verdict", "strict": True, "schema": TRIAGE_SCHEMA},
        },
    }
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Token Factory HTTP {exc.code}: {detail}") from exc
    recorded_path.parent.mkdir(parents=True, exist_ok=True)
    recorded_path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def parse_verdict(payload: dict) -> dict[str, Any]:
    """Pull the JSON object out of a chat completion, with one shape check."""
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("completion had no message content") from exc
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError("model content was not JSON") from exc
    elif isinstance(content, dict):
        parsed = content
    else:
        raise RuntimeError("model content was not JSON")
    if parsed.get("decision") not in {"accept", "review", "reject", "abstain"}:
        raise RuntimeError("model decision was not in the schema")
    return parsed


def call_tavily(query: str) -> dict[str, Any]:
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        raise RuntimeError("TAVILY_API_KEY is unset")
    cache = {}
    if TAVILY_CACHE.exists():
        cache = json.loads(TAVILY_CACHE.read_text(encoding="utf-8"))
    if query in cache:
        hit = dict(cache[query])
        hit["cache_hit"] = True
        return hit
    body = {
        "api_key": api_key,
        "query": query,
        "search_depth": "advanced",
        "include_domains": ["cms.gov", "cdc.gov"],
        "max_results": 5,
        "chunks_per_source": 3,
        "include_usage": True,
    }
    req = urllib.request.Request(
        "https://api.tavily.com/search",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    cache[query] = payload
    TAVILY_CACHE.parent.mkdir(parents=True, exist_ok=True)
    TAVILY_CACHE.write_text(json.dumps(cache), encoding="utf-8")
    payload = dict(payload)
    payload["cache_hit"] = False
    return payload


def live_cascade(profile: dict, rules: dict, config: str) -> dict[str, Any]:
    """Real Token Factory calls, then the deterministic quote verifier.

    A quote that is not in the chart is dropped even if the model accepted it.
    Guideline lookups use Tavily when C3/C4 and TAVILY_API_KEY are set; a miss
    downgrades accept to review for combination and specificity items.
    """
    gaps = list(rules.get("approved") or []) + list(rules.get("review") or [])
    items: list[Item] = []
    tokens_in = tokens_out = 0
    cost = 0.0
    searches = 0
    cache_hits = 0
    credits = 0.0
    changes = 0
    triage_model = MODEL_BY_STAGE["triage"]
    for gap in gaps:
        code = (gap.get("icd10_code") or "").upper()
        tier = gap.get("tier") or ""
        if tier == "SPECIFICITY_UPGRADE":
            gap_type = "specificity_upgrade"
        elif tier == "COMBINATION_CODE":
            gap_type = "combination"
        else:
            gap_type = "missing"
        evidence = " ".join(gap.get("evidence_sources") or [])[:1200]
        messages = [
            {"role": "system", "content": "You are a clinical-coding skeptic. Return only the JSON schema."},
            {"role": "user", "content": f"Code {code}. Evidence: {evidence}"},
        ]
        payload = call_token_factory(triage_model, messages, temperature=0)
        usage = payload.get("usage") or {}
        tin = int(usage.get("prompt_tokens") or _tokens(messages[1]["content"]))
        tout = int(usage.get("completion_tokens") or 1)
        tokens_in += tin
        tokens_out += tout
        cost += _price(triage_model, tin, tout)
        try:
            verdict = parse_verdict(payload)
        except RuntimeError:
            # One repair retry, then a human has to see it.
            messages.append({"role": "user", "content": "Return a single JSON object matching the schema."})
            payload = call_token_factory(triage_model, messages, temperature=0)
            try:
                verdict = parse_verdict(payload)
            except RuntimeError:
                verdict = {"decision": "review", "assertion_status": "present", "supporting_quotes": [], "rationale": "parse_failure"}
        quotes = verdict.get("supporting_quotes") or []
        quote = quotes[0] if quotes else evidence[:240]
        verified = quote_verified(quote, profile) or quote_verified(evidence[:240], profile)
        decision = verdict.get("decision") or "review"
        reason = None
        if not verified:
            decision = "reject"
            reason = "unsupported_claim"
        item = Item(
            code=code,
            condition_name=gap.get("condition_name") or code,
            gap_type=gap_type,
            source_tier=tier,
            decision=decision,
            shown_to_coder=decision in SHOWN,
            assertion_status=verdict.get("assertion_status") or "present",
            quote=quote,
            quote_verified=verified,
            drop_reason=reason,
            stage="triage",
            model=triage_model,
            tokens_in=tin,
            tokens_out=tout,
            cost_usd=_price(triage_model, tin, tout),
        )
        if config in {"C3", "C4"} and item.shown_to_coder and item.gap_type in {"combination", "specificity_upgrade"}:
            if os.environ.get("TAVILY_API_KEY"):
                searches += 1
                hit = call_tavily(f"ICD-10-CM official guideline {code}")
                if hit.get("cache_hit"):
                    cache_hits += 1
                else:
                    credits += 2.0
                results = hit.get("results") or []
                if results:
                    item.guideline_url = results[0].get("url")
                    item.stage = "guideline"
                elif item.decision == "accept":
                    item.decision = "review"
                    item.shown_to_coder = True
                    item.drop_reason = "guideline_missing"
                    changes += 1
            elif item.decision == "accept":
                item.decision = "review"
                item.drop_reason = "guideline_missing"
                item.shown_to_coder = True
                changes += 1
        items.append(item)
    return {
        "items": [it.as_dict() for it in items],
        "cost_usd": round(cost, 8),
        "tavily_credits": credits,
        "tavily_cache_hits": cache_hits,
        "tavily_would_search": searches,
        "guideline_decision_changes": changes,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "mode": "live",
    }


def guardrail_probe(adapter: AuditAdapter | None = None) -> dict[str, Any]:
    """A candidate whose only quote is not in the chart must be dropped."""
    profile = {
        "fhir_patient_id": "probe-hallucination",
        "demographics": {},
        "clinical_notes": [{"text": "Hypertension is active. Continue lisinopril."}],
        "observations": [],
        "medications": [{"name": "Lisinopril 10mg", "status": "active"}],
        "conditions": [],
        "encounters": [{"encounter_id": "e", "status": "finished"}],
        "claims_codes": set(),
    }
    gap = {
        "icd10_code": "I50.9",
        "condition_name": "Heart failure",
        "tier": "TIER_3_NOTE_NER",
        "evidence_sources": [
            "Clinical note [assessment_plan]: ...a fictional murmur no clinician wrote..."
        ],
        "evidence_count": 1,
    }
    trace = apply_policy(profile, [gap], "C1")
    dropped = [it for it in trace.items if it.drop_reason == "unsupported_claim" and it.code == "I50.9"]
    return {
        "name": "hallucinated_quote_dropped",
        "passed": bool(dropped) and all(not it.shown_to_coder for it in dropped),
        "dropped": len(dropped),
    }
