from pathlib import Path

from bc_science.cache import CacheDB


def test_summary_cache_roundtrip(tmp_path: Path):
    db = CacheDB(tmp_path / "cache.db")
    try:
        assert db.get_summary("abc") is None
        db.set_summary("abc", "riassunto")
        assert db.get_summary("abc") == "riassunto"
    finally:
        db.close()


def test_vector_search_returns_most_similar(tmp_path: Path):
    db = CacheDB(tmp_path / "cache.db")
    try:
        db.replace_document(
            "a.txt",
            "hash",
            "A",
            ["cuore", "muscolo"],
            [[1.0, 0.0], [0.0, 1.0]],
        )
        hits = db.search([0.9, 0.1], limit=1)
        assert hits[0]["text"] == "cuore"
    finally:
        db.close()



def test_prune_documents_removes_stale_workspace_entries(tmp_path: Path):
    db = CacheDB(tmp_path / "cache.db")
    try:
        db.replace_document("a.txt", "hash-a", "A", ["uno"], [[1.0, 0.0]])
        db.replace_document("b.txt", "hash-b", "B", ["due"], [[0.0, 1.0]])

        removed = db.prune_documents({"a.txt"})

        assert removed == 1
        assert db.stats()["documents"] == 1
        assert db.document_hash("a.txt") == "hash-a"
        assert db.document_hash("b.txt") is None
    finally:
        db.close()


def test_remove_documents_deletes_only_requested_sources(tmp_path: Path):
    db = CacheDB(tmp_path / "cache.db")
    try:
        db.replace_document("source.pdf", "hash-a", "Source", ["uno"], [[1.0, 0.0]])
        db.replace_document(
            "corso-dispensa-finale.pdf",
            "hash-b",
            "Derived",
            ["due"],
            [[0.0, 1.0]],
        )

        removed = db.remove_documents(["corso-dispensa-finale.pdf"])

        assert removed == 1
        assert db.document_hash("source.pdf") == "hash-a"
        assert db.document_hash("corso-dispensa-finale.pdf") is None
        assert db.stats()["documents"] == 1
    finally:
        db.close()
