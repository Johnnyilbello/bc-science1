import json

import pytest

from bc_science.claim_review import review_paragraph
from bc_science.claim_review import rewrite_reviewed_paragraph


def test_association_replaces_invented_causal_claim_with_literal_evidence():
    source = "Potenziali meccanismi alla base dell'associazione tra attività fisica e rischio ridotto."
    result, count = review_paragraph(
        "L'attività fisica causa una riduzione del rischio.", source,
        lambda _: json.dumps([dict(id=1, supported=False, quotes=[source])]),
    )
    assert source in result
    assert count == 1
    assert "causa" not in result


@pytest.mark.parametrize("verdicts", [
    [],
    [dict(id=2, supported=True, quotes=["Una fonte."])],
    [dict(id=1, supported=True, quotes=[])],
    [dict(id=1, supported=False, quotes=[])],
    [dict(id=1, supported=True, quotes=["Una citazione inventata."])],
    [dict(id=1, supported="true", quotes=["Una fonte."])],
])
def test_review_rejects_missing_decisions_or_unverifiable_evidence(verdicts):
    with pytest.raises(ValueError, match="non verificabile"):
        review_paragraph("Una frase.", "Una fonte.", lambda _: json.dumps(verdicts))


def test_supported_simplification_is_preserved():
    source = "La sarcopenia comporta una diminuzione della massa muscolare."
    draft = "La sarcopenia riduce la massa muscolare."
    result, count = review_paragraph(draft, source, lambda _: json.dumps([
        dict(id=1, supported=True, quotes=[source]),
    ]))
    assert result == draft
    assert count == 0


def test_model_cannot_certify_certain_causation_from_hypothesis():
    source = "Potenziali meccanismi alla base dell'associazione tra attività fisica e rischio ridotto."
    claim = "L'attività fisica agisce come fattore protettivo riducendo il rischio."
    result, count = review_paragraph(claim, source, lambda _: json.dumps([
        dict(id=1, supported=True, quotes=[source]),
    ]))
    assert claim not in result
    assert source in result
    assert count == 1


def test_action_potential_is_not_mistaken_for_uncertainty():
    source = "Il potenziale d'azione raggiunge il muscolo."
    result, count = review_paragraph(source, source, lambda _: json.dumps([
        dict(id=1, supported=True, quotes=[source]),
    ]))
    assert result == source
    assert count == 0


def test_source_reference_is_resolved_without_retyping_ocr():
    source = "FEV, FEV/FVC: capacità ventilatoria."
    result, count = review_paragraph("Una spiegazione errata.", source, lambda _: json.dumps([
        dict(id=1, supported=False, evidence_ids=["S1"]),
    ]))
    assert source in result
    assert count == 1
    with pytest.raises(ValueError, match="non verificabile"):
        review_paragraph("Una frase.", source, lambda _: json.dumps([
            dict(id=1, supported=True, evidence_ids=["S99"]),
        ]))


def test_didactic_repair_preserves_source_fact_without_quote_wrapper():
    source = "L'efficienza fisica associata alla salute comprende forza e flessibilità."
    candidate = "Forza e flessibilità sono componenti dell'efficienza fisica associata alla salute."
    responses = iter([
        [dict(id=1, supported=False, quotes=[source])],
        [dict(id=1, supported=True, quotes=[source])],
        [dict(id=1, supported=True, quotes=[candidate])],
    ])
    result, count = rewrite_reviewed_paragraph(
        "L'esercizio migliora tutti i componenti.", source,
        lambda _: json.dumps(next(responses)), lambda _: candidate,
    )
    assert result == candidate
    assert "La dispensa riporta" not in result
    assert count == 1


def test_didactic_repair_rejects_number_changes_even_if_reviewer_would_accept():
    source = "La quota è pari al 90%."
    calls = []
    def chat(prompt):
        calls.append(prompt)
        return json.dumps([dict(id=1, supported=False, quotes=[source])])
    with pytest.raises(ValueError, match="valori numerici"):
        rewrite_reviewed_paragraph("La quota è elevata.", source, chat, lambda _: "La quota è pari al 95%.")
    assert len(calls) == 1


