from pathlib import Path

from bc_science.cache import CacheDB
from bc_science.config import AppConfig
from bc_science.indexer import ingest_files


def test_ingest_files_skips_and_prunes_generated_artifacts(tmp_path: Path, monkeypatch):
    source = tmp_path / "Contrazione muscolare.txt"
    source.write_text(
        "La contrazione muscolare dipende dall'interazione tra actina e miosina.",
        encoding="utf-8",
    )
    generated = tmp_path / "corso-dispensa-finale.txt"
    generated.write_text("Riassunto derivato", encoding="utf-8")
    db_path = tmp_path / "index.db"

    db = CacheDB(db_path)
    try:
        db.replace_document(
            str(generated.resolve()),
            "old-hash",
            "Derived",
            ["testo derivato"],
            [[0.0, 1.0]],
        )
    finally:
        db.close()

    def fake_embed(self, model, texts):
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr("bc_science.indexer.OllamaClient.embed", fake_embed)

    result = ingest_files(
        [source, generated],
        AppConfig(),
        force=True,
        db_path=db_path,
    )

    assert result["files_found"] == 1
    assert result["indexed"] == 1
    assert result["removed"] == 1

    db = CacheDB(db_path)
    try:
        assert db.document_hash(str(source.resolve())) is not None
        assert db.document_hash(str(generated.resolve())) is None
        assert db.stats()["documents"] == 1
    finally:
        db.close()
