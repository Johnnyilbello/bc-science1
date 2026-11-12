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
    _matched_proof_token,
)
from .evidence_engine import OriginalEvidence, validate_original_quote

_SPLIT = re.compile(r"(?<=[.!?;])\s+|\r?\n+")
_DIGIT = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?")
_LABELS = frozenset({"SUPPORTED", "CONTRADICTED", "UNVERIFIED"})
_TRAILING_CONNECTOR = re.compile(
    r"\b(?:con|di|da|dal|dallo|della|al|alla|a|il|lo|la|un|una|che|per|e|ma)\s*$",
    re.IGNORECASE,
)
_AFTER_CONDITION = re.compile(
    r"\bdopo\s+un(?:a)?\s+(normale|massimale)\s+([^\W\d_]{4,})\b",
    re.IGNORECASE,
)
_TIME_CONDITION = re.compile(
    r"\b(?:in|entro)\s+un(?:a)?\s+(secondo|minuto|ora|giorno|mese|anno)\b",
    re.IGNORECASE,
)
_MEASUREMENT_OBJECT = re.compile(
    r"\bmisur\w*\s+(?:dal|dallo|dalla|con\s+(?:lo|la|il|l['’]))\s+"
    r"([^\W\d_]{4,})\b",
    re.IGNORECASE,
)
_DENIED_UNSUPPORTED_PARTS = re.compile(
    r"\bnon\s+(?:sono|risultano)\s+(?:presenti|indicate)\s+"
    r"(?:parti|componenti|affermazioni)\s+non\s+(?:dimostrate|supportate)\b",
    re.IGNORECASE,
)


def _unresolved_evidence_doubt(reason: str) -> bool:
    """Distinguish an admission of missing evidence from its explicit denial.

    Not a truth check: an overconfident explanation still needs source gates.
    """
    cleaned = _DENIED_UNSUPPORTED_PARTS.sub("nessuna lacuna dichiarata", reason)
    return bool(_EXPLICIT_EVIDENCE_DOUBT.search(cleaned))


def _material_condition_gaps(claim_text: str, quotes: list[dict]) -> list[str]:
    """Fail closed on a few explicit, verifiable claim qualifiers.

    Evidence absence is not contradiction. These rules are intentionally
    generic and narrow; scientific equivalence remains a model/human task.
    """
    cited = " ".join(str(item["quote"]) for item in quotes).casefold()
    claim = claim_text.casefold()
    gaps: list[str] = []
    for match in _AFTER_CONDITION.finditer(claim):
        qualifier, noun = match.groups()
        adjacency = (
            rf"\b(?:{re.escape(qualifier)}\s+{re.escape(noun)}"
            rf"|{re.escape(noun)}\s+{re.escape(qualifier)})\b"
        )
        if not re.search(adjacency, cited):
            gaps.append("condizione_dopo:" + qualifier + "_" + noun)
    for match in _TIME_CONDITION.finditer(claim):
        unit = match.group(1)
        if not re.search(r"\b" + re.escape(unit) + r"\b", cited):
            gaps.append("condizione_temporale:" + unit)
    cited_terms = _proof_words(cited)
    for match in _MEASUREMENT_OBJECT.finditer(claim):
        instrument = match.group(1)
        if not _matched_proof_token(instrument, cited_terms):
            gaps.append("strumento_misura:" + instrument)
    return sorted(set(gaps))




