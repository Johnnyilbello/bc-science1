"""Local, source-original grounding for suspicious BC Science summary passages.

Only reads previously extracted or indexed original files from the course SQLite
workspace. Does not OCR, call a model, send network requests or use the Complete
summary as evidence. Page numbers are cited only when cached page markers exist.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

ENGINE_VERSION = "evidence-original-fts-v2-marked-chunks"
_PAGE = re.compile(r"(?m)^--- Pagina (\d+) ---\s*$")
_WORD = re.compile(r"[a-zà-ÿ0-9]{4,}", re.IGNORECASE)
_SKIP = {
    "della", "delle", "degli", "dallo", "dalla", "dello", "nella", "nelle",
    "questo", "questa", "quello", "quella", "sono", "essere", "anche",
    "perché", "come", "quando", "dopo", "prima", "dove", "dentro",
    "attraverso", "circa", "tutte", "tutti", "loro", "allora",
    "all'interno", "materiale", "capitolo", "apparato",
}


@dataclass(frozen=True, slots=True)
class OriginalEvidence:
    source_path: str
    page: int | None
    block: int | None
    excerpt: str
    rank: int
    provenance: str  # "cached_page" or "indexed_chunk"

    def to_dict(self) -> dict:
        return asdict(self)


def _parts(text: str) -> list[tuple[int | None, int | None, str, str]]:
    matches = list(_PAGE.finditer(text))
    if not matches:
        return [(None, None, text, "cached_page")] if text.strip() else []
    result = []
    for i, match in enumerate(matches):
        body = text[match.end():matches[i + 1].start() if i + 1 < len(matches) else None]
        if body.strip():
            result.append((int(match.group(1)), None, body.strip(), "cached_page"))
    return result


def _terms(query: str) -> list[str]:
    words = []
    for value in _WORD.findall(query.casefold()):
        if value not in _SKIP and value not in words:
            words.append(value)
    return words[:12]


def _excerpt(body: str, terms: list[str], width: int = 1500) -> str:
    if len(body) <= width:
        return body.strip()
    low = body.casefold()
    # Query terms are ordered by relevance: anchor at the first available
    # concept, not at the earliest generic word in a long cached page.
    anchor = next((low.find(word) for word in terms if word in low), 0)
    start = max(0, anchor - 250)
    end = min(len(body), start + width)
    return body[start:end].strip()


class OriginalEvidenceIndex:
    """FTS5 BM25 index, incrementally populated from the original-file cache.

    A matching sha256 from the manifest is mandatory for every source. A
    changed file is not indexed or used until the existing document pipeline
    has extracted the new version. If FTS5 is absent, lexical fallback works.
    """

    def __init__(self, db_path: Path, file_hashes: dict[str, str]):
        self.path = Path(db_path)
        self.file_hashes = dict(file_hashes)
        self.conn: sqlite3.Connection | None = None

    def __enter__(self):
        if not self.path.is_file():
            raise ValueError("Indice delle dispense originali non disponibile.")
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._schema()
        self._populate()
        return self

    def __exit__(self, *_exc):
        if self.conn is not None:
            self.conn.close()
        self.conn = None

    def _schema(self):
        assert self.conn is not None
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS evidence_sources (
                source_path TEXT PRIMARY KEY, file_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evidence_pages (
                id INTEGER PRIMARY KEY,
                source_path TEXT NOT NULL,
                page INTEGER,
                block INTEGER,
                body TEXT NOT NULL,
                provenance TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_evidence_source
                ON evidence_pages(source_path);
        """)
        try:
            existing = self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='evidence_pages_fts'"
            ).fetchone() is not None
            self.conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS evidence_pages_fts USING "
                "fts5(body, content='evidence_pages', content_rowid='id', "
                "tokenize='unicode61 remove_diacritics 2')"
            )
            self.conn.executescript("""
                CREATE TRIGGER IF NOT EXISTS ep_ai AFTER INSERT ON evidence_pages BEGIN
                    INSERT INTO evidence_pages_fts(rowid,body)
                    VALUES (new.id,new.body);
                END;
                CREATE TRIGGER IF NOT EXISTS ep_ad AFTER DELETE ON evidence_pages BEGIN
                    INSERT INTO evidence_pages_fts(evidence_pages_fts,rowid,body)
                    VALUES ('delete',old.id,old.body);
                END;
                CREATE TRIGGER IF NOT EXISTS ep_au AFTER UPDATE ON evidence_pages BEGIN
                    INSERT INTO evidence_pages_fts(evidence_pages_fts,rowid,body)
                    VALUES ('delete',old.id,old.body);
                    INSERT INTO evidence_pages_fts(rowid,body)
                    VALUES (new.id,new.body);
                END;
            """)
            if not existing:
                self.conn.execute(
                    "INSERT INTO evidence_pages_fts(evidence_pages_fts) "
                    "VALUES ('rebuild')"
                )
            self.fts_available = True
        except sqlite3.OperationalError:
            self.fts_available = False
        self.conn.commit()

    def _populate(self) -> None:
        assert self.conn is not None
        db = self.conn
        with db:
            for source, expected in self.file_hashes.items():
                extracted = db.execute(
                    "SELECT text FROM extracted_documents WHERE source_path=? AND file_hash=?",
                    (source, expected),
                ).fetchone()
                # The same PDF can yield better text after OCR/vision updates.
                # Provenance still uses the original file hash; only this derived
                # evidence-cache fingerprint includes the extracted contents.
                fingerprint = expected + ":" + ENGINE_VERSION + ":" + hashlib.sha256(
                    str(extracted[0]).encode("utf-8") if extracted else b"indexed-fallback"
                ).hexdigest()
                current = db.execute(
                    "SELECT file_hash FROM evidence_sources WHERE source_path=?", (source,),
                ).fetchone()
                if current and current[0] == fingerprint:
                    continue
                parts = _parts(str(extracted[0])) if extracted else []
                if not parts:
                    document = db.execute(
                        "SELECT file_hash FROM documents WHERE source_path=?",
                        (source,),
                    ).fetchone()
                    if document and document[0] == expected:
                        chunks = db.execute(
                            "SELECT chunk_index,text FROM chunks WHERE source_path=? "
                            "ORDER BY chunk_index",
                            (source,),
                        ).fetchall()
                        parts = []
                        for row in chunks:
                            chunk = str(row[1])
                            if not chunk.strip():
                                continue
                            block = int(row[0]) + 1
                            if _PAGE.search(chunk):
                                # A page is cited ONLY if the indexed source
                                # itself contains a page marker.
                                parts.extend(
                                    (page, block, body, "indexed_chunk")
                                    for page, _unused, body, _origin in _parts(chunk)
                                )
                            else:
                                parts.append((None, block, chunk, "indexed_chunk"))
                db.execute(
                    "DELETE FROM evidence_pages WHERE source_path=?", (source,),
                )
                db.execute(
                    "DELETE FROM evidence_sources WHERE source_path=?", (source,),
                )
                if parts:
                    db.executemany(
                        "INSERT INTO evidence_pages "
                        "(source_path,page,block,body,provenance) VALUES (?,?,?,?,?)",
                        [(source, page, block, body, origin)
                         for page, block, body, origin in parts],
                    )
                    db.execute(
                        "INSERT INTO evidence_sources(source_path,file_hash) "
                        "VALUES (?,?)", (source, fingerprint),
                    )

    def search_semantic(
        self, query_vector: list[float], *, limit: int = 4,
    ) -> list[OriginalEvidence]:
        """Optional semantic fallback from existing original-file embeddings.

        The caller supplies a locally produced vector; this method never
        invokes Ollama. Validates original file hashes before accepting hits.
        """
        from .cache import CacheDB

        if not self.file_hashes:
            return []
        db = CacheDB(self.path)
        try:
            hits = db.search(query_vector, limit=max(16, limit * 5))
            output: list[OriginalEvidence] = []
            for hit in hits:
                source = str(hit["source"])
                if source not in self.file_hashes:
                    continue
                file_hash = db.document_hash(source)
                if file_hash != self.file_hashes[source]:
                    continue
                output.append(OriginalEvidence(
                    source_path=source,
                    page=None,
                    block=int(hit["chunk_index"]) + 1,
                    excerpt=str(hit["text"])[:1800],
                    rank=len(output) + 1,
                    provenance="indexed_chunk",
                ))
                if len(output) >= limit:
                    break
            return output
        finally:
            db.close()

    def search(
        self, query: str, *, chapter_title: str = "",
        limit: int = 4,
    ) -> list[OriginalEvidence]:
        assert self.conn is not None
        terms = _terms(f"{query} {chapter_title}")
        if not terms:
            return []
        sources = tuple(self.file_hashes)
        if not sources:
            return []
        placeholders = ",".join("?" for _ in sources)
        rows = []
        if self.fts_available:
            expression = " OR ".join('"' + w.replace('"', '') + '"' for w in terms)
            try:
                rows = self.conn.execute(
                    "SELECT e.source_path,e.page,e.block,e.body,e.provenance,"
                    "bm25(evidence_pages_fts) AS weight "
                    "FROM evidence_pages_fts f "
                    "JOIN evidence_pages e ON e.id=f.rowid "
                    f"WHERE evidence_pages_fts MATCH ? AND e.source_path IN ({placeholders}) "
                    "ORDER BY weight LIMIT 50",
                    (expression, *sources),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
        if not rows:
            rows = self.conn.execute(
                "SELECT source_path,page,block,body,provenance,0 AS weight "
                f"FROM evidence_pages WHERE source_path IN ({placeholders})",
                sources,
            ).fetchall()
        ranked = []
        for item in rows:
            body = str(item["body"])
            present = sum(term in body.casefold() for term in terms)
            filename = Path(str(item["source_path"])).stem.casefold()
            title_terms = _terms(chapter_title)
            file_hit = sum(term in filename for term in title_terms)
            if present == 0 and file_hit == 0:
                continue
            score = present + 2 * file_hit + 4 * (terms[0] in body.casefold())
            ranked.append((score, item))
        ranked.sort(key=lambda item: item[0], reverse=True)
        output = []
        for index, (_score, row) in enumerate(ranked[:limit], start=1):
            output.append(OriginalEvidence(
                source_path=str(row["source_path"]),
                page=int(row["page"]) if row["page"] is not None else None,
                block=int(row["block"]) if row["block"] is not None else None,
                excerpt=_excerpt(str(row["body"]), terms),
                rank=index,
                provenance=str(row["provenance"]),
            ))
        return output


def validate_original_quote(quote: dict, evidence: list[OriginalEvidence]) -> bool:
    """Verify quotation and source/page identity against retrieved original text."""
    if not isinstance(quote, dict):
        return False
    value = quote.get("quote")
    source = quote.get("source")
    page = quote.get("page")
    block = quote.get("block")
    if not isinstance(value, str) or len(value.strip()) < 16:
        return False
    normalized = " ".join(value.casefold().split())
    return any(
        item.source_path == source
        and item.page == page
        and item.block == block
        and normalized in " ".join(item.excerpt.casefold().split())
        for item in evidence
    )
