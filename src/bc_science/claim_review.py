"""Bidirectional source review: omissions alone cannot detect added claims.

The model decides entailment; code validates IDs, full coverage and literal
evidence. This is an additional guard, never a scientific certification.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable

VERDICT_SCHEMA = {
    "type": "array", "items": {
        "type": "object", "properties": {
            "id": {"type": "integer"}, "supported": {"type": "boolean"},
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
        }, "required": ["id", "supported", "evidence_ids"], "additionalProperties": False,
    },
}


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _retains_uncertainty(claim: str, evidence: str, context: str = "") -> bool:
    # "Potenziale d'azione" is a technical name, not epistemic uncertainty.
    possibility = r"\b(?:potenzialmente|potenzial[ei]\s+(?:meccanism|effett|benefic|risch)|potreb\w*|possibil\w*|può|possono)"
    association = r"\bassocia(?:zion\w*|t\w*)"
    causal = r"\b(?:caus\w*|provoc\w*|determin\w*|port\w*\s+a|miglior\w*|aument\w*|riduc\w*|proteg\w*|protettiv\w*)"
    # List continuations such as "Anche la composizione corporea è inclusa"
    # inherit the introductory qualifier. They need not repeat it in every
    # sentence. A causal assertion, however, cannot borrow a distant qualifier.
    qualified = bool(re.search(possibility, claim, re.I))
    strong = bool(re.search(causal, claim, re.I))
    if re.search(possibility, evidence, re.I):
        if not qualified and (strong or not re.search(possibility, context, re.I)):
            return False
    if re.search(association, evidence, re.I) and strong and not re.search(
        association + "|" + possibility, claim, re.I,
    ):
        return False
    return True


def _preserves_direction_and_conditions(claim: str, evidence: str) -> bool:
    # Improvement does not imply an increase (e.g. residual lung volume).
    increasing_claim = r"\b(?:aument[ao]\w*|increment[ao]\w*|cresce|crescono)\b"
    increasing_source = r"\b(?:aument\w*|increment\w*|crescit\w*|cresce|crescono)\b"
    if re.search(increasing_claim, claim, re.I) and not re.search(increasing_source, evidence, re.I):
        return False
    for stem in ("regolar", "sedentar", "quotidian", "costant"):
        if re.search(r"\b" + stem + r"\w*", claim, re.I) and not re.search(r"\b" + stem + r"\w*", evidence, re.I):
            return False
    return True


def review_paragraph(
    paragraph: str, source: str, chat: Callable[[str], str],
    *, evidence_sink: list[str] | None = None,
    unsupported_sink: list[str] | None = None,
) -> tuple[str, int]:
    """Reject incomplete verdicts; replace rejected claims only with source text.

    A response cannot introduce a second generated paraphrase as a correction.
    Missing evidence aborts the diagnostic instead of silently losing content.
    """
    claims = re.split(r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Ý])", paragraph.strip())
    claims = [claim for claim in claims if claim]
    if not claims:
        return paragraph, 0
    source_blocks = {
        f"S{i}": block.strip()
        for i, block in enumerate(re.split(r"\n\s*\n", source), 1) if block.strip()
    }
    prompt = (
        "Valuta OGNI affermazione rispetto alla sola FONTE, senza conoscenze esterne. "
        "La domanda non è se sia plausibile o vera in generale, ma se la fonte la sostiene. "
        "Etichette di una figura NON dimostrano che l'esercizio migliori ogni componente. "
        "Associazione e meccanismi potenziali NON sono cause certe. Non aggiungere "
        "condizioni come sedentarietà, frequenza o regolarità se assenti. Una frase "
        "parzialmente sostenuta è non sostenuta. Per ogni ID rispondi con un oggetto: "
        '{"id":1,"supported":false,"evidence_ids":["S1"]}. '
        "evidence_ids contiene SOLO gli ID S1, S2, ecc. dei passaggi pertinenti che "
        "sostengono la frase oppure correggono il suo significato. Non trascrivere "
        "citazioni. Se non esistono passaggi pertinenti usa []. supported=true "
        "richiede almeno un passaggio che sostenga TUTTE le parti della frase. "
        "Per una frase non sostenuta, scegli comunque i passaggi sullo stesso "
        "argomento che permettono di correggerla senza perdere il contenuto utile. "
        "Preferisci testo nativo e trascrizione visiva al rumore OCR supplementare. "
        "ESEMPI OBBLIGATORI DI VALUTAZIONE:\n"
        "Fonte: 'Efficienza fisica associata alla salute: flessibilità'. Frase: "
        "'L'esercizio aumenta la flessibilità'. supported=false: nella fonte mancano "
        "il verbo aumentare e il legame causale con l'esercizio.\n"
        "Fonte: 'Composizione corporea (rapporto massa magra/massa grassa)'. Frase: "
        "'L'esercizio favorisce un rapporto equilibrato'. supported=false: non si parla "
        "di equilibrio né di un effetto dell'esercizio.\n"
        "Fonte: 'L'attività fisica migliora la capacità vitale'. Frase: "
        "'La capacità vitale migliora con l'attività fisica'. supported=true.\n"
        "Prima del verdetto controlla il predicato e tutte le condizioni, non solo "
        "la presenza del soggetto nella fonte. Un'etichetta senza verbo non dimostra "
        "aumenti, miglioramenti, riduzioni o relazioni causali.\n"
        "Restituisci SOLO un array JSON valido, tutti gli ID una sola volta.\n\n"
        + "AFFERMAZIONI:\n"
        + "\n".join(f"{i}: {claim}" for i, claim in enumerate(claims, 1))
        + "\n\nFONTE:\n" + "\n\n".join(f"{key}: {block}" for key, block in source_blocks.items())
    )
    raw = chat(prompt).strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I)
    try:
        verdicts = json.loads(raw)
        if not isinstance(verdicts, list) or len(verdicts) != len(claims):
            raise ValueError("incomplete verdicts")
        by_id = {}
        source_text = _normalized(source)
        for verdict in verdicts:
            if not isinstance(verdict, dict):
                raise ValueError("invalid verdict")
            claim_id = verdict.get("id")
            supported = verdict.get("supported")
            quotes = verdict.get("quotes")
            if "evidence_ids" in verdict:
                refs = verdict["evidence_ids"]
                if (not isinstance(refs, list)
                        or any(not isinstance(ref, str) or ref not in source_blocks for ref in refs)):
                    raise ValueError("unknown source reference")
                quotes = [source_blocks[ref] for ref in dict.fromkeys(refs)]
            if (type(claim_id) is not int or claim_id not in range(1, len(claims) + 1)
                    or claim_id in by_id or type(supported) is not bool
                    or not isinstance(quotes, list)):
                raise ValueError("invalid claim ID or decision")
            if any(not isinstance(q, str) or not _normalized(q)
                   or _normalized(q) not in source_text for q in quotes):
                raise ValueError("nonliteral evidence")
            if not quotes:
                raise ValueError("no evidence for claim decision")
            by_id[claim_id] = (supported, [_normalized(q) for q in quotes])
    except (ValueError, TypeError) as exc:
        raise ValueError("Controllo delle affermazioni non verificabile: riprovare la generazione.") from exc

    output: list[str] = []
    corrections = 0
    for claim_id, claim in enumerate(claims, 1):
        supported, quotes = by_id[claim_id]
        if evidence_sink is not None:
            evidence_sink.extend(q for q in quotes if q not in evidence_sink)
        supported = supported and _retains_uncertainty(claim, " ".join(quotes), paragraph)
        supported = supported and _preserves_direction_and_conditions(claim, " ".join(quotes))
        if supported:
            output.append(claim)
        else:
            corrections += 1
            if unsupported_sink is not None:
                unsupported_sink.append(claim)
            for quote in quotes:
                cited = f"La dispensa riporta: «{quote}»."
                if cited not in output:
                    output.append(cited)
    return " ".join(output), corrections


def rewrite_reviewed_paragraph(
    paragraph: str, source: str, chat: Callable[[str], str],
    rewrite: Callable[[str], str],
) -> tuple[str, int]:
    """Bounded repair with forward entailment and reverse evidence coverage.

    The candidate must pass both checks. A failed candidate never replaces the
    input silently; callers can preserve the previous published edition.
    """
    evidence: list[str] = []
    _, corrections = review_paragraph(paragraph, source, chat, evidence_sink=evidence)
    if not corrections:
        return paragraph, 0
    required = "\n\n".join(evidence)
    candidate = rewrite(
        "Riscrivi un solo paragrafo didattico usando esclusivamente i FATTI ORIGINALI "
        "qui sotto. Il testo precedente contiene affermazioni non sostenute: non "
        "usarlo come fonte. Spiega in modo semplice, con frasi brevi e progressive. "
        "Conserva TUTTI i fatti, numeri, termini, condizioni e incertezze. "
        "Trasforma etichette in un elenco discorsivo di componenti, non in effetti "
        "causali. Mantieni ipotesi e associazioni come tali. Non usare citazioni, "
        "formule come 'la dispensa riporta', titoli o preamboli. Nessuna lunghezza "
        "obbligatoria: usa lo spazio necessario. Non dedurre che un legame non esista "
        "solo perché la fonte non lo afferma. Non aggiungere benefici, rischi, funzioni "
        "o spiegazioni fisiologiche assenti dai fatti.\n"
        "ESEMPIO: fatti 'Componenti del sangue: plasma, globuli rossi, globuli bianchi, "
        "piastrine'. Riscrittura: 'Il sangue comprende plasma, globuli rossi, globuli "
        "bianchi e piastrine.' Non aggiungere le loro funzioni se assenti.\n"
        "ESEMPIO: fatti 'Potenziali meccanismi dell'associazione: A, B'. Riscrittura: "
        "'A e B sono indicati come possibili meccanismi alla base dell'associazione.' "
        "Non trasformarli in cause dimostrate.\n\nFATTI DA CONSERVARE:\n" + required
        + "\n\nCONTESTO ORIGINALE: usalo per chiarire a cosa si riferiscono i fatti "
        "selezionati, senza riassumere gli altri argomenti.\n" + source
    ).strip()
    if not candidate or candidate.startswith(("#", ">", "```")):
        raise ValueError("Riscrittura didattica assente o non utilizzabile.")
    # Numeric precision is independently enforced, even if a reviewer overlooks it.
    numbers = lambda text: set(re.findall(r"(?<!\w)\d+(?:[.,]\d+)?(?:%|\b)", text))
    if not numbers(required) <= numbers(candidate) <= numbers(source):
        raise ValueError("La riscrittura modifica o omette valori numerici della fonte.")
    unsupported_claims: list[str] = []
    _, unsupported = review_paragraph(candidate, source, chat, unsupported_sink=unsupported_claims)
    if unsupported:
        raise ValueError("La riscrittura aggiunge affermazioni non sostenute: " + unsupported_claims[0])
    # Reverse the comparison: every selected source fact must remain expressed.
    for fact in evidence:
        _, omitted = review_paragraph(fact, candidate, chat)
        if omitted:
            raise ValueError("La riscrittura omette un fatto originale da conservare.")
    return candidate, corrections
