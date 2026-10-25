"""Refuse obvious identifiers. This is a guard, not a de-identification system."""

from __future__ import annotations

import re

_PATTERNS = (
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(r"\bMRN[:\s#]*[A-Za-z0-9]{5,}\b", re.IGNORECASE),
    re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    re.compile(r"\b(?:\+?1[-.\s])?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b"),
)

PHI_DETAIL = "Possible PHI detected. This endpoint accepts synthetic data only."


def phi_detected(text: str) -> bool:
    if not text:
        return False
    return any(pattern.search(text) for pattern in _PATTERNS)
