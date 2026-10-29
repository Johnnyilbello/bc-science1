"""OCR extraction must be reused across indexing, summaries and app restarts."""
from pathlib import Path

import pymupdf

from bc_science.cache import CacheDB
from bc_science.documents import extract_document_cached


def _blank_pdf(path: Path) -> None:
    doc = pymupdf.open()
    doc.new_page()
    doc.save(path)
    doc.close()


def test_ocr_extracted_only_once_across_connections(tmp_path: Path, monkeypatch):
    pdf = tmp_path / "anatomia.pdf"
    _blank_pdf(pdf)
    db_path = tmp_path / "course.db"
    calls = []

    def fake_to_markdown(*args, **kwargs):
        calls.append(kwargs)
        return [{"text": "I processi ossei forniscono inserzioni muscolari." * 3}]

    monkeypatch.setattr(
        "bc_science.documents.pymupdf4llm.to_markdown", fake_to_markdown,
    )

    db = CacheDB(db_path)
    try:
        first = extract_document_cached(pdf, db)
        second = extract_document_cached(pdf, db)
        assert first.text == second.text
        assert first.ocr_pages == second.ocr_pages == 1
    finally:
        db.close()

    db = CacheDB(db_path)
    try:
        third = extract_document_cached(pdf, db)
        assert third.text == first.text
        assert len(calls) == 1
    finally:
        db.close()


def test_changed_document_invalidates_extraction_cache(tmp_path: Path, monkeypatch):
    path = tmp_path / "fisiologia.txt"
    path.write_text("Actina", encoding="utf-8")
    db = CacheDB(tmp_path / "cache.db")
    try:
        assert extract_document_cached(path, db).text == "Actina"
        path.write_text("Miosina", encoding="utf-8")
        assert extract_document_cached(path, db).text == "Miosina"
    finally:
        db.close()


def test_changed_ocr_policy_invalidates_extraction_cache(tmp_path: Path, monkeypatch):
    pdf = tmp_path / "anatomia.pdf"
    _blank_pdf(pdf)
    calls = []

    def fake_to_markdown(*args, **kwargs):
        calls.append(kwargs["ocr_dpi"])
        return [{"text": "Legamenti articolari e strutture ossee." * 4}]

    monkeypatch.setattr(
        "bc_science.documents.pymupdf4llm.to_markdown", fake_to_markdown,
    )
    db = CacheDB(tmp_path / "cache.db")
    try:
        monkeypatch.setenv("BC_SCIENCE_OCR_DPI", "150")
        extract_document_cached(pdf, db)
        monkeypatch.setenv("BC_SCIENCE_OCR_DPI", "200")
        extract_document_cached(pdf, db)
        extract_document_cached(pdf, db)
        assert calls == [150, 200]
    finally:
        db.close()


def test_invalid_extracted_text_is_not_persisted(tmp_path: Path, monkeypatch):
    pdf = tmp_path / "vuoto.pdf"
    _blank_pdf(pdf)
    calls = []

    def fake_to_markdown(*args, **kwargs):
        calls.append(1)
        return []

    monkeypatch.setattr(
        "bc_science.documents.pymupdf4llm.to_markdown", fake_to_markdown,
    )
    monkeypatch.setattr("bc_science.documents._legacy_ocr_page", lambda page: "")
    db = CacheDB(tmp_path / "cache.db")
    try:
        assert not extract_document_cached(pdf, db).text
        assert not extract_document_cached(pdf, db).text
        assert len(calls) == 2
    finally:
        db.close()
