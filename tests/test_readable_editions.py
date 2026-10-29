"""Two real, readable summary editions; fast Essential has no LLM calls."""
from pathlib import Path

import pytest

from bc_science import courses
from bc_science.config import AppConfig
from bc_science.fast_audit import audit_fast_essential
from bc_science.study_editions import (
    FAST_STUDY_VERSION,
    make_fast_essential,
    make_fast_essential_chapter,
    readable_complete_chapter,
)


def _example() -> str:
    return (
        "# Anatomia\n\n## Indice degli argomenti\n- Stomaco\n- Ossa\n"
        "\n---\n\n## Stomaco\n"
        "### In parole semplici\n"
        "Lo stomaco partecipa alla digestione degli alimenti e mescola il cibo. "
        "Il movimento dello stomaco rende più omogeneo il contenuto alimentare.\n"
        "### Spiegazione ordinata\n"
        "La parete dello stomaco è composta da diversi strati muscolari. "
        "La digestione avviene attraverso processi meccanici e chimici. "
        "I succhi gastrici svolgono un ruolo importante nella digestione.\n"
        "### Da ricordare per l'esame\n"
        "- Lo stomaco presenta 3 strati muscolari.\n"
        "\n---\n\n## Ossa\n"
        "### In parole semplici\n"
        "Le ossa sostengono il corpo e proteggono gli organi. "
        "Le articolazioni consentono diversi movimenti.\n"
        "### Spiegazione ordinata\n"
        "Le ossa formano lo scheletro e partecipano al movimento. "
        "Il cranio protegge l'encefalo.\n"
        "### Da ricordare per l'esame\n"
        "- Lo scheletro comprende una parte assile e una appendicolare.\n"
    )


def test_fast_essential_is_a_unified_readable_summary():
    text = _example()
    essential, records = make_fast_essential(text, "Anatomia Essenziale")
    assert len(records) == 2
    assert essential.count("## Stomaco") == 1
    assert essential.count("## Ossa") == 1
    assert "3 strati muscolari" in essential
    assert "In parole semplici" not in essential
    assert "Spiegazione ordinata" not in essential
    assert "### " not in essential
    assert len(essential.split()) < len(text.split())
    assert "digestione" in essential and "organi" in essential


def test_complete_is_one_readable_summary_without_duplicate_sections():
    complete = _example().split("## Stomaco\n", 1)[1].split("\n---\n\n## Ossa", 1)[0]
    clean = readable_complete_chapter("Stomaco", "## Stomaco\n" + complete)
    assert clean.startswith("## Stomaco")
    assert "In parole semplici" not in clean
    assert "Spiegazione ordinata" not in clean
    assert "3 strati muscolari" in clean
    assert "La digestione" in clean


def test_fast_audit_expects_shorter_text_not_97_percent_all_facts():
    text = _example()
    essential, _ = make_fast_essential(text, "Anatomia Essenziale")
    audit = audit_fast_essential(text, essential, source_documents=2)
    assert audit.status in {"PASS", "WARN"}
    assert audit.chapters_present == 2
    assert not audit.missing_exam_number_chapters
    assert audit.compression_percent > 0


def test_fast_audit_rejects_missing_exam_number_and_missing_chapter():
    text = _example()
    essential, _ = make_fast_essential(text, "Anatomia Essenziale")
    bad = essential.replace("3 strati muscolari", "strati muscolari")
    assert audit_fast_essential(text, bad).status == "FAIL"
    no_ossa = essential.split("\n---\n\n## Ossa\n", 1)[0]
    assert audit_fast_essential(text, no_ossa).status == "FAIL"


def _source_course(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    root = tmp_path / "SCIENZE MOTORIE"
    folder = root / "ANATOMIA"
    folder.mkdir(parents=True)
    (folder / "stomaco.txt").write_text("Lo stomaco ha 3 strati.", encoding="utf-8")
    scan = courses.scan_course(folder)
    complete = _example()
    output = courses.course_output_path(scan)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(complete, encoding="utf-8")
    courses._write_manifest(
        scan, courses._snapshot(scan), output, complete,
        {"stomaco.txt": "Stomaco"},
        pdf_path=None, pdf_pages=0,
        summary_pipeline="summary-previous-verified",
    )
    return scan, complete, output


def test_fast_bulk_uses_no_ollama_preserves_original_and_reuses_pdf(
    tmp_path: Path, monkeypatch,
):
    scan, complete, output = _source_course(tmp_path, monkeypatch)
    pdf_calls = []

    def pdf_export(content, destination, title):
        pdf_calls.append(content)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"%PDF-FAKE")
        return 3

    monkeypatch.setattr(courses, "export_pdf", pdf_export)
    monkeypatch.setattr(
        courses, "Summarizer",
        lambda *args, **kwargs: pytest.fail("Ollama must not be called"),
    )
    logs = []
    first = courses.build_fast_essential_only(
        scan, AppConfig(), progress=logs.append,
    )
    assert first.study_generated
    assert first.audit_status in {"PASS", "WARN"}
    assert output.read_text(encoding="utf-8") == complete
    assert pdf_calls
    assert "Nessuna chiamata Ollama" in "\n".join(logs)
    manifest = courses._load_manifest(scan)
    assert manifest["summary_pipeline"] == "summary-previous-verified"
    assert manifest["study"]["pipeline"] == FAST_STUDY_VERSION
    again = courses.build_fast_essential_only(scan, AppConfig())
    assert not again.study_generated
    assert len(pdf_calls) == 1
    assert courses.audit_course(scan).status in {"PASS", "WARN"}


def test_fast_bulk_rejects_source_changes(tmp_path: Path, monkeypatch):
    scan, _, output = _source_course(tmp_path, monkeypatch)
    (scan.path / "stomaco.txt").write_text(
        "Dispensa cambiata", encoding="utf-8",
    )
    with pytest.raises(ValueError, match="dispense modificate"):
        courses.build_fast_essential_only(
            courses.scan_course(scan.path), AppConfig(),
        )
