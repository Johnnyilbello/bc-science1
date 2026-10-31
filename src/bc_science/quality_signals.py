"""Conservative, source-linked educational-summary quality signals.

Inspired by fact-level evaluation and SummEval dimensions. These rules are
heuristics, not semantic entailment or independent scientific verification.
Only material already in the user's verified Complete is considered.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

_WORD = re.compile(r"[a-zà-ÿ0-9]+", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Ý])")
_NUM = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?(?:\s*(?:%|mm|cm|kg|g|mg|ml|bpm|°C))?")
_NUMBER_WORD = re.compile(
    r"\b(?:due|tre|quattro|cinque|sei|sette|otto|nove|dieci|"
    r"undici|dodici|venti|trenta|quaranta|cinquanta|cento|mille)\b",
    re.IGNORECASE,
)
_MARKDOWN = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+", re.MULTILINE)
_HEADING_ONLY = re.compile(
    r"^(?:classificazione\s+(?:\S+\s*){1,7}|tipi\s+di\s+(?:\S+\s*){1,7}|"
    r"[\wÀ-ÿ ]{2,50}:\s*(?:definizione|classificazione|meccanismo)"
    r"(?:\s+e\s+\w+){0,3})$",
    re.IGNORECASE,
)
_ABSOLUTE = re.compile(
    r"\b(?:sempre|mai|esclusivamente|soltanto|necessariamente)\b", re.IGNORECASE,
)
_NEGATIVE = re.compile(
    r"\b(?:non|senza|assenza|privo|priva|eccetto|tranne)\b", re.IGNORECASE,
)
_CONTEXT = re.compile(
    r"^(?:terziariamente|secondariamente|tale ormone|questo ormone)\b",
    re.IGNORECASE,
)
_GLOSSARY = re.compile(
    r"^[A-ZÀ-Ý][A-Za-zÀ-ÿ0-9]*(?:\s+[A-Za-zÀ-ÿ0-9]+){0,3}\s*"
    r"(?:[—–]|:)\s+.{16,}$"
)
_TYPES = (
    ("definition", re.compile(
        r"\b(?:si definisc\w*|è definito|sono definit\w*|consiste in|"
        r"è costituit\w*|è compost\w*|è formato|comprende|rappresenta)\b",
        re.IGNORECASE,
    )),
    ("mechanism", re.compile(
        r"\b(?:avviene|attraverso|mediante|produce|consente|permette|"
        r"determina|innesca|secerne|trasporta|trasform\w*|attiva|inibisce)\b",
        re.IGNORECASE,
    )),
    ("classification", re.compile(
        r"\b(?:si divid\w*|si distingu\w*|classific\w*|tipi di|"
        r"categorie|fasi|si suddivide|tre gruppi|due gruppi)\b",
        re.IGNORECASE,
    )),
    ("causal", re.compile(
        r"\b(?:a causa di|provoca|causa|di conseguenza|pertanto|"
        r"perché|determina|dipende da|comporta)\b",
        re.IGNORECASE,
    )),
    ("exception", re.compile(
        r"\b(?:eccetto|tranne|salvo|tuttavia|a differenza|nonostante|"
        r"invece|solo quando|esclusivamente)\b",
        re.IGNORECASE,
    )),
)


def _text(body: str) -> str:
    body = _MARKDOWN.sub("", body)
    body = re.sub(r"\*\*|__|[\x60]", "", body)
    return body


def _tokens(body: str) -> set[str]:
    return {x for x in _WORD.findall(body.casefold()) if len(x) >= 4}


def _number_markers(body: str) -> set[str]:
    return {
        *(match.casefold() for match in _NUMBER_WORD.findall(body)),
        *_NUM.findall(body),
    }


def _lines(body: str) -> list[str]:
    """Return content statements, excluding Markdown and outline headings."""
    result = []
    for raw in body.splitlines():
        stripped = raw.strip()
        if (
            not stripped
            or stripped.startswith((">", "#", "---"))
            or _HEADING_ONLY.fullmatch(_text(stripped).strip())
        ):
            continue
        paragraph = _text(stripped)
        for item in _SENTENCE.split(paragraph):
            item = re.sub(r"\s+", " ", item).strip()
            if len(item) >= 22 and not _HEADING_ONLY.fullmatch(item):
                result.append(item)
    return result


@dataclass(frozen=True, slots=True)
class KeyFact:
    category: str
    quote: str


@dataclass(frozen=True, slots=True)
class ChapterSignals:
    title: str
    key_facts: int
    possible_missing: tuple[KeyFact, ...]
    difficult_sentences: int
    long_paragraphs: int
    unsupported_phrases: int
    numerical_mismatch: bool
    review_priority: str
    source_context_warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


def critical_facts(complete_chapter: str) -> tuple[KeyFact, ...]:
    """Detect candidate key sentences, without claiming complete recall."""
    facts: list[KeyFact] = []
    seen: set[str] = set()
    for sentence in _lines(complete_chapter):
        kind = None
        if _GLOSSARY.match(sentence):
            kind = "definition"
        elif _NUM.search(sentence) or _NUMBER_WORD.search(sentence):
            kind = "number_or_formula"
        elif _ABSOLUTE.search(sentence):
            kind = "absolute_condition"
        elif _NEGATIVE.search(sentence):
            kind = "negative_condition"
        else:
            for category, pattern in _TYPES:
                if pattern.search(sentence):
                    kind = category
                    break
        if kind is not None:
            fingerprint = " ".join(sentence.casefold().split())
            if fingerprint not in seen:
                seen.add(fingerprint)
                facts.append(KeyFact(kind, sentence))
    return tuple(facts)


def _overlap(source: str, candidate: str) -> float:
    """Source-fact recall, not overlap divided by the shorter statement.

    The old min-length score treated a mere term label as equivalent to a
    longer definition. Recall is deliberately a triage heuristic, not NLI.
    """
    original, output = _tokens(source), _tokens(candidate)
    if not original or not output:
        return 0.0
    return len(original & output) / len(original)


def _qualifiers_compatible(source: str, target: str) -> bool:
    """Never equate a positive claim with its negation or change absolutes."""
    source_nums = _number_markers(source)
    target_nums = _number_markers(target)
    if not source_nums <= target_nums:
        return False
    if bool(_NEGATIVE.search(source)) != bool(_NEGATIVE.search(target)):
        return False
    # Exact restrictive terms matter; "always" is not "exclusively".
    source_absolute = {x.casefold() for x in _ABSOLUTE.findall(source)}
    target_absolute = {x.casefold() for x in _ABSOLUTE.findall(target)}
    return source_absolute == target_absolute


def _supported(item: str, text: str, sentences: list[str]) -> bool:
    """Conservative lexical evidence for candidate triage, not entailment."""
    normalized = " ".join(_text(item).casefold().split())
    for sentence in sentences:
        if not _qualifiers_compatible(item, sentence):
            continue
        candidate = " ".join(_text(sentence).casefold().split())
        if normalized in candidate:
            return True
        # Require most source detail, never just a shorter label.
        if _overlap(item, sentence) >= 0.76 and len(
            _tokens(item) & _tokens(sentence)
        ) >= 3:
            return True
    return False


def assess_chapter(
    title: str,
    complete_chapter: str,
    essential_chapter: str,
) -> ChapterSignals:
    """Triage uncertain omissions and readability; never return 'verified'."""
    source_lines = _lines(complete_chapter)
    target_lines = _lines(essential_chapter)
    candidates = critical_facts(complete_chapter)
    uncertain = tuple(
        fact for fact in candidates
        if not _supported(fact.quote, essential_chapter, target_lines)
    )

    # A short extractive summary should not suddenly introduce facts absent
    # from the provided Complete. This is only a heuristic overlap check.
    unsupported = sum(
        1 for line in target_lines
        if not _supported(line, complete_chapter, source_lines)
    )
    bad_sentences = sum(len(_WORD.findall(line)) > 52 for line in target_lines)
    too_long = sum(
        len(_WORD.findall(p)) > 130
        for p in re.split(r"\n\s*\n", essential_chapter)
        if p.strip() and not p.lstrip().startswith("#")
    )
    source_nums = _number_markers(complete_chapter)
    target_nums = _number_markers(essential_chapter)
    numbers_unmatched = bool(target_nums - source_nums)
    # Preserve possible ambiguity inherited from the Complete. Do not guess
    # whether "terziariamente" refers to the kidney, a hormone, or a gland.
    context_warnings = tuple(
        line for line in source_lines
        if _CONTEXT.search(line)
    )
    critical_missing = sum(
        fact.category in {"absolute_condition", "negative_condition",
                          "number_or_formula", "causal"}
        for fact in uncertain
    )
    # Candidate omissions are NOT confirmed errors. Escalation guides review,
    # particularly for numbers, qualifications and causal claims.
    if numbers_unmatched or unsupported:
        priority = "alta"
    elif critical_missing or len(uncertain) >= 3 or too_long or bad_sentences:
        priority = "media"
    elif uncertain or context_warnings:
        priority = "bassa"
    else:
        priority = "nessuna"
    return ChapterSignals(
        title=title,
        key_facts=len(candidates),
        possible_missing=uncertain,
        difficult_sentences=bad_sentences,
        long_paragraphs=too_long,
        unsupported_phrases=unsupported,
        numerical_mismatch=numbers_unmatched,
        review_priority=priority,
        source_context_warnings=context_warnings,
    )
