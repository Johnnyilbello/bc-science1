from pathlib import Path

from bc_science.config import AppConfig
from bc_science.ollama_client import ChatResult
from bc_science.summarizer import (
    Summarizer,
    _normalize_chapter_heading,
    _summary_system_prompt,
)


def test_summary_prompt_forbids_external_clarifications():
    prompt = _summary_system_prompt("Fisiologia umana e dello sport")
    assert "NON aggiungere conoscenza generale" in prompt
    assert "NON correggere scientificamente" in prompt


def test_chapter_normalization_removes_internal_rules_and_obvious_typos():
    raw = "# Titolo generato\n\nIl calcio viene ricucinato.\n\n---\n\nFine."
    cleaned = _normalize_chapter_heading(raw, "Contrazione muscolare")
    assert cleaned.startswith("## Contrazione muscolare")
    assert "ricaptato" in cleaned
    assert "\n---\n" not in cleaned


def test_cached_chat_continues_after_length_stop(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    summarizer = Summarizer(AppConfig())
    responses = iter(
        [
            ChatResult(
                content="La frase continua",
                done_reason="length",
                eval_count=10,
                eval_duration=1_000_000_000,
                total_duration=1_200_000_000,
            ),
            ChatResult(
                content=" correttamente.",
                done_reason="stop",
                eval_count=5,
                eval_duration=500_000_000,
                total_duration=700_000_000,
            ),
        ]
    )

    monkeypatch.setattr(
        summarizer.client,
        "chat_stream",
        lambda *args, **kwargs: next(responses),
    )

    try:
        result = summarizer._cached_chat(
            "test-continuation",
            "system",
            "user",
            num_predict=20,
        )
    finally:
        summarizer.close()

    assert result == "La frase continua correttamente."
    assert summarizer.stats.continuation_calls == 1
    assert summarizer.stats.generated_tokens == 15
