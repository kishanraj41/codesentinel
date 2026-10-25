"""Deterministic quote check. The model does not get a vote here."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MIN_QUOTE_CHARS = 12


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def lab_line(observation: dict) -> str:
    oid = str(observation.get("observation_id") or "")
    return (
        f"Lab {observation.get('name', '')} LOINC {observation.get('loinc', '')} "
        f"= {observation.get('value')} {observation.get('unit', '')} id={oid}"
    ).strip()


def medication_line(medication: dict) -> str:
    mid = str(medication.get("medication_id") or "")
    return f"Medication {medication.get('name', '')} id={mid}".strip()


@dataclass
class ChartCorpus:
    notes_text: str
    notes_norm: str
    structured_lines: dict[str, str] = field(default_factory=dict)
    record_ids: set[str] = field(default_factory=set)

    def grounded(self, quote: str) -> bool:
        raw = (quote or "").strip()
        if not raw:
            return False
        if raw in self.record_ids:
            return True
        normalized = normalize_ws(raw)
        if normalized in self.structured_lines:
            return True
        if len(normalized) >= MIN_QUOTE_CHARS and normalized in self.notes_norm:
            return True
        return False


def build_corpus(profile: dict) -> ChartCorpus:
    notes = []
    for note in profile.get("clinical_notes") or []:
        if isinstance(note, dict):
            notes.append(note.get("text") or "")
        elif isinstance(note, str):
            notes.append(note)
    notes_text = "\n".join(notes)
    lines: dict[str, str] = {}
    record_ids: set[str] = set()
    for observation in profile.get("observations") or []:
        if not isinstance(observation, dict):
            continue
        oid = str(observation.get("observation_id") or "")
        if oid:
            record_ids.add(oid)
        line = lab_line(observation)
        lines[normalize_ws(line)] = line
    for medication in profile.get("medications") or []:
        if not isinstance(medication, dict):
            continue
        mid = str(medication.get("medication_id") or "")
        if mid:
            record_ids.add(mid)
        line = medication_line(medication)
        lines[normalize_ws(line)] = line
    return ChartCorpus(
        notes_text=notes_text,
        notes_norm=normalize_ws(notes_text),
        structured_lines=lines,
        record_ids=record_ids,
    )


def verified_quotes(quotes: list[str], corpus: ChartCorpus) -> list[str]:
    kept: list[str] = []
    seen: set[str] = set()
    for quote in quotes:
        key = normalize_ws(quote)
        if not key or key in seen:
            continue
        if corpus.grounded(quote):
            kept.append(quote.strip())
            seen.add(key)
    return kept
