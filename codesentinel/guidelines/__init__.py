"""Curated FY2026 guideline sections cited ahead of a live search."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).resolve().parent
ANCHORS_PATH = _DIR / "anchors.json"
EXCERPT_PATH = _DIR / "excerpt.txt"
OFFICIAL_PDF_URL = "https://www.cms.gov/files/document/fy-2026-icd-10-cm-coding-guidelines.pdf"
OFFICIAL_TITLE = "ICD-10-CM Official Guidelines for Coding and Reporting"


def collapse_ws(text: str) -> str:
    """Collapse extractor line breaks. Words and punctuation stay as extracted."""
    return re.sub(r"\s+", " ", text or "").strip()


@lru_cache(maxsize=1)
def load_anchors() -> dict:
    return json.loads(ANCHORS_PATH.read_text(encoding="utf-8"))


def anchor_rows() -> list[dict]:
    rows = load_anchors().get("anchors") or []
    return [row for row in rows if isinstance(row, dict)]


def anchor_for(code_family: str, issue: str) -> dict | None:
    key = f"{(code_family or '').upper()}|{(issue or '').strip().lower()}"
    for row in anchor_rows():
        if row.get("key") == key:
            return row
    return None
