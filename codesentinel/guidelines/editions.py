"""ICD-10-CM Official Guidelines editions and the fiscal year in force.

The federal fiscal year starts October 1. FY 2027 guidelines apply to
encounters from 2026-10-01 through 2027-09-30. The current edition is the
citation target; older editions are kept so a curated passage can say which
year it was checked against.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

OFFICIAL_TITLE = "ICD-10-CM Official Guidelines for Coding and Reporting"
OFFICIAL_DOMAINS = ("cms.gov", "cdc.gov")


@dataclass(frozen=True)
class Edition:
    fiscal_year: int
    effective_from: str
    effective_to: str
    pdf_url: str
    mirror_urls: tuple[str, ...] = field(default_factory=tuple)
    sha256: str = ""
    pages: int = 0
    url_markers: tuple[str, ...] = field(default_factory=tuple)

    @property
    def label(self) -> str:
        return f"FY {self.fiscal_year}"

    @property
    def urls(self) -> tuple[str, ...]:
        return (self.pdf_url,) + tuple(self.mirror_urls)

    def matches_url(self, url: str) -> bool:
        """True for this edition's official PDF on cms.gov or cdc.gov."""
        lowered = (url or "").lower()
        if any(lowered.rstrip("/") == candidate.lower().rstrip("/") for candidate in self.urls):
            return True
        return any(marker in lowered for marker in self.url_markers)


EDITIONS: dict[int, Edition] = {
    2027: Edition(
        fiscal_year=2027,
        effective_from="2026-10-01",
        effective_to="2027-09-30",
        pdf_url="https://www.cms.gov/files/document/fy-2027-icd-10-cm-coding-guidelines.pdf",
        mirror_urls=(
            "https://ftp.cdc.gov/pub/health_statistics/NCHS/Publications/ICD10CM/2027/ICD-10-CM-October-1-2026-FY27-Guidelines.pdf",
        ),
        sha256="1f0065b609b678e98f85bf047b3b012c6a88ba3222e42be2f2435889e05738ab",
        pages=121,
        url_markers=("fy-2027-icd-10-cm-coding-guidelines", "fy27-guidelines"),
    ),
    2026: Edition(
        fiscal_year=2026,
        effective_from="2025-10-01",
        effective_to="2026-09-30",
        pdf_url="https://www.cms.gov/files/document/fy-2026-icd-10-cm-coding-guidelines.pdf",
        sha256="2e87b3b088d6032edf6cda8c946cf342619df0c07237055a0e3be68410a0c267",
        pages=121,
        url_markers=("fy-2026-icd-10-cm-coding-guidelines", "fy26-guidelines", "fy26guidelines"),
    ),
}


def fiscal_year_for(day: date | datetime) -> int:
    """October 1 starts the next fiscal year."""
    return day.year + 1 if day.month >= 10 else day.year


def current_edition(today: Optional[date | datetime] = None) -> Edition:
    """The edition in force today, or GUIDELINE_FISCAL_YEAR when set.

    A fiscal year with no registered edition falls back to the newest one.
    """
    override = os.environ.get("GUIDELINE_FISCAL_YEAR", "").strip().upper().replace("FY", "").strip()
    if override.isdigit():
        year = int(override)
    else:
        year = fiscal_year_for(today or date.today())
    if year in EDITIONS:
        return EDITIONS[year]
    known = sorted(year_ for year_ in EDITIONS if year_ <= year) or sorted(EDITIONS)
    return EDITIONS[known[-1]]


def edition_for_url(url: str) -> Optional[Edition]:
    for edition in EDITIONS.values():
        if edition.matches_url(url):
            return edition
    return None
