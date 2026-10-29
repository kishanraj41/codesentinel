"""Response models for POST /audit. Field names match docs/api.md contract 1.0.0."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from codesentinel import CONTRACT_VERSION


class MeatModel(BaseModel):
    monitor: Optional[str] = None
    evaluate: Optional[str] = None
    assess: Optional[str] = None
    treat: Optional[str] = None


class LabModel(BaseModel):
    name: str = ""
    loinc: str = ""
    value: Optional[float] = None
    unit: str = ""
    date: str = ""
    observation_id: str = ""


class CitationModel(BaseModel):
    url: str
    quote: str
    source: str = ""
    title: str = ""
    retrieved: Literal["live", "cache", "fixture", "anchor"]
    cached_at: Optional[str] = None
    code_family: str
    issue: str
    source_tier: Literal["official", "secondary", ""] = ""
    source_label: str = ""
    section: str = ""
    page: Optional[int] = None
    provenance: Literal["tavily_live", "curated_fallback", "fixture", ""] = ""
    fiscal_year: str = ""
    verified_verbatim: bool = False
    verified_against: str = ""
    pdf_sha256: str = ""
    anchor_status: Literal["verified", "superseded", "unverified", "none", ""] = ""
    supersedes: Optional[dict] = None


class WorklistItem(BaseModel):
    id: str
    icd10_code: str
    code_display: str
    gap_type: Literal["missing", "specificity_upgrade", "combination", "unsupported_existing"]
    upgrade_from: Optional[str] = None
    decision: Literal["accept", "review", "abstain"]
    confidence: float
    assertion_status: Literal[
        "present",
        "negated",
        "historical_resolved",
        "family_history",
        "screening_or_rule_out",
        "hypothetical_plan",
        "copied_forward_unclear",
        "medication_supported",
        "lab_only",
        "unsupported_existing",
    ]
    section: str = ""
    supporting_quotes: list[str]
    verified_quotes: list[str]
    meat: MeatModel
    lab: Optional[LabModel] = None
    hcc_category: str = ""
    hcc_description: str = ""
    raf_value: float = 0
    annual_revenue_impact: float = 0
    rationale: str
    physician_query_needed: bool
    physician_query: Optional[str] = None
    decided_by: str
    tier: str = ""
    guideline_citations: list[CitationModel] = Field(default_factory=list)
    evidence_sources: list[str] = Field(default_factory=list)


class DepthModel(BaseModel):
    """Which cascade depth ran for this chart."""

    config: str
    adaptive: bool = False
    depth_used: str
    escalated: bool = False
    reason: str = ""
    deep_candidates: list[str] = Field(default_factory=list)


class DroppedItem(BaseModel):
    id: str
    icd10_code: str
    code_display: str
    gap_type: Literal["missing", "specificity_upgrade", "combination"]
    tier: str = ""
    reason: Literal[
        "unsupported",
        "negated",
        "historical_resolved",
        "family_history",
        "screening_or_rule_out",
        "hypothetical_plan",
        "copied_forward_unclear",
        "rejected",
    ]
    reason_detail: str
    supporting_quotes: list[str] = Field(default_factory=list)
    dropped_by: Literal["triage", "verifier", "adjudicator", "escalation"]
    model: Optional[str] = None


class TimelineStep(BaseModel):
    step: Literal[
        "rules",
        "triage",
        "verifier",
        "guidelines",
        "adjudication",
        "escalation",
        "physician_query",
    ]
    label: str
    model: Optional[str] = None
    status: Literal["ok", "skipped", "error"]
    latency_ms: int
    detail: str = ""
    mock: bool


class ReceiptStep(BaseModel):
    step: str
    model: Optional[str] = None
    calls: int
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: float
    mock: bool
    usage_source: Literal["provider", "estimated", "none"]
    tavily_credits: float = 0
    cache_hits: int = 0


class PriceModel(BaseModel):
    input: float
    output: float


class ReceiptTotals(BaseModel):
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: float
    tavily_credits: float
    tavily_cache_hits: int
    calls: int


class Receipt(BaseModel):
    steps: list[ReceiptStep]
    totals: ReceiptTotals
    models_used: list[str]
    catalog_prices_per_million: dict[str, PriceModel]


class AuditSummary(BaseModel):
    candidates_generated: int
    rules_approved: int
    rules_review: int
    rules_rejected: int
    worklist_count: int
    accept_count: int
    review_count: int
    abstain_count: int
    specificity_upgrades: int
    combination_codes: int
    dropped_count: int


class AuditResponse(BaseModel):
    contract_version: str = CONTRACT_VERSION
    audit_id: str
    patient_id: str
    patient_name: str = ""
    mode: Literal["mock", "live", "degraded"]
    guideline_mode: Literal["live", "cache", "fixture", "anchor", "mixed", "none"]
    guideline_provenance: dict = Field(default_factory=dict)
    degraded_reasons: list[str] = Field(default_factory=list)
    summary: AuditSummary
    worklist: list[WorklistItem]
    depth: Optional[DepthModel] = None
    dropped: list[DroppedItem]
    timeline: list[TimelineStep]
    receipt: Receipt
    citations: list[CitationModel]
    rules_only: Optional[dict] = None
