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


_SUBJECT_WORDS = re.compile(r"[^\W\d_]+", re.UNICODE)
_SUBJECT_IGNORE = frozenset({
    "il", "lo", "la", "l", "un", "uno", "una",
    "questo", "questa", "questi", "queste",
})


def _initial_subject_anchor(text: str) -> str | None:
    """Conservative leading subject anchor; prefer a named entity modifier."""
    terms = _SUBJECT_WORDS.findall(text)
    for index, term in enumerate(terms):
        if term.casefold() in _SUBJECT_IGNORE or len(term) < 4:
            continue
        if index + 1 < len(terms):
            next_term = terms[index + 1]
            # "Il dispositivo Alfa" must not match "Il dispositivo Beta".
            if (
                next_term[:1].isupper()
                and next_term.casefold() not in {"è", "ha", "sono"}
                and len(next_term) >= 4
            ):
                return next_term.casefold()
        return term.casefold()
    return None


def _adjudicate_reported_contradiction(
    claim: CandidateClaim,
    quotes: list[dict],
    chat: Callable[[list[dict[str, str]]], str],
) -> dict:
    """Second-pass, source-quoted logical incompatibility check.

    A citation's existence does not establish logical contradiction.
    Fail closed for invalid responses and citations about other subjects.
    """
    result = {
        "relationship": "UNDECIDABLE",
        "valid": False,
        "reason": "Contraddizione non confermata",
        "diagnostic": "NOT_VALIDATED",
        "claim_span": None,
        "counter_quote": None,
    }
    instruction = (
        "Sei un revisore logico INDIPENDENTE. Verifica se l'affermazione "
        "e la citazione possono essere vere ENTRAMBE nello stesso tempo, "
        "considerando il medesimo soggetto, predicato, condizioni e valori. "
        "ATTENZIONE ALL'AMBITO DI SOLO/ESCLUSIVAMENTE: "
        "'A svolge soltanto X' non significa 'soltanto A svolge X'. "
        "Un'altra entità che svolge anche X non contraddice la prima frase. "
        "La semplice presenza di termini in comune non è una controprova. "
        "Scegli COMPATIBLE se possono essere vere entrambe, "
        "INCOMPATIBLE solo se una contraddice specificamente l'altra, "
        "UNDECIDABLE se le prove non bastano. "
        "Non inventare contesto o citazioni. Rispondi SOLO con JSON: "
        '{"relationship":"COMPATIBLE|INCOMPATIBLE|UNDECIDABLE",'
        '"reason":"motivazione logica sintetica",'
        '"claim_span":"passaggio letterale della frase",'
        '"quote_id":1,"counter_span":"passaggio letterale della citazione"}. '
        "Per COMPATIBLE e UNDECIDABLE, claim_span e counter_span possono "
        "essere stringhe vuote e quote_id puo essere null."
    )
    prompt = {
        "claim": claim.text,
        "cited_passages": [
            {"id": index, "quote": item["quote"]}
            for index, item in enumerate(quotes[:3], 1)
        ],
    }
    try:
        answer = _json_object(chat([
            {"role": "system", "content": instruction},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ]))
        if answer is None:
            return {**result, "diagnostic": "INVALID_JSON"}
        relation = answer.get("relationship")
        reason = answer.get("reason")
        if (
            relation not in {"COMPATIBLE", "INCOMPATIBLE", "UNDECIDABLE"}
            or not isinstance(reason, str)
            or not 8 <= len(reason.strip()) <= 400
        ):
            return {**result, "diagnostic": "INVALID_SCHEMA"}
        if relation != "INCOMPATIBLE":
            return {
                **result, "relationship": relation,
                "reason": reason.strip(), "valid": True,
                "diagnostic": "LOGICAL_REVIEW_COMPLETED",
            }
        index = answer.get("quote_id")
        claim_span = answer.get("claim_span")
        counter_span = answer.get("counter_span")
        if (
            type(index) is not int or not 1 <= index <= len(quotes[:3])
            or not isinstance(claim_span, str)
            or not 7 <= len(claim_span.strip()) <= 250
            or claim_span.casefold() not in claim.text.casefold()
            or not isinstance(counter_span, str)
            or not 16 <= len(counter_span.strip()) <= 500
            or counter_span.casefold() not in quotes[index - 1]["quote"].casefold()
        ):
            return {**result, "diagnostic": "COUNTERPROOF_NOT_LITERAL"}
        # A passage about a different entity must never validate a
        # contradiction merely because its function/predicate overlaps.
        subject = _initial_subject_anchor(claim.text)
        quoted_terms = set(
            _SUBJECT_WORDS.findall(quotes[index - 1]["quote"].casefold())
        )
        if subject is not None and subject not in quoted_terms:
            return {**result, "diagnostic": "CLAIM_SUBJECT_NOT_IN_COUNTERQUOTE"}
        return {
            **result, "relationship": "INCOMPATIBLE", "valid": True,
            "reason": reason.strip(), "diagnostic": "COUNTERPROOF_VALID",
            "claim_span": claim_span, "counter_quote": quotes[index - 1],
        }
    except (ValueError, TypeError, KeyError, IndexError):
        return {**result, "diagnostic": "LOGICAL_REVIEW_INVALID"}


