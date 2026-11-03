"""Local role separation and bounded, non-publishing scientific previews."""
import json

import pytest

from bc_science.config import AppConfig
from bc_science.evidence_engine import OriginalEvidence
from bc_science.scientific_autopilot import (
    review_priority_course,
    review_with_agents,
)
from bc_science.targeted_review import ReviewOutcome


def _evidence():
    return [OriginalEvidence(
        source_path="originale.pdf", page=2, block=None,
        excerpt=(
            "La cellula possiede una membrana che regola gli scambi "
            "e il nucleo contiene informazioni genetiche."
        ),
        rank=1, provenance="cached_page",
    )]


class FakeModel:
    model = "qwen3.5:4b"

    def __init__(self, response=None):
        self.calls = []
        self.response = response or json.dumps({
            "approve": True, "reason": "", "evidence_ids": ["E1"],
        })

    def _cached_chat(self, key, system, prompt, **kwargs):
        self.calls.append((system, prompt))
        return self.response


def test_missing_original_evidence_never_calls_any_agent():
    model = FakeModel()
    result = review_with_agents(
        "Cellula", "## Cellula\n\nUn fatto.", "## Cellula\n\nUn fatto.",
        AppConfig(), summarizer=model, original_evidence=[],
    )
    assert result.status == "BLOCKED_NO_ORIGINAL_EVIDENCE"
    assert result.candidate == "## Cellula\n\nUn fatto."
    assert result.attempts == ()
    assert model.calls == []


def test_bounded_retry_stops_without_overwriting(monkeypatch):
    import bc_science.scientific_autopilot as autopilot

    calls = []

    def reject(title, source, previous, config, **kwargs):
        calls.append(kwargs["retry_context"])
        return ReviewOutcome(title, False, previous, "Mancano citazioni")

    monkeypatch.setattr(autopilot, "review_chapter", reject)
    model = FakeModel()
    old = "## Cellula\n\nLa cellula ha una membrana e un nucleo."
    result = review_with_agents(
        "Cellula", old, old, AppConfig(),
        summarizer=model, original_evidence=_evidence(), max_attempts=3,
    )
    assert result.status == "BLOCKED_FOR_MANUAL_REVIEW"
    assert result.candidate == old
    assert len(result.attempts) == 3
    assert calls == ["", "Mancano citazioni", "Mancano citazioni"]
    assert not model.calls


def test_critic_missing_provenance_rejects_and_never_claims_scientific_pass(
    monkeypatch,
):
    import bc_science.scientific_autopilot as autopilot

    original = (
        "## Cellula\n\nLa cellula possiede una membrana che regola "
        "gli scambi e il nucleo contiene informazioni genetiche. "
        "La membrana partecipa alla regolazione degli scambi."
    )
    revised = (
        "## Cellula\n\nLa cellula possiede una membrana che regola "
        "gli scambi e il nucleo contiene informazioni genetiche."
    )
    monkeypatch.setattr(
        autopilot, "review_chapter",
        lambda title, _source, _previous, _config, **_kwargs: (
            ReviewOutcome(title, True, revised, "proposta")
        ),
    )
    monkeypatch.setattr(autopilot, "_issues", lambda *_args: (0, 0, 0, 0))
    model = FakeModel(json.dumps({"approve": True, "evidence_ids": ["E99"]}))
    result = review_with_agents(
        "Cellula", original, original, AppConfig(), summarizer=model,
        original_evidence=_evidence(), max_attempts=2,
    )
    assert result.status == "BLOCKED_FOR_MANUAL_REVIEW"
    assert result.candidate == original
    assert len(result.attempts) == 2
    assert result.attempts[-1].status == "STALLED"
    assert len(model.calls) == 1


