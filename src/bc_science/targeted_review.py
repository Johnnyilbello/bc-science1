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
from .evidence_engine import OriginalEvidence, validate_original_quote
from .italian_proofreading import grammar_findings
from .quality_signals import _lines, _overlap, assess_chapter
from .summarizer import (
    Summarizer,
    _key,
    _normalize_chapter_heading,
    _preserves_exam_numbers,
)

REVIEW_VERSION = "targeted-review-v3-original-grounding"
_NUMBER = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?")


@dataclass(frozen=True, slots=True)
class ReviewOutcome:
    title: str
    applied: bool
    text: str
    reason: str


def _validate_proposal(
    title: str, source: str, previous: str, proposed: dict,
    *, original_evidence: list[OriginalEvidence] | None = None,
    edition: str = "essential",
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
    if original_evidence is not None:
        refs = proposed.get("original_quotes")
        if not isinstance(refs, list) or not refs:
            return False, "Mancano citazioni dalle dispense originali"
        if len(refs) > 8 or not all(
            validate_original_quote(q, original_evidence) for q in refs
        ):
            return False, "Citazioni delle dispense non verificabili"
    headings = re.findall(r"(?m)^##\s+(.+?)\s*$", text)
    if len(headings) != 1 or headings[0].strip() != title:
        return False, "Titolo o struttura alterati"
    if text.strip() == previous.strip():
        return False, "Nessuna correzione necessaria"
    if "### " in text or "In parole semplici" in text:
        return False, "Il riassunto ripete sezioni didattiche"
    if not _preserves_exam_numbers(source, text):
        return False, "Numeri d'esame non preservati"
    allowed_numbers = set(_NUMBER.findall(source))
    if original_evidence is not None:
        for passage in original_evidence:
            allowed_numbers.update(_NUMBER.findall(passage.excerpt))
    if set(_NUMBER.findall(text)) - allowed_numbers:
        return False, "Valore numerico estraneo alle fonti"
    # Claims must be lexically anchored; paraphrase support cannot be
    # fully certified with this check and the report remains unverified.
    source_sents = _lines(source)
    if original_evidence is not None:
        for item in original_evidence:
            source_sents.extend(_lines(item.excerpt))
    sentences = _lines(text)
    if not sentences:
        return False, "Risposta vuota"
    source_length = len(source.split())
    output_length = len(text.split())
    if edition == "complete":
        if output_length < max(5, int(source_length * 0.7)):
            return False, "La Completa perde troppo contenuto"
        if output_length > max(120, int(source_length * 1.3)):
            return False, "La Completa non è più un riassunto"
    elif output_length > max(100, int(source_length * 0.7)):
        return False, "Essenziale troppo lungo"
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
        and not original_evidence
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
    original_evidence: list[OriginalEvidence] | None = None,
    edition: str = "essential",
) -> ReviewOutcome:
    signals = assess_chapter(title, complete_source, essential)
    language = grammar_findings(essential)
    from .structural_checks import find_structural_issues

    structural = find_structural_issues(essential)
    if (
        edition != "complete"
        and signals.review_priority == "nessuna"
        and not language and not structural
    ):
        return ReviewOutcome(title, False, essential, "Nessuna criticita rilevata")
    key = _key(
        REVIEW_VERSION, summarizer.model, title,
        hashlib.sha256(complete_source.encode("utf-8")).hexdigest(),
        hashlib.sha256(essential.encode("utf-8")).hexdigest(),
        json.dumps([
            (e.source_path, e.page, e.block, e.excerpt)
            for e in (original_evidence or [])
        ], ensure_ascii=False),
        edition,
    )
    del config  # Restrict execution to the supplied local Ollama Summarizer.
    requested = [x.quote for x in signals.possible_missing[:5]]
    language_notes = [x.quote + ": " + x.message for x in language[:6]]
    structure_notes = [x.quote for x in structural[:5]]
    original_context = json.dumps([
        {
            "source": e.source_path,
            "page": e.page,
            "block": e.block,
            "text": e.excerpt,
        }
        for e in (original_evidence or [])[:4]
    ], ensure_ascii=False)
    system = (
        "Sei un revisore di riassunti scientifici. "
        "Le fonti fornite sono l'unico riferimento. "
        "Non aggiungere conoscenze esterne, valori inventati o sezioni ripetitive."
    )
    prompt = (
        f"Rivedi la versione {edition} del SOLO capitolo seguente, limitandoti a "
        "correggere concordanze, forme verbali, ortografia, punteggiatura, "
        "periodi spezzati e a includere i concetti cruciali mancanti. "
        "Mantieni uno stile narrativo breve con un solo titolo ## identico. "
        "Per la Completa confronta le affermazioni con gli estratti originali "
        "per individuare anche contraddizioni scientifiche non evidenti nella "
        "grammatica. Correggi soltanto quelle sostenute da citazioni originali. "
        "Conserva tutte le conoscenze necessarie allo studio, senza trasformare "
        "la Completa in una versione Essenziale. "
        "Fornisci un JSON con rewrite, evidence e original_quotes. "
        "original_quotes e una lista di oggetti con source, page, block, quote. "
        "Ogni quote deve essere copiata letteralmente da FONTI_ORIGINALI e "
        "riferita alla sua esatta pagina/blocco. "
        '{"rewrite":"## TITOLO\\n\\nparagrafi",'
        '"evidence":["citazione letterale dalla COMPLETA"],'
        '"original_quotes":[{"source":"path esatto","page":1,"block":null,'
        '"quote":"frase originale continua"}]}. '
        "La lista evidence deve contenere citazioni LETTERALI continue della "
        "Completa che giustificano tutte le correzioni. "
        "Se non riesci a citare una correzione non introdurla. "
        "Non inserire note o tagli di spiegazione senza riferimento. "
        f"Titolo: {title}\n\n"
        f"Possibili lacune (se non sono gia coperte con altre parole):\n{json.dumps(requested, ensure_ascii=False)}\n\n"
        f"Anomalie linguistiche: {json.dumps(language_notes, ensure_ascii=False)}\n\n"
        f"Frammenti o ripetizioni: {json.dumps(structure_notes, ensure_ascii=False)}\n\n"
        f"FONTI_ORIGINALI (solo dispense importate, NON Completa):\n{original_context}\n\n"
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
            original_evidence=original_evidence,
            edition=edition,
        )
        if not accepted:
            return ReviewOutcome(title, False, essential, reason)
        rewrite = _normalize_chapter_heading(payload["rewrite"], title)
        return ReviewOutcome(
            title, True, rewrite, "Revisione assistita con prove testuali",
        )
    except (ValueError, TypeError, KeyError) as exc:
        return ReviewOutcome(title, False, essential, str(exc))
