"""Pilot protocol must not depend on Spark making up citation identifiers."""
import json

import pytest

from bc_science.config import AppConfig
from bc_science.evidence_engine import OriginalEvidence
from bc_science.targeted_review import _ground_pilot_rewrite, review_chapter

SOURCE = (
    "## Membrana\n\n"
    "La membrana cellulare contiene un doppio strato fosfolipidico "
    "e regola il passaggio delle sostanze.\n"
    "Alcune proteine di membrana partecipano al trasporto "
    "attraverso canali e trasportatori."
)
FACT = (
    "La membrana cellulare contiene un doppio strato fosfolipidico "
    "e regola il passaggio delle sostanze."
)
OTHER = (
    "Alcune proteine di membrana partecipano al trasporto "
    "attraverso canali e trasportatori."
)
PREVIOUS = "## Membrana\n\n" + FACT + "\n\n" + FACT + "\n\n" + OTHER
REWRITE = "## Membrana\n\n" + FACT + "\n\n" + OTHER


def _original():
    return [
        OriginalEvidence(
            "prova-originale.pdf", 3, None, FACT, 1, "original_pdf_page",
        ),
    ]


def test_pilot_grounding_assigns_local_source_quotes_without_any_model_ids():
    result = _ground_pilot_rewrite(
        {
            "rewrite": REWRITE,
            "complete_ids": ["C999"],
            "original_ids": ["E999"],
            "evidence": ["Una citazione del modello inventata."],
        },
        SOURCE, PREVIOUS, _original(),
    )
    assert result["evidence"] == [FACT, OTHER]
    assert result["original_quotes"] == [{
        "source": "prova-originale.pdf",
        "page": 3,
        "block": None,
        "quote": FACT,
    }]
    assert "citazione del modello inventata" not in str(result["evidence"])


def test_pilot_rejects_unsupported_claim_without_faking_source():
    with pytest.raises(ValueError, match="senza appoggio"):
        _ground_pilot_rewrite(
            {"rewrite": (
                "## Membrana\n\n"
                "Tutte le sostanze vengono convertite direttamente "
                "in cellule immortali grazie a una forza sconosciuta."
            )},
            SOURCE, PREVIOUS, _original(),
        )


def test_pilot_rejects_original_evidence_not_supporting_any_claim():
    unrelated = [OriginalEvidence(
        "altra-dispensa.pdf", 8, None,
        "Una pianta tropicale coltivata in suolo argilloso "
        "produce frutti dopo la stagione piovosa.",
        1, "original_pdf_page",
    )]
    with pytest.raises(ValueError, match="dispense originali"):
        _ground_pilot_rewrite(
            {"rewrite": REWRITE}, SOURCE, PREVIOUS, unrelated,
        )


def test_pilot_writer_returns_only_rewrite_and_numeric_gate_stays_on():
    class Model:
        model = "hf.co/XHToken/Spark-X2.5-4B-GGUF:Q4_K_M"

        def __init__(self, rewrite):
            self.rewrite = rewrite
            self.calls = []

        def _cached_chat(self, _key, _system, user, **kwargs):
            self.calls.append((user, kwargs))
            return json.dumps({"rewrite": self.rewrite}, ensure_ascii=False)

    model = Model(REWRITE)
    result = review_chapter(
        "Membrana", SOURCE, PREVIOUS, AppConfig(),
        summarizer=model, original_evidence=_original(),
        autopilot_mode=True,
    )
    assert result.applied
    assert result.text == REWRITE
    assert model.calls[0][1]["response_format"] == {"type": "object"}
    assert "NON generare ID" in model.calls[0][0]
    assert "DATI NUMERICI" in model.calls[0][0]

    numeric = SOURCE + "\n### Da ricordare per l'esame\n- La membrana contiene 3 strati."
    model = Model(REWRITE)
    result = review_chapter(
        "Membrana", numeric, PREVIOUS, AppConfig(),
        summarizer=model, original_evidence=_original(),
        autopilot_mode=True,
    )
    assert not result.applied
    assert "Numeri d'esame" in result.reason


def test_pilot_missing_rewrite_is_not_silently_accepted():
    with pytest.raises(ValueError, match="rewrite"):
        _ground_pilot_rewrite({}, SOURCE, PREVIOUS, _original())
