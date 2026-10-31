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
# Targeted scientific-risk patterns: these are verified against the actual
# course PDF before being promoted from a warning to a correction.
# The respiratory chain pumps H+ OUT of the mitochondrial matrix; ATP synthase
# uses H+ returning IN. Flag an inverted chain direction, never auto-rewrite.
_INVERTED_PROTON_PUMP = re.compile(
    r"(?:catena\s+respiratoria|complessi\s+proteici)\b.{0,95}?"
    r"\bpomp\w*\b.{0,65}?\bprotoni\b.{0,95}?"
    r"\bdallo\s+spazio\s+intermembrana\s+alla\s+matrice\b",
    re.IGNORECASE,
)
_ADP_PROTON_CONFUSION = re.compile(
    r"\bADP\b.{0,65}?\b(?:ricevere|riceve)\s+(?:i\s+)?protoni\b",
    re.IGNORECASE,
)
_DETACHED_GLOSSARY = re.compile(
    r"(?:^|(?<!es)(?<!ecc)[.!?]\s+)[A-Z]{2,6}\)\s+che\b",
    re.IGNORECASE,
)



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
        # Flag exact high-risk patterns that cannot be fixed by removing
        # repeated words or raising the compression budget.
        for sentence in sentences:
            proton = _INVERTED_PROTON_PUMP.search(sentence)
            if proton:
                add(
                    "scientific_direction_risk", proton.group(),
                    "La direzione del pompaggio protonico attribuita alla "
                    "catena respiratoria è invertita: verificare i PDF originali",
                )
            adp = _ADP_PROTON_CONFUSION.search(sentence)
            if adp:
                add(
                    "scientific_mechanism_risk", adp.group(),
                    "L'ADP non riceve protoni per formare ATP: verificare "
                    "il meccanismo con la fonte originale",
                )
            fragment = _DETACHED_GLOSSARY.search(sentence)
            if fragment:
                add(
                    "detached_glossary_fragment", sentence,
                    "Possibile definizione spezzata dopo una sigla tra parentesi",
                )
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
