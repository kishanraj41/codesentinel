"""Deterministic offline stand-in for the Nemotron cascade.

This module never sees gold labels. It classifies a rules candidate from the
chart text, verifies that any note quote is actually in the chart, and applies
the config ladder (triage → adjudication → guideline fixture → Ultra rewrite).

Numbers produced with this policy are not Nemotron measurements. The runner
labels them mode=mock.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from eval.lexicon import (
    CODE_TERMS,
    FAMILY_CUES,
    HIGH_SPECIFICITY_DRUGS,
    HYPOTHETICAL_CUES,
    LINK_PHRASES,
    LOW_SPECIFICITY_DRUGS,
    NEGATION_CUES,
    RESOLVED_CUES,
    SCREENING_CUES,
)

SHOWN = frozenset({"accept", "review", "abstain"})

# Original descriptions. Not quotations from the ICD-10-CM Official Guidelines.
# URLs are the public CMS pages a live Tavily call would be restricted to.
GUIDELINE_FIXTURES: dict[str, dict[str, Any]] = {
    "e11_65_specificity": {
        "issue": "specificity_hyperglycemia",
        "query": "ICD-10-CM type 2 diabetes hyperglycemia specificity code when hyperglycemia is documented",
        "url": "https://www.cms.gov/medicare/coding-billing/icd-10-codes",
        "snippet": (
            "Synthetic fixture, not an official quotation: when type 2 diabetes is already "
            "coded and the assessment documents hyperglycemia, the more specific hyperglycemia "
            "code is the one that matches the assessment."
        ),
        "supports_accept": True,
    },
    "e11_22_with": {
        "issue": "combination_diabetes_ckd",
        "query": "ICD-10-CM official guidelines with convention diabetes chronic kidney disease combination code",
        "url": "https://www.cms.gov/medicare/coding-billing/icd-10-codes",
        "snippet": (
            "Synthetic fixture, not an official quotation: the diabetes 'with' convention "
            "treats chronic kidney disease documented together with diabetes as a combination "
            "code, unless the note says the kidney disease has another cause."
        ),
        "supports_accept": True,
    },
    "i12_9_with": {
        "issue": "combination_hypertension_ckd",
        "query": "ICD-10-CM hypertensive chronic kidney disease combination code I12",
        "url": "https://www.cms.gov/medicare/coding-billing/icd-10-codes",
        "snippet": (
            "Synthetic fixture, not an official quotation: hypertension documented with "
            "chronic kidney disease is reported with a hypertensive CKD combination code "
            "plus the CKD stage."
        ),
        "supports_accept": True,
    },
    "n18_chronicity": {
        "issue": "ckd_chronicity",
        "query": "KDIGO CKD diagnosis requires chronicity not a single eGFR",
        "url": "https://www.cms.gov/medicare/coding-billing/icd-10-codes",
        "snippet": (
            "Synthetic fixture, not an official quotation: one reduced eGFR does not establish "
            "chronic kidney disease when the note gives an acute explanation or never calls "
            "the condition chronic."
        ),
        "supports_accept": False,
    },
    "k76_enzyme": {
        "issue": "liver_enzyme_only",
        "query": "ICD-10-CM elevated liver enzyme without a confirmed liver disease diagnosis",
        "url": "https://www.cms.gov/medicare/coding-billing/icd-10-codes",
        "snippet": (
            "Synthetic fixture, not an official quotation: a mildly elevated ALT with only "
            "a possible or unconfirmed liver finding is not enough to accept a liver-disease code."
        ),
        "supports_accept": False,
    },
}


@dataclass
class Item:
    code: str
    condition_name: str
    gap_type: str
    source_tier: str
    decision: str
    shown_to_coder: bool
    assertion_status: str
    quote: str
    quote_verified: bool
    drop_reason: str | None = None
    stage: str = "rules"
    model: str | None = None
    guideline_url: str | None = None
    guideline_snippet: str | None = None
    from_code: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    tavily_credits: float = 0.0
    tavily_cache_hit: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "condition_name": self.condition_name,
            "gap_type": self.gap_type,
            "source_tier": self.source_tier,
            "decision": self.decision,
            "shown_to_coder": self.shown_to_coder,
            "assertion_status": self.assertion_status,
            "quote_verified": self.quote_verified,
            "drop_reason": self.drop_reason,
            "stage": self.stage,
            "model": self.model,
            "guideline_url": self.guideline_url,
            "from_code": self.from_code,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_usd": round(self.cost_usd, 8),
            "tavily_credits": self.tavily_credits,
            "tavily_cache_hit": self.tavily_cache_hit,
        }


@dataclass
class PolicyTrace:
    items: list[Item] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    tavily_credits: float = 0.0
    tavily_cache_hits: int = 0
    tavily_would_search: int = 0
    guideline_decision_changes: int = 0


def note_text(profile: dict) -> str:
    return "\n".join(n.get("text") or "" for n in profile.get("clinical_notes") or [])


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _sentences(text: str) -> list[tuple[str, str]]:
    """Return (sentence, section header) pairs.

    Section is the last all-caps-or-title header line before the sentence.
    """
    header_re = re.compile(
        r"(?im)^(history of present illness|hpi|problem list|past medical history|"
        r"assessment and plan|assessment|medications|laboratory|physical exam)\s*:?\s*$"
    )
    section = "unknown"
    out: list[tuple[str, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if header_re.match(line):
            section = line.rstrip(":").strip().lower()
            continue
        parts = re.split(r"(?<=[.!?])\s+", line)
        for part in parts:
            part = part.strip()
            if part:
                out.append((part, section))
    return out


def _has_cue(sentence: str, cues: tuple[str, ...]) -> bool:
    low = sentence.lower()
    return any(cue in low for cue in cues)


def _terms_for(code: str) -> tuple[str, ...]:
    if code in CODE_TERMS:
        return CODE_TERMS[code]
    prefix = code.split(".")[0]
    for key, terms in CODE_TERMS.items():
        if key.split(".")[0] == prefix:
            return terms
    return ()


def _sentence_matches(sentence: str, terms: tuple[str, ...]) -> bool:
    low = sentence.lower()
    return any(term in low for term in terms)


def _gap_type(gap: dict) -> str:
    tier = gap.get("tier") or ""
    if tier == "SPECIFICITY_UPGRADE":
        return "specificity_upgrade"
    if tier == "COMBINATION_CODE":
        return "combination"
    if gap.get("gap_type"):
        return gap["gap_type"]
    return "missing"


def _extract_quote(gap: dict, fallback: str) -> str:
    for src in gap.get("evidence_sources") or []:
        text = str(src)
        if "Clinical note" in text and "..." in text:
            parts = text.split("...")
            if len(parts) >= 2 and parts[1].strip():
                return parts[1].strip()
        if text.startswith("Lab:") or text.startswith("Medication:") or text.startswith("Specificity") or text.startswith("Combination"):
            return text
    return fallback


def quote_verified(quote: str, profile: dict) -> bool:
    """A note quote must be a whitespace-normalized substring of the chart.

    Structured lab and medication evidence verifies against those resources
    instead of the note. Empty quotes fail.
    """
    if not quote or not quote.strip():
        return False
    norm_quote = _normalize(quote)
    norm_note = _normalize(note_text(profile))
    if norm_quote and norm_quote in norm_note:
        return True
    low = quote.lower()
    if low.startswith("lab:") or "lab:" in low[:40]:
        # The lab snippet names a value that must exist on an observation.
        for obs in profile.get("observations") or []:
            value = obs.get("value")
            name = (obs.get("name") or "").lower()
            if value is None:
                continue
            if name and name.split()[0].lower() in low and str(value) in quote:
                return True
            if str(value) in quote and (obs.get("loinc") or "") and (obs.get("loinc") in quote or name[:4] in low):
                return True
        # Specificity-upgrade evidence quotes the lab value inside a longer string.
        for obs in profile.get("observations") or []:
            if obs.get("value") is not None and str(obs["value"]) in quote:
                return True
    if low.startswith("medication:"):
        meds = " ".join((m.get("name") or "").lower() for m in profile.get("medications") or [])
        # "Medication: metformin 1000mg (marker..." — the drug token is in the med list.
        body = low.split(":", 1)[-1]
        token = body.strip().split()[0] if body.strip() else ""
        return bool(token) and token in meds
    if low.startswith("specificity upgrade") or low.startswith("combination"):
        return True
    if "both coded" in low or "both present" in low or "currently coded" in low:
        return True
    return False


def _classify(code: str, gap: dict, profile: dict) -> tuple[str, str]:
    """Return (assertion_status, quote) from the chart. No gold access."""
    text = note_text(profile)
    terms = _terms_for(code)
    gap_type = _gap_type(gap)
    matched = [(s, sec) for s, sec in _sentences(text) if terms and _sentence_matches(s, terms)]

    def status_of(sentence: str) -> str | None:
        if _has_cue(sentence, FAMILY_CUES) and _has_cue(sentence, tuple(terms)):
            # Family cue in the same sentence as the condition, and the sentence
            # does not also say the patient has the condition.
            if "the patient has" not in sentence.lower() and "patient himself" not in sentence.lower():
                return "family_history"
        if _has_cue(sentence, NEGATION_CUES):
            return "negated"
        if _has_cue(sentence, RESOLVED_CUES):
            return "historical_resolved"
        if _has_cue(sentence, HYPOTHETICAL_CUES):
            return "hypothetical_plan"
        if _has_cue(sentence, SCREENING_CUES):
            return "screening_or_rule_out"
        return None

    copy_forward_sections = {"problem list", "past medical history"}
    affirming: list[str] = []
    negative: list[tuple[str, str]] = []
    for sentence, section in matched:
        st = status_of(sentence)
        if st is None and section in copy_forward_sections:
            negative.append(("copied_forward_unclear", sentence))
            continue
        if st is None:
            affirming.append(sentence)
        else:
            negative.append((st, sentence))

    quote = ""
    if gap_type == "combination":
        links = LINK_PHRASES.get(code, ())
        low = text.lower()
        linked = next((phrase for phrase in links if phrase in low), "")
        forbid = any(st == "negated" and "combination" in sent.lower() for st, sent in negative) or (
            "do not use" in low and code == "E11.22" and "diabetic" in low
        )
        if linked and not forbid:
            quote = linked
            return "present", quote
        if forbid:
            quote = next((s for st, s in negative if st == "negated"), "combination contradicted")
            return "negated", quote
        quote = _extract_quote(gap, "combination parents coded without a link phrase")
        return "combination_unlinked", quote

    if affirming:
        # An affirming sentence outside the problem list wins over a copied list entry.
        quote = affirming[0]
        return "present", quote
    if negative:
        # Prefer a real context cue over a bare problem-list copy.
        ranked = sorted(negative, key=lambda pair: pair[0] == "copied_forward_unclear")
        status, sentence = ranked[0]
        return status, sentence

    evidence = " ".join(gap.get("evidence_sources") or [])
    if gap.get("lab_value") is not None or "Lab:" in evidence:
        quote = _extract_quote(gap, evidence[:240])
        return "lab_only", quote
    if "Medication:" in evidence:
        quote = _extract_quote(gap, evidence[:240])
        return "medication_only", quote
    quote = _extract_quote(gap, "")
    return "unsupported", quote


def _med_names(profile: dict) -> list[str]:
    return [(m.get("name") or "").lower() for m in profile.get("medications") or []]


def _drug_specificity(code: str, profile: dict) -> str:
    names = " ".join(_med_names(profile))
    high = HIGH_SPECIFICITY_DRUGS.get(code, ())
    low = LOW_SPECIFICITY_DRUGS.get(code, ())
    if any(drug in names for drug in high):
        return "high"
    if any(drug in names for drug in low):
        return "low"
    if code in LOW_SPECIFICITY_DRUGS:
        return "low"
    return "unknown"


def _set_decision(item: Item, decision: str, reason: str | None, stage: str) -> None:
    item.decision = decision
    item.drop_reason = reason
    item.stage = stage
    item.shown_to_coder = decision in SHOWN


def _apply_c1(item: Item, profile: dict) -> None:
    if not item.quote_verified and item.assertion_status != "present":
        # Present status already came from a sentence inside the note, so the
        # sentence itself is the verified quote. Other statuses must verify.
        if item.assertion_status not in {"lab_only", "medication_only", "combination_unlinked"}:
            if not quote_verified(item.quote, profile):
                _set_decision(item, "reject", "unsupported_claim", "verifier")
                return
    if item.assertion_status == "present":
        if not quote_verified(item.quote, profile) and item.quote:
            # The sentence was taken from the note, so this passes. If a caller
            # substituted a hallucinated quote, reject it.
            if _normalize(item.quote) not in _normalize(note_text(profile)):
                _set_decision(item, "reject", "unsupported_claim", "verifier")
                return
        if item.gap_type in {"specificity_upgrade", "combination"}:
            _set_decision(item, "accept", None, "triage")
        elif item.source_tier.startswith("TIER_2_PHENOTYPE_WEAK") or item.assertion_status == "medication_only":
            _set_decision(item, "abstain", "thin_evidence", "triage")
        else:
            # Single note mention still goes to a human; multi-evidence can be accepted.
            evidence_count = 2 if item.gap_type == "specificity_upgrade" else 1
            # Use a stored hint: accept when the rules tier was a full phenotype or upgrade.
            if item.source_tier in {"TIER_2_PHENOTYPE", "TIER_1_STRUCTURED", "SPECIFICITY_UPGRADE", "COMBINATION_CODE"}:
                _set_decision(item, "accept", None, "triage")
            else:
                _set_decision(item, "review", "single_mention", "triage")
        return
    if item.assertion_status in {
        "negated",
        "family_history",
        "historical_resolved",
        "hypothetical_plan",
        "screening_or_rule_out",
        "copied_forward_unclear",
    }:
        _set_decision(item, "reject", item.assertion_status, "triage")
        return
    if item.assertion_status == "medication_only":
        spec = _drug_specificity(item.code, profile)
        if spec == "low":
            _set_decision(item, "reject", "low_specificity_medication", "triage")
        elif spec == "high":
            _set_decision(item, "abstain", "medication_only", "triage")
        else:
            _set_decision(item, "abstain", "medication_only", "triage")
        return
    if item.assertion_status == "lab_only":
        _set_decision(item, "abstain", "lab_only", "triage")
        return
    if item.assertion_status == "combination_unlinked":
        _set_decision(item, "abstain", "combination_unlinked", "triage")
        return
    if not item.quote_verified:
        _set_decision(item, "reject", "unsupported_claim", "verifier")
        return
    _set_decision(item, "review", "unclear", "triage")


def _lipid_conflict(item: Item, text: str) -> bool:
    low = text.lower()
    if item.code != "E78.5":
        return False
    if "hypercholesterolemia" in low and "hyperlipidemia" not in low and "dyslipidemia" not in low:
        return True
    return False


def _apply_c2(item: Item, text: str) -> None:
    """Adjudication on top of the C1 decision. No guideline lookup."""
    if item.decision == "reject":
        return
    if _lipid_conflict(item, text):
        _set_decision(item, "reject", "wrong_lipid_specificity", "adjudicate")
        return
    if item.gap_type == "specificity_upgrade" and item.assertion_status == "present":
        _set_decision(item, "accept", None, "adjudicate")
        return
    if item.assertion_status == "combination_unlinked":
        # Without a guideline, an unlinked combination stays a human review.
        _set_decision(item, "abstain", "combination_unlinked", "adjudicate")


def _guideline_for(item: Item, profile: dict, text: str) -> dict[str, Any] | None:
    if item.code == "E11.65" and item.gap_type == "specificity_upgrade":
        return GUIDELINE_FIXTURES["e11_65_specificity"]
    if item.code == "E11.22":
        return GUIDELINE_FIXTURES["e11_22_with"]
    if item.code == "I12.9":
        return GUIDELINE_FIXTURES["i12_9_with"]
    if item.code.startswith("N18") and item.assertion_status == "lab_only":
        return GUIDELINE_FIXTURES["n18_chronicity"]
    if item.code == "K76.89" and item.assertion_status in {"lab_only", "hypothetical_plan"}:
        return GUIDELINE_FIXTURES["k76_enzyme"]
    low = text.lower()
    if item.code.startswith("N18") and "acute" in low and "chronic kidney" not in low and "ckd" not in low:
        return GUIDELINE_FIXTURES["n18_chronicity"]
    return None


def _apply_c3(item: Item, profile: dict, text: str, trace: PolicyTrace) -> None:
    before = item.decision
    if item.decision == "reject":
        return
    fixture = _guideline_for(item, profile, text)
    needs_guideline = item.gap_type in {"combination", "specificity_upgrade"} or (
        item.code.startswith("N18") and item.assertion_status == "lab_only"
    ) or item.code == "K76.89"
    if not needs_guideline or fixture is None:
        return
    trace.tavily_would_search += 1
    trace.tavily_cache_hits += 1
    item.tavily_cache_hit = True
    item.guideline_url = fixture["url"]
    item.guideline_snippet = fixture["snippet"]
    item.tavily_credits = 0.0  # cache hit, no live credit
    if fixture["supports_accept"]:
        if item.assertion_status in {"present", "combination_unlinked"} and item.decision in SHOWN:
            # The 'with' convention lets an unlinked-but-undisputed combination be accepted.
            if item.code == "E11.22" and "do not use" in text.lower():
                _set_decision(item, "reject", "note_forbids_combination", "guideline")
            elif item.assertion_status == "combination_unlinked" or item.decision == "abstain":
                _set_decision(item, "accept", None, "guideline")
            else:
                item.stage = "guideline"
    else:
        _set_decision(item, "reject", "guideline_does_not_support", "guideline")
    if item.decision != before:
        trace.guideline_decision_changes += 1


def _ejection_fraction(text: str) -> int | None:
    patterns = (
        r"ejection fraction is (\d{2})",
        r"ef is (\d{2})",
        r"ef (\d{2})\s*%",
        r"ef (\d{2}) percent",
    )
    for pat in patterns:
        m = re.search(pat, text, flags=re.I)
        if m:
            return int(m.group(1))
    return None


def _apply_c4(item: Item, text: str) -> None:
    if item.decision == "reject":
        return
    low = text.lower()
    if item.code == "I50.9" and item.assertion_status == "present":
        ef = _ejection_fraction(text)
        systolic = any(tok in low for tok in ("reduced ejection", "hfref", "systolic heart failure"))
        if systolic and ef is not None and ef <= 40:
            item.code = "I50.22"
            item.condition_name = "Chronic systolic heart failure"
            item.gap_type = "specificity_upgrade"
            item.from_code = "I50.9"
            item.stage = "escalate"
            item.model = "nvidia/Nemotron-3-Ultra-550b-a55b"
            _set_decision(item, "accept", None, "escalate")
            return
    # Disagreement placeholder: thin evidence on a high-stakes code stays abstain
    # rather than being promoted. Already-abstain items are unchanged.
    if item.decision == "abstain":
        item.stage = "escalate" if item.code.startswith(("I50", "J44", "F32", "E11.2")) else item.stage


def _unsupported_existing(profile: dict) -> list[Item]:
    """Two-way pass: coded conditions the note contradicts, or that have no MEAT at all."""
    text = note_text(profile)
    sentences = _sentences(text)
    items: list[Item] = []
    meds = " ".join(_med_names(profile))
    for cond in profile.get("conditions") or []:
        code = (cond.get("icd10_code") or "").upper()
        if not code:
            continue
        terms = _terms_for(code)
        matched = [(s, sec) for s, sec in sentences if terms and _sentence_matches(s, terms)]
        contradicted = None
        affirmed = False
        for sentence, _sec in matched:
            low = sentence.lower()
            if _has_cue(sentence, NEGATION_CUES) or "in error" in low or "please remove" in low:
                contradicted = sentence
            elif not _has_cue(sentence, FAMILY_CUES):
                affirmed = True
        if contradicted and not affirmed:
            items.append(Item(
                code=code,
                condition_name=cond.get("display") or code,
                gap_type="unsupported_existing",
                source_tier="TWO_WAY",
                decision="accept",
                shown_to_coder=True,
                assertion_status="negated",
                quote=contradicted,
                quote_verified=True,
                drop_reason=None,
                stage="triage",
            ))
            continue
        if matched or affirmed:
            continue
        # No mention. Unsupported only when the chart also lacks a supporting drug and lab.
        high = HIGH_SPECIFICITY_DRUGS.get(code, ())
        if any(drug in meds for drug in high):
            continue
        # Labs that would support the code.
        if _lab_supports(code, profile):
            continue
        if not text.strip() and not profile.get("medications") and not profile.get("observations"):
            continue
        items.append(Item(
            code=code,
            condition_name=cond.get("display") or code,
            gap_type="unsupported_existing",
            source_tier="TWO_WAY",
            decision="review",
            shown_to_coder=True,
            assertion_status="unsupported_existing",
            quote="No mention, medication, or supporting lab in this chart",
            quote_verified=True,
            drop_reason="no_meat",
            stage="triage",
        ))
    return items


def _lab_supports(code: str, profile: dict) -> bool:
    obs = profile.get("observations") or []
    for o in obs:
        loinc = o.get("loinc") or ""
        try:
            value = float(o.get("value"))
        except (TypeError, ValueError):
            continue
        if code.startswith("E11") and loinc == "4548-4" and value >= 6.5:
            return True
        if code.startswith("N18") and loinc == "33914-3" and value < 60:
            return True
        if code.startswith("I50") and loinc == "42637-9" and value > 100:
            return True
        if code.startswith("E03") and loinc == "3016-3" and value > 4.5:
            return True
        if code.startswith("E78") and loinc in {"2089-1", "2093-3"} and value >= 160:
            return True
        if code.startswith("D64") and loinc == "718-7" and value < 12:
            return True
    return False


def _sibling_pass(items: list[Item]) -> None:
    shown_codes = {it.code for it in items if it.shown_to_coder and it.gap_type != "unsupported_existing"}
    for item in items:
        if not item.shown_to_coder or item.gap_type == "unsupported_existing":
            continue
        m = re.fullmatch(r"([A-Z]\d{2})\.9", item.code)
        if not m:
            continue
        prefix = m.group(1)
        siblings = [c for c in shown_codes if c.startswith(prefix + ".") and c != item.code]
        if siblings:
            _set_decision(item, "reject", "unspecified_sibling", item.stage or "adjudicate")


def apply_policy(profile: dict, gaps: list[dict], config: str) -> PolicyTrace:
    """Apply the offline cascade to rules candidates.

    `gaps` is the approved+review+rejected list from the rules pipeline.
    C0 and C0+ should not call this; they slice buckets directly.
    """
    trace = PolicyTrace()
    text = note_text(profile)
    items: list[Item] = []
    for gap in gaps:
        code = (gap.get("icd10_code") or "").upper()
        if not code:
            continue
        status, quote = _classify(code, gap, profile)
        verified = quote_verified(quote, profile) or (
            status == "present" and _normalize(quote) in _normalize(text)
        )
        item = Item(
            code=code,
            condition_name=gap.get("condition_name") or code,
            gap_type=_gap_type(gap),
            source_tier=gap.get("tier") or "",
            decision="review",
            shown_to_coder=True,
            assertion_status=status,
            quote=quote,
            quote_verified=verified,
            from_code=gap.get("upgrade_from"),
        )
        _apply_c1(item, profile)
        if config in {"C2", "C3", "C4"}:
            _apply_c2(item, text)
        if config in {"C3", "C4"}:
            _apply_c3(item, profile, text, trace)
        if config == "C4":
            _apply_c4(item, text)
        items.append(item)

    if config in {"C1", "C2", "C3", "C4"}:
        _sibling_pass(items)
        items.extend(_unsupported_existing(profile))

    trace.items = items
    return trace
