"""Bulk Essential speed paths: safe pair, single fallback, reuse and previews."""
import json
from pathlib import Path

from bc_science.config import AppConfig
from bc_science.summarizer import Summarizer


def _course() -> str:
    return (
        "# ANATOMIA\n\n## Indice degli argomenti\n"
        "- Ossa\n- Stomaco\n\n---\n\n"
        "## Ossa\n### In parole semplici\n"
        "Le ossa proteggono gli organi e supportano il movimento.\n"
        "### Da ricordare per l'esame\n"
        "- Le ossa hanno funzione di protezione.\n\n---\n\n"
        "## Stomaco\n### In parole semplici\n"
        "Lo stomaco partecipa alla digestione degli alimenti.\n"
        "### Da ricordare per l'esame\n"
        "- Lo stomaco ha 3 strati muscolari.\n"
    )


def test_batch_two_short_chapters_one_model_call_and_reusable(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("BC_SCIENCE_ESSENTIAL_BATCH", "2")
    summarizer = Summarizer(AppConfig(), db_path=tmp_path / "study.db")
    calls = []
    def chat(key, system, prompt, *, num_predict=None):
        calls.append((key, prompt))
        assert '"chapters"' in prompt
        return json.dumps({
            "chapters": [
                {
                    "title": "Ossa",
                    "markdown": "## Ossa\n- Le ossa proteggono gli organi e supportano il movimento.",
                },
                {
                    "title": "Stomaco",
                    "markdown": "## Stomaco\n- Lo stomaco digerisce gli alimenti.",
                },
            ],
        }, ensure_ascii=False)
    monkeypatch.setattr(summarizer, "_cached_chat", chat)
    checkpoint = tmp_path / "study.checkpoint.json"
    logs = []
    try:
        result = summarizer.build_study_summary(
            _course(), "Ripasso", checkpoint_path=checkpoint,
            progress=logs.append,
        )
        assert "Lo stomaco ha 3 strati muscolari" in result
        assert "## Ossa" in result
        assert len(calls) == 1
        assert not checkpoint.exists()
        preview_dir = tmp_path / "essential_chapters"
        assert len(list(preview_dir.glob("*.json"))) == 2
        assert any("1 lotti da 2" in log for log in logs)
        second = summarizer.build_study_summary(
            _course(), "Ripasso", checkpoint_path=checkpoint,
        )
        assert second == result
        assert len(calls) == 1
    finally:
        summarizer.close()


def test_invalid_batch_falls_back_to_individual_chapters(
    tmp_path: Path, monkeypatch,
):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("BC_SCIENCE_ESSENTIAL_BATCH", "2")
    summarizer = Summarizer(AppConfig(), db_path=tmp_path / "study.db")
    calls = []
    def chat(key, system, prompt, *, num_predict=None):
        calls.append(prompt)
        if "Riassumi DUE capitoli" in prompt:
            return "Non è un JSON valido."
        if "CAPITOLO:\n## Ossa" in prompt:
            return "## Ossa\n- Le ossa proteggono gli organi."
        return "## Stomaco\n- Lo stomaco partecipa alla digestione."
    monkeypatch.setattr(summarizer, "_cached_chat", chat)
    try:
        result = summarizer.build_study_summary(_course(), "Ripasso")
        assert "Lo stomaco ha 3 strati muscolari" in result
        assert len(calls) == 3  # one rejected pair, two validated singles
        assert result.count("## Stomaco") == 1
    finally:
        summarizer.close()


def test_batch_can_be_disabled_without_losing_quality(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("BC_SCIENCE_ESSENTIAL_BATCH", "1")
    summarizer = Summarizer(AppConfig(), db_path=tmp_path / "study.db")
    calls = []
    def chat(key, system, prompt, *, num_predict=None):
        calls.append(prompt)
        if "CAPITOLO:\n## Ossa" in prompt:
            return "## Ossa\n- Le ossa proteggono gli organi."
        return "## Stomaco\n- Lo stomaco partecipa alla digestione."
    monkeypatch.setattr(summarizer, "_cached_chat", chat)
    try:
        result = summarizer.build_study_summary(_course(), "Ripasso")
        assert len(calls) == 2
        assert "Lo stomaco ha 3 strati muscolari" in result
    finally:
        summarizer.close()
