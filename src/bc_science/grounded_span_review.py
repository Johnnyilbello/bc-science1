"""Experimental original-span-first verification; models return IDs, not quotes.

Each offered span is selected by Python from an original SHA-pinned
evidence excerpt. A selected ID proves provenance only, NEVER truth.
Scientific approval is conservative; no automatic edits or publication.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable

from .autonomous_claim_audit import (
    CandidateClaim,
    _adjudicate_reported_contradiction,
    _json_object,
    _proof_completeness,
    _proof_words,
)
from .evidence_engine import OriginalEvidence, validate_original_quote

_SPLIT = re.compile(r"(?<=[.!?;])\s+|\r?\n+")
_DIGIT = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?")
_LABELS = frozenset({"SUPPORTED", "CONTRADICTED", "UNVERIFIED"})


def evidence_spans(
    claim: CandidateClaim,
    evidence: list[OriginalEvidence],
    *,
    limit: int = 8,
) -> list[dict]:
    """Select small verbatim, diverse citations from the original excerpts."""
    if not 1 <= limit <= 15:
        raise ValueError("Limite passaggi non valido")
    claim_terms = set(_proof_words(claim.text))
    claim_numbers = set(_DIGIT.findall(claim.text))
    ranked: list[tuple[int, int, int, dict]] = []
    seen: set[tuple[str, int | None, int | None, str]] = set()
    for source_index, source in enumerate(evidence[:5]):
        pieces = _SPLIT.split(source.excerpt[:1700])
        for position, raw in enumerate(pieces):
            piece = raw.strip()
            if not 16 <= len(piece) <= 480:
                continue
            key = (source.source_path, source.page, source.block, piece.casefold())
            if key in seen:
                continue
            seen.add(key)
            exact = {
                "source": source.source_path,
                "page": source.page,
                "block": source.block,
                "quote": piece,
            }
            if not validate_original_quote(exact, evidence):
                continue
            words = set(_proof_words(piece))
            overlap = len(words & claim_terms)
            number_overlap = len(_DIGIT.findall(piece) and (
                claim_numbers & set(_DIGIT.findall(piece))
            ))
            if overlap < 2 and number_overlap < 1:
                continue
            score = overlap + number_overlap * 2
            ranked.append((-score, source_index, position, exact))
    ranked.sort(key=lambda row: row[:3])
    chosen: list[tuple[int, int, int, dict]] = []
    used_sources: set[int] = set()
    # Reserve one high-ranked excerpt per original source before filling.
    for row in ranked:
        if row[1] not in used_sources:
            chosen.append(row)
            used_sources.add(row[1])
            if len(chosen) == limit:
                break
    chosen_keys = {(row[1], row[2]) for row in chosen}
    for row in ranked:
        if len(chosen) >= limit:
            break
        if (row[1], row[2]) not in chosen_keys:
            chosen.append(row)
            chosen_keys.add((row[1], row[2]))
    return [
        {"id": index, **row[3]}
        for index, row in enumerate(chosen, start=1)
    ]


def review_with_source_ids(
    claim: CandidateClaim,
    evidence: list[OriginalEvidence],
    chat: Callable[[list[dict[str, str]]], str],
) -> dict:
    """Separate source selection from judging whether the source proves it."""
    base = {
        "claim": claim.to_dict(), "status": "UNVERIFIED",
        "model_label": "UNVERIFIED", "rationale": "Non verificato",
        "quotes": [], "diagnostic": "NO_RELEVANT_SOURCE_SPANS",
        "evidence_spans_count": 0,
        "used_span_ids": [], "independent_logical_review": None,
        "verified_scientifically": False,
        "review_method": "original-span-ids-v1",
    }
    spans = evidence_spans(claim, evidence)
    base["evidence_spans_count"] = len(spans)
    if not spans:
        return base
    messages = [
        {"role": "system", "content": (
            "Verifica la FRASE rispetto ai passaggi ORIGINALI numerati. "
            "Scegli SUPPORTED soltanto se i passaggi dimostrano TUTTE "
            "le componenti, inclusi soggetto, predicato, direzione, "
            "numeri, unità, causa e negazioni. "
            "Scegli CONTRADICTED solo con una vera incompatibilità "
            "riguardante il medesimo soggetto/condizioni. "
            "A svolge esclusivamente X NON significa che solo A svolge X. "
            "Se manca la prova scegli UNVERIFIED. "
            "Non inventare fonti o dati. NON copiare citazioni, "
            "scegli solo ID presenti. Rispondi SOLO JSON."
        )},
        {"role": "user", "content": (
            'Formato JSON: {"label":"SUPPORTED|CONTRADICTED|UNVERIFIED",'
            '"source_ids":[1],"reason":"motivazione specifica"}. '
            "UNVERIFIED richiede source_ids [] se nessuna fonte è pertinente. "
            "Per SUPPORTED/CONTRADICTED indicare 1-3 ID "
            "dei passaggi che provano l'intera affermazione o la negano. "
            f"\nFRASE: {claim.text}\nFONTI: "
            f"{json.dumps([
                {'id': row['id'], 'text': row['quote']}
                for row in spans
            ], ensure_ascii=False)}"
        )},
    ]
    try:
        response = _json_object(chat(messages))
    except (ValueError, TypeError, KeyError, IndexError):
        return {**base, "diagnostic": "MODEL_CALL_INVALID"}
    if response is None:
        return {**base, "diagnostic": "MODEL_JSON_INVALID"}
    label = response.get("label")
    reason = response.get("reason")
    ids = response.get("source_ids")
    if (
        label not in _LABELS or not isinstance(reason, str)
        or not 4 <= len(reason.strip()) <= 700
        or not isinstance(ids, list) or len(ids) > 3
        or any(type(i) is not int or i < 1 or i > len(spans) for i in ids)
        or len(set(ids)) != len(ids)
    ):
        return {**base, "diagnostic": "MODEL_SCHEMA_INVALID"}
    chosen = [spans[i - 1] for i in ids]
    quotes = [
        {k: row[k] for k in ("source", "page", "block", "quote")}
        for row in chosen
    ]
    details = {
        **base, "model_label": label,
        "rationale": reason.strip(), "quotes": quotes,
        "used_span_ids": ids,
    }
    if label == "UNVERIFIED":
        return {**details, "diagnostic": "VALID_ABSTENTION"}
    if not quotes:
        return {**details, "diagnostic": "MISSING_EVIDENCE_ID"}
    if label == "CONTRADICTED":
        logical = _adjudicate_reported_contradiction(claim, quotes, chat)
        status = (
            "SUSPECTED_CONTRADICTION"
            if logical["valid"] and logical["relationship"] == "INCOMPATIBLE"
            else "UNVERIFIED"
        )
        return {
            **details, "status": status, "independent_logical_review": logical,
            "diagnostic": "LOGICAL_INCOMPATIBILITY_REVIEWED",
        }
    # IDs cannot certify a semantic relationship. The lexical check is only
    # a conservative filter; the study source itself may also be inaccurate.
    complete, missing, coverage = _proof_completeness(claim.text, quotes)
    if re.search(r"\b(?:da|dal|dallo|dalla)\s+(\w+)\s+(?:a|al|allo|alla)\s+\1\b",
                 claim.text, re.IGNORECASE):
        complete = False
        missing.append("possible_circular_direction")
    return {
        **details,
        "status": "PROVISIONALLY_SUPPORTED" if complete else "UNVERIFIED",
        "diagnostic": (
            "TEXTUAL_SUPPORT_PROVISIONAL" if complete else "PARTIAL_EVIDENCE"
        ),
        "proof_validation": {
            "anchor_coverage": coverage,
            "unsupported_anchors": sorted(set(missing)),
            "all_material_claim_parts_checked": complete,
        },
    }
