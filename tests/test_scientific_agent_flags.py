"""Science red flags are warnings in the 100-agent mesh, not silent passes."""
from __future__ import annotations

from bc_science.agent_checks import _editorial_and_known_claim_flags


def test_complete_known_scientific_error_is_warning():
    text = (
        "# Biologia\n\n## Biosintesi\n"
        "La sintesi del palmitato richiede 7 molecole di acetil-CoA, "
        "14 di malonil-CoA, 14 di NADPH e 14 di ATP."
    )
    status, evidence = _editorial_and_known_claim_flags(
        text, edition="Completa",
    )
    assert status == "WARN"
    assert "PALMITATE_STOICHIOMETRY" in evidence
    assert "NON esaustivo" in evidence


def test_essential_known_error_also_warns():
    text = (
        "# Anatomia\n\n## Esofago\n"
        "Esofago: tubo che porta il cibo dallo stomaco allo stomaco."
    )
    status, evidence = _editorial_and_known_claim_flags(
        text, edition="Essenziale",
    )
    assert status == "WARN"
    assert "ESOPHAGUS_DIRECTION" in evidence


def test_placeholder_failure_is_not_hidden_by_science_warning():
    status, evidence = _editorial_and_known_claim_flags(
        "TODO: verificare la sintesi del palmitato richiede 7 acetil-CoA "
        "e 14 di ATP.",
        edition="Completa",
    )
    assert status == "FAIL"
    assert "Segnaposto" in evidence


def test_no_known_flags_means_limited_pass_not_scientific_approval():
    status, evidence = _editorial_and_known_claim_flags(
        "# Biologia\n\n## Introduzione\nLa cellula contiene organelli.",
        edition="Completa",
    )
    assert status == "PASS"
    assert "NON equivale a validazione scientifica" in evidence