def _challenge_incomplete_support(
    claim: CandidateClaim,
    evidence: list[OriginalEvidence],
    chat: Callable[[list[dict[str, str]]], str],
    missing_anchors: list[str] | None = None,
) -> dict:
    """Second, independent local challenge: cite contrary evidence or abstain."""
    passages = [
        {
            "id": index,
            "text": item.excerpt[:1300],
        }
        for index, item in enumerate(evidence[:5], start=1)
    ]
    prompt = (
        "Esamina questa frase come revisore INDIPENDENTE. "
        "Non dare per vera la frase: controlla ogni relazione, luogo, "
        "quantità, negazione, causa ed eccezione. "
        "Cerca anche prove CONTRARIE. "
        "Una fonte che nomina il soggetto NON conferma automaticamente "
        "tutto ciò che il soggetto farebbe. "
        "Una parafrasi corretta può essere supportata, ma devi citare "
        "il passaggio originale che dimostra il medesimo fatto. "
        "Non inventare numeri, fonti, elementi o citazioni. "
        "Se manca una prova, scrivi UNVERIFIED, NON CONTRADICTED. "
        "Non elencare categorie astratte: nomina soltanto le parti "
        "specifiche della frase che non sono dimostrate. "
        "Per ogni parte supportata, copia in claim_part le parole "
        "esatte della frase e in quote le parole esatte della fonte. "
        "Rispondi SOLTANTO con un oggetto JSON compatto, senza Markdown: "
        '{"assessment":"SUPPORTED|CONTRADICTED|UNVERIFIED",'
        '"missing_parts":[],'
        '"facets":[{"claim_part":"testo dalla frase",'
        '"state":"SUPPORTED|CONTRADICTED|NOT_SHOWN",'
        '"source_id":1,"quote":"citazione della fonte"}],'
        '"counter_quotes":[]}. '
        "CONTRADICTED richiede counter_quotes con source_id e quote. "
        "Massimo 4 facets, ognuna deve rappresentare un fatto distinto. "
        f"\nFRASE: {claim.text}"
        f"\nPAROLE_NON_COPERTE_DALLE_CITAZIONI_PRECEDENTI: "
        f"{json.dumps(missing_anchors or [], ensure_ascii=False)}"
        f"\nFONTI: {json.dumps(passages, ensure_ascii=False)}"
    )
    result = {
        "assessment": "UNVERIFIED",
        "missing_parts": [],
        "counter_quotes": [],
        "facets": [],
        "semantic_support_candidate": False,
        "model_challenge_valid": False,
        "diagnostic": "NOT_EXECUTED",
        "response_chars": 0,
    }
    try:
        response = chat([
            {"role": "system", "content": (
                "Sei un revisore critico indipendente di prove testuali. "
                "Non presumere che l'affermazione sia vera. "
                "Evita conclusioni senza contraddizione testuale pertinente."
            )},
            {"role": "user", "content": prompt},
        ])
        result["response_chars"] = len(response)
        result["diagnostic"] = "RESPONSE_REJECTED"
        obj = _json_object(response)
        if obj is None:
            result["diagnostic"] = "JSON_INVALID"
            return result
        assessment = obj.get("assessment")
        missing_parts = obj.get("missing_parts")
        counter_quotes = obj.get("counter_quotes")
        facets = obj.get("facets", [])
        if (
            assessment not in {"UNVERIFIED", "CONTRADICTED", "SUPPORTED"}
            or not isinstance(missing_parts, list)
            or len(missing_parts) > 8
            or not all(
                isinstance(part, str) and 1 <= len(part.strip()) <= 250
                for part in missing_parts
            )
            or not isinstance(counter_quotes, list)
            or len(counter_quotes) > 3
            or not isinstance(facets, list)
            or len(facets) > 8
        ):
            result["diagnostic"] = "SCHEMA_INVALID"
            return result
        canonical_quotes = []
        for quote in counter_quotes:
            if not isinstance(quote, dict):
                return result
            source_id = quote.get("source_id")
            if (
                type(source_id) is not int
                or not 1 <= source_id <= len(evidence[:5])
            ):
                return result
            passage = evidence[source_id - 1]
            verified = {
                "source": passage.source_path,
                "page": passage.page,
                "block": passage.block,
                "quote": quote.get("quote"),
            }
            if not validate_original_quote(verified, evidence):
                return result
            canonical_quotes.append(verified)
        if assessment == "CONTRADICTED" and not canonical_quotes:
            return result
        validated_facets = []
        for facet in facets:
            if not isinstance(facet, dict):
                return result
            phrase, state = facet.get("claim_part"), facet.get("state")
            if (
                not isinstance(phrase, str)
                or not 5 <= len(phrase) <= 250
                or phrase.casefold() not in claim.text.casefold()
                or state not in {"SUPPORTED", "CONTRADICTED", "NOT_SHOWN"}
            ):
                return result
            checked_quote = None
            if state != "NOT_SHOWN":
                source_id = facet.get("source_id")
                if (
                    type(source_id) is not int
                    or not 1 <= source_id <= len(evidence[:5])
                ):
                    return result
                item = evidence[source_id - 1]
                checked_quote = {
                    "source": item.source_path,
                    "page": item.page,
                    "block": item.block,
                    "quote": facet.get("quote"),
                }
                if not validate_original_quote(checked_quote, evidence):
                    return result
            validated_facets.append({
                "claim_part": phrase, "state": state,
                "source_quote": checked_quote,
            })

        # Never use this as an automatic scientific approval. It is
        # *candidate* semantic coverage for subsequent measurement.
        supported_facets = [
            facet for facet in validated_facets
            if facet["state"] == "SUPPORTED"
        ]
        facet_text = " ".join(facet["claim_part"] for facet in supported_facets)
        material_terms = _proof_words(claim.text)
        facet_terms = _proof_words(facet_text)
        facet_coverage = (
            sum(_matched_proof_token(term, facet_terms) for term in material_terms)
            / len(material_terms)
            if material_terms else 0.0
        )
        missing_numbers = (
            set(_PROOF_NUMBER.findall(claim.text))
            - set(_PROOF_NUMBER.findall(facet_text))
        )
        semantic_candidate = (
            assessment == "SUPPORTED"
            and not missing_parts
            and len(supported_facets) > 0
            and len(supported_facets) == len(validated_facets)
            and facet_coverage >= 0.90
            and not missing_numbers
            and not _DIRECTION_LOOP.search(claim.text)
        )
        return {
            "assessment": assessment,
            "missing_parts": missing_parts,
            "counter_quotes": canonical_quotes,
            "facets": validated_facets,
            "facet_coverage": round(facet_coverage, 3),
            "semantic_support_candidate": semantic_candidate,
            "model_challenge_valid": True,
            "diagnostic": (
                "VALID_WITH_FACETS" if validated_facets
                else "VALID_WITHOUT_FACETS"
            ),
            "response_chars": len(response),
        }
    except (ValueError, TypeError, KeyError, IndexError):
        result["diagnostic"] = "MODEL_RESULT_INVALID"
        return result


