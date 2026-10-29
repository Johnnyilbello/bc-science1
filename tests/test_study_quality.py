"""Regression tests for measurable Essential summaries and Qwen 4B A/B settings."""
from bc_science.ollama_client import OllamaClient
from bc_science.study_quality import assess_essential


def _doc(body: str) -> str:
    return (
        "# Fisiologia\n\n## Indice degli argomenti\n- Contrazione muscolare"
        "\n\n---\n\n## Contrazione muscolare\n" + body + "\n"
    )


def test_essential_measures_compression_without_deleting_facts():
    fact = "- L'unità motoria è definita come un motoneurone e le fibre che innerva."
    complete = _doc("\n".join([fact] * 40))
    essential = _doc(fact)
    result = assess_essential(complete, essential)
    assert result.target_applies
    assert result.compression_percent >= 25.0
    assert result.compression_target_met
    assert not result.missing_numeric_facts
    assert not result.missing_critical_facts


def test_essential_flags_uncompressed_long_document():
    fact = "- L'unità motoria è definita come un motoneurone e le fibre che innerva."
    complete = _doc("\n".join([fact] * 40))
    result = assess_essential(complete, complete)
    assert result.target_applies
    assert not result.compression_target_met
    assert any("troppo simile" in warning for warning in result.warnings)


def test_short_material_does_not_force_artificial_compression():
    text = _doc("- La forza è una grandezza vettoriale.")
    result = assess_essential(text, text)
    assert not result.target_applies
    assert result.compression_target_met


def test_missing_exam_numeric_fact_is_flagged_for_review():
    complete = _doc(
        "- La frequenza cardiaca di riferimento è 120 bpm."
        "\n- La forza muscolare dipende dall'attivazione delle fibre."
    )
    essential = _doc("- La forza muscolare dipende dall'attivazione delle fibre.")
    result = assess_essential(complete, essential)
    assert result.missing_numeric_facts > 0
    assert result.needs_review


def test_ollama_thinking_is_opt_in_and_parameters_are_explicit():
    args = ("qwen3.5:4b", [{"role": "user", "content": "Prova"}])
    common = {"stream": False, "num_ctx": 8192, "num_predict": 900, "keep_alive": "20m"}
    default = OllamaClient._chat_payload(*args, **common)
    thinking = OllamaClient._chat_payload(
        *args, **common, think=True, temperature=0.35
    )
    assert default["think"] is False
    assert default["options"]["temperature"] == 0.1
    assert thinking["think"] is True
    assert thinking["options"]["temperature"] == 0.35
    assert thinking["model"] == "qwen3.5:4b"
