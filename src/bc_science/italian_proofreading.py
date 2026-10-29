"""Conservative Italian grammar and meaning-risk checks for BC Science.

The fast path corrects only a small, reviewed set of unambiguous mistakes.
Everything else is a review finding; never silently change scientific meaning
or claim a complete grammatical certification from lightweight regex checks.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

# These replacements do not alter anatomy facts, numbers, measurements or names.
_SAFE_FIXES = (
    (
        re.compile(r"\bil scheletro\b", re.IGNORECASE),
        "lo scheletro",
        "Articolo davanti a s impura",
    ),
    (
        re.compile(r"\bpiegare e dritto il braccio\b", re.IGNORECASE),
        "piegare e distendere il braccio",
        "Forma verbale incoerente",
    ),
    (
        re.compile(
            r"\bla prima stanza dell'apparato digerente\b", re.IGNORECASE,
        ),
        "la prima parte dell'apparato digerente",
        "Metafora poco appropriata al contesto scientifico",
    ),
)

# These require comparing the user's PDFs and reading the surrounding passage.
# They must not be auto-rewritten even when the correction seems obvious.
_REVIEW_RULES = (
    (
        re.compile(
            r"\bdall[oa]\s+(?P<place>\w{4,})\s+"
            r"all[oa]\s+(?P=place)\b", re.IGNORECASE,
        ),
        "meaning",
        "Percorso con origine e destinazione identiche: verificare sulla fonte",
    ),
    (
        re.compile(
            r"\b(?:cavitò|introduzone|conviega|condiglio|giglimo)\b",
            re.IGNORECASE,
        ),
        "spelling",
        "Possibile refuso, anche in un termine scientifico",
    ),
    (
        re.compile(r"\b(?:il|lo)\s+stomaco\s+è\s+stato\s+secerne\b",
                   re.IGNORECASE),
        "grammar",
        "Costruzione verbale sospetta",
    ),
)


@dataclass(frozen=True, slots=True)
class GrammarFinding:
    category: str
    quote: str
    message: str
    needs_source_check: bool

    def to_dict(self) -> dict:
        return asdict(self)


def conservative_italian_fixes(text: str) -> tuple[str, int]:
    """Apply only pre-reviewed low-risk edits, retaining title and numbers."""
    fixed = text
    changes = 0
    for regex, replacement, _description in _SAFE_FIXES:
        def replace(match: re.Match[str]) -> str:
            nonlocal changes
            changes += 1
            new = replacement
            if match.group(0)[:1].isupper():
                new = new[:1].upper() + new[1:]
            return new

        fixed = regex.sub(replace, fixed)
    return fixed, changes


def grammar_findings(text: str, *, limit: int = 30) -> tuple[GrammarFinding, ...]:
    """Flag suspicious language without rewriting unverified factual content."""
    results: list[GrammarFinding] = []
    seen: set[tuple[str, str]] = set()

    def push(kind: str, quote: str, description: str, check: bool) -> None:
        key = (kind, quote.casefold())
        if key in seen or len(results) >= limit:
            return
        seen.add(key)
        results.append(GrammarFinding(kind, quote, description, check))

    for regex, _replacement, description in _SAFE_FIXES:
        for match in regex.finditer(text):
            push("grammar", match.group(0), description, False)
    for regex, category, description in _REVIEW_RULES:
        for match in regex.finditer(text):
            push(category, match.group(0), description, category == "meaning")

    # Detect orphaned fragments often introduced by extractive sentence stitching:
    # never guess the missing verb, and avoid treating headings as sentences.
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block or block.startswith(("#", "-", ">", "*")):
            continue
        if len(block) > 700:
            continue
        last = block.splitlines()[-1].strip()
        if (
            3 <= len(last.split()) <= 13
            and last[-1:] not in {".", "!", "?", ":", ";", "»", "”"}
        ):
            push(
                "fragment", last[:140],
                "Possibile periodo interrotto o titolo rimasto nel testo",
                False,
            )

    return tuple(results)
