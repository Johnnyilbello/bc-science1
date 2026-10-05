from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np

from .config import app_home


class CacheDB:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (app_home() / "bc_science.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._schema()

    def _schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS summary_cache (
                cache_key TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS documents (
                source_path TEXT PRIMARY KEY,
                file_hash TEXT NOT NULL,
                title TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_path TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                embedding BLOB NOT NULL,
                dim INTEGER NOT NULL,
                UNIQUE(source_path, chunk_index)
            );
            CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_path);
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def get_summary(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT content FROM summary_cache WHERE cache_key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def set_summary(self, key: str, content: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO summary_cache(cache_key, content) VALUES (?, ?)",
            (key, content),
        )
        self.conn.commit()

    def document_hash(self, source_path: str) -> str | None:
        row = self.conn.execute(
            "SELECT file_hash FROM documents WHERE source_path = ?", (source_path,)
        ).fetchone()
        return row[0] if row else None

    def replace_document(
        self,
        source_path: str,
        file_hash: str,
        title: str,
        chunks: list[str],
        embeddings: list[list[float]],
    ) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM chunks WHERE source_path = ?", (source_path,))
            self.conn.execute(
                """
                INSERT OR REPLACE INTO documents(source_path, file_hash, title, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (source_path, file_hash, title),
            )
            rows = []
            for index, (text, vector) in enumerate(zip(chunks, embeddings, strict=True)):
                arr = np.asarray(vector, dtype=np.float32)
                rows.append((source_path, index, text, arr.tobytes(), arr.size))
            self.conn.executemany(
                """
                INSERT INTO chunks(source_path, chunk_index, text, embedding, dim)
                VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )

    def document_sources(self) -> list[str]:
        rows = self.conn.execute("SELECT source_path FROM documents").fetchall()
        return [source for (source,) in rows]

    def remove_documents(self, source_paths: list[str]) -> int:
        unique = list(dict.fromkeys(source_paths))
        if not unique:
            return 0

        with self.conn:
            self.conn.executemany(
                "DELETE FROM chunks WHERE source_path = ?",
                [(source,) for source in unique],
            )
            self.conn.executemany(
                "DELETE FROM documents WHERE source_path = ?",
                [(source,) for source in unique],
            )
        return len(unique)

    def prune_documents(self, allowed_source_paths: set[str]) -> int:
        """Remove indexed documents and chunks no longer present in a workspace."""
        stale = [
            source
            for source in self.document_sources()
            if source not in allowed_source_paths
        ]
        return self.remove_documents(stale)

    def search(self, query_vector: list[float], limit: int = 6) -> list[dict]:
        q = np.asarray(query_vector, dtype=np.float32)
        q_norm = np.linalg.norm(q)
        if q_norm == 0:
            return []

        scored: list[tuple[float, str, int, str]] = []
        cursor = self.conn.execute(
            "SELECT source_path, chunk_index, text, embedding, dim FROM chunks"
        )
        for source, index, text, blob, dim in cursor:
            vector = np.frombuffer(blob, dtype=np.float32, count=dim)
            denom = float(np.linalg.norm(vector) * q_norm)
            score = float(np.dot(vector, q) / denom) if denom else 0.0
            scored.append((score, source, index, text))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {"score": score, "source": source, "chunk_index": index, "text": text}
            for score, source, index, text in scored[:limit]
        ]

    def stats(self) -> dict[str, int]:
        docs = self.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        chunks = self.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        cached = self.conn.execute("SELECT COUNT(*) FROM summary_cache").fetchone()[0]
        return {"documents": docs, "chunks": chunks, "summaries": cached}
