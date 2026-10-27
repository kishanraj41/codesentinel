"""Rebuild the committed FY2026 guideline excerpt and check anchor quotes.

The official PDF is not stored in the repo. Pass its path. The script checks
the SHA-256, extracts the pages the anchors cite, and checks that every quote
is a contiguous span of that extract after whitespace is collapsed. It does
not rewrite words, and it does not join spans with "[...]".

    python scripts/extract_guideline_excerpt.py path/to/fy-2026-icd-10-cm-coding-guidelines.pdf
    python scripts/extract_guideline_excerpt.py path/to/file.pdf --write

Requires pypdf. The unit tests do not: they compare anchors to the committed
excerpt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANCHORS_PATH = ROOT / "codesentinel" / "guidelines" / "anchors.json"
EXCERPT_PATH = ROOT / "codesentinel" / "guidelines" / "excerpt.txt"
PDF_URL = "https://www.cms.gov/files/document/fy-2026-icd-10-cm-coding-guidelines.pdf"
PDF_SHA256 = "2e87b3b088d6032edf6cda8c946cf342619df0c07237055a0e3be68410a0c267"
PAGES = (11, 39, 50, 51, 62)

# Contiguous spans. start/end are matched against whitespace-collapsed page text.
SPANS = (
    {
        "key": "E11|combination-code-diabetes-ckd",
        "code": "E11.22",
        "issue": "combination-code-diabetes-ckd",
        "section": "I.A.15",
        "page": 11,
        "start": "The word “with” or “in” should be interpreted",
        "end": "the sepsis”).",
    },
    {
        "key": "E11|specificity-hyperglycemia",
        "code": "E11.65",
        "issue": "specificity-hyperglycemia",
        "section": "I.C.4.a",
        "page": 39,
        "start": "The diabetes mellitus codes are combination codes",
        "end": "that the patient has.",
    },
    {
        "key": "I12|combination-code-hypertensive-ckd",
        "code": "I12",
        "issue": "combination-code-hypertensive-ckd",
        "section": "I.C.9.a.2",
        "page": 50,
        "start": "Assign codes from category I12, Hypertensive chronic kidney disease,",
        "end": "stage of chronic kidney disease.",
    },
    {
        "key": "I13|combination-code-hypertensive-ckd",
        "code": "I13",
        "issue": "combination-code-hypertensive-ckd",
        "section": "I.C.9.a.3",
        "page": 51,
        "start": "The codes in category I13, Hypertensive heart and chronic kidney disease,",
        "end": "the type of heart failure.",
    },
    {
        "key": "I50|specificity-upgrade",
        "code": "I50",
        "issue": "specificity-upgrade",
        "section": "I.C.9.a.3",
        "page": 51,
        "start": "If heart failure is present, assign an additional code from category I50",
        "end": "the type of heart failure.",
    },
    {
        "key": "N18|specificity-upgrade",
        "code": "N18",
        "issue": "specificity-upgrade",
        "section": "I.C.14.a.1",
        "page": 62,
        "start": "The ICD-10-CM classifies CKD based on severity.",
        "end": "equates to severe CKD.",
    },
)


def collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_pages(pdf_path: Path) -> dict[int, str]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise SystemExit("pypdf is required to extract the guideline PDF") from exc
    reader = PdfReader(str(pdf_path))
    pages: dict[int, str] = {}
    for number in PAGES:
        if number < 1 or number > len(reader.pages):
            raise SystemExit(f"PDF has no page {number}")
        pages[number] = reader.pages[number - 1].extract_text() or ""
    return pages


def span_quote(page_text: str, start: str, end: str) -> str:
    collapsed = collapse_ws(page_text)
    begin = collapsed.find(start)
    if begin < 0:
        raise SystemExit(f"start not found: {start[:48]}")
    stop = collapsed.find(end, begin)
    if stop < 0:
        raise SystemExit(f"end not found: {end[:48]}")
    quote = collapsed[begin : stop + len(end)]
    if "[...]" in quote or quote != collapse_ws(quote):
        raise SystemExit("quote is not one contiguous span")
    if quote not in collapsed:
        raise SystemExit("quote is not contained in the page extract")
    return quote


def build_anchors(pages: dict[int, str]) -> dict:
    rows = []
    for spec in SPANS:
        quote = span_quote(pages[spec["page"]], spec["start"], spec["end"])
        rows.append(
            {
                "key": spec["key"],
                "code": spec["code"],
                "issue": spec["issue"],
                "section": spec["section"],
                "page": spec["page"],
                "quote": quote,
            }
        )
    return {
        "pdf_url": PDF_URL,
        "pdf_sha256": PDF_SHA256,
        "title": "ICD-10-CM Official Guidelines for Coding and Reporting",
        "fiscal_year": "FY 2026",
        "anchors": rows,
    }


def render_excerpt(pages: dict[int, str]) -> str:
    parts = [
        "FY2026 ICD-10-CM Official Guidelines for Coding and Reporting",
        f"Source: {PDF_URL}",
        f"sha256: {PDF_SHA256}",
        "Extracted with pypdf. Line breaks are the extractor's. Quotes in anchors.json",
        "are contiguous spans of this text with whitespace collapsed, and nothing else changed.",
        "",
    ]
    for number in PAGES:
        parts.append(f"===== PAGE {number} =====")
        parts.append(pages[number].rstrip())
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--write", action="store_true", help="overwrite the committed excerpt and anchors")
    args = parser.parse_args()
    pdf_path = args.pdf
    if not pdf_path.is_file():
        raise SystemExit(f"PDF not found: {pdf_path}")
    digest = sha256_file(pdf_path)
    if digest != PDF_SHA256:
        raise SystemExit(f"sha256 mismatch: {digest}")
    pages = extract_pages(pdf_path)
    payload = build_anchors(pages)
    excerpt = render_excerpt(pages)
    if args.write:
        ANCHORS_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        EXCERPT_PATH.write_text(excerpt, encoding="utf-8")
        print(f"wrote {ANCHORS_PATH}")
        print(f"wrote {EXCERPT_PATH}")
    else:
        current = json.loads(ANCHORS_PATH.read_text(encoding="utf-8"))
        if current != payload:
            raise SystemExit("anchors.json does not match this PDF extract")
        if EXCERPT_PATH.read_text(encoding="utf-8") != excerpt:
            raise SystemExit("excerpt.txt does not match this PDF extract")
        print("anchors and excerpt match the PDF")
    for row in payload["anchors"]:
        words = row["quote"].split()
        print(f"{row['code']} {row['section']} p.{row['page']}: {' '.join(words[:15])}")


if __name__ == "__main__":
    sys.exit(main())