def _context_windows(raw: str, *, width: int = 640, stride: int = 270) -> list[str]:
    """Overlapping original-text slices, avoiding cuts through words.

    PDF extraction may remove punctuation entirely. Multiple overlapping
    windows keep the continuation of a scientific relation available.
    Never add or change source words: all output is checked against the
    hash-pinned original excerpt before it can be offered to a model.
    """
    if len(raw) <= width:
        return [raw.strip()] if raw.strip() else []
    windows: list[str] = []
    for offset in range(0, len(raw), stride):
        start = max(0, offset - 90)
        stop = min(len(raw), start + width)
        if start > 0:
            boundary = re.search(r"\s", raw[start:min(len(raw), start + 70)])
            if boundary:
                start += boundary.end()
        if stop < len(raw):
            boundary = raw.rfind(" ", max(start + 400, stop - 70), stop)
            if boundary > start:
                stop = boundary
        piece = raw[start:stop].strip()
        if len(piece) >= 16 and (not windows or piece != windows[-1]):
            windows.append(piece)
        if stop >= len(raw):
            break
    return windows


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
        original = source.excerpt[:2200]
        raw_sentences = [
            sentence.strip() for sentence in _SPLIT.split(original)
            if sentence.strip()
        ]
        pieces: list[str] = []
        for sentence_index, raw in enumerate(raw_sentences):
            if len(raw) <= 640:
                pieces.append(raw)
                # Longer extracted pages often break a related fact over
                # adjacent fragments. Preserve both as one contextual span.
                if len(original) > 640 and sentence_index + 1 < len(raw_sentences):
                    joined = raw + " " + raw_sentences[sentence_index + 1]
                    if len(joined) <= 640:
                        pieces.append(joined)
            else:
                pieces.extend(_context_windows(raw))
        for position, piece in enumerate(pieces):
            if not 16 <= len(piece) <= 640:
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
            number_overlap = len(claim_numbers & set(_DIGIT.findall(piece)))
            if overlap < 2 and number_overlap < 1:
                continue
            # Do not rank a clause ending in "con la" above a complete
            # citation just because it repeats more claim keywords.
            score = overlap + number_overlap * 2
            if _TRAILING_CONNECTOR.search(piece):
                score -= 10
            elif re.search(r"[.!?;]$", piece):
                score += 1
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


_EXPLICIT_EVIDENCE_DOUBT = re.compile(
    r"\b(?:non\s+(?:bast\w*|suffic\w*|dimostr\w*|conferm\w*|prov\w*)"
    r"|(?:necessit\w*|richied\w*|serv\w*).{0,90}(?:analisi|verific\w*|approfondiment\w*)"
    r"|(?:prove|evidenze)\s+insufficienti)\b",
    re.IGNORECASE,
)


def _model_schema_issues(response: dict, span_count: int) -> list[str]:
    """Expose precise failure classes without trusting malformed model fields."""
    issues: list[str] = []
    if not isinstance(response.get("label"), str) or response["label"] not in _LABELS:
        issues.append("INVALID_LABEL")
    reason = response.get("reason")
    if not isinstance(reason, str) or not 4 <= len(reason.strip()) <= 700:
        issues.append("INVALID_REASON")
    ids = response.get("source_ids")
    if not isinstance(ids, list):
        issues.append("SOURCE_IDS_NOT_ARRAY")
    else:
        if len(ids) > 3:
            issues.append("TOO_MANY_SOURCE_IDS")
        if any(type(i) is not int or not 1 <= i <= span_count for i in ids):
            issues.append("INVALID_SOURCE_ID")
        if any(type(i) is not int for i in ids):
            issues.append("NON_INTEGER_SOURCE_ID")
        if len({str(i) for i in ids}) != len(ids):
            issues.append("DUPLICATE_SOURCE_IDS")
    return issues


