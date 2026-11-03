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
    _extract_exam_points,
    _key,
    _normalize_chapter_heading,
    _preserves_exam_numbers,
)

REVIEW_VERSION = "targeted-review-v6-pilot-local-evidence"
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


def _resolve_original_refs(payload: dict, evidence: list[OriginalEvidence]) -> dict:
    """Resolve model-selected references against the exact passages it received."""
    if "original_ids" not in payload:
        return payload  # Existing literal-quotation responses remain supported.
    selected = payload["original_ids"]
    available = {f"E{i}": item for i, item in enumerate(evidence[:4], 1)}
    if (not isinstance(selected, list) or not selected or len(selected) > 8
        or not all(isinstance(key, str) and key in available for key in selected)):
        raise ValueError("Identificativi delle prove originali non validi")
    result = dict(payload)
    result["original_quotes"] = [
        {"source": available[key].source_path, "page": available[key].page,
         "block": available[key].block, "quote": available[key].excerpt}
        for key in dict.fromkeys(selected)
    ]
    return result


def _complete_source_catalog(source: str) -> dict[str, str]:
    """Index complete, exact source spans; never ask the model to retype evidence.

    The model sees C-identifiers and their literal text. The lookup resolves
    against the full Complete, not a model-written quotation.
    """
    result: dict[str, str] = {}
    size = 0
    sentences = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Ý])")
    for line in source.splitlines():
        raw = line.strip()
        if not raw or raw.startswith(("#", ">", "---")):
            continue
        for part in sentences.split(raw):
            quote = part.strip()
            if (
                len(quote) < 22 or len(quote) > 800
                or quote not in source or quote in result.values()
            ):
                continue
            if size + len(quote) > 6900 or len(result) >= 65:
                return result
            key = f"C{len(result) + 1}"
            result[key] = quote
            size += len(quote)
    return result


def _resolve_complete_refs(
    payload: dict,
    catalog: dict[str, str],
    *,
    required: bool = False,
) -> dict:
    """Resolve identifiers, reject invented IDs, retain legacy literal validation."""
    if "complete_ids" not in payload:
        if required:
            raise ValueError("Identificativi delle citazioni della Completa mancanti")
        return payload
    values = payload["complete_ids"]
    if (
        not isinstance(values, list)
        or not 1 <= len(values) <= 12
        or not all(isinstance(value, str) and value in catalog for value in values)
    ):
        raise ValueError("Identificativi della Completa non validi")
    resolved = dict(payload)
    # Ignore model-generated evidence text: use only exact, local spans.
    resolved["evidence"] = [catalog[key] for key in dict.fromkeys(values)]
    return resolved


