"""Bounded, reference-backed scientific red flags for educational material.

This is NOT a general scientific validation model. It alerts on a small set
of documented errors observed in the local study corpus. Findings require
checking against the original documents and independent scientific sources.
No user documents are mutated or silently corrected.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .summarizer import _extract_summary_chapters

PALMITATE_REFERENCE = "https://pmc.ncbi.nlm.nih.gov/articles/PMC8411952/"
SPIROMETRY_REFERENCE = "https://www.ncbi.nlm.nih.gov/books/NBK493170/"

_QUANTITIES = re.compile(
    r"(\d+)\s+(?:(?:molecole|moli)\s+di\s+|di\s+)?"
    r"(acetil\s*[-–]?\s*co\s*[-–]?\s*a|"
    r"malonil\s*[-–]?\s*co\s*[-–]?\s*a|nadph|atp)\b",
    re.IGNORECASE,
)

_PALMITATE = re.compile(
    r"\b(?:sintesi|biosintesi)\s+(?:del|di)\s+palmitat\w*"
    r"[^.!?]{0,350}",
    re.IGNORECASE,
)
_ESOPHAGUS = re.compile(
    r"\bdallo\s+stomaco\s+allo\s+stomaco\b", re.IGNORECASE,
)
_BILE = re.compile(
    r"\bdott[io]\s+epatic[io]\b[^.!?]{0,220}"
    r"verso\s+l['’]esterno\s+del\s+corpo",
    re.IGNORECASE,
)
_TIDAL = re.compile(
    r"\b(?:volume\s+corrente|volume\s+tidale|VT)\b"
    r".{0,320}?\bquesto\s+volume\s+non\s+pu[oò]\s+"
    r"essere\s+misurat\w*\s+dall[oa]\s+spirometr\w*",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ScientificClaimFlag:
    rule: str
    chapter: str
    excerpt: str
    issue: str
    reference: str
    validation: str = "REVISIONE_UMANA_NECESSARIA"


def _excerpt(text: str, start: int, end: int, *, width: int = 430) -> str:
    begin = max(0, start - 55)
    finish = min(len(text), max(end, start + 120) + 55)
    return text[begin:min(finish, begin + width)].strip()


def _palmitate_issue(statement: str) -> str | None:
    # Only a fully specified numeric coefficient is judged; other wording
    # and missing quantities cannot be safely inferred by a regex.
    counts: dict[str, int] = {}
    for matched in _QUANTITIES.finditer(statement):
        name = re.sub(r"[\s–-]", "", matched.group(2)).casefold()
        counts[name] = int(matched.group(1))
    if not counts:
        return None

    expected = {
        "malonilcoa": {7},
        "nadph": {14},
        "atp": {7},
        # One direct acetyl-CoA enters FAS; eight if counting the seven
        # acetyl-CoA first used to synthesize the malonyl-CoA.
        "acetilcoa": {1, 8},
    }
    wrong = [
        f"{key}={value}"
        for key, value in counts.items()
        if key in expected and value not in expected[key]
    ]
    if len(counts) < 2 or not wrong:
        return None
    return (
        "Coefficienti sospetti per la sintesi del palmitato ("
        + ", ".join(wrong)
        + "). Distinguere 1 acetil-CoA + 7 malonil-CoA nella reazione "
        "FAS da 8 acetil-CoA + 7 ATP + 14 NADPH nel bilancio completo."
    )


def inspect_scientific_claims(text: str) -> tuple[ScientificClaimFlag, ...]:
    """Flag only high-confidence, source-comparable patterns; never rewrite."""
    chapters = _extract_summary_chapters(text)
    if not chapters:
        chapters = [("documento", text)]

    results: list[ScientificClaimFlag] = []
    seen: set[tuple[str, str]] = set()

    def add(
        rule: str, chapter: str, excerpt: str, issue: str, reference: str,
    ) -> None:
        key = (chapter.casefold(), rule)
        if key in seen:
            return
        seen.add(key)
        results.append(ScientificClaimFlag(rule, chapter, excerpt, issue, reference))

    for title, body in chapters:
        # Normalize wrapped sentences, preserving their order and numbers.
        compact = re.sub(r"\s+", " ", body)

        for match in _PALMITATE.finditer(compact):
            issue = _palmitate_issue(match.group(0))
            if issue:
                add(
                    "PALMITATE_STOICHIOMETRY", title,
                    _excerpt(compact, match.start(), match.end()),
                    issue, PALMITATE_REFERENCE,
                )

        match = _ESOPHAGUS.search(compact)
        if match and "esofag" in compact[max(0, match.start() - 180):match.start()].casefold():
            add(
                "ESOPHAGUS_DIRECTION", title,
                _excerpt(compact, match.start(), match.end()),
                "Direzione apparente errata: l'esofago collega la faringe allo stomaco.",
                "",
            )

        match = _BILE.search(compact)
        if match:
            add(
                "BILIARY_DIRECTION", title,
                _excerpt(compact, match.start(), match.end()),
                "La bile percorre le vie biliari fino al duodeno, "
                "non viene diretta all'esterno del corpo.",
                "",
            )

        match = _TIDAL.search(compact)
        if match:
            add(
                "TIDAL_VOLUME_SPIROMETRY", title,
                _excerpt(compact, match.start(), match.end()),
                "Il volume corrente è misurabile con lo spirometro; "
                "il volume residuo non lo è direttamente.",
                SPIROMETRY_REFERENCE,
            )

    return tuple(results)