def _recover_source_candidates(
    claim: CandidateClaim,
    evidence: list[OriginalEvidence],
    response: dict | None,
) -> list[dict]:
    """Pick original-text candidates by source ID; NEVER assert they prove a fact.

    This is deliberately separate from quote validation. A model's invented
    quotation remains INVALID; a source pointer may only initiate a second
    adversarial logical check of a possible contradiction.
    """
    if not isinstance(response, dict) or not isinstance(response.get("quotes"), list):
        return []
    original_claim_terms = set(_proof_words(claim.text))
    results: list[dict] = []
    seen: set[int] = set()
    for row in response["quotes"][:3]:
        if not isinstance(row, dict):
            continue
        source_id = row.get("source_id")
        if (
            type(source_id) is not int
            or source_id < 1 or source_id > len(evidence[:5])
            or source_id in seen
        ):
            continue
        seen.add(source_id)
        source = evidence[source_id - 1]
        # Sentences are extracted only from a genuine original excerpt.
        # Scoring uses overlap for retrieval only, NOT a truth threshold.
        lines = re.split(r"(?<=[.!?;])\s+|\r?\n+", source.excerpt[:1700])
        candidates = [
            piece.strip() for piece in lines if 16 <= len(piece.strip()) <= 500
        ]
        if not candidates:
            continue
        scored = [
            (
                len(original_claim_terms.intersection(_proof_words(piece))),
                -index,
                piece,
            )
            for index, piece in enumerate(candidates)
        ]
        overlap, _, selected = max(scored)
        if overlap < 2:
            continue
        grounded = {
            "source": source.source_path,
            "page": source.page,
            "block": source.block,
            "quote": selected,
        }
        if validate_original_quote(grounded, evidence):
            results.append(grounded)
    return results


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
    obj: dict | None = None
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
        challenge = None
        adjudication = None
        if label == "CONTRADICTED":
            # The first model can confuse "A is only X" with "only A is X".
            # Quote authenticity alone cannot certify logical incompatibility.
            status = "UNVERIFIED"
            adjudication = _adjudicate_reported_contradiction(
                claim, verified_quotes, chat,
            )
            if (
                adjudication["valid"]
                and adjudication["relationship"] == "INCOMPATIBLE"
            ):
                status = "SUSPECTED_CONTRADICTION"
            else:
                rationale = (
                    "Contraddizione iniziale non confermata "
                    f"({adjudication['diagnostic']}): "
                    + adjudication["reason"] + ". " + rationale
                )
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
            if not accepted or base["review_signals"]:
                status = "UNVERIFIED"
                challenge = _challenge_incomplete_support(
                    claim, evidence, chat, missing,
                )
                critique = ""
                if challenge["model_challenge_valid"]:
                    critique = " Revisione critica: " + "; ".join(
                        challenge["missing_parts"][:3]
                    )
                    if challenge["assessment"] == "CONTRADICTED":
                        status = "SUSPECTED_CONTRADICTION"
                        critique += " (controprova letterale disponibile)"
                    elif challenge["semantic_support_candidate"]:
                        critique += (
                            " (parafrasi plausibile secondo il secondo "
                            "modello, NON approvata automaticamente)"
                        )
                rationale = (
                    "Conferma del modello respinta: le citazioni non "
                    "coprono l'intera affermazione (ancore mancanti: "
                    + ", ".join(missing[:10])
                    + "). " + rationale + critique
                )
        return {
            **base, "status": status, "model_label": label,
            "rationale": rationale[:750], "quotes": verified_quotes,
            "proof_validation": proof, "independent_challenge": challenge,
            "contradiction_adjudication": adjudication,
        }
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        raw_label = obj.get("label") if isinstance(obj, dict) else None
        grounded = _recover_source_candidates(claim, evidence, obj)
        status = "UNVERIFIED"
        check = None
        if raw_label == "CONTRADICTED" and grounded:
            # An inexact quote is NEVER accepted as source evidence.
            # A new, separate logical check must locate an incompatibility
            # in verbatim text selected by the host from the original.
            check = _adjudicate_reported_contradiction(claim, grounded, chat)
            if check["valid"] and check["relationship"] == "INCOMPATIBLE":
                status = "SUSPECTED_CONTRADICTION"
        return {
            **base, "status": status,
            "model_label": "INVALID",
            "raw_model_label": raw_label,
            "rationale": f"Risposta del modello rifiutata: {exc}",
            "source_pointer_candidates": grounded,
            "source_pointer_review": check,
            "source_pointer_is_proof": False,
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
        "semantic_support_candidates": sum(
            bool(
                (outcome.get("independent_challenge") or {})
                .get("semantic_support_candidate")
            )
            for outcome in outcomes
        ),
        "suspected_contradictions": sum(
            outcome["status"] == "SUSPECTED_CONTRADICTION"
            for outcome in outcomes
        ),
        "raw_model_contradictions": sum(
            (
                outcome.get("model_label") == "CONTRADICTED"
                or outcome.get("raw_model_label") == "CONTRADICTED"
            )
            for outcome in outcomes
        ),
        "initial_contradictions_rejected": sum(
            (
                outcome.get("model_label") == "CONTRADICTED"
                or outcome.get("raw_model_label") == "CONTRADICTED"
            )
            and outcome["status"] != "SUSPECTED_CONTRADICTION"
            for outcome in outcomes
        ),
        "source_pointer_candidates": sum(
            bool(outcome.get("source_pointer_candidates"))
            for outcome in outcomes
        ),
        "source_pointer_contradictions_reviewed": sum(
            outcome.get("source_pointer_review") is not None
            for outcome in outcomes
        ),
        "complete_scientific_validation": False,
        "published_modified": False,
        "scope": "Verifica locale sperimentale, limitata e non esaustiva",
    }
