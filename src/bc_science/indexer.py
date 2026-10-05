from __future__ import annotations

from pathlib import Path

from rich.console import Console

from .cache import CacheDB
from .config import AppConfig
from .documents import (
    chunk_text,
    extract_document,
    file_sha256,
    is_generated_artifact,
    iter_source_files,
)
from .ollama_client import OllamaClient

console = Console()

DOCUMENT_INSTRUCTION = (
    "Represent this Italian university sports-science passage for semantic retrieval. "
    "Preserve scientific terms, mechanisms, definitions and exam-relevant relationships.\n\n"
)
QUERY_INSTRUCTION = (
    "Retrieve the passages from Italian university sports-science materials that best answer "
    "this student's question.\n\n"
)


def ingest_files(
    files: list[Path],
    config: AppConfig,
    *,
    force: bool = False,
    db_path: Path | None = None,
    prune_missing: bool = False,
) -> dict[str, int]:
    client = OllamaClient(config.ollama_url)
    resolved_files = [
        path.expanduser().resolve()
        for path in files
        if not is_generated_artifact(path)
    ]
    db = CacheDB(db_path)
    indexed = 0
    skipped = 0
    chunks_total = 0
    removed = 0

    try:
        generated_sources = [
            source
            for source in db.document_sources()
            if is_generated_artifact(Path(source))
        ]
        removed += db.remove_documents(generated_sources)

        if prune_missing:
            removed += db.prune_documents({str(path) for path in resolved_files})

        for path in resolved_files:
            digest = file_sha256(path)
            key = str(path)
            if not force and db.document_hash(key) == digest:
                skipped += 1
                continue

            console.print(f"Indicizzo [cyan]{path.name}[/]...")
            extracted = extract_document(path)
            chunks = chunk_text(
                extracted.text,
                chunk_chars=config.chunk_chars,
                overlap=config.chunk_overlap,
            )
            if not chunks:
                continue

            embeddings: list[list[float]] = []
            batch_size = 12
            for start in range(0, len(chunks), batch_size):
                batch = [
                    DOCUMENT_INSTRUCTION + chunk
                    for chunk in chunks[start:start + batch_size]
                ]
                embeddings.extend(client.embed(config.embedding_model, batch))

            db.replace_document(key, digest, path.stem, chunks, embeddings)
            indexed += 1
            chunks_total += len(chunks)
    finally:
        db.close()

    return {
        "files_found": len(resolved_files),
        "indexed": indexed,
        "skipped": skipped,
        "chunks": chunks_total,
        "removed": removed,
    }


def ingest(
    source: Path,
    config: AppConfig,
    *,
    force: bool = False,
    db_path: Path | None = None,
) -> dict[str, int]:
    return ingest_files(
        iter_source_files(source),
        config,
        force=force,
        db_path=db_path,
    )


def retrieve(question: str, config: AppConfig, limit: int | None = None) -> list[dict]:
    client = OllamaClient(config.ollama_url)
    vector = client.embed(config.embedding_model, [QUERY_INSTRUCTION + question])[0]
    db = CacheDB()
    try:
        return db.search(vector, limit=limit or config.context_chunks)
    finally:
        db.close()
