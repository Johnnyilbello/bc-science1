from pathlib import Path
import zipfile

from bc_science.documents import chunk_text, iter_source_files, normalize_text


def test_normalize_text_collapses_spaces_and_blank_lines():
    raw = "Uno   due\r\n\r\n\r\nTre"
    assert normalize_text(raw) == "Uno due\n\nTre"


def test_chunk_text_splits_long_material():
    text = "\n\n".join(["paragrafo " + ("x" * 900) for _ in range(8)])
    chunks = chunk_text(text, chunk_chars=1600, overlap=100)
    assert len(chunks) > 1
    assert all(chunk.strip() for chunk in chunks)


def test_zip_import_blocks_parent_traversal(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    archive = tmp_path / "course.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("lezione.txt", "contenuto")
        zf.writestr("../escape.txt", "non deve uscire")

    files = iter_source_files(archive)
    assert [path.name for path in files] == ["lezione.txt"]
    assert not (tmp_path / "escape.txt").exists()
