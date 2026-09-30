from pathlib import Path

from bc_science.config import AppConfig
from bc_science.summarizer import (
    Summarizer,
    group_course_files,
    normalize_topic_stem,
)


def test_normalize_topic_stem_collapses_numbered_lessons():
    assert normalize_topic_stem("Contrazione muscolare  4") == "Contrazione muscolare"
    assert normalize_topic_stem("Vista 2") == "Vista"
    assert normalize_topic_stem("Omeostasi (in allenamento 3)") == "Omeostasi (in allenamento)"


def test_group_course_files_merges_numbered_and_article_variants():
    files = [
        Path("Contrazione muscolare.pdf"),
        Path("Contrazione muscolare 2.pdf"),
        Path("il tatto.pdf"),
        Path("Tatto 2.pdf"),
        Path("Tatto 3.pdf"),
    ]
    groups = group_course_files(files)

    assert len(groups) == 2
    assert groups[0].title == "Contrazione muscolare"
    assert len(groups[0].files) == 2
    assert groups[1].title == "il tatto"
    assert len(groups[1].files) == 3


def test_course_summary_keeps_complete_topic_chapters(tmp_path, monkeypatch):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    summarizer = Summarizer(AppConfig())

    files = [
        Path("Neuroni.pdf"),
        Path("Neuroni 2.pdf"),
        Path("Contrazione muscolare.pdf"),
    ]

    def fake_topic(group, *, progress=None):
        return f"## {group.title}\nContenuto completo di {group.title}."

    def fake_cached(cache_key, system, user, *, num_predict=None):
        return "# Corso - Riassunto completo\n\n## Mappa della materia\nPanoramica."

    monkeypatch.setattr(summarizer, "_summarize_topic", fake_topic)
    monkeypatch.setattr(summarizer, "_cached_chat", fake_cached)

    try:
        result = summarizer.summarize_course(files, "Corso - Riassunto completo")
    finally:
        summarizer.close()

    assert "## Neuroni" in result
    assert "Contenuto completo di Neuroni." in result
    assert "## Contrazione muscolare" in result
    assert "Contenuto completo di Contrazione muscolare." in result
    assert "## Indice degli argomenti" in result
