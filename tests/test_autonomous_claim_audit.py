"""Generic claim-verification mechanics, not a claim of LLM scientific accuracy."""
from __future__ import annotations

import json

import pytest

from bc_science.autonomous_claim_audit import (
    extract_claim_candidates,
    run_claim_audit,
    verify_claim_with_evidence,
)
from bc_science.evidence_engine import OriginalEvidence


def _course(title: str, text: str) -> str:
    return (
        "# Materia\n\n## Indice degli argomenti\n"
        "- " + title + "\n\n---\n\n## " + title + "\n\n" + text
    )


def _source(text: str, *, name: str = "source.pdf", page: int = 2):
    return OriginalEvidence(
        source_path=name,
        page=page,
        block=None,
        excerpt=text,
        provenance="cached_page",
        rank=1,
    )


def test_candidate_extraction_is_independent_of_four_hardcoded_errors():
    content = _course(
        "Fisica generale",
        "L'acqua pura bolle a 80 gradi Celsius alla pressione di 1 atmosfera. "
        "Il magnete esercita una forza su alcuni materiali ferromagnetici. "
        "Non tutte le forze hanno uguale direzione.",
    )
    candidates = extract_claim_candidates(content, "Fisica generale", limit=8)
    assert len(candidates) == 3
    assert candidates[0].category == "quantity"
    assert any("80 gradi Celsius" in c.text for c in candidates)
    assert all(len(c.claim_sha256) == 64 for c in candidates)


def test_provisional_contradiction_needs_real_source_quote():
    claim = extract_claim_candidates(_course(
        "Fisica", "L'acqua pura bolle a 80 gradi Celsius a 1 atmosfera."
    ), "Fisica")[0]
    ev = _source(
        "Alla pressione atmosferica standard l'acqua pura bolle "
        "a circa 100 gradi Celsius.",
    )
    model = lambda _messages: json.dumps({
        "label": "CONTRADICTED",
        "rationale": "80 e 100 gradi non coincidono nello stesso contesto",
        "quotes": [{
            "source": ev.source_path,
            "page": ev.page,
            "block": ev.block,
            "quote": "l'acqua pura bolle a circa 100 gradi Celsius",
        }],
    })
    result = verify_claim_with_evidence(claim, [ev], model)
    assert result["status"] == "SUSPECTED_CONTRADICTION"
    assert result["quotes"]
    assert result["claim"]["chapter"] == "Fisica"


def test_hallucinated_quote_is_refused_not_approved():
    claim = extract_claim_candidates(
        _course("Storia", "La battaglia si concluse nell'anno 1540."),
        "Storia",
    )[0]
    ev = _source("La battaglia si concluse nell'anno 1541.")
    response = json.dumps({
        "label": "CONTRADICTED", "rationale": "Anno errato",
        "quotes": [{
            "source": ev.source_path, "page": ev.page, "block": ev.block,
            "quote": "La battaglia si concluse nell'anno 1540.",
        }],
    })
    verdict = verify_claim_with_evidence(claim, [ev], lambda _m: response)
    assert verdict["status"] == "UNVERIFIED"
    assert verdict["model_label"] == "INVALID"


def test_supported_label_is_provisional_and_source_can_be_wrong():
    claim = extract_claim_candidates(
        _course("Biologia", "L'esempio attribuisce 14 ATP alla sintesi del palmitato."),
        "Biologia",
    )[0]
    ev = _source("Nel testo di lezione si riportano 14 ATP per la sintesi del palmitato.")
    result = verify_claim_with_evidence(
        claim, [ev],
        lambda _m: json.dumps({
            "label": "SUPPORTED", "rationale": "Presente nella dispensa",
            "quotes": [{
                "source": ev.source_path, "page": ev.page, "block": ev.block,
                "quote": "14 ATP per la sintesi del palmitato",
            }],
        }),
    )
    assert result["status"] == "PROVISIONALLY_SUPPORTED"
    assert result["limitations"].startswith("Giudizio locale fallibile")


