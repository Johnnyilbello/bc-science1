"""Source-span IDs eliminate model quotation copying without proving correctness."""
from __future__ import annotations

import json

from bc_science.autonomous_claim_audit import extract_claim_candidates, run_claim_audit
from bc_science.evidence_engine import OriginalEvidence, validate_original_quote
from bc_science.grounded_span_review import evidence_spans, review_with_source_ids


def _doc(title: str, claim: str) -> str:
    return (
        "# Materia\n\n## Indice degli argomenti\n"
        "- " + title + "\n\n---\n\n## " + title + "\n\n" + claim + "\n"
    )


def _source(text: str) -> OriginalEvidence:
    return OriginalEvidence(
        source_path="source.pdf", page=6, block=None,
        excerpt=text, rank=1, provenance="cached_page",
    )


def _claim(title: str, text: str):
    return extract_claim_candidates(_doc(title, text), title)[0]


def test_source_spans_are_verbatim_even_when_model_cannot_quote():
    claim = _claim("Geografia", "Il traghetto va da Genova a Genova.")
    original = _source(
        "Il traghetto parte da Genova e raggiunge Livorno. "
        "Il traghetto trasporta auto e passeggeri."
    )
    spans = evidence_spans(claim, [original])
    assert len(spans) == 1
    assert spans[0]["id"] == 1
    assert "Il traghetto parte" in spans[0]["quote"]
    assert all(validate_original_quote(row, [original]) for row in spans)
    assert all(row["source"] == "source.pdf" for row in spans)


def test_source_span_model_cannot_invent_quote_and_must_pass_logical_review():
    claim = _claim("Geografia", "Il traghetto va da Genova a Genova.")
    evidence = [_source("Il traghetto parte da Genova e raggiunge Livorno.")]
    calls = []

    def local(messages):
        calls.append(messages)
        if len(calls) == 1:
            assert '"source_ids"' in messages[-1]["content"]
            assert '"source_id":' not in messages[-1]["content"]
            return json.dumps({
                "label": "CONTRADICTED",
                "reason": "Il traghetto arriva a Livorno, non a Genova.",
                "source_ids": [1],
            })
        return json.dumps({
            "relationship": "INCOMPATIBLE",
            "reason": "La destinazione del traghetto è diversa dalla frase.",
            "quote_id": 1,
            "claim_span": "va da Genova a Genova",
            "counter_span": "Il traghetto parte da Genova e raggiunge Livorno",
        })

    result = review_with_source_ids(claim, evidence, local)
    assert len(calls) == 2
    assert result["status"] == "SUSPECTED_CONTRADICTION"
    assert result["verified_scientifically"] is False
    assert result["used_span_ids"] == [1]
    assert result["independent_logical_review"]["valid"] is True
    assert result["quotes"][0]["quote"].startswith("Il traghetto parte")
    assert result["evidence_spans_count"] == 1


def test_false_logical_contradiction_is_rejected():
    claim = _claim(
        "Tecnologia", "Il dispositivo Alfa è dedicato esclusivamente alla lettura.",
    )
    evidence = [_source("Il dispositivo Beta legge e scrive documenti digitali.")]
    calls = []

    def local(_messages):
        calls.append(1)
        return json.dumps({
            "label": "CONTRADICTED",
            "reason": "Anche il dispositivo Beta sa leggere.",
            "source_ids": [1],
        })

    result = review_with_source_ids(claim, evidence, local)
    assert result["status"] == "UNVERIFIED"
    # No relevant spans or incompatibility should be invented.
    assert len(calls) <= 2
    assert result["verified_scientifically"] is False


def test_invalid_span_ids_are_rejected_with_no_second_call():
    claim = _claim("Chimica", "Il metallo fonde a circa 130 gradi.")
    evidence = [_source("Il metallo fonde a circa 200 gradi.")]
    calls = []

    def local(_messages):
        calls.append(1)
        return json.dumps({
            "label": "CONTRADICTED",
            "reason": "Le temperature sono diverse.",
            "source_ids": [99],
        })

    result = review_with_source_ids(claim, evidence, local)
    assert len(calls) == 1
    assert result["diagnostic"] == "MODEL_SCHEMA_INVALID"
    assert result["status"] == "UNVERIFIED"


