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


def test_generate_single_chapter_is_ocr_free_and_immediately_readable(
    tmp_path, monkeypatch,
):
    scan, source, complete, output = _prepared_course(tmp_path, monkeypatch)
    called = []

    class FakeSummarizer:
        def __init__(self, *args, **kwargs):
            pass

        def build_study_chapter(self, title, chapter, *, progress=None):
            called.append(title)
            assert title == "Scheletro"
            assert "Lo scheletro assile" in chapter
            return "## Scheletro\n- Il cranio appartiene allo scheletro assile."

        def close(self):
            pass

    def forbidden(*args, **kwargs):
        raise AssertionError("Non deve rileggere PDF, fare OCR, embedding o Completa")

    monkeypatch.setattr(courses, "Summarizer", FakeSummarizer)
    monkeypatch.setattr(courses, "ingest_files", forbidden)
    monkeypatch.setattr(courses, "_full_rebuild", forbidden)
    logs = []
    result = courses.build_essential_chapter(
        scan, AppConfig(), "Scheletro", progress=logs.append,
    )
    assert "Il cranio" in result
    assert called == ["Scheletro"]
    assert courses.read_essential_chapter_previews(scan, complete)["Scheletro"] == result
    assert output.read_text(encoding="utf-8") == complete

    again = courses.build_essential_chapter(scan, AppConfig(), "Scheletro")
    assert again == result
    assert called == ["Scheletro"]
    with pytest.raises(ValueError, match="Argomento non presente"):
        courses.build_essential_chapter(scan, AppConfig(), "../../NonEsiste")

    source.write_text("Modifica delle dispense", encoding="utf-8")
    with pytest.raises(ValueError, match="dispense modificate"):
        courses.build_essential_chapter(
            courses.scan_course(scan.path), AppConfig(), "Scheletro",
        )


def test_desktop_can_read_generated_chapter_without_complete_essential(
    tmp_path, monkeypatch,
):
    from bc_science.ui import DesktopAPI

    scan, _, complete, _ = _prepared_course(tmp_path, monkeypatch)
    preview = "## Scheletro\n- Scheletro assile e cranio."
    path = courses._essential_chapter_preview_path(scan, "Scheletro")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "schema": 1,
            "title": "Scheletro",
            "pipeline": courses.STUDY_PROMPT_VERSION,
            "source_summary_sha256": courses._summary_sha256(complete),
            "chapter": preview,
        }), encoding="utf-8",
    )
    result = DesktopAPI(scan.path.parent).read("ANATOMIA", "essential")
    assert result["ready"]
    assert "Scheletro assile" in result["chapters"][0]["html"]
    assert "## Scheletro" not in result["chapters"][0]["html"]
