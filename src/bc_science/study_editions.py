"""Fast readable editions derived from a source-verified Complete.

No LLM generation for the extractive Essential. Statements come from the
existing Complete; do not label this as scientific verification of PDFs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .italian_proofreading import conservative_italian_fixes
from .quality_signals import critical_facts
from .summarizer import (
    _extract_exam_points,
    _extract_summary_chapters,
    _preserves_exam_numbers,
)

FAST_STUDY_VERSION = "study-v7-original-evidence-diagnostics"
_BOILERPLATE = {
    "in parole semplici", "spiegazione semplice", "spiegazione ordinata",
    "concetti chiave", "concetti da ricordare", "definizioni e termini",
    "parole chiave", "da ricordare per l'esame", "attenzione all'esame",
    "sequenze / meccanismi", "spiegazione", "meccanismi",
    "dettagli recuperati dall'audit", "dettagli recuperati dalle dispense",
}
_WORD = re.compile(r"[a-zà-ÿ0-9]+", re.IGNORECASE)


def _clean_line(line: str, *, preserve_bold: bool = False) -> str:
    line = re.sub(r"^\s*(?:[-*]|\d+[.)])\s+", "", line.strip())
    if not preserve_bold:
        line = re.sub(r"\*\*([^*]+)\*\*", r"\1", line)
    line = re.sub(r"[\x60]([^\x60]+)[\x60]", r"\1", line)
    return re.sub(r"\s+", " ", line).strip()


def _sentences(line: str) -> list[str]:
    return [
        value.strip()
        for value in re.split(r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Ý])", line)
        if len(value.strip()) >= 15
    ]


def _keywords(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.casefold()) if len(w) >= 4}


def _similar(a: str, b: str) -> bool:
    nums = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?")
    if set(nums.findall(a)) != set(nums.findall(b)):
        return False
    la, lb = _keywords(a), _keywords(b)
    if not la or not lb:
        return a.casefold() == b.casefold()
    return len(la & lb) / min(len(la), len(lb)) >= 0.83


@dataclass(frozen=True, slots=True)
class ReadableChapter:
    title: str
    markdown: str
    source_words: int
    output_words: int
    preserved_exam_numbers: bool


def readable_complete_chapter(title: str, chapter: str) -> str:
    """Present old Complete as readable prose without mandatory template sections.

    This changes presentation, not the stored document or provenance.
    """
    paragraphs: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            paragraph = " ".join(buffer)
            if paragraph.casefold() not in {
                other.casefold() for other in paragraphs
            }:
                paragraphs.append(paragraph)
            buffer.clear()

    for raw in chapter.splitlines():
        stripped = raw.strip()
        if stripped.startswith("## "):
            continue
        if stripped.startswith("### "):
            flush()
            section = stripped[4:].strip()
            if section.casefold() not in _BOILERPLATE:
                buffer.append(section + ".")
            continue
        if not stripped or stripped == "---":
            flush()
            continue
        value = _clean_line(stripped, preserve_bold=True)
        if value:
            buffer.append(value)
    flush()
    text = f"## {title}\n\n" + "\n\n".join(paragraphs)
    text, _fix_count = conservative_italian_fixes(text)
    return text


def make_fast_essential_chapter(title: str, complete_chapter: str) -> ReadableChapter:
    """Select readable source statements while retaining exam numbers literally."""
    sections: dict[str, list[str]] = {}
    current = "body"
    for raw in complete_chapter.splitlines():
        s = raw.strip()
        if not s or s == "---" or s.startswith("## "):
            continue
        if s.startswith("### "):
            current = s[4:].strip().casefold()
            continue
        if s.startswith("#"):
            continue
        value = _clean_line(s)
        if value:
            sections.setdefault(current, []).extend(_sentences(value) or [value])

    source_words = len(_WORD.findall(complete_chapter))
    intro: list[str] = []
    for key in ("in parole semplici", "body", "spiegazione semplice"):
        for sentence in sections.get(key, []):
            if sentence not in intro and len(intro) < 3:
                intro.append(sentence)
    if not intro:
        for candidates in sections.values():
            intro.extend(candidates[:2])
            if intro:
                break

    important: list[str] = []
    for key, sentences in sections.items():
        if key in {"in parole semplici", "body", "spiegazione semplice"}:
            continue
        important.extend(sentence for sentence in sentences if sentence not in important)

    mandatory = [
        _clean_line(point) for point in _extract_exam_points(complete_chapter)
        if re.search(r"\d", point)
    ]
    # Also preserve numerical statements from non-exam sections: anatomical
    # measurements and physiological values may be exam-critical even without
    # a dedicated recap heading.
    for sentences in sections.values():
        for sentence in sentences:
            if re.search(r"(?<!\w)\d+(?:[.,]\d+)?", sentence):
                mandatory.append(sentence)
    selected: list[str] = []
    max_words = min(550, max(95, int(source_words * 0.43)))
    current_words = 0

    def add(statement: str, *, force: bool = False) -> None:
        nonlocal current_words
        value = statement.strip()
        if not value or any(_similar(value, prev) for prev in selected):
            return
        count = len(_WORD.findall(value))
        if not force and selected and current_words + count > max_words:
            return
        selected.append(value)
        current_words += count

    for sentence in intro:
        add(sentence)
    for sentence in mandatory:
        add(sentence, force=True)

    # Preserve a diverse sample of indispensable concepts before less salient
    # prose. Use source sentences verbatim; never generate unsupported claims.
    category_selected: dict[str, int] = {}
    for fact in critical_facts(complete_chapter):
        if fact.category == "number_or_formula":
            continue  # all numerical evidence already preserved above
        already_selected = category_selected.get(fact.category, 0)
        if already_selected >= 3:
            continue
        before = len(selected)
        add(fact.quote)
        if len(selected) > before:
            category_selected[fact.category] = already_selected + 1

    priority = re.compile(
        r"\b(?:si definisce|è costituit|è formato|comprende|permette|causa|"
        r"produce|funzione|classific|fase|principale|differenz|"
        r"muscol|vascolar|innerv|sintetizz|secerne|regola)\w*",
        re.IGNORECASE,
    )
    ranked = sorted(
        enumerate(important),
        key=lambda pair: (not bool(priority.search(pair[1])), pair[0]),
    )
    for _index, sentence in ranked:
        add(sentence)
    for sentence in sections.get("body", []):
        add(sentence)

    if not selected:
        raise ValueError(f"Nessun testo utilizzabile per {title}")

    # The ranking decides WHICH statements to keep, never their final order.
    # Returning them to the order found in the verified Complete prevents
    # scrambled cause/effect, definitions detached from their subject, and
    # the uncontrolled jumps seen in the 80-page Anatomia PDF.
    source_order = [
        sentence
        for sentences in sections.values()
        for sentence in sentences
    ]
    order: dict[str, int] = {}
    for position, sentence in enumerate(source_order):
        order.setdefault(sentence.strip().casefold(), position)
    selected.sort(
        key=lambda sentence: order.get(
            sentence.strip().casefold(), len(source_order),
        )
    )
    paragraphs: list[str] = []
    group: list[str] = []
    words = 0
    for sentence in selected:
        word_count = len(_WORD.findall(sentence))
        if group and words + word_count > 90:
            paragraphs.append(" ".join(group))
            group, words = [], 0
        group.append(sentence)
        words += word_count
    if group:
        paragraphs.append(" ".join(group))

    text = f"## {title}\n\n" + "\n\n".join(paragraphs)
    text, _fix_count = conservative_italian_fixes(text)
    if not _preserves_exam_numbers(complete_chapter, text):
        raise ValueError(f"Numeri d'esame mancanti in {title}")
    return ReadableChapter(
        title=title,
        markdown=text,
        source_words=source_words,
        output_words=len(_WORD.findall(text)),
        preserved_exam_numbers=True,
    )


def make_fast_essential(complete: str, title: str) -> tuple[str, list[ReadableChapter]]:
    chapters = _extract_summary_chapters(complete)
    if not chapters:
        raise ValueError("La Completa non contiene capitoli verificabili.")
    records = [
        make_fast_essential_chapter(name, chapter) for name, chapter in chapters
    ]
    toc = "\n".join(f"- {record.title}" for record in records)
    body = "\n\n---\n\n".join(record.markdown for record in records)
    return (
        f"# {title}\n\n## Indice degli argomenti\n{toc}\n\n---\n\n{body}\n",
        records,
    )


def readable_complete_document(complete: str, title: str) -> str:
    """Reflow the archived Complete as one continuous summary per topic.

    Does not mutate the source Complete, so provenance stays honest.
    """
    chapters = _extract_summary_chapters(complete)
    if not chapters:
        raise ValueError("Versione Completa senza capitoli leggibili.")
    toc = "\n".join(f"- {name}" for name, _ in chapters)
    body = "\n\n---\n\n".join(
        readable_complete_chapter(name, chapter) for name, chapter in chapters
    )
    return f"# {title}\n\n## Indice degli argomenti\n{toc}\n\n---\n\n{body}\n"