def test_supported_with_partial_evidence_does_not_get_approved():
    claim = _claim(
        "Storia",
        "La nave partì da Napoli verso Genova nel 1456.",
    )
    evidence = [_source("La nave partì da Napoli per commercio marittimo.")]
    result = review_with_source_ids(
        claim, evidence,
        lambda _messages: json.dumps({
            "label": "SUPPORTED",
            "reason": "La fonte menziona la nave e la partenza.",
            "source_ids": [1],
        }),
    )
    assert result["status"] == "UNVERIFIED"
    assert result["diagnostic"] == "PARTIAL_EVIDENCE"
    assert result["proof_validation"]["all_material_claim_parts_checked"] is False


def test_spans_mode_reports_no_publication_and_preserves_original():
    text = _doc("Geografia", "Il fiume attraversa la città in estate.")
    old = text[:]
    evidence = [_source("Il fiume attraversa la città in estate.")]
    result = run_claim_audit(
        text, "Geografia", max_claims=2,
        find_evidence=lambda _q: evidence,
        chat=lambda _messages: json.dumps({
            "label": "SUPPORTED",
            "reason": "Descrizione riportata nella dispensa.",
            "source_ids": [1],
        }),
        evidence_mode="spans",
    )
    assert result["evidence_mode"] == "spans"
    assert result["published_modified"] is False
    assert result["complete_scientific_validation"] is False
    assert result["claims_total"] == 1
    assert text == old


def test_long_unpunctuated_pdf_excerpt_still_offers_verbatim_spans():
    claim = _claim("Biologia", "La proteina Alfa contiene 200 amminoacidi.")
    raw = (
        "discorso generico sulle varie strutture molecolari " * 18
        + "la proteina Alfa contiene 200 amminoacidi "
        + "descrizione conclusiva della molecola "
    )
    original = _source(raw)
    spans = evidence_spans(claim, [original])
    assert spans
    assert any("200 amminoacidi" in row["quote"] for row in spans)
    assert all(validate_original_quote(row, [original]) for row in spans)


def test_v8_pdf_continuation_stays_in_same_original_span():
    claim = _claim(
        "Fisiologia",
        "Il volume residuo VR è pari a circa 1200 mL e non può essere "
        "misurato con lo spirometro.",
    )
    # The relevant clause straddles legacy 420-character fixed slices.
    original = _source(
        "materiale didattico sulla ventilazione e sui volumi " * 13
        + "contenuto qui "
        + "Il volume residuo VR è pari a circa 1200 mL, "
        + "che però non può essere misurato con lo spirometro. "
        + "Si considerano poi le altre capacità respiratorie."
    )
    spans = evidence_spans(claim, [original])
    assert spans
    assert any(
        "1200 mL" in row["quote"]
        and "spirometro" in row["quote"]
        and "non può essere misurato" in row["quote"]
        for row in spans
    )
    assert all(validate_original_quote(row, [original]) for row in spans)
    assert all(len(row["quote"]) <= 640 for row in spans)


def test_v8_abstention_exposes_unapproved_offered_spans():
    claim = _claim(
        "Fisiologia",
        "La capacità polmonare dipende dal contesto respiratorio.",
    )
    evidence = [_source(
        "La capacità polmonare comprende diversi volumi respiratori."
    )]
    verdict = review_with_source_ids(
        claim, evidence,
        lambda _messages: json.dumps({
            "label": "UNVERIFIED",
            "source_ids": [],
            "reason": "La relazione richiesta non è dimostrata.",
        }),
    )
    assert verdict["status"] == "UNVERIFIED"
    assert verdict["diagnostic"] == "VALID_ABSTENTION"
    assert verdict["quotes"] == []
    assert len(verdict["span_candidates"]) == verdict["evidence_spans_count"]
    assert verdict["span_candidates"]
    assert all(
        validate_original_quote(row, evidence)
        for row in verdict["span_candidates"]
    )
    assert verdict["verified_scientifically"] is False


