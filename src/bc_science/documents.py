from __future__ import annotations

import hashlib
import os
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from docx import Document

from .config import app_home

SUPPORTED = {".pdf", ".txt", ".md", ".docx"}


@dataclass(slots=True)
class ExtractedDocument:
    path: Path
    text: str
    pages: int = 0
    ocr_pages: int = 0


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_text(text: str) -> str:
    lines = [
        line.rstrip()
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    out: list[str] = []
    blank = False
    for line in lines:
        stripped = " ".join(line.split())
        if not stripped:
            if out and not blank:
                out.append("")
            blank = True
            continue
        out.append(stripped)
        blank = False
    return "\n".join(out).strip()


def _extract_pdf(path: Path, ocr: bool = True) -> ExtractedDocument:
    parts: list[str] = []
    ocr_pages = 0
    with pymupdf.open(path) as doc:
        page_count = len(doc)
        for index, page in enumerate(doc):
            text = page.get_text("text", sort=True).strip()
            if ocr and len(text) < 80:
                ocr_text = ""
                try:
                    textpage = page.get_textpage_ocr(
                        language=os.getenv("BC_SCIENCE_OCR_LANG", "eng")
                    )
                    ocr_text = page.get_text(
                        "text",
                        textpage=textpage,
                        sort=True,
                    ).strip()
                except (RuntimeError, OSError):
                    ocr_text = ""

                if len(ocr_text) > len(text):
                    text = ocr_text
                    ocr_pages += 1

            if text:
                parts.append(f"\n--- Pagina {index + 1} ---\n{text}")
    return ExtractedDocument(path, normalize_text("\n".join(parts)), page_count, ocr_pages)


def _extract_docx(path: Path) -> ExtractedDocument:
    doc = Document(path)
    text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    return ExtractedDocument(path, normalize_text(text))


def extract_document(path: Path, *, ocr: bool = True) -> ExtractedDocument:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(path, ocr=ocr)
    if suffix == ".docx":
        return _extract_docx(path)
    if suffix in {".txt", ".md"}:
        return ExtractedDocument(
            path,
            normalize_text(path.read_text(encoding="utf-8", errors="replace")),
        )
    raise ValueError(f"Formato non supportato: {path.suffix}")


def _safe_extract_zip(path: Path) -> Path:
    digest = file_sha256(path)[:16]
    target = app_home() / "imports" / digest
    marker = target / ".ready"
    if marker.exists():
        return target

    temp = target.with_name(target.name + ".tmp")
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir(parents=True, exist_ok=True)
    root = temp.resolve()

    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            member = Path(info.filename)
            if member.is_absolute() or ".." in member.parts:
                continue
            destination = (temp / member).resolve()
            if root not in destination.parents and destination != root:
                continue
            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, destination.open("wb") as dst:
                shutil.copyfileobj(src, dst)

    if target.exists():
        shutil.rmtree(target)
    temp.rename(target)
    marker.touch()
    return target


def iter_source_files(source: Path) -> list[Path]:
    source = source.expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(source)

    if source.is_file() and source.suffix.lower() == ".zip":
        source = _safe_extract_zip(source)

    if source.is_file():
        return [source] if source.suffix.lower() in SUPPORTED else []

    return sorted(
        p
        for p in source.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED
    )


def chunk_text(text: str, chunk_chars: int = 5200, overlap: int = 450) -> list[str]:
    if not text.strip():
        return []
    if len(text) <= chunk_chars:
        return [text.strip()]

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > chunk_chars:
            if current:
                chunks.append(current.strip())
                current = ""
            start = 0
            step = max(1, chunk_chars - overlap)
            while start < len(paragraph):
                piece = paragraph[start:start + chunk_chars].strip()
                if piece:
                    chunks.append(piece)
                start += step
            continue

        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= chunk_chars:
            current = candidate
        else:
            chunks.append(current.strip())
            tail = current[-overlap:] if overlap and current else ""
            current = f"{tail}\n\n{paragraph}".strip()

    if current:
        chunks.append(current.strip())
    return chunks
