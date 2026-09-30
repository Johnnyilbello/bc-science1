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
