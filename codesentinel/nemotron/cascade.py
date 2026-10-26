"""Rules candidates → triage → verifier → guidelines → Super → Ultra."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from codesentinel.nemotron.catalog import CATALOG_PRICE_TABLE, SUPER, ULTRA, cost_usd
from codesentinel.nemotron.client import Completion, NemotronClient
from codesentinel.nemotron.mock_reasoner import (
    TRIAGE_INSTRUCTIONS,
    apply_guideline_gate,
    kidney_disease_is_established,
    reconcile_triage,
    gap_type_of,
    guideline_issue,
    keywords_for,
    mention_windows,
    needs_escalation,
    needs_super,
    safe_physician_query,
)
from codesentinel.nemotron.schemas import (
    ADJUDICATION_SCHEMA,
    PHYSICIAN_QUERY_SCHEMA,
    TRIAGE_SCHEMA,
    AdjudicationResult,
    PhysicianQueryResult,
    TriageResult,
    limit_words,
)
from codesentinel.nemotron.verifier import build_corpus, verified_quotes
from codesentinel.tavily.client import GuidelineClient

_TIMELINE = (
    ("rules", "Rules", None),
    ("triage", "Lightning triage", "triage"),
    ("verifier", "Quote verifier", None),
    ("guidelines", "Tavily guidelines", None),
    ("adjudication", "Super adjudication", "adjudication"),
    ("escalation", "Ultra escalation", "escalation"),
    ("physician_query", "Physician query", "physician_query"),
)

_DROP_DETAIL = {
    "negated": "Negated in the chart.",
    "historical_resolved": "Mention is historical or resolved.",
    "family_history": "Mention is family history, not the patient's condition.",
    "screening_or_rule_out": "Mention is screening or a rule-out, not a diagnosed condition.",
    "hypothetical_plan": "Mention is a hypothetical or possible diagnosis.",
    "copied_forward_unclear": "No current chart evidence for this candidate.",
    "unsupported": "No supporting quote was found verbatim in the chart.",
    "rejected": "The adjudicator rejected this candidate.",
}

# These statuses stay on the worklist. Medication-list and lab-only charts are
# review evidence, not negations, even when Lightning tagged them as dropped.
_KEEP_ASSERTIONS = {"present", "medication_supported", "lab_only"}


def run_cascade(
    profile: dict,
    rules: dict,
    *,
    nemotron_client: Optional[NemotronClient] = None,
    guideline_client: Optional[GuidelineClient] = None,
    config: str = "C4",
) -> dict:
    """config selects how far the same cascade runs.

    C1 stops after triage and the quote verifier. C2 adds Super. C3 adds
    guideline lookup. C4 adds Ultra. C0 is not handled here.
    """
    depth = config if config in {"C1", "C2", "C3", "C4"} else "C4"
    use_super = depth in {"C2", "C3", "C4"}
    use_guidelines = depth in {"C3", "C4"}
    use_ultra = depth == "C4"
    client = nemotron_client or NemotronClient()
    guidelines = guideline_client or GuidelineClient()
    client.ensure_models()
    triage_model = client.select_triage_model()
    corpus = build_corpus(profile)
    candidates = list(rules.get("approved") or []) + list(rules.get("review") or [])
    notes = corpus.notes_text

    timeline_latency = {step: 0 for step, _label, _key in _TIMELINE}
    timeline_status = {step: "skipped" for step, _label, _key in _TIMELINE}
    timeline_detail = {step: "" for step, _label, _key in _TIMELINE}
    timeline_model = {
        "rules": None,
        "triage": triage_model,
        "verifier": None,
        "guidelines": None,
        "adjudication": SUPER,
        "escalation": ULTRA,
        "physician_query": ULTRA,
    }
    timeline_mock = {
        "rules": False,
        "triage": client.mock,
        "verifier": False,
        "guidelines": bool(getattr(guidelines, "use_fixtures", False)),
        "adjudication": client.mock,
        "escalation": client.mock,
        "physician_query": client.mock,
    }

    started = time.perf_counter()
    timeline_status["rules"] = "ok"
    timeline_detail["rules"] = (
        f"{len(candidates)} candidates sent to Nemotron; "
        f"rules approved {len(rules.get('approved') or [])}, "
        f"review {len(rules.get('review') or [])}, "
        f"rejected {len(rules.get('rejected') or [])}"
    )
    timeline_latency["rules"] = int((time.perf_counter() - started) * 1000)

    triage_calls: list[Completion] = []
    started = time.perf_counter()
    if candidates:
        workers = min(8, len(candidates))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(_triage_one, client, triage_model, gap, profile, notes, corpus)
                for gap in candidates
            ]
            triage_calls = [future.result() for future in futures]
        timeline_status["triage"] = "ok"
        timeline_detail["triage"] = f"{len(triage_calls)} calls on {triage_model}"
    else:
        timeline_status["triage"] = "ok"
        timeline_detail["triage"] = "no candidates"
    timeline_latency["triage"] = int((time.perf_counter() - started) * 1000)

    started = time.perf_counter()
    survivors: list[dict] = []
    dropped: list[dict] = []
    unsupported = 0
    asserted = 0
    for gap, completion in zip(candidates, triage_calls):
        triage = completion.data if isinstance(completion.data, TriageResult) else None
        if triage is None:
            triage = TriageResult(
                assertion_status="present",
                supporting_quotes=[str(item) for item in (gap.get("evidence_sources") or [])],
                section="unknown",
                confidence=0.5,
            )
        triage = reconcile_triage(
            triage,
            code=str(gap.get("icd10_code") or ""),
            condition=str(gap.get("condition_name") or ""),
            notes=notes,
            profile=profile,
            gap=gap,
        )
        if triage.assertion_status not in _KEEP_ASSERTIONS:
            asserted += 1
            dropped.append(
                _dropped(
                    gap,
                    reason=triage.assertion_status,
                    quotes=triage.supporting_quotes,
                    dropped_by="triage",
                    model=completion.model,
                    profile=profile,
                )
            )
            continue
        grounded = verified_quotes(triage.supporting_quotes, corpus)
        if not grounded:
            unsupported += 1
            dropped.append(
                _dropped(
                    gap,
                    reason="unsupported",
                    quotes=triage.supporting_quotes,
                    dropped_by="verifier",
                    model=None,
                    profile=profile,
                )
            )
            continue
        survivors.append({"gap": gap, "triage": triage, "quotes": grounded, "triage_model": completion.model})
    timeline_status["verifier"] = "ok"
    timeline_detail["verifier"] = (
        f"kept {len(survivors)}; dropped {unsupported} unsupported; "
        f"dropped {asserted} on assertion status"
    )
    timeline_latency["verifier"] = int((time.perf_counter() - started) * 1000)

    started = time.perf_counter()
    credits_used = 0.0
    cache_hits = 0
    if not use_guidelines:
        for row in survivors:
            row["citations"] = []
        timeline_status["guidelines"] = "skipped"
        timeline_detail["guidelines"] = "this config stops before guideline lookup"
    else:
        groups: dict[tuple[str, str], list[dict]] = {}
        for row in survivors:
            issue = guideline_issue(row["gap"])
            if not issue:
                row["citations"] = []
                continue
            family = str(row["gap"].get("icd10_code") or "")[:3]
            groups.setdefault((family, issue), []).append(row)
        credits_before = float(getattr(guidelines, "credits", 0) or 0)
        hits_before = int(getattr(guidelines, "cache_hits", 0) or 0)
        for (family, issue), rows in groups.items():
            found = _citation_dicts(guidelines.lookup(family, issue))
            for row in rows:
                row["citations"] = found
        for row in survivors:
            row.setdefault("citations", [])
        credits_used = float(getattr(guidelines, "credits", 0) or 0) - credits_before
        cache_hits = int(getattr(guidelines, "cache_hits", 0) or 0) - hits_before
        timeline_status["guidelines"] = "ok"
        timeline_detail["guidelines"] = (
            f"{len(groups)} lookups; cache hits {cache_hits}; credits {credits_used:g}"
        )
        timeline_latency["guidelines"] = int((time.perf_counter() - started) * 1000)
        if getattr(guidelines, "use_fixtures", False) and getattr(guidelines, "live_requests", 0) == 0:
            timeline_mock["guidelines"] = True

    adjudication_calls: list[Completion] = []
    escalation_calls: list[Completion] = []
    query_calls: list[Completion] = []
    worklist: list[dict] = []
    legacy_approved: list[dict] = []
    legacy_review: list[dict] = []
    legacy_rejected: list[dict] = []

    adjudicated = 0
    for row in survivors:
        gap = row["gap"]
        triage: TriageResult = row["triage"]
        gap_type = gap_type_of(gap)
        citations: list = row["citations"]
        citation_dicts = list(citations)
        decided_by = row["triage_model"]
        super_called = False
        super_decision = None
        windows = mention_windows(
            notes, keywords_for(str(gap.get("icd10_code") or ""), str(gap.get("condition_name") or ""))
        )
        if use_super and needs_super(triage.confidence, gap_type):
            completion = _adjudicate(client, gap, triage, row["quotes"], citation_dicts, windows)
            adjudication_calls.append(completion)
            adjudicated += 1
            super_called = True
            if isinstance(completion.data, AdjudicationResult):
                decision = completion.data.decision
                rationale = completion.data.rationale
                query_needed = completion.data.physician_query_needed
            else:
                decision = "review"
                rationale = "Model output failed schema validation. Held for human review."
                query_needed = True
            super_decision = decision
            decided_by = SUPER
        else:
            decision = "accept"
            rationale = (
                "Chart quote verified and triage confidence is high. "
                "No combination or specificity rule required a guideline lookup."
            )
            query_needed = False
            if needs_super(triage.confidence, gap_type):
                decision = "review"
                rationale = "Triage held this candidate. This config stops before Super adjudication."
                query_needed = gap_type in {"specificity_upgrade", "combination"}

        final_by = decided_by
        escalated = False
        if use_ultra and needs_escalation(
            assertion_status=triage.assertion_status,
            super_decision=super_decision,
            gap_type=gap_type,
            raf=float(gap.get("raf_value") or 0),
            super_called=super_called,
        ):
            completion = _escalate(
                client, gap, triage, row["quotes"], citation_dicts, decision, rationale, windows
            )
            escalation_calls.append(completion)
            escalated = True
            if isinstance(completion.data, AdjudicationResult):
                decision = completion.data.decision
                rationale = completion.data.rationale
                query_needed = completion.data.physician_query_needed
            else:
                decision = "abstain"
                rationale = "Ultra output failed schema validation. Abstaining to a human reviewer."
                query_needed = True
            final_by = ULTRA

        decision, rationale = apply_guideline_gate(decision, gap_type, citation_dicts, rationale)
        if triage.assertion_status == "medication_supported" and decision in {"accept", "reject"}:
            decision = "abstain"
            rationale = (
                "An active medication list with a current refill supports human review, not an automatic add."
            )
            query_needed = True
        elif triage.assertion_status == "lab_only" and decision in {"accept", "reject"}:
            decision = "review"
            rationale = "The chart has lab evidence only. A coder should confirm MEAT before this is billed."
            query_needed = True
        if decision == "review" and gap_type in {"specificity_upgrade", "combination"} and not citation_dicts:
            query_needed = True
        if decision == "accept":
            query_needed = False

        if decision == "reject":
            dropped.append(
                _dropped(
                    gap,
                    reason="rejected",
                    quotes=row["quotes"],
                    dropped_by="escalation" if escalated else "adjudicator",
                    model=final_by,
                    profile=profile,
                    detail=rationale,
                )
            )
            legacy_rejected.append(_legacy(gap, "reject"))
            continue

        physician_query = None
        if query_needed and use_ultra:
            completion = _draft_query(client, gap, row["quotes"])
            query_calls.append(completion)
            drafted = ""
            if isinstance(completion.data, PhysicianQueryResult) and not completion.data.leading:
                drafted = completion.data.query
            physician_query = safe_physician_query(
                drafted, str(gap.get("icd10_code") or ""), str(gap.get("condition_name") or ""), row["quotes"]
            )
        elif query_needed:
            physician_query = safe_physician_query(
                "", str(gap.get("icd10_code") or ""), str(gap.get("condition_name") or ""), row["quotes"]
            )

        item = _work_item(
            gap,
            profile,
            triage,
            row["quotes"],
            decision,
            limit_words(rationale, 60),
            query_needed,
            physician_query,
            final_by,
            citation_dicts,
        )
        worklist.append(item)
        legacy = _legacy(gap, decision)
        if decision == "accept":
            legacy_approved.append(legacy)
        else:
            legacy_review.append(legacy)

    if not use_super:
        timeline_status["adjudication"] = "skipped"
        timeline_detail["adjudication"] = "this config stops before Super adjudication"
    elif adjudicated:
        timeline_status["adjudication"] = "ok"
        timeline_detail["adjudication"] = f"{adjudicated} calls on {SUPER}"
    else:
        timeline_status["adjudication"] = "skipped"
        timeline_detail["adjudication"] = "no medium-confidence, specificity, or combination rows"
    timeline_latency["adjudication"] = sum(call.latency_ms for call in adjudication_calls)

    if not use_ultra:
        timeline_status["escalation"] = "skipped"
        timeline_detail["escalation"] = "this config stops before Ultra escalation"
    elif escalation_calls:
        timeline_status["escalation"] = "ok"
        timeline_detail["escalation"] = f"{len(escalation_calls)} calls on {ULTRA}"
        timeline_latency["escalation"] = sum(call.latency_ms for call in escalation_calls)
    else:
        timeline_status["escalation"] = "skipped"
        timeline_detail["escalation"] = "no conflicts or high-value upgrades"
    if query_calls:
        timeline_status["physician_query"] = "ok"
        timeline_detail["physician_query"] = f"{len(query_calls)} non-leading drafts on {ULTRA}"
        timeline_latency["physician_query"] = sum(call.latency_ms for call in query_calls)
    else:
        timeline_status["physician_query"] = "skipped"
        timeline_detail["physician_query"] = "no query requested"

    worklist, ckd_dropped = _drop_unestablished_n18(worklist, notes, profile.get("observations") or [])
    dropped.extend(ckd_dropped)
    ckd_codes = {item["icd10_code"] for item in ckd_dropped}
    if ckd_codes:
        legacy_approved = [gap for gap in legacy_approved if gap.get("icd10_code") not in ckd_codes]
        legacy_review = [gap for gap in legacy_review if gap.get("icd10_code") not in ckd_codes]
        for item in ckd_dropped:
            legacy_rejected.append({"icd10_code": item["icd10_code"], "condition_name": item["code_display"], "decision": "reject"})

    worklist, sibling_dropped = _suppress_unspecified_siblings(worklist)
    dropped.extend(sibling_dropped)
    dropped_codes = {item["icd10_code"] for item in sibling_dropped}
    if dropped_codes:
        legacy_approved = [gap for gap in legacy_approved if gap.get("icd10_code") not in dropped_codes]
        legacy_review = [gap for gap in legacy_review if gap.get("icd10_code") not in dropped_codes]
        for item in sibling_dropped:
            legacy_rejected.append({"icd10_code": item["icd10_code"], "condition_name": item["code_display"], "decision": "reject"})
    for row in _unsupported_existing_rows(profile):
        worklist.append(row)
        legacy_review.append({
            "icd10_code": row["icd10_code"],
            "condition_name": row["code_display"],
            "tier": "TWO_WAY",
            "decision": "review",
        })

    order = {"accept": 0, "review": 1, "abstain": 2}
    worklist.sort(key=lambda row: (order.get(row["decision"], 9), -row["confidence"], row["icd10_code"]))

    citations = _unique_citations(worklist)
    timeline = []
    for step, label, _key in _TIMELINE:
        timeline.append(
            {
                "step": step,
                "label": label,
                "model": timeline_model[step],
                "status": timeline_status[step],
                "latency_ms": timeline_latency[step],
                "detail": timeline_detail[step],
                "mock": timeline_mock[step] if timeline_status[step] != "skipped" else False,
            }
        )
    receipt = _receipt(
        triage_model,
        triage_calls,
        adjudication_calls,
        escalation_calls,
        query_calls,
        timeline_latency,
        guidelines,
        credits_used,
        cache_hits,
        client,
    )
    if client.mock and not client.used_fallback:
        mode = "mock"
    elif client.used_fallback:
        mode = "degraded"
    else:
        mode = "live"
    return {
        "worklist": worklist,
        "dropped": dropped,
        "timeline": timeline,
        "receipt": receipt,
        "citations": citations,
        "mode": mode,
        "guideline_mode": _guideline_mode(citations),
        "degraded_reasons": list(client.degraded_reasons) + list(getattr(guidelines, "degraded_reasons", [])),
        "legacy": {
            "approved": legacy_approved,
            "review": legacy_review,
            "rejected": legacy_rejected,
        },
    }


def _triage_one(client, model, gap, profile, notes, corpus) -> Completion:
    windows = mention_windows(
        notes, keywords_for(str(gap.get("icd10_code") or ""), str(gap.get("condition_name") or ""))
    )
    context = {
        "candidate": _public_gap(gap),
        "profile": profile,
        "notes": notes,
        "mention_windows": windows,
    }
    messages = [
        {
            "role": "system",
            "content": (
                "You are a clinical-coding skeptic. "
                + TRIAGE_INSTRUCTIONS
                + "\nReturn JSON matching this schema:\n"
                + json.dumps(TRIAGE_SCHEMA)
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "candidate": _public_gap(gap),
                    "notes": notes[:8000],
                    "mention_windows": windows,
                    "structured_lines": list(corpus.structured_lines.values())[:40],
                },
                default=str,
            ),
        },
    ]
    try:
        return client.complete_json(
            model=model,
            messages=messages,
            schema_name="triage",
            schema=TRIAGE_SCHEMA,
            pydantic_model=TriageResult,
            temperature=0,
            reasoning=False,
            mock_context=context,
        )
    except Exception as exc:
        client._record_degraded(f"triage failed: {exc}")
        return Completion(
            data=None,
            raw="",
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            mock=True,
            usage_source="none",
            model=model,
            error=str(exc),
            fallback=True,
        )


def _adjudicate(client, gap, triage: TriageResult, quotes, citations, windows=None) -> Completion:
    windows = windows or []
    payload = {
        "icd10_code": gap.get("icd10_code"),
        "code_display": gap.get("condition_name"),
        "gap_type": gap_type_of(gap),
        "verified_quotes": quotes,
        "triage": triage.model_dump(),
        "guideline_excerpts": citations,
        "confidence": triage.confidence,
        "mention_windows": windows,
    }
    messages = [
        {
            "role": "system",
            "content": (
                "You adjudicate one clinical-coding candidate. "
                + TRIAGE_INSTRUCTIONS
                + " Accept only when the verified quotes support the code. "
                "Combination codes and specificity upgrades need a guideline excerpt and still need chart evidence. "
                "Do not accept a code the note denies, attributes to a relative, or marks as screening or a rule-out. "
                "A non-specific medication with no assessment is a reject. "
                "A specific medication alone is an abstain, not an accept. "
                "medication_supported and lab_only are human-review evidence, not negations: "
                "abstain or review, and do not reject them. "
                "If evidence is thin, abstain. rationale must be 60 words or fewer. "
                "Return JSON matching this schema:\n"
                + json.dumps(ADJUDICATION_SCHEMA)
            ),
        },
        {"role": "user", "content": json.dumps(payload, default=str)},
    ]
    return client.complete_json(
        model=SUPER,
        messages=messages,
        schema_name="adjudication",
        schema=ADJUDICATION_SCHEMA,
        pydantic_model=AdjudicationResult,
        temperature=0.6,
        reasoning=True,
        mock_context={
            "gap_type": gap_type_of(gap),
            "citations": citations,
            "confidence": triage.confidence,
            "icd10_code": gap.get("icd10_code"),
            "code_display": gap.get("condition_name"),
            "assertion_status": triage.assertion_status,
            "mention_windows": windows,
        },
    )


def _escalate(client, gap, triage, quotes, citations, decision, rationale, windows=None) -> Completion:
    windows = windows or []
    payload = {
        "icd10_code": gap.get("icd10_code"),
        "code_display": gap.get("condition_name"),
        "gap_type": gap_type_of(gap),
        "verified_quotes": quotes,
        "prior_decision": decision,
        "prior_rationale": rationale,
        "guideline_excerpts": citations,
        "raf_value": gap.get("raf_value"),
        "mention_windows": windows,
    }
    messages = [
        {
            "role": "system",
            "content": (
                "You are the escalation reviewer for a hard or high-value coding candidate. "
                + TRIAGE_INSTRUCTIONS
                + " Combination codes and specificity upgrades still need chart evidence and a guideline excerpt. "
                "Do not accept a code the note denies, attributes to a relative, or marks as screening. "
                "If the evidence conflicts or is thin, abstain. Do not guess. "
                "Return JSON matching this schema:\n"
                + json.dumps(ADJUDICATION_SCHEMA)
            ),
        },
        {"role": "user", "content": json.dumps(payload, default=str)},
    ]
    return client.complete_json(
        model=ULTRA,
        messages=messages,
        schema_name="adjudication",
        schema=ADJUDICATION_SCHEMA,
        pydantic_model=AdjudicationResult,
        temperature=0.6,
        reasoning=True,
        mock_context={
            "gap_type": gap_type_of(gap),
            "citations": citations,
            "confidence": triage.confidence,
            "icd10_code": gap.get("icd10_code"),
            "code_display": gap.get("condition_name"),
            "prior_decision": decision,
            "assertion_status": triage.assertion_status,
            "mention_windows": windows,
        },
    )


def _draft_query(client, gap, quotes) -> Completion:
    code = str(gap.get("icd10_code") or "")
    display = str(gap.get("condition_name") or "")
    messages = [
        {
            "role": "system",
            "content": (
                "Draft a non-leading physician query. Ask the clinician to clarify status. "
                "Do not tell them to add, remove, or confirm a code. "
                "Return JSON matching this schema:\n"
                + json.dumps(PHYSICIAN_QUERY_SCHEMA)
            ),
        },
        {
            "role": "user",
            "content": json.dumps({"code_for_staff_only": code, "display": display, "quotes": quotes}),
        },
    ]
    return client.complete_json(
        model=ULTRA,
        messages=messages,
        schema_name="physician_query",
        schema=PHYSICIAN_QUERY_SCHEMA,
        pydantic_model=PhysicianQueryResult,
        temperature=0.6,
        reasoning=True,
        mock_context={"icd10_code": code, "code_display": display, "quotes": quotes},
    )


def _citation_dicts(found) -> list[dict]:
    rows = []
    for item in found or []:
        if hasattr(item, "as_dict"):
            rows.append(item.as_dict())
        elif isinstance(item, dict):
            rows.append(item)
    return rows


def _drop_unestablished_n18(worklist: list[dict], notes: str, observations: list) -> tuple[list[dict], list[dict]]:
    """An N18 code needs a CKD diagnosis or chronicity of at least 3 months.

    One low eGFR, one creatinine, or an acute kidney lab stays off the worklist.
    """
    if kidney_disease_is_established(notes, observations):
        return worklist, []
    kept: list[dict] = []
    removed: list[dict] = []
    for item in worklist:
        code = str(item.get("icd10_code") or "")
        if code.upper().startswith("N18"):
            removed.append(
                {
                    "id": item.get("id") or code,
                    "icd10_code": code,
                    "code_display": item.get("code_display") or code,
                    "gap_type": item.get("gap_type") or "missing",
                    "tier": item.get("tier") or "",
                    "reason": "rejected",
                    "reason_detail": (
                        "A single kidney lab does not establish an N18 code without a documented "
                        "CKD diagnosis or chronicity of at least 3 months."
                    ),
                    "supporting_quotes": list(item.get("verified_quotes") or item.get("supporting_quotes") or []),
                    "dropped_by": "adjudicator",
                    "model": None,
                }
            )
        else:
            kept.append(item)
    return kept, removed


def _code_family(code: str) -> str:
    head = str(code or "").split(".", 1)[0]
    return head[:3].upper()


def _is_unspecified(code: str) -> bool:
    return str(code or "").upper().endswith(".9")


def _suppress_unspecified_siblings(worklist: list[dict]) -> tuple[list[dict], list[dict]]:
    """Drop an unspecified .9 code once a different, more specific family member is accepted.

    N18.3 accepted drops N18.9. I50.22 accepted drops I50.9. E11.65 accepted does not
    drop E11.22, and a code that is only in review does not suppress its sibling.
    """
    accepted = [
        item for item in worklist
        if item.get("decision") == "accept"
        and item.get("gap_type") != "unsupported_existing"
        and not _is_unspecified(str(item.get("icd10_code") or ""))
    ]
    kept: list[dict] = []
    removed: list[dict] = []
    for item in worklist:
        code = str(item.get("icd10_code") or "")
        if item.get("gap_type") == "unsupported_existing":
            kept.append(item)
            continue
        family = _code_family(code)
        more_specific = any(
            _code_family(str(other.get("icd10_code") or "")) == family
            and str(other.get("icd10_code") or "").upper() != code.upper()
            for other in accepted
        )
        if _is_unspecified(code) and more_specific:
            removed.append(
                {
                    "id": item.get("id") or code,
                    "icd10_code": code,
                    "code_display": item.get("code_display") or code,
                    "gap_type": item.get("gap_type") or "missing",
                    "tier": item.get("tier") or "",
                    "reason": "rejected",
                    "reason_detail": "Unspecified sibling of an accepted more specific code in the same family.",
                    "supporting_quotes": list(item.get("verified_quotes") or item.get("supporting_quotes") or []),
                    "dropped_by": "adjudicator",
                    "model": None,
                }
            )
        else:
            kept.append(item)
    return kept, removed


def _unsupported_existing_rows(profile: dict) -> list[dict]:
    """Same two-way check the offline policy runs, attached to the live cascade.

    Contradicted or meat-less coded conditions are review items. They are not
    approved adds, so the decision stays review even when the policy would
    accept the finding.
    """
    from eval.policy import unsupported_existing_items

    rows = []
    patient = str(profile.get("fhir_patient_id") or "chart")
    for item in unsupported_existing_items(profile):
        quote = item.quote or "No mention, medication, or supporting lab in this chart"
        if item.assertion_status == "negated":
            rationale = "This coded condition is contradicted by the chart and should be reviewed for removal."
        else:
            rationale = "No mention, medication, or supporting lab supports this coded condition."
        rows.append({
            "id": f"{patient}:{item.code}",
            "icd10_code": item.code,
            "code_display": item.condition_name or item.code,
            "gap_type": "unsupported_existing",
            "upgrade_from": None,
            "decision": "review",
            "confidence": 0.86 if item.assertion_status == "negated" else 0.7,
            "assertion_status": item.assertion_status if item.assertion_status in {
                "negated", "unsupported_existing",
            } else "unsupported_existing",
            "section": "chart",
            "supporting_quotes": [quote],
            "verified_quotes": [quote],
            "meat": {"monitor": None, "evaluate": None, "assess": None, "treat": None},
            "lab": None,
            "hcc_category": "",
            "hcc_description": "",
            "raf_value": 0.0,
            "annual_revenue_impact": 0.0,
            "rationale": rationale,
            "physician_query_needed": False,
            "physician_query": None,
            "decided_by": "codesentinel.unsupported_existing",
            "tier": "TWO_WAY",
            "guideline_citations": [],
            "evidence_sources": [quote],
        })
    return rows


def _patient_id(profile: dict, gap: dict) -> str:
    return str(profile.get("fhir_patient_id") or gap.get("patient_id") or "chart")


def _dropped(gap, *, reason, quotes, dropped_by, model, profile, detail=None) -> dict:
    code = str(gap.get("icd10_code") or "")
    text = detail or (quotes[0] if quotes else _DROP_DETAIL.get(reason, reason))
    return {
        "id": f"{_patient_id(profile, gap)}:{code}",
        "icd10_code": code,
        "code_display": str(gap.get("condition_name") or code),
        "gap_type": gap_type_of(gap),
        "tier": str(gap.get("tier") or ""),
        "reason": reason,
        "reason_detail": str(text)[:500],
        "supporting_quotes": list(quotes or []),
        "dropped_by": dropped_by,
        "model": model,
    }


def _work_item(gap, profile, triage, quotes, decision, rationale, query_needed, physician_query, decided_by, citations):
    code = str(gap.get("icd10_code") or "")
    lab = None
    if gap.get("lab_value") is not None:
        try:
            value = float(gap.get("lab_value"))
        except (TypeError, ValueError):
            value = None
        lab = {
            "name": str(gap.get("lab_name") or ""),
            "loinc": str(gap.get("lab_loinc") or ""),
            "value": value,
            "unit": str(gap.get("lab_unit") or ""),
            "date": str(gap.get("lab_date") or ""),
            "observation_id": str(gap.get("lab_observation_id") or ""),
        }
    return {
        "id": f"{_patient_id(profile, gap)}:{code}",
        "icd10_code": code,
        "code_display": str(gap.get("condition_name") or code),
        "gap_type": gap_type_of(gap),
        "upgrade_from": gap.get("upgrade_from"),
        "decision": decision,
        "confidence": float(triage.confidence),
        "assertion_status": triage.assertion_status,
        "section": triage.section or "",
        "supporting_quotes": list(triage.supporting_quotes),
        "verified_quotes": list(quotes),
        "meat": triage.meat.model_dump(),
        "lab": lab,
        "hcc_category": str(gap.get("hcc_category") or ""),
        "hcc_description": str(gap.get("hcc_description") or ""),
        "raf_value": float(gap.get("raf_value") or 0),
        "annual_revenue_impact": float(gap.get("annual_revenue_impact") or 0),
        "rationale": rationale,
        "physician_query_needed": bool(query_needed),
        "physician_query": physician_query,
        "decided_by": decided_by,
        "tier": str(gap.get("tier") or ""),
        "guideline_citations": citations,
        "evidence_sources": list(gap.get("evidence_sources") or []),
    }


def _legacy(gap: dict, decision: str) -> dict:
    copied = dict(gap)
    copied["rules_decision"] = gap.get("decision")
    copied["decision"] = decision
    copied["nemotron_decision"] = decision
    return copied


def _public_gap(gap: dict) -> dict:
    return {
        "icd10_code": gap.get("icd10_code"),
        "condition_name": gap.get("condition_name"),
        "tier": gap.get("tier"),
        "evidence_sources": gap.get("evidence_sources") or [],
        "upgrade_from": gap.get("upgrade_from"),
    }


def _unique_citations(worklist: list[dict]) -> list[dict]:
    seen = set()
    unique = []
    for item in worklist:
        for citation in item.get("guideline_citations") or []:
            key = (citation.get("url"), citation.get("quote"), citation.get("issue"))
            if key in seen:
                continue
            seen.add(key)
            unique.append(citation)
    return unique


def _guideline_mode(citations: list[dict]) -> str:
    found = {item.get("retrieved") for item in citations if item.get("retrieved")}
    if not found:
        return "none"
    if found == {"live"}:
        return "live"
    if found == {"cache"}:
        return "cache"
    if found == {"fixture"}:
        return "fixture"
    return "mixed"


def _sum_calls(calls: list[Completion], step: str, model: Optional[str], latency_ms: int, *, credits=0, cache_hits=0, mock=False):
    if not calls and not credits and not cache_hits:
        return None
    input_tokens = sum(call.input_tokens for call in calls)
    output_tokens = sum(call.output_tokens for call in calls)
    sources = {call.usage_source for call in calls}
    if "provider" in sources:
        usage_source = "provider"
    elif calls:
        usage_source = "estimated"
    else:
        usage_source = "none"
    step_mock = mock if not calls else all(call.mock for call in calls)
    return {
        "step": step,
        "model": model,
        "calls": len(calls),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "latency_ms": latency_ms,
        "cost_usd": cost_usd(model or "", input_tokens, output_tokens),
        "mock": step_mock,
        "usage_source": usage_source,
        "tavily_credits": credits,
        "cache_hits": cache_hits,
    }


def _receipt(triage_model, triage_calls, adjudication_calls, escalation_calls, query_calls, latency, guidelines, credits, cache_hits, client):
    rows = []
    specs = (
        ("triage", triage_model, triage_calls, latency["triage"], 0, 0),
        ("adjudication", SUPER, adjudication_calls, latency["adjudication"], 0, 0),
        ("escalation", ULTRA, escalation_calls, latency["escalation"], 0, 0),
        ("physician_query", ULTRA, query_calls, latency["physician_query"], 0, 0),
    )
    for step, model, calls, step_latency, _credits, _hits in specs:
        row = _sum_calls(calls, step, model, step_latency)
        if row:
            rows.append(row)
    if credits or cache_hits or getattr(guidelines, "fixture_hits", 0) or getattr(guidelines, "live_requests", 0):
        rows.append(
            {
                "step": "guidelines",
                "model": None,
                "calls": int(getattr(guidelines, "live_requests", 0) or 0),
                "input_tokens": 0,
                "output_tokens": 0,
                "latency_ms": latency["guidelines"],
                "cost_usd": 0.0,
                "mock": bool(getattr(guidelines, "use_fixtures", False)) and not getattr(guidelines, "live_requests", 0),
                "usage_source": "provider" if getattr(guidelines, "live_requests", 0) else "none",
                "tavily_credits": credits,
                "cache_hits": cache_hits,
            }
        )
    totals = {
        "input_tokens": sum(row["input_tokens"] for row in rows),
        "output_tokens": sum(row["output_tokens"] for row in rows),
        "latency_ms": sum(latency.values()),
        "cost_usd": round(sum(row["cost_usd"] for row in rows), 6),
        "tavily_credits": round(sum(row["tavily_credits"] for row in rows), 4),
        "tavily_cache_hits": sum(row["cache_hits"] for row in rows),
        "calls": sum(row["calls"] for row in rows),
    }
    models_used = []
    for row in rows:
        if row["model"] and row["calls"] and row["model"] not in models_used:
            models_used.append(row["model"])
    return {
        "steps": rows,
        "totals": totals,
        "models_used": models_used,
        "catalog_prices_per_million": CATALOG_PRICE_TABLE,
    }