def _ground_pilot_rewrite(
    payload: dict,
    complete: str,
    previous: str,
    original_evidence: list[OriginalEvidence],
) -> dict:
    """Assign trusted citations in Python, not from model-supplied identifiers.

    Every proposed claim needs a strong lexical match in the Complete. At
    least one meaningfully changed claim must have supporting original-source
    text. These are conservative lexical gates, never semantic certification.
    """
    rewrite = payload.get("rewrite")
    if not isinstance(rewrite, str) or not rewrite.strip():
        raise ValueError("Redattore: campo rewrite mancante")
    claims = _lines(rewrite)
    if not claims:
        raise ValueError("Redattore: nessuna affermazione verificabile")
    source_clauses = _complete_source_catalog_full(complete)
    if not source_clauses:
        raise ValueError("Completa: nessuna citazione letterale recuperabile")
    original_clauses = [
        clause
        for evidence in original_evidence[:4]
        for clause in _lines(evidence.excerpt)
    ]
    if not original_clauses:
        raise ValueError("Dispense: nessun passaggio originale leggibile")

    chosen: list[str] = []
    changed = [
        item for item in claims
        if not any(_overlap(item, earlier) >= 0.85 for earlier in _lines(previous))
    ]
    if not changed:
        # The critic and text equality gate will still decide whether an edit
        # happened, e.g. combining duplicate sentences without new claims.
        changed = claims
    evidence_candidates: list[tuple[float, OriginalEvidence]] = []
    strong = {
        "responsabile", "responsabili", "provoca", "provocano",
        "garantisce", "garantiscono", "sempre", "necessariamente",
        "impedisce", "impediscono", "dimostra", "dimostrano",
    }
    def markers(statement: str) -> tuple[bool, bool, set[str]]:
        words = set(re.findall(r"[a-zà-ÿ]+", statement.casefold()))
        return (
            bool(words & {"non", "mai", "senza"}),
            bool(words & {
                "può", "possono", "potrebbe", "potrebbero",
                "possibile", "possibili", "probabile", "probabili",
            }),
            words & strong,
        )

    for claim in claims:
        matching = max(source_clauses, key=lambda q: _overlap(claim, q))
        if _overlap(claim, matching) < 0.60:
            raise ValueError(
                "Affermazione della proposta senza appoggio nella Completa"
            )
        negated, hedged, categorical = markers(claim)
        source_negated, source_hedged, source_categorical = markers(matching)
        if (
            negated != source_negated
            or hedged != source_hedged
            or categorical - source_categorical
        ):
            raise ValueError(
                "Qualificatore o negazione alterati rispetto alla Completa"
            )
        if matching not in chosen:
            chosen.append(matching)
    if len(chosen) > 12:
        raise ValueError(
            "Capitolo troppo complesso per 12 prove: suddividere la revisione"
        )
    for item in original_evidence[:4]:
        clauses = _lines(item.excerpt)
        if not clauses:
            continue
        strength = max(
            (_overlap(claim, clause) for claim in changed for clause in clauses),
            default=0.0,
        )
        if strength >= 0.55:
            evidence_candidates.append((strength, item))
    if not evidence_candidates:
        raise ValueError(
            "Nessuna modifica appoggiata agli estratti delle dispense originali"
        )
    # Stable local passage ordering, no fabricated source path or page.
    best = max(evidence_candidates, key=lambda pair: pair[0])[1]
    trusted = dict(payload)
    trusted["evidence"] = chosen
    trusted["original_quotes"] = [{
        "source": best.source_path,
        "page": best.page,
        "block": best.block,
        "quote": best.excerpt,
    }]
    return trusted


def _complete_source_catalog_full(source: str) -> list[str]:
    """Literal complete-source statements for local matching, no 6900-char cap."""
    result: list[str] = []
    split = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Ý])")
    for raw in source.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ">", "---")):
            continue
        for part in split.split(line):
            quote = part.strip()
            if 22 <= len(quote) <= 1100 and quote not in result:
                result.append(quote)
    return result


