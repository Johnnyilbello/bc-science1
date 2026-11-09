from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from bc_science.cli import app
from bc_science.courses import (
    _snapshot,
    _write_manifest,
    course_output_path,
    scan_courses,
)
from bc_science.universal_audit import (
    REPORT_VERSION,
    verify_courses,
    write_verification_reports,
)


def _make_subject(root: Path, name: str, filename: str = "lezione.txt") -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    source = folder / filename
    source.write_text("Materiale della materia.", encoding="utf-8")
    return source


def test_three_unrelated_subjects_audited_offline_without_ollama(
    tmp_path: Path, monkeypatch,
):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    root = tmp_path / "Universita"
    for name in ("ANATOMIA", "MATEMATICA", "LETTERATURA"):
        _make_subject(root, name)

    result = verify_courses(root)

    assert result.version == REPORT_VERSION
    assert result.status == "FAIL"
    assert {item.name for item in result.subjects} == {
        "ANATOMIA", "MATEMATICA", "LETTERATURA"
    }
    assert all(item.status == "FAIL" for item in result.subjects)
    assert all(item.source_count == 1 for item in result.subjects)
    assert all(item.sources[0].status == "NON_COLLEGATO" for item in result.subjects)

    markdown, report = write_verification_reports(result, tmp_path / "reports")
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "FAIL"
    assert len(payload["subjects"]) == 3
    assert "non certifica la correttezza scientifica" in markdown.read_text(
        encoding="utf-8"
    )


def test_manifest_coverage_is_traced_then_stale_hash_is_detected(
    tmp_path: Path, monkeypatch,
):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    root = tmp_path / "Universita"
    source = _make_subject(root, "STORIA")
    scan = scan_courses(root)[0]
    summary = course_output_path(scan)
    summary.parent.mkdir(parents=True, exist_ok=True)
    text = (
        "# STORIA\n\n## Indice degli argomenti\n"
        "- lezione\n\n---\n\n## lezione\n"
        "### In parole semplici\nContenuto delle dispense.\n"
        "### Da ricordare per l'esame\n- Concetto storico.\n"
    )
    summary.write_text(text, encoding="utf-8")
    _write_manifest(
        scan, _snapshot(scan), summary, text, {"lezione.txt": "lezione"},
        pdf_path=None, pdf_pages=0,
    )

    ready = verify_courses(root).subjects[0]
    assert ready.status == "WARN"  # PDF/studio assenti, ma fonti tracciate.
    assert ready.coverage == "completa"
    assert ready.mapped_sources == 1
    assert ready.sources[0].status == "OK"
    assert ready.sources[0].chapter == "lezione"

    source.write_text("Nuova lezione storica.", encoding="utf-8")
    stale = verify_courses(root).subjects[0]
    assert stale.status == "FAIL"
    assert stale.coverage == "incompleta"
    assert stale.mapped_sources == 0
    assert stale.sources[0].status == "MODIFICATO"


def test_cli_verify_strict_fails_and_preserves_reports(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    root = tmp_path / "Universita"
    _make_subject(root, "CHIMICA")
    report_dir = tmp_path / "run-reports"
    runner = CliRunner()

    non_strict = runner.invoke(
        app, ["courses", "verify", str(root), "--output-dir", str(report_dir)]
    )
    assert non_strict.exit_code == 0, non_strict.output
    assert (report_dir / "verification.json").exists()

    strict = runner.invoke(
        app, ["courses", "verify", str(root), "--strict", "--output-dir", str(report_dir)]
    )
    assert strict.exit_code == 1, strict.output


def test_empty_root_is_not_reported_as_pass(tmp_path: Path):
    empty = tmp_path / "no-subjects"
    empty.mkdir()
    result = verify_courses(empty)
    assert result.status == "FAIL"
    assert result.subjects == ()
