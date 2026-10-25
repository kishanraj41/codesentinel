"""Refuse pasted text that looks like a real identifier.

Synthetic charts in this demo are checked with the same scanner. Patterns are
deliberately narrow so a progress note with a lab date is not treated as a
date of birth.
"""

from __future__ import annotations

import re

_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_PHONE = re.compile(
    r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)"
)
_EMAIL = re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.I)
_MRN = re.compile(
    r"\b(?:MRN|medical record(?:\s+number)?)\b[:\s#\-]*[A-Z0-9][A-Z0-9\-]{2,}",
    re.I,
)
_DOB = re.compile(
    r"\b(?:dob|d\.o\.b\.|date of birth|birth\s*date|born on|born)\b"
    r".{0,40}?"
    r"(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2}|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2},?\s+\d{4})",
    re.I | re.S,
)

_CHECKS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ssn", _SSN),
    ("mrn", _MRN),
    ("email", _EMAIL),
    ("phone", _PHONE),
    ("dob", _DOB),
)


def scan_phi(text: str) -> list[str]:
    """Return identifier kinds found in text. Does not return the match."""
    if not text:
        return []
    found: list[str] = []
    for kind, pattern in _CHECKS:
        if pattern.search(text):
            found.append(kind)
    return found


def phi_detail(kinds: list[str]) -> str:
    labels = {
        "ssn": "a Social Security number",
        "mrn": "a medical record number",
        "email": "an email address",
        "phone": "a phone number",
        "dob": "a date of birth",
    }
    pretty = [labels.get(kind, kind) for kind in kinds]
    if len(pretty) == 1:
        joined = pretty[0]
    elif len(pretty) == 2:
        joined = f"{pretty[0]} and {pretty[1]}"
    else:
        joined = ", ".join(pretty[:-1]) + f", and {pretty[-1]}"
    return (
        f"Possible PHI detected ({joined}). "
        "This demo accepts synthetic data only. The note was not stored or sent."
    )