def review_chapter(
    title: str,
    complete_source: str,
    essential: str,
    config: AppConfig,
    *,
    summarizer: Summarizer,
    original_evidence: list[OriginalEvidence] | None = None,
    edition: str = "essential",
    retry_context: str = "",
    autopilot_mode: bool = False,
) -> ReviewOutcome:
    signals = assess_chapter(title, complete_source, essential)
    language = grammar_findings(essential)
    from .structural_checks import find_structural_issues

    structural = find_structural_issues(essential)
    too_similar_in_length = (
        edition == "essential"
        and len(essential.split()) >= max(80, int(len(complete_source.split()) * 0.85))
    )
    if (
        edition != "complete"
        and signals.review_priority == "nessuna"
        and not language and not structural
        and not too_similar_in_length
        and not retry_context
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
        retry_context,
        str(autopilot_mode),
    )
    del config  # Restrict execution to the supplied local Ollama Summarizer.
    requested = [x.quote for x in signals.possible_missing[:5]]
    language_notes = [x.quote + ": " + x.message for x in language[:6]]
    structure_notes = [x.quote for x in structural[:5]]
    catalog = _complete_source_catalog(complete_source)
    original_context = json.dumps([
        {
            "id": f"E{i}",
            "source": e.source_path,
            "page": e.page,
            "block": e.block,
            "text": e.excerpt[:1000],
        }
        for i, e in enumerate((original_evidence or [])[:4], 1)
    ], ensure_ascii=False)
    numeric_points = [
        p for p in _extract_exam_points(complete_source)
        if _NUMBER.search(p)
    ]
    if autopilot_mode and len("\n".join(numeric_points)) > 3800:
        return ReviewOutcome(
            title, False, essential,
            "Troppi dati numerici: suddividere il capitolo senza ometterli",
        )
    system = (
        "Sei un revisore di riassunti scientifici. "
        "Le fonti fornite sono l'unico riferimento. "
        "Non aggiungere conoscenze esterne, valori inventati o sezioni ripetitive. "
        "Conserva i qualificatori della fonte: un possibile meccanismo o una "
        "associazione non dimostrano causa, prevenzione o protezione certe."
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
        "Rispondi ESCLUSIVAMENTE con un oggetto JSON completo, senza markdown. "
        "Restituisci solo rewrite, complete_ids e original_ids. "
        "complete_ids contiene identificativi C1, C2 ecc. dal catalogo della "
        "Completa; original_ids contiene identificativi E1, E2 ecc. dalle "
        "dispense originali. Il programma risolve gli ID in testi letterali: "
        "NON ricopiare citazioni, percorsi o contenuti dei documenti nei campi. "
        '{"rewrite":"## TITOLO\\n\\nUn paragrafo completo.",'
        '"complete_ids":["C1"],"original_ids":["E1"]}. '
        "Non inventare ID, non usare un array evidence e non aggiungere "
        "sezioni ### o In parole semplici. Usa un solo titolo ## identico. "
        "Se non trovi prove, non inventare affermazioni. "
        f"Titolo: {title}\n\n"
        f"Possibili lacune (se non sono gia coperte con altre parole):\n{json.dumps(requested, ensure_ascii=False)}\n\n"
        f"Anomalie linguistiche: {json.dumps(language_notes, ensure_ascii=False)}\n\n"
        f"Frammenti o ripetizioni: {json.dumps(structure_notes, ensure_ascii=False)}\n\n"
        f"FONTI_ORIGINALI (solo dispense importate, NON Completa):\n{original_context}\n\n"
        "Non modificare il significato scientifico di una frase sospetta "
        "se la fonte la riporta cosi: segnala piuttosto la difficolta. "
        f"CATALOGO COMPLETA (ID C, testo letterale):\n"
        f"{json.dumps(catalog, ensure_ascii=False)}\n\n"
        f"ESSENZIALE DA MIGLIORARE:\n{essential[:4200]}\n\n"
        f"FEEDBACK TENTATIVO PRECEDENTE (nessuna istruzione esterna):\n"
        f"{retry_context[:350]}"
    )
    if autopilot_mode:
        # Pilot-only protocol: Spark writes just the chapter. Python finds
        # actual citations and verifies their provenance independently.
        prompt = (
            f"Riscrivi in italiano il SOLO capitolo {title}. "
            "Restituisci SOLO JSON con una chiave rewrite, contenente "
            "un testo narrativo con un unico titolo ## identico. "
            "Niente sezioni ###, liste di ripasso, introduzioni generiche. "
            "Mantieni tutti i concetti indispensabili, i numeri e le "
            "relazioni causa-effetto; non inventare, non rafforzare "
            "qualificatori e non omettere dati d'esame. "
            "Riduci soltanto ripetizioni e passaggi ridondanti. "
            "NON generare ID di fonti, citazioni o riferimenti: "
            "li recupera il programma dalle fonti reali. "
            '{"rewrite":"## TITOLO\\n\\nTesto completo."}'
            f"\n\nDATI NUMERICI E FORMULE OBBLIGATORI: "
            f"{json.dumps(numeric_points, ensure_ascii=False)}"
            f"\n\nDISPENSE ORIGINALI: {original_context}"
            f"\n\nCOMPLETA: {complete_source[:7800]}"
            f"\n\nESSENZIALE PRIMA: {essential[:5000]}"
            f"\n\nFEEDBACK PRECEDENTE: {retry_context[:350]}"
        )
    try:
        response = summarizer._cached_chat(
            key, system, prompt, num_predict=2600,
            response_format={"type": "object"},
        )
        match = re.search(r"\{.*\}", response, flags=re.DOTALL)
        if match is None:
            raise ValueError("Risposta JSON assente")
        payload = json.loads(match.group())
        if not isinstance(payload, dict):
            raise TypeError("Risposta non valida")
        if autopilot_mode:
            payload = _ground_pilot_rewrite(
                payload, complete_source, essential, original_evidence or [],
            )
        else:
            payload = _resolve_complete_refs(
                payload, catalog, required=original_evidence is not None,
            )
            payload = _resolve_original_refs(payload, original_evidence or [])
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