def test_source_conflict_requires_two_verifiable_distinct_passages():
    claim = extract_claim_candidates(
        _course("Biologia", "Le riduzioni richiedono NADPH nella sintesi dei lipidi."),
        "Biologia",
    )[0]
    ev1 = _source("La sintesi degli acidi grassi richiede NADPH per le riduzioni.")
    ev2 = _source(
        "La seconda riduzione usa NADH come donatore di elettroni.",
        name="other.pdf",
    )
    response = lambda _messages: json.dumps({
        "label": "SOURCE_CONFLICT", "rationale": "NADPH oppure NADH",
        "quotes": [
            {"source": ev1.source_path, "page": ev1.page, "block": ev1.block,
             "quote": "La sintesi degli acidi grassi richiede NADPH"},
            {"source": ev2.source_path, "page": ev2.page, "block": ev2.block,
             "quote": "La seconda riduzione usa NADH"},
        ],
    })
    verdict = verify_claim_with_evidence(claim, [ev1, ev2], response)
    assert verdict["status"] == "SOURCE_CONFLICT"


def test_no_evidence_does_not_call_model_or_claim_success():
    content = _course("Diritto", "Il diritto si divide in due rami fondamentali.")
    calls = []
    result = run_claim_audit(
        content, "Diritto", max_claims=8,
        find_evidence=lambda _claim: [],
        chat=lambda m: calls.append(m) or '{"label":"SUPPORTED"}',
    )
    assert calls == []
    assert result["claims_total"] == 1
    assert result["claims"][0]["status"] == "UNVERIFIED"
    assert result["complete_scientific_validation"] is False
    assert result["published_modified"] is False


def test_invalid_sample_size_and_invalid_chapter_fail_closed():
    text = _course("Storia", "La storia umana comprende molti eventi.")
    with pytest.raises(ValueError, match="limit"):
        extract_claim_candidates(text, "Storia", limit=31)
    with pytest.raises(ValueError, match="assente"):
        extract_claim_candidates(text, "Fisica")


def test_generic_selection_includes_self_loop_despite_many_numbers():
    text = _course(
        "Geografia",
        "La capitale del paese A ha 10 milioni di abitanti. "
        "Il paese B ha 20 milioni di abitanti. "
        "Il lago è profondo 300 metri. "
        "Il fiume è lungo 250 chilometri. "
        "La rotta percorre la tratta da Torino a Torino. "
        "Il percorso non può essere completato per mancanza di ponti.",
    )
    claims = extract_claim_candidates(text, "Geografia", limit=3)
    assert len(claims) == 3
    assert any("Torino a Torino" in x.text for x in claims)
    assert any(x.category == "quantity" for x in claims)
    assert any(x.category == "negation" for x in claims)


def test_generic_risk_detects_multiple_quantities_with_no_subject_specific_rule():
    text = _course(
        "Economia",
        "Il bilancio richiede 7 unità di riserva, 14 di spesa, "
        "22 di investimento e 6 di accantonamento. "
        "Un trimestre dura 3 mesi.",
    )
    claims = extract_claim_candidates(text, "Economia", limit=1)
    assert len(claims) == 1
    assert "7 unità" in claims[0].text
    assert claims[0].priority == 5


def test_short_evidence_id_is_resolved_to_real_original_provenance():
    claim = extract_claim_candidates(
        _course("Astronomia", "Marte è il terzo pianeta dal Sole."),
        "Astronomia",
    )[0]
    original = _source(
        "Nella classificazione astronomica Marte è il quarto pianeta dal Sole.",
        name="astro.pdf",
        page=9,
    )
    response = lambda messages: json.dumps({
        "label": "CONTRADICTED",
        "rationale": "Terzo e quarto non coincidono.",
        "quotes": [{
            "source_id": 1,
            "quote": "Marte è il quarto pianeta dal Sole",
        }],
    })
    result = verify_claim_with_evidence(claim, [original], response)
    assert result["status"] == "SUSPECTED_CONTRADICTION"
    assert result["quotes"] == [{
        "source": "astro.pdf",
        "page": 9,
        "block": None,
        "quote": "Marte è il quarto pianeta dal Sole",
    }]


def test_out_of_range_source_id_fails_closed():
    claim = extract_claim_candidates(
        _course("Fisica", "La massa è una proprietà dei corpi."),
        "Fisica",
    )[0]
    original = _source("La massa è una proprietà dei corpi materiali.")
    response = lambda _messages: json.dumps({
        "label": "SUPPORTED", "rationale": "Fonte",
        "quotes": [{"source_id": 99, "quote": "La massa è una proprietà dei corpi"}],
    })
    verdict = verify_claim_with_evidence(claim, [original], response)
    assert verdict["status"] == "UNVERIFIED"
    assert verdict["model_label"] == "INVALID"
