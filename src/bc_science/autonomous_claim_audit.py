"""Generic source-grounded factual review for any subject, using local Ollama.

No fixed scientific error catalogue and no automatic edits. Model judgments are
provisional: exact original-source quotes are validated in Python.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import asdict, dataclass

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
_MULTI_QUANTITY = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?")
_DIRECTION_LOOP = re.compile(
    r"\b(?:da|dal|dallo|dalla|dai|dagli|dalle)\s+"
    r"([a-zà-ÿ]{4,})\s+(?:a|al|allo|alla|ai|agli|alle)\s+\1\b",
    re.IGNORECASE,
)
_STRONG_NEGATION = re.compile(
    r"\b(?:non\s+(?:pu[oò]|possono|è\s+possibile|riesce)|impossibile)\b",
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
        if (
            not 28 <= len(sentence) <= 550
            or sentence.casefold() in seen
            or sentence.casefold() == chapter.casefold()
        ):
            continue
        seen.add(sentence.casefold())
        number_count = len(_MULTI_QUANTITY.findall(sentence))
        # Generic risk ranking only: these patterns do not imply an error.
        if _DIRECTION_LOOP.search(sentence):
            kind, priority = "relation", 6
        elif number_count >= 3:
            kind, priority = "quantity", 5
        elif _STRONG_NEGATION.search(sentence):
            kind, priority = "negation", 5
        elif _NUMBER.search(sentence):
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
    # A purely numeric top-N sample can omit wrong directions/negations.
    # Reserve a slot for each present category before filling by priority.
    selected: list[CandidateClaim] = []
    selected_hashes: set[str] = set()
    for category in ("quantity", "negation", "relation", "other"):
        for _, candidate in ranked:
            if candidate.category == category:
                selected.append(candidate)
                selected_hashes.add(candidate.claim_sha256)
                break
        if len(selected) >= limit:
            return tuple(selected)
    for _, candidate in ranked:
        if candidate.claim_sha256 not in selected_hashes:
            selected.append(candidate)
            selected_hashes.add(candidate.claim_sha256)
        if len(selected) >= limit:
            break
    return tuple(selected)


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


# These are generic Italian function words, NOT subject-specific corrections.
_PROOF_STOPWORDS = frozenset({
    "il", "lo", "la", "i", "gli", "le", "un", "uno",
    "una", "di", "del", "della", "dello", "delle", "dei", "degli",
    "a", "ad", "al", "allo", "alla", "ai", "agli", "alle",
    "da", "dal", "dallo", "dalla", "dai", "dagli", "dalle", "nel",
    "nello", "nella", "nei", "nelle", "con", "per", "su", "tra",
    "fra", "e", "ed", "o", "oppure", "ma", "che", "cui",
    "quando", "come", "se", "si", "sua", "suo", "suoi", "sue",
    "loro", "questo", "questa", "queste", "questi", "quello", "quella", "quelli",
    "quelle", "è", "sono", "essere", "era", "erano", "ha", "hanno",
    "abbia", "negli", "in", "non",
})
_PROOF_WORD = re.compile(r"[a-z]{3,}|\d+(?:[.,]\d+)?")
_PROOF_NUMBER = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?")
_PROOF_NEGATION = re.compile(
    r"\b(?:non|mai|nessun[ao]?|esclusivamente|soltanto|solo)\b",
    re.IGNORECASE,
)


def _proof_words(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", text.casefold())
    normalized = "".join(
        character for character in normalized
        if not unicodedata.combining(character)
    )
    return [
        word for word in _PROOF_WORD.findall(normalized)
        if word not in _PROOF_STOPWORDS
    ]


def _matched_proof_token(term: str, proof_tokens: list[str]) -> bool:
    return any(
        term == other or (
            len(term) >= 6 and len(other) >= 6 and term[:5] == other[:5]
        )
        for other in proof_tokens
    )


def _proof_completeness(
    claim_text: str, quotes: list[dict],
) -> tuple[bool, list[str], float]:
    """Conservative generic guard against partial-evidence confirmations.

    Does not infer truth: it asks whether *material content words, numbers
    and negation* in a claim are represented by its cited passages.
    Unrecognized paraphrases are marked UNVERIFIED, never CONTRADICTED.
    """
    claim_terms = _proof_words(claim_text)
    if not claim_terms:
        return False, [], 0.0
    cited = " ".join(str(quote["quote"]) for quote in quotes)
    cited_terms = _proof_words(cited)
    unsupported = [
        term for term in claim_terms
        if not _matched_proof_token(term, cited_terms)
    ]
    ratio = (len(claim_terms) - len(unsupported)) / len(claim_terms)

    claim_numbers = set(_PROOF_NUMBER.findall(claim_text))
    quote_numbers = set(_PROOF_NUMBER.findall(cited))
    missing_numbers = claim_numbers - quote_numbers
    for term in sorted(missing_numbers):
        if term not in unsupported:
            unsupported.append(term)

    # A statement with polarity/exception cannot be approved if quotes
    # do not even express any comparable restrictive cue.
    claim_has_restriction = bool(_PROOF_NEGATION.search(claim_text))
    evidence_has_restriction = bool(_PROOF_NEGATION.search(cited))
    polarity_missing = claim_has_restriction and not evidence_has_restriction
    accepted = ratio >= 0.65 and not missing_numbers and not polarity_missing
    if polarity_missing:
        unsupported.append("negazione_o_limitazione")
    return accepted, sorted(set(unsupported)), round(ratio, 3)


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
        "review_signals": (
            ["POSSIBLE_DIRECTION_SELF_LOOP"]
            if _DIRECTION_LOOP.search(claim.text) else []
        ),
    }
    if not evidence:
        return base
    excerpts = [
        {"id": index, "source": e.source_path, "page": e.page,
         "block": e.block, "excerpt": e.excerpt[:1700]}
        for index, e in enumerate(evidence[:5], start=1)
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
            '"quotes":[{"source_id":1,'
            '"quote":"testo letterale di almeno 16 caratteri"}]}. '
            "source_id è l'id numerico del passaggio nelle ORIGINALI. "
            "Non ricopiare percorsi o numeri di pagina: li aggiunge il sistema. "
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
        verified_quotes = []
        for quote in quotes:
            if not isinstance(quote, dict):
                raise TypeError("Oggetto citazione non valido")
            if "source_id" in quote:
                source_id = quote["source_id"]
                if (
                    type(source_id) is not int
                    or source_id < 1
                    or source_id > len(evidence[:5])
                ):
                    raise ValueError("Indice citazione non valido")
                passage = evidence[source_id - 1]
                canonical = {
                    "source": passage.source_path,
                    "page": passage.page,
                    "block": passage.block,
                    "quote": quote.get("quote"),
                }
            else:
                # Compatible with earlier complete-provenance model output.
                canonical = quote
            if not validate_original_quote(canonical, evidence):
                raise ValueError("Citazione non letterale o non attribuibile")
            verified_quotes.append(canonical)
        if label in {"SUPPORTED", "CONTRADICTED"} and not verified_quotes:
            raise ValueError("Manca la prova letterale")
        if label == "SOURCE_CONFLICT" and (
            len(verified_quotes) < 2
            or len({
                (q["source"], q["page"], q["block"])
                for q in verified_quotes
            }) < 2
        ):
            raise ValueError("Conflitto senza due passaggi distinti")
        status = {
            "SUPPORTED": "PROVISIONALLY_SUPPORTED",
            "CONTRADICTED": "SUSPECTED_CONTRADICTION",
            "SOURCE_CONFLICT": "SOURCE_CONFLICT",
            "UNVERIFIED": "UNVERIFIED",
        }[label]
        proof = None
        if label == "SUPPORTED":
            accepted, missing, coverage = _proof_completeness(
                claim.text, verified_quotes,
            )
            proof = {
                "anchor_coverage": coverage,
                "unsupported_anchors": missing,
                "all_material_claim_parts_checked": accepted,
                "method": "conservative-lexical-proof-v1",
            }
            if not accepted:
                status = "UNVERIFIED"
                rationale = (
                    "Conferma del modello respinta: le citazioni non "
                    "coprono l'intera affermazione (ancore mancanti: "
                    + ", ".join(missing[:10])
                    + "). " + rationale
                )
        return {
            **base, "status": status, "model_label": label,
            "rationale": rationale[:750], "quotes": verified_quotes,
            "proof_validation": proof,
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