def test_v8_offered_spans_cannot_approve_missing_large_quantities():
    claim = _claim(
        "Fisiologia",
        "Il volume corrente VT oscilla fra 2200 e 2700 mL "
        "ma a riposo equivale a 500 mL.",
    )
    evidence = [_source(
        "Il volume corrente VT a riposo è circa 500 mL per atto respiratorio."
    )]
    verdict = review_with_source_ids(
        claim, evidence,
        lambda _messages: json.dumps({
            "label": "SUPPORTED",
            "source_ids": [1],
            "reason": "Il valore di riposo è menzionato dalla fonte.",
        }),
    )
    assert verdict["status"] == "UNVERIFIED"
    assert verdict["diagnostic"] == "PARTIAL_EVIDENCE"
    assert {"2200", "2700"}.issubset(
        set(verdict["proof_validation"]["unsupported_anchors"])
    )
    assert verdict["verified_scientifically"] is False


def test_v82_support_requires_second_pass():
    claim = _claim("Fisica", "Il corpo Alfa misura 200 centimetri.")
    evidence = [_source("Il corpo Alfa misura 200 centimetri.")]
    calls = []

    def chat(_messages):
        calls.append(1)
        if len(calls) == 1:
            return json.dumps({
                "label": "SUPPORTED", "source_ids": [1],
                "reason": "La fonte indica soggetto e misura.",
            })
        return json.dumps({
            "assessment": "PARTIAL",
            "reason": "La prova potrebbe riferirsi a una condizione differente.",
            "missing_parts": ["condizione di misura"],
            "evidence_ids": [1],
        })

    verdict = review_with_source_ids(claim, evidence, chat)
    assert len(calls) == 2
    assert verdict["status"] == "UNVERIFIED"
    assert verdict["diagnostic"] == "SECOND_PASS_NOT_CONFIRMED"
    assert verdict["proof_validation"]["lexical_coverage_passed"] is True
    assert verdict["support_challenge"]["assessment"] == "PARTIAL"


def test_v82_complete_second_pass_is_only_provisional():
    claim = _claim("Fisica", "Il corpo Alfa misura 200 centimetri.")
    evidence = [_source("Il corpo Alfa misura 200 centimetri.")]
    calls = []

    def chat(_messages):
        calls.append(1)
        if len(calls) == 1:
            return json.dumps({
                "label": "SUPPORTED", "source_ids": [1],
                "reason": "La fonte indica soggetto e misura.",
            })
        return json.dumps({
            "assessment": "COMPLETE",
            "reason": "La citazione copre soggetto, misura e unita.",
            "missing_parts": [], "evidence_ids": [1],
        })

    verdict = review_with_source_ids(claim, evidence, chat)
    assert len(calls) == 2
    assert verdict["status"] == "PROVISIONALLY_SUPPORTED"
    assert verdict["diagnostic"] == "SECOND_PASS_SUPPORT_PROVISIONAL"
    assert verdict["support_challenge"]["valid"] is True
    assert verdict["verified_scientifically"] is False


def test_v82_supported_reason_with_explicit_doubt_is_blocked():
    claim = _claim("Fisica", "Il corpo Alfa misura 200 centimetri.")
    evidence = [_source("Il corpo Alfa misura 200 centimetri.")]
    calls = []

    def chat(_messages):
        calls.append(1)
        return json.dumps({
            "label": "SUPPORTED", "source_ids": [1],
            "reason": (
                "Le prove non contraddicono il dato, tuttavia "
                "necessiterebbe di un'analisi piu approfondita."
            ),
        })

    verdict = review_with_source_ids(claim, evidence, chat)
    assert len(calls) == 1
    assert verdict["status"] == "UNVERIFIED"
    assert verdict["diagnostic"] == "MODEL_REASON_INCONSISTENT"


def test_v82_schema_errors_explain_invalid_source_id():
    claim = _claim("Fisica", "Il corpo Alfa misura 200 centimetri.")
    evidence = [_source("Il corpo Alfa misura 200 centimetri.")]
    verdict = review_with_source_ids(
        claim, evidence, lambda _messages: json.dumps({
            "label": "SUPPORTED",
            "reason": "La fonte indica soggetto e misura.",
            "source_ids": [99],
        }),
    )
    assert verdict["status"] == "UNVERIFIED"
    assert verdict["diagnostic"] == "MODEL_SCHEMA_INVALID"
    assert "INVALID_SOURCE_ID" in verdict["model_schema_errors"]
    assert "source_ids" in verdict["model_response_fields"]


