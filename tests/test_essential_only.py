"""Explicit Essential-only generation never rebuilds or re-OCRs Complete."""
import json
from pathlib import Path

import pytest

from bc_science import courses
from bc_science.config import AppConfig


def _prepared_course(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    folder = tmp_path / "SCIENZE MOTORIE" / "ANATOMIA"
    folder.mkdir(parents=True)
    source = folder / "Scheletro.txt"
    source.write_text("Lo scheletro assile comprende il cranio.", encoding="utf-8")
    scan = courses.scan_course(folder)
    complete = (
        "# Anatomia completa\n\n"
        "## Indice degli argomenti\n- Scheletro\n\n---\n\n"
        "## Scheletro\n### In parole semplici\n"
        "Lo scheletro assile comprende il cranio.\n"
        "### Da ricordare per l'esame\n"
        "- Lo scheletro assile comprende il cranio.\n"
    )
    path = courses.course_output_path(scan)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(complete, encoding="utf-8")
    pdf = courses.course_pdf_path(scan)
    pdf.write_bytes(b"%PDF-test")
    courses._write_manifest(
        scan, courses._snapshot(scan), path, complete,
        {"Scheletro.txt": "Scheletro"},
        pdf_path=pdf, pdf_pages=2,
        summary_pipeline="summary-old-pipeline",
    )
    return scan, source, complete, path


def test_essential_only_refuses_absent_or_unverified_complete(tmp_path, monkeypatch):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    folder = tmp_path / "files" / "ANATOMIA"
    folder.mkdir(parents=True)
    (folder / "Scheletro.txt").write_text("Cranio", encoding="utf-8")
    with pytest.raises(ValueError, match="Genera prima Completa"):
        courses.build_essential_only(courses.scan_course(folder), AppConfig())


def test_essential_only_uses_legacy_complete_without_ocr_or_rebuild(
    tmp_path, monkeypatch,
):
    scan, source, complete, output = _prepared_course(tmp_path, monkeypatch)
    calls = {"study": 0, "pdf": 0}
    study = (
        "# Anatomia ripasso\n\n## Indice degli argomenti\n- Scheletro"
        "\n\n---\n\n## Scheletro\n"
        "### Concetti chiave\n- Lo scheletro assile comprende il cranio.\n"
    )

    class FakeSummarizer:
        def __init__(self, *args, **kwargs):
            pass

        def build_study_summary(self, text, title, *, progress=None, checkpoint_path=None):
            assert text == complete
            calls["study"] += 1
            return study

        def close(self):
            pass

    def fake_pdf(text, path, title):
        calls["pdf"] += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-test")
        return 1

    def forbidden(*args, **kwargs):
        raise AssertionError("Essential-only must not extract or rebuild PDF sources")

    monkeypatch.setattr(courses, "Summarizer", FakeSummarizer)
    monkeypatch.setattr(courses, "export_pdf", fake_pdf)
    monkeypatch.setattr(courses, "ingest_files", forbidden)
    monkeypatch.setattr(courses, "_full_rebuild", forbidden)
    messages = []
    first = courses.build_essential_only(scan, AppConfig(), progress=messages.append)
    assert first.study_generated
    assert first.coverage_complete
    assert calls == {"study": 1, "pdf": 1}
    assert "senza" not in output.read_text(encoding="utf-8")
    assert output.read_text(encoding="utf-8") == complete
    assert any("nessun OCR" in x for x in messages)

    manifest = json.loads(courses._manifest_path(scan).read_text(encoding="utf-8"))
    assert manifest["summary_pipeline"] == "summary-old-pipeline"
    assert manifest["study"]["pipeline"] == courses.STUDY_PROMPT_VERSION

    again = courses.build_essential_only(scan, AppConfig())
    assert not again.study_generated
    assert calls == {"study": 1, "pdf": 1}

    source.write_text("Il testo originale e cambiato.", encoding="utf-8")
    with pytest.raises(ValueError, match="Genera prima Completa"):
        courses.build_essential_only(courses.scan_course(scan.path), AppConfig())
    assert output.read_text(encoding="utf-8") == complete