def test_didactic_repair_rejects_source_fact_lost_during_simplification():
    source = "I neuroni ricevono e trasmettono segnali."
    candidate = "I neuroni ricevono segnali."
    responses = iter([
        [dict(id=1, supported=False, quotes=[source])],
        [dict(id=1, supported=True, quotes=[source])],
        [dict(id=1, supported=False, quotes=[candidate])],
    ])
    with pytest.raises(ValueError, match="omette un fatto"):
        rewrite_reviewed_paragraph("I neuroni producono ormoni.", source,
            lambda _: json.dumps(next(responses)), lambda _: candidate)


def test_reasoned_generation_does_not_reuse_fast_answer_cache(tmp_path):
    from bc_science.config import AppConfig
    from bc_science.ollama_client import ChatResult
    from bc_science.summarizer import Summarizer

    calls = []
    class Client:
        def chat_stream(self, model, messages, **kwargs):
            calls.append(kwargs.get("think", False))
            return ChatResult(content="Ragionata" if calls[-1] else "Rapida", done_reason="stop")
    summarizer = Summarizer(AppConfig(), db_path=tmp_path / "cache.db")
    summarizer.client = Client()
    try:
        assert summarizer._cached_chat("same", "system", "prompt") == "Rapida"
        assert summarizer._cached_chat("same", "system", "prompt", think=True) == "Ragionata"
        assert summarizer._cached_chat("same", "system", "prompt", think=True) == "Ragionata"
        assert calls == [False, True]
    finally:
        summarizer.close()


def test_association_list_continuation_does_not_require_repeated_qualifier():
    source = "Efficienza fisica associata alla salute: forza e composizione corporea."
    paragraph = "L'efficienza fisica è associata alla salute. Anche la composizione corporea è inclusa."
    result, corrections = review_paragraph(paragraph, source, lambda _: json.dumps([
        dict(id=1, supported=True, quotes=[source]),
        dict(id=2, supported=True, quotes=[source]),
    ]))
    assert result == paragraph
    assert corrections == 0


def test_distant_hypothesis_cannot_justify_a_certain_causal_assertion():
    source = "Potenziali meccanismi dell'associazione: minore obesità e migliore funzione neuronale."
    paragraph = "Si tratta di potenziali meccanismi. L'esercizio provoca una migliore funzione neuronale."
    result, corrections = review_paragraph(paragraph, source, lambda _: json.dumps([
        dict(id=1, supported=True, quotes=[source]),
        dict(id=2, supported=True, quotes=[source]),
    ]))
    assert "L'esercizio provoca" not in result
    assert corrections == 1


def test_diagnostic_checks_later_paragraphs_but_never_returns_partial_repair(tmp_path, monkeypatch):
    from bc_science.config import AppConfig
    from bc_science.summarizer import Summarizer
    visited = []
    def review(paragraph, source, chat):
        visited.append(paragraph)
        if paragraph == "Primo paragrafo.":
            raise ValueError("prima frase non verificabile")
        return paragraph, 0
    monkeypatch.setattr("bc_science.summarizer.review_paragraph", review)
    report = []
    summarizer = Summarizer(AppConfig(), db_path=tmp_path / "cache.db")
    try:
        with pytest.raises(ValueError, match="paragrafi 1; capitolo non pubblicato"):
            summarizer._review_source_claims("Titolo", "Primo paragrafo.\n\nSecondo paragrafo.",
                ["Una fonte diversa."], diagnostics=report)
        assert "Secondo paragrafo." in visited
        assert [item["status"] for item in report] == ["failed", "checked"]
    finally:
        summarizer.close()


@pytest.mark.parametrize("claim", [
    "Il volume polmonare residuo aumenta con l'esercizio.",
    "Il volume polmonare residuo migliora con l'esercizio regolare.",
])
def test_model_cannot_turn_improvement_into_increase_or_add_exercise_frequency(claim):
    source = "Con l'attività fisica migliorano i valori di volume polmonare residuo."
    result, corrections = review_paragraph(claim, source, lambda _: json.dumps([
        dict(id=1, supported=True, quotes=[source]),
    ]))
    assert claim not in result
    assert corrections == 1


def test_explicit_source_increase_and_regular_exercise_are_retained():
    source = "Con l'esercizio regolare aumenta il valore misurato."
    result, corrections = review_paragraph(source, source, lambda _: json.dumps([
        dict(id=1, supported=True, quotes=[source]),
    ]))
    assert result == source
    assert corrections == 0
