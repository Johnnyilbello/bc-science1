import zipfile
from pathlib import Path

import pymupdf

from bc_science.documents import (
    chunk_text,
    extract_document,
    is_generated_artifact,
    iter_source_files,
    normalize_text,
)


def test_normalize_text_collapses_spaces_and_blank_lines():
    raw = "Uno   due\r\n\r\n\r\nTre"
    assert normalize_text(raw) == "Uno due\n\nTre"


def test_chunk_text_splits_long_material():
    text = "\n\n".join(["paragrafo " + ("x" * 900) for _ in range(8)])
    chunks = chunk_text(text, chunk_chars=1600, overlap=100)
    assert len(chunks) > 1
    assert all(chunk.strip() for chunk in chunks)


def test_pdf_native_fast_path_preserves_pages(tmp_path: Path):
    pdf = tmp_path / "native.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Prima pagina di fisiologia con testo digitale.")
    page = doc.new_page()
    page.insert_text((72, 72), "Seconda pagina sul muscolo scheletrico.")
    doc.save(pdf)
    doc.close()

    extracted = extract_document(pdf, ocr=False)

    assert extracted.pages == 2
    assert extracted.ocr_pages == 0
    assert "--- Pagina 1 ---" in extracted.text
    assert "--- Pagina 2 ---" in extracted.text
    assert "Prima pagina di fisiologia" in extracted.text
    assert "Seconda pagina sul muscolo" in extracted.text


def test_pdf_weak_page_uses_llm_recovery(tmp_path: Path, monkeypatch):
    pdf = tmp_path / "scan.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(pdf)
    doc.close()

    calls = []

    def fake_to_markdown(path, **kwargs):
        calls.append((path, kwargs))
        return [
            {
                "text": (
                    "Testo OCR recuperato correttamente dalla pagina scannerizzata. "
                    "Il contenuto e sufficientemente lungo da superare la soglia "
                    "e viene quindi preferito al testo nativo vuoto."
                )
            }
        ]

    monkeypatch.setattr("bc_science.documents.pymupdf4llm.to_markdown", fake_to_markdown)

    extracted = extract_document(pdf)

    assert extracted.pages == 1
    assert extracted.ocr_pages == 1
    assert "Testo OCR recuperato correttamente" in extracted.text
    assert calls
    _, kwargs = calls[0]
    assert kwargs["pages"] == [0]
    assert kwargs["page_chunks"] is True
    assert kwargs["use_ocr"] is True


def test_pdf_recovery_failure_does_not_break_ingest(tmp_path: Path, monkeypatch):
    pdf = tmp_path / "blank.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(pdf)
    doc.close()

    def broken_to_markdown(*args, **kwargs):
        raise RuntimeError("layout unavailable")

    monkeypatch.setattr("bc_science.documents.pymupdf4llm.to_markdown", broken_to_markdown)
    monkeypatch.setattr("bc_science.documents._legacy_ocr_page", lambda page: "")

    extracted = extract_document(pdf)

    assert extracted.pages == 1
    assert extracted.ocr_pages == 0
    assert extracted.text == ""


def test_zip_import_blocks_parent_traversal(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    archive = tmp_path / "course.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("lezione.txt", "contenuto")
        zf.writestr("../escape.txt", "non deve uscire")

    files = iter_source_files(archive)
    assert [path.name for path in files] == ["lezione.txt"]
    assert not (tmp_path / "escape.txt").exists()


def test_generated_artifacts_are_recognized():
    assert is_generated_artifact(Path("SCIENZE-MOTORIE-eCampus-2026-2027-dispensa-finale.pdf"))
    assert is_generated_artifact(Path("ANATOMIA-riassunto-unico.md"))
    assert is_generated_artifact(Path("riassunto-studio.pdf"))
    assert is_generated_artifact(Path("approfondimento-scientifico.md"))
    assert is_generated_artifact(Path("benchmark-20261005.md"))
    assert not is_generated_artifact(Path("Contrazione muscolare.pdf"))


def test_iter_source_files_excludes_generated_outputs(tmp_path: Path):
    (tmp_path / "Contrazione muscolare.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "corso-dispensa-finale.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "riassunto-unico.md").write_text("output", encoding="utf-8")
    (tmp_path / "lezione.txt").write_text("fonte", encoding="utf-8")

    files = iter_source_files(tmp_path)
    assert [path.name for path in files] == [
        "Contrazione muscolare.pdf",
        "lezione.txt",
    ]