def _challenge_supported_claim(
    claim: CandidateClaim,
    quotes: list[dict],
    chat: Callable[[list[dict[str, str]]], str],
) -> dict:
    """A second, fail-closed local semantic check; never scientific approval.

    This uses the same model, so it is not independent scientific evidence.
    It can reject incomplete relations that a lexical score overlooks.
    """
    result = {
        "assessment": "UNVERIFIED", "valid": False,
        "diagnostic": "CHALLENGE_NOT_VALIDATED",
        "reason": "", "missing_parts": [], "evidence_ids": [],
    }
    messages = [
        {"role": "system", "content": (
            "Sei un secondo revisore severo, NON devi confermare per concordanza "
            "di parole o numeri. Controlla TUTTE le componenti della frase: "
            "soggetto, relazione, direzione, grandezza, unità, condizioni, "
            "tempi, negazioni, cause ed eccezioni. "
            "Una fonte può parlare di un fatto diverso pur usando gli stessi "
            "numeri: in tal caso scegli PARTIAL. "
            "Se la frase introduce un vincolo non presente nelle fonti scegli "
            "PARTIAL e indica esattamente quale parte manca. "
            "Completa solo se ogni proposizione è dimostrata dalle fonti "
            "NUMERATE fornite. Nessuna conoscenza esterna. SOLO JSON."
        )},
        {"role": "user", "content": (
            'Formato JSON: {"assessment":"COMPLETE|PARTIAL|UNVERIFIED",'
            '"reason":"spiegazione specifica",'
            '"missing_parts":["frase mancante"],"evidence_ids":[1]}. '
            "Per COMPLETE: missing_parts [] e 1-3 ID delle prove "
            "che sostengono TUTTA la frase. "
            "Per PARTIAL o UNVERIFIED elenca le parti non dimostrate; "
            "evidence_ids [] è consentito. "
            f"\nFRASE: {claim.text}\nCITAZIONI: "
            + json.dumps(
                [{"id": index, "text": quote["quote"]}
                 for index, quote in enumerate(quotes, 1)],
                ensure_ascii=False,
            )
        )},
    ]
    try:
        obj = _json_object(chat(messages))
    except (ValueError, TypeError, KeyError, IndexError):
        return {**result, "diagnostic": "CHALLENGE_CALL_INVALID"}
    if obj is None:
        return {**result, "diagnostic": "CHALLENGE_JSON_INVALID"}
    assessment = obj.get("assessment")
    reason = obj.get("reason")
    missing = obj.get("missing_parts")
    ids = obj.get("evidence_ids")
    if (
        assessment not in {"COMPLETE", "PARTIAL", "UNVERIFIED"}
        or not isinstance(reason, str)
        or not 8 <= len(reason.strip()) <= 700
        or not isinstance(missing, list) or len(missing) > 8
        or any(not isinstance(part, str) or not 1 <= len(part.strip()) <= 250
               for part in missing)
        or not isinstance(ids, list) or len(ids) > 3
        or any(type(i) is not int or not 1 <= i <= len(quotes) for i in ids)
        or len(set(ids)) != len(ids)
    ):
        return {**result, "diagnostic": "CHALLENGE_SCHEMA_INVALID"}
    valid = (
        assessment == "COMPLETE" and len(missing) == 0 and len(ids) > 0
        and not _unresolved_evidence_doubt(reason)
    )
    return {
        "assessment": assessment, "valid": valid,
        "reason": reason.strip(), "missing_parts": missing,
        "evidence_ids": ids,
        "diagnostic": (
            "CHALLENGE_COMPLETE_PROVISIONAL" if valid
            else "CHALLENGE_INCOMPLETE"
        ),
    }


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
        "review_method": "original-span-ids-v2-context",
        "span_candidates": [],
    }
    spans = evidence_spans(claim, evidence)
    base["evidence_spans_count"] = len(spans)
    # Preserve exactly the passages shown to the model even on abstention.
    # They remain unapproved candidates, not proof of a scientific claim.
    base["span_candidates"] = [dict(item) for item in spans]
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
            + json.dumps(
                [{"id": row["id"], "text": row["quote"]} for row in spans],
                ensure_ascii=False,
            )
        )},
    ]
    try:
        response = _json_object(chat(messages))
    except (ValueError, TypeError, KeyError, IndexError):
        return {**base, "diagnostic": "MODEL_CALL_INVALID"}
    if response is None:
        return {**base, "diagnostic": "MODEL_JSON_INVALID"}
    schema_issues = _model_schema_issues(response, len(spans))
    first_schema_issues = list(schema_issues)
    schema_retry: dict | None = None
    # Only formatting errors are recoverable. Never repair a fabricated or
    # out-of-range source ID by clipping it to a valid one.
    recoverable = {"INVALID_REASON", "TOO_MANY_SOURCE_IDS"}
    if schema_issues and set(schema_issues) <= recoverable:
        retry_messages = [
            {"role": "system", "content": (
                "Correggi soltanto il FORMATO della verifica. Riesamina la "
                "frase e le stesse fonti; non dare automaticamente ragione "
                "alla precedente risposta. Rispondi con un solo JSON valido, "
                "senza Markdown. reason: 1-2 frasi, massimo 250 caratteri. "
                "source_ids: lista di massimo 3 ID distinti presenti, "
                "non creare ID. Scegli UNVERIFIED se le prove non bastano."
            )},
            {"role": "user", "content": (
                'JSON: {"label":"SUPPORTED|CONTRADICTED|UNVERIFIED",'
                '"source_ids":[1],"reason":"spiegazione breve"}. '
                f"Errori del primo tentativo: {', '.join(schema_issues)}. "
                f"FRASE: {claim.text}\nFONTI: "
                + json.dumps(
                    [{"id": row["id"], "text": row["quote"]} for row in spans],
                    ensure_ascii=False,
                )
            )},
        ]
        retry: dict | None = None
        try:
            retry = _json_object(chat(retry_messages))
        except (ValueError, TypeError, KeyError, IndexError):
            pass
        retry_issues = (
            _model_schema_issues(retry, len(spans))
            if retry is not None else ["RETRY_JSON_INVALID"]
        )
        schema_retry = {
            "attempted": True,
            "recovered": not retry_issues,
            "initial_errors": first_schema_issues,
            "retry_errors": retry_issues,
        }
        if not retry_issues:
            response = retry
            schema_issues = []
    if schema_issues:
        return {
            **base, "diagnostic": "MODEL_SCHEMA_INVALID",
            "model_schema_errors": schema_issues,
            "model_response_fields": sorted(str(key) for key in response),
            "schema_retry": schema_retry,
        }
    label = response.get("label")
    reason = response.get("reason")
    ids = response.get("source_ids")
    chosen = [spans[i - 1] for i in ids]
    quotes = [
        {k: row[k] for k in ("source", "page", "block", "quote")}
        for row in chosen
    ]
    details = {
        **base, "model_label": label,
        "rationale": reason.strip(), "quotes": quotes,
        "used_span_ids": ids,
        "schema_retry": schema_retry,
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
    condition_gaps = _material_condition_gaps(claim.text, quotes)
    if condition_gaps:
        complete = False
        missing.extend(condition_gaps)
    reason_doubt = _unresolved_evidence_doubt(reason)
    challenge = (
        _challenge_supported_claim(claim, quotes, chat)
        if complete and not reason_doubt else None
    )
    accepted = (
        complete and not reason_doubt
        and challenge is not None and challenge["valid"]
    )
    diagnostic = (
        "PARTIAL_EVIDENCE" if not complete
        else "MODEL_REASON_INCONSISTENT" if reason_doubt
        else "SECOND_PASS_SUPPORT_PROVISIONAL" if accepted
        else "SECOND_PASS_NOT_CONFIRMED"
    )
    return {
        **details,
        "status": "PROVISIONALLY_SUPPORTED" if accepted else "UNVERIFIED",
        "diagnostic": diagnostic,
        "support_challenge": challenge,
        "proof_validation": {
            "anchor_coverage": coverage,
            "unsupported_anchors": sorted(set(missing)),
            "lexical_coverage_passed": complete,
            "all_material_claim_parts_checked": accepted,
            "material_condition_gaps": condition_gaps,
        },
    }
