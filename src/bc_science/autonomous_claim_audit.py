"""Generic source-grounded factual review for any subject, using local Ollama.

No fixed scientific error catalogue and no automatic edits. Model judgments are
provisional: exact original-source quotes are validated in Python.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Callable

from .evidence_engine import OriginalEvidence, validate_original_quote
from .quality_signals import _lines
from .summarizer import _extract_summary_chapters

AUDIT_VERSION = "autonomous-claim-audit-v1"
_NUMBER = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?")
_NEGATION = re.compile(r"\b(?:non|mai|nessun[ao]?|impossibile|esclusivamente)\b", re.IGNORECASE)
_RELATION = re.compile(
    r"\b(?:trasporta|misur\w*|produce|richiede|converte|secerne|permette|"
    r"determina|causa|avviene|collega|consiste|compost\w*|format\w*|è|sono)\b",
    re.IGNORECASE,
)
_VALID = {"SUPPORTED", "CONTRADICTED", "SOURCE_CONFLICT", "UNVERIFIED"}


@dataclass(frozen=True, slots=True)
class CandidateClaim:
    chapter: str
    text: str
    category: str
    priority: int
    claim_sha256: str

    def to_dict(self) -> dict:
        return asdict(self)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extract_claim_candidates(
    document: str, chapter: str, *, limit: int = 8,
) -> tuple[CandidateClaim, ...]:
    """Find risky statements without hardcoding answers from any discipline."""
    if not 1 <= limit <= 30:
        raise ValueError("limit deve essere compreso tra 1 e 30")
    chapters = _extract_summary_chapters(document)
    matches = [body for name, body in chapters if name.casefold() == chapter.casefold()]
    if len(matches) != 1:
        raise ValueError(f"Capitolo assente o ambiguo: {chapter}")

    candidates: list[CandidateClaim] = []
    seen: set[str] = set()
    for sentence in _lines(matches[0]):
        sentence = re.sub(r"\s+", " ", sentence).strip()
        if not 28 <= len(sentence) <= 550 or sentence.casefold() in seen:
            continue
        seen.add(sentence.casefold())
        if _NUMBER.search(sentence):
            kind, priority = "quantity", 4
        elif _NEGATION.search(sentence):
            kind, priority = "negation", 3
        elif _RELATION.search(sentence):
            kind, priority = "relation", 2
        else:
            kind, priority = "other", 1
        candidates.append(CandidateClaim(
            chapter, sentence, kind, priority, _digest(sentence),
        ))
    ranked = sorted(enumerate(candidates), key=lambda x: (-x[1].priority, x[0]))
    return tuple(claim for _, claim in ranked[:limit])


def _json_object(raw: str) -> dict | None:
    value = raw.strip()
    fence = chr(96) * 3
    if value.startswith(fence):
        value = re.sub(r"^" + fence + r"(?:json)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*" + fence + r"$", "", value)
    try:
        obj = json.loads(value)
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def verify_claim_with_evidence(
    claim: CandidateClaim,
    evidence: list[OriginalEvidence],
    chat: Callable[[list[dict[str, str]]], str],
) -> dict:
    """Return a provisional label; fail-closed if quotes are not literal."""
    base = {
        "claim": claim.to_dict(),
        "evidence": [e.to_dict() for e in evidence[:5]],
        "status": "UNVERIFIED",
        "model_label": "UNVERIFIED",
        "rationale": "Nessuna prova originale pertinente o verificabile",
        "quotes": [],
        "limitations": "Giudizio locale fallibile, non validazione scientifica",
    }
    if not evidence:
        return base
    excerpts = [
        {"source": e.source_path, "page": e.page, "block": e.block,
         "excerpt": e.excerpt[:1700]}
        for e in evidence[:5]
    ]
    messages = [
        {"role": "system", "content": (
            "Sei un revisore fattuale per ogni materia. Confronta SOLTANTO "
            "l'affermazione con estratti di dispense ORIGINALI. Non usare "
            "il riassunto stesso come prova. Controlla soggetti, direzione, "
            "negazioni, quantità, unità e causalità. Parole simili non bastano. "
            "Se le fonti sono insufficienti scegli UNVERIFIED; se si "
            "contraddicono scegli SOURCE_CONFLICT. Non correggere nulla. "
            "Cita solo stringhe letterali dalle fonti fornite."
        )},
        {"role": "user", "content": (
            "Confronta l'affermazione con le prove. Rispondi SOLO JSON: "
            '{"label":"SUPPORTED|CONTRADICTED|SOURCE_CONFLICT|UNVERIFIED",'
            '"rationale":"motivazione breve in italiano",'
            '"quotes":[{"source":"percorso esatto", "page":null,'
            '"block":1, "quote":"testo letterale di almeno 16 caratteri"}]}. '
            "SUPPORTED e CONTRADICTED esigono almeno una citazione "
            "pertinente; SOURCE_CONFLICT due passaggi diversi. "
            "NON giudicare una formula corretta solo perché i numeri "
            "appaiono nel testo senza collegamenti espliciti. "
            "Se non sei sicuro: UNVERIFIED e quotes []. "
            f"\nCAPITOLO: {claim.chapter}\nAFFERMAZIONE: {claim.text}\n"
            f"ORIGINALI: {json.dumps(excerpts, ensure_ascii=False)}"
        )},
    ]
    try:
        obj = _json_object(chat(messages))
        if obj is None:
            raise ValueError("JSON non interpretabile")
        label, rationale, quotes = (
            obj.get("label"), obj.get("rationale"), obj.get("quotes"),
        )
        if label not in _VALID or not isinstance(rationale, str):
            raise ValueError("Etichetta o motivazione non valida")
        if not isinstance(quotes, list) or len(quotes) > 3:
            raise ValueError("Citazioni malformate")
        if not all(validate_original_quote(q, evidence) for q in quotes):
            raise ValueError("Citazioni non letterali o non attribuibili")
        if label in {"SUPPORTED", "CONTRADICTED"} and not quotes:
            raise ValueError("Manca la prova letterale")
        if label == "SOURCE_CONFLICT" and (
            len(quotes) < 2
            or len({(q["source"], q["page"], q["block"]) for q in quotes}) < 2
        ):
            raise ValueError("Conflitto senza due passaggi distinti")
        status = {
            "SUPPORTED": "PROVISIONALLY_SUPPORTED",
            "CONTRADICTED": "SUSPECTED_CONTRADICTION",
            "SOURCE_CONFLICT": "SOURCE_CONFLICT",
            "UNVERIFIED": "UNVERIFIED",
        }[label]
        return {
            **base, "status": status, "model_label": label,
            "rationale": rationale[:750], "quotes": quotes,
        }
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        return {
            **base, "model_label": "INVALID",
            "rationale": f"Risposta del modello rifiutata: {exc}",
        }


def run_claim_audit(
    document: str,
    chapter: str,
    *,
    max_claims: int,
    find_evidence: Callable[[str], list[OriginalEvidence]],
    chat: Callable[[list[dict[str, str]]], str],
    progress: Callable[[int, int, str], None] | None = None,
) -> dict:
    """Small read-only pilot; no implicit publication or self-certification."""
    claims = extract_claim_candidates(document, chapter, limit=max_claims)
    outcomes = []
    for index, claim in enumerate(claims, 1):
        try:
            evidence = find_evidence(claim.text)
        except (ValueError, OSError):
            evidence = []
        verdict = verify_claim_with_evidence(claim, evidence, chat)
        outcomes.append(verdict)
        if progress is not None:
            progress(index, len(claims), verdict["status"])
    return {
        "schema": AUDIT_VERSION,
        "chapter": chapter,
        "document_sha256": _digest(document),
        "claims_total": len(claims),
        "claims": outcomes,
        "complete_scientific_validation": False,
        "published_modified": False,
        "scope": "Verifica locale sperimentale, limitata e non esaustiva",
    }
