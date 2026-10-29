"""Regression for slow 77-chapter Essential generation and missing exam numbers."""
from pathlib import Path

import pytest

from bc_science.config import AppConfig
from bc_science.summarizer import (
    Summarizer,
    _preserves_exam_numbers,
    _restore_missing_exam_points,
)


def test_missing_stomach_numbers_are_restored_without_special_heading():
    source = (
        "## Stomaco\n### In parole semplici\nLo stomaco digerisce il cibo.\n"
        "### Da ricordare per l'esame\n"
        "- Lo stomaco ha 3 strati muscolari.\n"
        "- Gli enzimi partecipano alla digestione.\n"
    )
    essential = "## Stomaco\n- Lo stomaco contribuisce alla digestione."
    fixed, n = _restore_missing_exam_points(source, essential)
    assert n == 1
    assert "### Da ricordare per l'esame" in fixed
    assert "- Lo stomaco ha 3 strati muscolari." in fixed
    assert "- Gli enzimi" not in fixed
    assert _preserves_exam_numbers(source, fixed)


def test_no_numeric_facts_not_duplicated_just_to_satisfy_formatting():
    source = (
        "## Stomaco\n### Da ricordare per l'esame\n"
        "- Il peritoneo riveste organi addominali.\n"
    )
    essential = "## Stomaco\n- Membrana viscerale e peritoneo."
    fixed, n = _restore_missing_exam_points(source, essential)
    assert n == 0
    assert fixed == essential


def test_validated_single_chapter_reuses_cache(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    summarizer = Summarizer(AppConfig(), db_path=tmp_path / "course.db")
    calls = []

    def fake_chat(key, system, user, *, num_predict=None):
        calls.append(key)
        return "## Stomaco\n- Lo stomaco digerisce gli alimenti."

    monkeypatch.setattr(summarizer, "_cached_chat", fake_chat)
    try:
        source = (
            "## Stomaco\n### In parole semplici\nDigestione gastrica.\n"
            "### Da ricordare per l'esame\n"
            "- Lo stomaco digerisce gli alimenti.\n"
        )
        first = summarizer.build_study_chapter("Stomaco", source)
        second = summarizer.build_study_chapter("Stomaco", source)
    finally:
        summarizer.close()
    assert first == second
    assert len(calls) == 1


def test_checkpoint_resumes_after_second_chapter_crashes(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    checkpoint = tmp_path / "study.checkpoint.json"
    source = (
        "# Anatomia\n\n## Indice degli argomenti\n- Ossa\n- Stomaco\n"
        "\n---\n\n## Ossa\nLo scheletro.\n\n---\n\n"
        "## Stomaco\nLa digestione.\n"
    )
    summarizer = Summarizer(AppConfig(), db_path=tmp_path / "course.db")
    calls = []

    def interrupted(title, chapter, *, progress=None):
        calls.append(title)
        if title == "Stomaco" and len(calls) == 2:
            raise RuntimeError("interruzione")
        return f"## {title}\n- Ripasso completato."

    monkeypatch.setattr(summarizer, "build_study_chapter", interrupted)
    try:
        with pytest.raises(RuntimeError, match="interruzione"):
            summarizer.build_study_summary(
                source, "Ripasso", checkpoint_path=checkpoint,
            )
        assert checkpoint.exists()
        result = summarizer.build_study_summary(
            source, "Ripasso", checkpoint_path=checkpoint,
        )
        assert calls == ["Ossa", "Stomaco", "Stomaco"]
        assert "## Ossa" in result and "## Stomaco" in result
        assert not checkpoint.exists()
    finally:
        summarizer.close()
