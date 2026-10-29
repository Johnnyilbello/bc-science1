"""Lightweight quality triage: repeated sentences, disconnected headings and risks.

These are alerts, not automatic corrections or semantic entailment. The source
text must be checked before changing any anatomical fact.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .italian_proofreading import grammar_findings

_HEAD_FRAGMENT = re.compile(
    r"(?:\b(?:Riepilogo|Classificazione|Organizzazione|Trasporto|"
    r"Distribuzione|Struttura|Introduzione)\s+(?:delle?|dei|alla?|e)\s+"
    r"[\wÀ-ÿ-]+(?:\s+[\wÀ-ÿ-]+){0,3})\s*$",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Ý])")


@dataclass(frozen=True, slots=True)
class StructuralIssue:
    category: str
    quote: str
    reason: str
    requires_original: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


def find_structural_issues(chapter: str, *, limit: int = 20) -> tuple[StructuralIssue, ...]:
    findings: list[StructuralIssue] = []
    seen: set[tuple[str, str]] = set()

    def add(category: str, quote: str, reason: str) -> None:
        key = (category, quote.casefold())
        if key not in seen and len(findings) < limit:
            seen.add(key)
            findings.append(StructuralIssue(category, quote[:260], reason))

    paragraphs = re.split(r"\n\s*\n", chapter)
    repeated: set[str] = set()
    for paragraph in paragraphs:
        if paragraph.strip().startswith("#"):
            continue
        sentences = _SENTENCE.split(re.sub(r"\s+", " ", paragraph.strip()))
        for sentence in sentences:
            cleaned = re.sub(r"[\W_]+", " ", sentence.casefold()).strip()
            if len(cleaned.split()) >= 8:
                if cleaned in repeated:
                    add("repetition", sentence, "Frase ripetuta nel capitolo")
                repeated.add(cleaned)
        tail = paragraph.strip()
        if tail and _HEAD_FRAGMENT.search(tail):
            add(
                "dangling_heading", tail[-180:],
                "Possibile titolo non collegato alla spiegazione",
            )

    for item in grammar_findings(chapter):
        if item.category in {"meaning", "fragment"}:
            add(
                "meaning_risk" if item.category == "meaning" else "fragment",
                item.quote, item.message,
            )
    return tuple(findings)