def test_distinct_risks_require_deterministic_gate_not_ai_approval(monkeypatch):
    import bc_science.scientific_autopilot as autopilot

    before = "## Cellula\n\nLa cellula non perde la sua membrana."
    after = "## Cellula\n\nLa cellula perde la membrana."
    monkeypatch.setattr(
        autopilot, "review_chapter",
        lambda title, _source, _old, _config, **_kwargs: (
            ReviewOutcome(title, True, after, "proposta")
        ),
    )
    monkeypatch.setattr(
        autopilot, "_issues",
        lambda _title, _source, text: (
            (0, 0, 0, 0) if "non perde" in text else (1, 0, 0, 0)
        ),
    )
    model = FakeModel()
    result = review_with_agents(
        "Cellula", before, before, AppConfig(), summarizer=model,
        original_evidence=_evidence(), max_attempts=1,
    )
    assert result.status == "BLOCKED_FOR_MANUAL_REVIEW"
    assert result.candidate == before
    assert not model.calls


def test_invalid_max_attempts_is_rejected():
    model = FakeModel()
    with pytest.raises(ValueError, match="tentativi"):
        review_with_agents(
            "Cellula", "Testo", "Testo", AppConfig(),
            summarizer=model, original_evidence=_evidence(), max_attempts=100,
        )


def test_priority_course_only_changes_authorized_chapter(monkeypatch):
    import bc_science.scientific_autopilot as autopilot

    source = (
        "## la celulla e il nuucleo\n\nLa cellula ha una membrana e il nucleo."
    )
    original = (
        "## la cellula e il nucleo\n\n"
        "La cellula ha una membrana e il nucleo. "
        "La membrana regola gli scambi tra la cellula e l'esterno."
    )
    candidate = (
        "## la cellula e il nucleo\n\n"
        "La cellula ha una membrana e il nucleo."
    )
    other_source = "## Il ciclo di Krebs\n\nIl ciclo produce energia."
    other = "## Il ciclo di Krebs\n\nIl ciclo produce energia."
    unrelated = "## Un altro capitolo\n\nTesto che deve restare invariato."
    complete = (
        "# Biologia\n\n## Indice degli argomenti\n"
        "- la celulla e il nuucleo\n"
        "- Il ciclo di Krebs\n"
        "- Un altro capitolo\n\n---\n\n"
        + f"{source}\n\n---\n\n{other_source}\n\n---\n\n{unrelated}"
    )
    study = (
        "# Biologia\n\n## Indice degli argomenti\n"
        "- la cellula e il nucleo\n"
        "- Il ciclo di Krebs\n"
        "- Un altro capitolo\n\n---\n\n"
        + f"{original}\n\n---\n\n{other}\n\n---\n\n{unrelated}"
    )
    monkeypatch.setattr(
        autopilot, "review_chapter",
        lambda title, _source, old, _config, **_kwargs: (
            ReviewOutcome(title, True, candidate, "proposta")
            if title == "la cellula e il nucleo"
            else ReviewOutcome(title, False, old, "Nessuna criticita rilevata")
        ),
    )
    monkeypatch.setattr(autopilot, "_issues", lambda *_args: (0, 0, 0, 0))
    monkeypatch.setattr(
        autopilot, "audit_fast_essential",
        lambda *_args: type("Audit", (), {"status": "WARN"})(),
    )
    model = FakeModel()
    output, outcomes = review_priority_course(
        complete, study, AppConfig(), summarizer=model,
        evidence_for=lambda _name: _evidence(), max_attempts=1,
    )
    assert len(outcomes) == 2
    assert outcomes[0].status == "CANDIDATE_UNVERIFIED"
    assert candidate in output
    assert original not in output
    assert other in output and unrelated in output
    assert output.replace(candidate, original, 1) == study
    assert model.calls


def test_ambiguous_title_collision_fails_before_model_or_retrieval():
    model = FakeModel()
    text = (
        "# B\n\n## Indice degli argomenti\n"
        "- la celulla e il nuucleo\n- la cellula e il nucleo\n\n---\n\n"
        "## la celulla e il nuucleo\n\nTesto.\n\n---\n\n"
        "## la cellula e il nucleo\n\nTesto."
    )
    calls = []
    with pytest.raises(ValueError, match="ambigui"):
        review_priority_course(
            text, text, AppConfig(), summarizer=model,
            evidence_for=lambda title: calls.append(title) or _evidence(),
        )
    assert not calls
    assert not model.calls
