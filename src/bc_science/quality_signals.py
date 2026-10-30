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
_MARKDOWN = re.compile(r"^(?:\s*#{1,6}\s+|\s*[-*]\s+)", re.MULTILINE)
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


def _lines(body: str) -> list[str]:
    result = []
    for paragraph in re.split(r"\n+", _text(body)):
        if not paragraph.strip() or paragraph.lstrip().startswith(">"):
            continue
        for item in _SENTENCE.split(paragraph):
            item = re.sub(r"\s+", " ", item).strip()
            if len(item) >= 22:
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
        elif _NUM.search(sentence):
            kind = "number_or_formula"
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


def _overlap(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, min(len(a), len(b)))


def _supported(item: str, text: str, sentences: list[str]) -> bool:
    if " ".join(item.casefold().split()) in " ".join(text.casefold().split()):
        return True
    return any(_overlap(item, sentence) >= 0.75 for sentence in sentences)


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
    source_nums = set(_NUM.findall(complete_chapter))
    target_nums = set(_NUM.findall(essential_chapter))
    numbers_unmatched = bool(target_nums - source_nums)
    # A possible omission is not a confirmed error: paraphrases are hard for
    # lexical matching. Escalate only with multiple independent concerns.
    if numbers_unmatched or unsupported:
        priority = "alta"
    elif len(uncertain) >= 3 or too_long or bad_sentences:
        priority = "media"
    elif uncertain:
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
    )
