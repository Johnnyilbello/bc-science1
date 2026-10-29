"""Optional, bounded local-Qwen improvements for flagged Essential chapters.

This step is explicitly user-triggered; the instant source-only summary does
not initialize an LLM. Candidate rewrites require literal source evidence and
strict deterministic checks. A failed review leaves the published text intact.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from .config import AppConfig
from .italian_proofreading import grammar_findings
from .quality_signals import _lines, _overlap, assess_chapter
from .summarizer import (
    Summarizer,
    _key,
    _normalize_chapter_heading,
    _preserves_exam_numbers,
)

REVIEW_VERSION = "targeted-review-v2-italian"
_NUMBER = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?")


@dataclass(frozen=True, slots=True)
class ReviewOutcome:
    title: str
    applied: bool
    text: str
    reason: str


def _validate_proposal(
    title: str, source: str, previous: str, proposed: dict,
) -> tuple[bool, str]:
    """Conservative gates, not semantic proof of correctness."""
    text = proposed.get("rewrite")
    quotes = proposed.get("evidence")
    if not isinstance(text, str) or not isinstance(quotes, list):
        return False, "JSON incompleto"
    if not quotes or len(quotes) > 12 or not all(
        isinstance(q, str) and q.strip() for q in quotes
    ):
        return False, "Citazioni mancanti"
    norm_source = re.sub(r"\s+", " ", source).casefold()
    if any(
        re.sub(r"\s+", " ", q).strip().casefold() not in norm_source
        for q in quotes
    ):
        return False, "Citazioni non letterali"
    headings = re.findall(r"(?m)^##\s+(.+?)\s*$", text)
    if len(headings) != 1 or headings[0].strip() != title:
        return False, "Titolo o struttura alterati"
    if "### " in text or "In parole semplici" in text:
        return False, "Il riassunto ripete sezioni didattiche"
    if not _preserves_exam_numbers(source, text):
        return False, "Numeri d'esame non preservati"
    if set(_NUMBER.findall(text)) - set(_NUMBER.findall(source)):
        return False, "Valore numerico estraneo alla fonte"
    # Claims must be lexically anchored; paraphrase support cannot be
    # fully certified with this check and the report remains unverified.
    source_sents = _lines(source)
    sentences = _lines(text)
    if not sentences or len(text.split()) > max(100, int(len(source.split()) * 0.7)):
        return False, "Risposta vuota o troppo lunga"
    if any(
        not any(_overlap(s, evidence) >= 0.55 for evidence in source_sents)
        for s in sentences
    ):
        return False, "Possibili affermazioni senza supporto"
    before = assess_chapter(title, source, previous)
    after = assess_chapter(title, source, text)
    grammar_before = grammar_findings(previous)
    grammar_after = grammar_findings(text)
    if len(grammar_after) > len(grammar_before):
        return False, "Sono comparsi nuovi problemi grammaticali o sintattici"
    # If the source itself contains an implausible anatomical statement, a
    # model cannot silently 'fix' it based on unsupported world knowledge.
    if (
        any(issue.category == "meaning" for issue in grammar_before)
        and not any(issue.category == "meaning" for issue in grammar_after)
    ):
        return False, (
            "Correzione del significato scientifico richiede confronto manuale "
            "con la dispensa originale"
        )
    if (
        after.unsupported_phrases > before.unsupported_phrases
        or after.long_paragraphs > before.long_paragraphs
        or after.difficult_sentences > before.difficult_sentences
    ):
        return False, "La riscrittura non migliora la comprensibilita"
    return True, ""


def review_chapter(
    title: str,
    complete_source: str,
    essential: str,
    config: AppConfig,
    *,
    summarizer: Summarizer,
) -> ReviewOutcome:
    signals = assess_chapter(title, complete_source, essential)
    language = grammar_findings(essential)
    if signals.review_priority == "nessuna" and not language:
        return ReviewOutcome(title, False, essential, "Nessuna criticita rilevata")
    key = _key(
        REVIEW_VERSION, summarizer.model, title,
        hashlib.sha256(complete_source.encode("utf-8")).hexdigest(),
        hashlib.sha256(essential.encode("utf-8")).hexdigest(),
    )
    del config  # Restrict execution to the supplied local Ollama Summarizer.
    requested = [x.quote for x in signals.possible_missing[:5]]
    language_notes = [x.quote + ": " + x.message for x in language[:6]]
    system = (
        "Sei un revisore di riassunti scientifici. "
        "Le fonti fornite sono l'unico riferimento. "
        "Non aggiungere conoscenze esterne, valori inventati o sezioni ripetitive."
    )
    prompt = (
        "Rendi più chiaro e fedele il SOLO capitolo seguente, limitandoti a "
        "correggere concordanze, forme verbali, ortografia, punteggiatura, "
        "periodi spezzati e a includere i concetti cruciali mancanti. "
        "Mantieni uno stile narrativo breve con un solo titolo ## identico. "
        "Fornisci JSON con esattamente due chiavi: "
        '{"rewrite":"## TITOLO\\n\\nparagrafi","evidence":["citazione letterale da FONTE"]}. '
        "La lista evidence deve contenere citazioni LETTERALI continue della "
        "Completa che giustificano tutte le correzioni. "
        "Se non riesci a citare una correzione non introdurla. "
        "Non inserire note o tagli di spiegazione senza riferimento. "
        f"Titolo: {title}\n\n"
        f"Possibili lacune (se non sono gia coperte con altre parole):\n{json.dumps(requested, ensure_ascii=False)}\n\n"
        f"Anomalie linguistiche: {json.dumps(language_notes, ensure_ascii=False)}\n\n"
        "Non modificare il significato scientifico di una frase sospetta "
        "se la fonte la riporta cosi: segnala piuttosto la difficolta. "
        f"COMPLETA VERIFICATA:\n{complete_source[:7800]}\n\n"
        f"ESSENZIALE DA MIGLIORARE:\n{essential[:5000]}"
    )
    try:
        response = summarizer._cached_chat(
            key, system, prompt, num_predict=1800,
        )
        match = re.search(r"\{.*\}", response, flags=re.DOTALL)
        if match is None:
            raise ValueError("Risposta JSON assente")
        payload = json.loads(match.group())
        if not isinstance(payload, dict):
            raise TypeError("Risposta non valida")
        accepted, reason = _validate_proposal(
            title, complete_source, essential, payload,
        )
        if not accepted:
            return ReviewOutcome(title, False, essential, reason)
        rewrite = _normalize_chapter_heading(payload["rewrite"], title)
        return ReviewOutcome(
            title, True, rewrite, "Revisione assistita con prove testuali",
        )
    except (ValueError, TypeError, KeyError) as exc:
        return ReviewOutcome(title, False, essential, str(exc))
