"""Spark writer source-reference protocol: no invented literal quotes."""
import json

import pytest

from bc_science.config import AppConfig
from bc_science.evidence_engine import OriginalEvidence
from bc_science.targeted_review import (
    _complete_source_catalog,
    _resolve_complete_refs,
    _validate_proposal,
    review_chapter,
)

SOURCE = (
    "## Membrana cellulare\n\n"
    "La membrana cellulare contiene un doppio strato fosfolipidico "
    "e regola il passaggio delle sostanze.\n"
    "Alcune proteine di membrana partecipano al trasporto attraverso "
    "canali e trasportatori."
)
FACT = (
    "La membrana cellulare contiene un doppio strato fosfolipidico "
    "e regola il passaggio delle sostanze."
)
OTHER = (
    "Alcune proteine di membrana partecipano al trasporto attraverso "
    "canali e trasportatori."
)
OLD = "## Membrana cellulare\n\n" + FACT + " " + FACT + "\n\n" + OTHER
NEW = "## Membrana cellulare\n\n" + FACT + "\n\n" + OTHER


def _evidence():
    return [OriginalEvidence(
        "originale.pdf", 2, None, FACT, 1, "original_pdf_page",
    )]


def test_source_catalog_resolves_exact_spans_not_paraphrased_claims():
    entries = _complete_source_catalog(SOURCE)
    assert entries == {"C1": FACT, "C2": OTHER}
    assert all(item in SOURCE for item in entries.values())


def test_source_ids_replace_untrusted_model_paraphrases_with_true_quotes():
    catalog = _complete_source_catalog(SOURCE)
    proposed = {
        "rewrite": NEW,
        "evidence": ["Le proteine sono responsabili di tutto."],
        "complete_ids": ["C1", "C2", "C1"],
    }
    resolved = _resolve_complete_refs(proposed, catalog, required=True)
    assert resolved["evidence"] == [FACT, OTHER]
    assert _validate_proposal(
        "Membrana cellulare", SOURCE, OLD, resolved,
    )[0]


@pytest.mark.parametrize("selected", [
    ["C99"],
    [""],
    [],
    [3],
    "C1",
    ["C1"] * 13,
])
def test_unknown_or_invalid_source_ids_are_rejected(selected):
    with pytest.raises(ValueError, match="non validi"):
        _resolve_complete_refs(
            {"complete_ids": selected},
            _complete_source_catalog(SOURCE),
            required=True,
        )


def test_cannot_bypass_source_id_requirement_with_model_written_evidence():
    with pytest.raises(ValueError, match="mancanti"):
        _resolve_complete_refs(
            {"evidence": [FACT]},
            _complete_source_catalog(SOURCE), required=True,
        )


def test_review_chapter_uses_json_mode_and_resolves_both_source_types():
    class SparkMock:
        model = "hf.co/XHToken/Spark-X2.5-4B-GGUF:Q4_K_M"

        def __init__(self):
            self.prompts = []
            self.kwargs = []

        def _cached_chat(self, _key, _system, prompt, **kwargs):
            self.prompts.append(prompt)
            self.kwargs.append(kwargs)
            return json.dumps({
                "rewrite": NEW,
                "complete_ids": ["C1", "C2"],
                "original_ids": ["E1"],
                # Must not be trusted, even if the model outputs it.
                "evidence": ["citazione falsa"],
            }, ensure_ascii=False)

    spark = SparkMock()
    result = review_chapter(
        "Membrana cellulare", SOURCE, OLD, AppConfig(),
        summarizer=spark,
        original_evidence=_evidence(),
    )
    assert result.applied
    assert result.text == NEW
    assert spark.kwargs[0]["response_format"] == {"type": "object"}
    assert spark.kwargs[0]["num_predict"] >= 2400
    assert '"C1"' in spark.prompts[0]
    assert '"E1"' in spark.prompts[0]
    assert "Non inserire note" not in spark.prompts[0]
    assert "non aggiungere" in spark.prompts[0].casefold() or "NON " in spark.prompts[0]


def test_invalid_original_page_still_blocks_model_after_valid_source_ids():
    from bc_science.targeted_review import _resolve_original_refs

    catalog = _complete_source_catalog(SOURCE)
    resolved = _resolve_complete_refs({
        "rewrite": NEW,
        "complete_ids": ["C1"],
        "original_ids": ["E1"],
    }, catalog, required=True)
    cited = _resolve_original_refs(resolved, _evidence())
    cited["original_quotes"][0]["page"] = 9
    accepted, reason = _validate_proposal(
        "Membrana cellulare", SOURCE, OLD, cited,
        original_evidence=_evidence(),
    )
    assert not accepted
    assert "dispense" in reason


def test_previous_literal_quote_contract_still_supported_without_original_evidence():
    proposed = {"rewrite": NEW, "evidence": [FACT]}
    assert _resolve_complete_refs(
        proposed, _complete_source_catalog(SOURCE),
    ) == proposed
