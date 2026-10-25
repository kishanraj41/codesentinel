"""Pydantic models and JSON schemas sent to Token Factory."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

ASSERTION_STATUS = (
    "present",
    "negated",
    "historical_resolved",
    "family_history",
    "screening_or_rule_out",
    "hypothetical_plan",
    "copied_forward_unclear",
)
DECISIONS = ("accept", "review", "reject", "abstain")


class MeatQuotes(BaseModel):
    monitor: Optional[str] = None
    evaluate: Optional[str] = None
    assess: Optional[str] = None
    treat: Optional[str] = None


class TriageResult(BaseModel):
    assertion_status: Literal[
        "present",
        "negated",
        "historical_resolved",
        "family_history",
        "screening_or_rule_out",
        "hypothetical_plan",
        "copied_forward_unclear",
    ]
    supporting_quotes: list[str] = Field(default_factory=list)
    section: str = ""
    meat: MeatQuotes = Field(default_factory=MeatQuotes)
    confidence: float = 0.0

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, value: object) -> float:
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, number))

    @field_validator("supporting_quotes", mode="before")
    @classmethod
    def _quotes(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]


class ModelCitation(BaseModel):
    url: str = ""
    quote: str = ""
    source: str = ""


class AdjudicationResult(BaseModel):
    decision: Literal["accept", "review", "reject", "abstain"]
    icd10_code: str
    code_display: str
    guideline_citations: list[ModelCitation] = Field(default_factory=list)
    rationale: str = ""
    physician_query_needed: bool = False

    @field_validator("rationale", mode="before")
    @classmethod
    def _rationale(cls, value: object) -> str:
        return limit_words(str(value or ""), 60)


class PhysicianQueryResult(BaseModel):
    query: str
    leading: bool = False


def limit_words(text: str, limit: int = 60) -> str:
    words = text.split()
    if len(words) <= limit:
        return text.strip()
    return " ".join(words[:limit])


TRIAGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "assertion_status": {"type": "string", "enum": list(ASSERTION_STATUS)},
        "supporting_quotes": {"type": "array", "items": {"type": "string"}},
        "section": {"type": "string"},
        "meat": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "monitor": {"type": ["string", "null"]},
                "evaluate": {"type": ["string", "null"]},
                "assess": {"type": ["string", "null"]},
                "treat": {"type": ["string", "null"]},
            },
            "required": ["monitor", "evaluate", "assess", "treat"],
        },
        "confidence": {"type": "number"},
    },
    "required": ["assertion_status", "supporting_quotes", "section", "meat", "confidence"],
}

ADJUDICATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "decision": {"type": "string", "enum": list(DECISIONS)},
        "icd10_code": {"type": "string"},
        "code_display": {"type": "string"},
        "guideline_citations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "url": {"type": "string"},
                    "quote": {"type": "string"},
                    "source": {"type": "string"},
                },
                "required": ["url", "quote", "source"],
            },
        },
        "rationale": {"type": "string"},
        "physician_query_needed": {"type": "boolean"},
    },
    "required": [
        "decision",
        "icd10_code",
        "code_display",
        "guideline_citations",
        "rationale",
        "physician_query_needed",
    ],
}

PHYSICIAN_QUERY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "query": {"type": "string"},
        "leading": {"type": "boolean"},
    },
    "required": ["query", "leading"],
}