def test_v82_invalid_second_pass_fails_closed():
    claim = _claim("Fisica", "Il corpo Alfa misura 200 centimetri.")
    evidence = [_source("Il corpo Alfa misura 200 centimetri.")]
    calls = []

    def chat(_messages):
        calls.append(1)
        if len(calls) == 1:
            return json.dumps({
                "label": "SUPPORTED", "source_ids": [1],
                "reason": "La fonte indica soggetto e misura.",
            })
        return "invalid json"

    verdict = review_with_source_ids(claim, evidence, chat)
    assert len(calls) == 2
    assert verdict["status"] == "UNVERIFIED"
    assert verdict["support_challenge"]["diagnostic"] == "CHALLENGE_JSON_INVALID"


def test_v83_schema_retry_recovers_only_format_and_still_requires_proof():
    claim = _claim("Fisica", "Il corpo Alfa misura 200 centimetri.")
    evidence = [_source("Il corpo Alfa misura 200 centimetri.")]
    calls = []

    def chat(_messages):
        calls.append(1)
        if len(calls) == 1:
            return json.dumps({
                "label": "SUPPORTED", "source_ids": [1], "reason": "",
            })
        if len(calls) == 2:
            return json.dumps({
                "label": "SUPPORTED", "source_ids": [1],
                "reason": "La fonte indica soggetto e misura.",
            })
        return json.dumps({
            "assessment": "PARTIAL",
            "reason": "La relazione tra soggetto e misura non e sufficiente.",
            "missing_parts": ["contesto di misurazione"],
            "evidence_ids": [1],
        })

    verdict = review_with_source_ids(claim, evidence, chat)
    assert len(calls) == 3
    assert verdict["status"] == "UNVERIFIED"
    assert verdict["diagnostic"] == "SECOND_PASS_NOT_CONFIRMED"
    assert verdict["schema_retry"]["attempted"]
    assert verdict["schema_retry"]["recovered"]
    assert verdict["schema_retry"]["initial_errors"] == ["INVALID_REASON"]


def test_v83_too_many_source_ids_can_retry_without_truncating():
    claim = _claim("Fisica", "Il metallo Alfa fonde a 200 gradi.")
    original = _source(
        "Il metallo Alfa fonde a 200 gradi. "
        "Il metallo Alfa fonde a 220 gradi. "
        "Il metallo Alfa fonde a 230 gradi. "
        "Il metallo Alfa fonde a 240 gradi."
    )
    assert len(evidence_spans(claim, [original])) >= 4
    calls = []

    def chat(_messages):
        calls.append(1)
        return json.dumps({
            "label": "UNVERIFIED",
            "source_ids": [1, 2, 3, 4] if len(calls) == 1 else [],
            "reason": "La prova originale non consente una conclusione.",
        })

    verdict = review_with_source_ids(claim, [original], chat)
    assert len(calls) == 2
    assert verdict["status"] == "UNVERIFIED"
    assert verdict["diagnostic"] == "VALID_ABSTENTION"
    assert verdict["schema_retry"]["recovered"] is True
    assert "TOO_MANY_SOURCE_IDS" in verdict["schema_retry"]["initial_errors"]


def test_v83_failed_retry_keeps_invalid_schema_and_no_auto_approval():
    claim = _claim("Fisica", "Il corpo Alfa misura 200 centimetri.")
    evidence = [_source("Il corpo Alfa misura 200 centimetri.")]
    calls = []

    def chat(_messages):
        calls.append(1)
        return json.dumps({
            "label": "SUPPORTED", "source_ids": [1], "reason": "",
        })

    verdict = review_with_source_ids(claim, evidence, chat)
    assert len(calls) == 2
    assert verdict["status"] == "UNVERIFIED"
    assert verdict["diagnostic"] == "MODEL_SCHEMA_INVALID"
    assert verdict["schema_retry"]["attempted"]
    assert verdict["schema_retry"]["recovered"] is False
    assert "INVALID_REASON" in verdict["schema_retry"]["retry_errors"]
