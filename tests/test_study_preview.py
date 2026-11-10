"""Preview stage must never publish a generated Essential or change source files."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from bc_science import study_preview
from bc_science.cli import app
from bc_science.study_preview import preview_essential_editions


def _setup(tmp_path: Path, monkeypatch) -> tuple[Path, Path, Path]:
    home = tmp_path / "home"
    monkeypatch.setenv("BC_SCIENCE_HOME", str(home))
    sources = tmp_path / "materie"
    subject = sources / "ANATOMIA"
    subject.mkdir(parents=True)
    (subject / "lezione.txt").write_text(
        "Le vertebre sono strutture ossee.", encoding="utf-8",
    )
    live = home / "outputs" / "courses" / "ANATOMIA"
    live.mkdir(parents=True)
    complete = live / "riassunto-unico.md"
    complete.write_text(
        "# Anatomia\n\n## Indice degli argomenti\n"
        "- Strutture ossee\n\n---\n\n"
        "## Strutture ossee\n"
        "### Da ricordare per l'esame\n"
        "- Il midollo origina 31 coppie di nervi.\n",
        encoding="utf-8",
    )
    (live / "riassunto-studio.md").write_text(
        "# Anatomia breve\n\n## Indice degli argomenti\n"
        "- Strutture ossee\n\n---\n\n## Strutture ossee\n"
        "Le vertebre sono strutture ossee.",
        encoding="utf-8",
    )
    (live / "audit.json").write_text('{"do_not_edit":true}', encoding="utf-8")
    (live / "riassunto-studio.pdf").write_bytes(b"%PDF-TEST" )
    monkeypatch.setattr(
        study_preview, "_load_manifest",
        lambda scan: {"schema": 3, "study": {"pipeline": "not-fast"}},
    )
    monkeypatch.setattr(
        study_preview, "_manifest_baseline_verified",
        lambda complete, manifest: True,
    )
    monkeypatch.setattr(
        study_preview, "audit_course_summary",
        lambda *args, **kwargs: SimpleNamespace(complete=True),
    )
    monkeypatch.setattr(
        study_preview, "make_fast_essential",
        lambda complete, title: ("# ANTEPRIMA\n\n## Strutture ossee\n- 31 coppie.", [1]),
    )
    monkeypatch.setattr(
        study_preview, "audit_fast_essential",
        lambda *args, **kwargs: SimpleNamespace(
            status="PASS", compression_percent=62.0,
            warnings=("Controllo automatico, non certifica la scienza.",),
        ),
    )
    monkeypatch.setattr(
        study_preview, "audit_study_pair",
        lambda *args, **kwargs: SimpleNamespace(
            missing_numeric_facts=0, missing_exam_facts=1,
            weighted_fact_coverage=0.70,
        ),
    )
    return sources, home, live


def test_preview_is_readonly_for_all_live_and_original_files(
    tmp_path: Path, monkeypatch,
):
    sources, home, live = _setup(tmp_path, monkeypatch)
    original = {
        str(path): path.read_bytes()
        for parent in (sources, live)
        for path in parent.rglob("*")
        if path.is_file()
    }
    out = tmp_path / "preview-output"
    report = preview_essential_editions(sources, out)
    assert len(report.subjects) == 1
    item = report.subjects[0]
    assert item.state == "REVIEW"
    assert item.candidate_missing_exam == 1
    assert item.candidate_missing_numeric == 0
    assert item.candidate_compression_percent == 62.0
    assert Path(item.preview_path).is_file()
    assert (out / "preview-report.md").exists()
    assert (out / "preview-report.json").exists()
    assert original == {
        str(path): path.read_bytes()
        for parent in (sources, live)
        for path in parent.rglob("*")
        if path.is_file()
    }
    assert not list(sources.rglob("anteprima*.md"))
    assert not (home / "outputs" / "courses" / "ANATOMIA" / "preview-report.md").exists()


def test_preview_never_uses_source_or_live_outputs_as_destination(
    tmp_path: Path, monkeypatch,
):
    sources, home, _ = _setup(tmp_path, monkeypatch)
    for target in (
        sources,
        sources / "ANATOMIA" / "review",
        home / "outputs",
        home / "outputs" / "courses" / "ANATOMIA" / "preview",
    ):
        with pytest.raises(ValueError, match="fuori dalle dispense"):
            preview_essential_editions(sources, target)
    assert not (sources / "ANATOMIA" / "review").exists()


def test_preview_blocks_unverified_complete(tmp_path: Path, monkeypatch):
    sources, _, _ = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        study_preview, "_manifest_baseline_verified", lambda *args: False,
    )
    output = tmp_path / "unverified-preview"
    report = preview_essential_editions(sources, output)
    assert report.subjects[0].state == "BLOCKED"
    assert report.subjects[0].preview_path is None
    assert len(list(output.glob("*-anteprima.md"))) == 0


def test_preview_does_not_replace_prior_diagnostic(tmp_path: Path, monkeypatch):
    sources, _, _ = _setup(tmp_path, monkeypatch)
    out = tmp_path / "report"
    out.mkdir()
    marker = out / "important.txt"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="già occupata"):
        preview_essential_editions(sources, out)
    assert marker.read_text(encoding="utf-8") == "keep"


def test_subject_filter_and_missing_subject(tmp_path: Path, monkeypatch):
    sources, _, _ = _setup(tmp_path, monkeypatch)
    out = tmp_path / "report"
    report = preview_essential_editions(sources, out, subject="anatomia")
    assert len(report.subjects) == 1
    assert report.subjects[0].subject == "ANATOMIA"
    with pytest.raises(ValueError, match="Materia non trovata"):
        preview_essential_editions(sources, tmp_path / "different", subject="CHIMICA")


def test_cli_help_exposes_non_publishing_preview():
    result = CliRunner().invoke(app, ["courses", "preview-essential", "--help"])
    assert result.exit_code == 0
    # Rich may insert ANSI formatting between "--" and the option name.
    assert "Crea solo bozze" in result.output or "bozze" in result.output
