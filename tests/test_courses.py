from pathlib import Path

from bc_science import courses
from bc_science.config import AppConfig
from bc_science.courses import (
    build_course,
    course_state,
    scan_courses,
    workspace_db_path,
)


def test_scan_courses_discovers_direct_subjects_and_ignores_noise(tmp_path: Path):
    root = tmp_path / "SCIENZE MOTORIE"
    anatomy = root / "ANATOMIA"
    physiology = root / "FISIOLOGIA"
    anatomy.mkdir(parents=True)
    physiology.mkdir(parents=True)

    (anatomy / "lezione 1.txt").write_text("ossa e muscoli", encoding="utf-8")
    nested = anatomy / "modulo 2"
    nested.mkdir()
    (nested / "lezione 2.md").write_text("articolazioni", encoding="utf-8")
    (anatomy / "note.csv").write_text("non supportato", encoding="utf-8")
    (anatomy / "~$temporaneo.docx").write_text("temp", encoding="utf-8")

    generated = anatomy / "outputs"
    generated.mkdir()
    (generated / "vecchio-riassunto-unico.md").write_text("output", encoding="utf-8")

    (physiology / "fisiologia.txt").write_text("omeostasi", encoding="utf-8")
    hidden_course = root / ".privata"
    hidden_course.mkdir()
    (hidden_course / "segreto.txt").write_text("x", encoding="utf-8")
    (root / "file-radice.txt").write_text("non e una materia", encoding="utf-8")

    scans = scan_courses(root)

    assert [scan.name for scan in scans] == ["ANATOMIA", "FISIOLOGIA"]
    anatomy_scan = scans[0]
    assert [path.name for path in anatomy_scan.supported_files] == [
        "lezione 1.txt",
        "lezione 2.md",
    ]
    assert anatomy_scan.folders_visited == 2
    assert anatomy_scan.total_files == 4
    assert anatomy_scan.ignored_files == 2


def test_course_build_uses_workspace_manifest_and_skips_unchanged(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    root = tmp_path / "SCIENZE MOTORIE"
    subject = root / "ANATOMIA"
    subject.mkdir(parents=True)
    source = subject / "lezione.txt"
    source.write_text("contenuto iniziale", encoding="utf-8")

    calls = {"ingest": 0, "summarize": 0}

    def fake_ingest(files, config, *, force=False, db_path=None, prune_missing=False):
        calls["ingest"] += 1
        assert len(files) == 1
        assert db_path == workspace_db_path(scan_courses(root)[0])
        assert prune_missing is True
        return {
            "files_found": 1,
            "indexed": 1,
            "skipped": 0,
            "chunks": 1,
            "removed": 0,
        }

    class FakeSummarizer:
        def __init__(self, config, profile=None, *, db_path=None):
            assert db_path == workspace_db_path(scan_courses(root)[0])

        def summarize_course(self, files, title, *, progress=None):
            calls["summarize"] += 1
            return f"# {title}\n\n## Capitolo\nContenuto.\n"

        def close(self):
            return None

    monkeypatch.setattr(courses, "ingest_files", fake_ingest)
    monkeypatch.setattr(courses, "Summarizer", FakeSummarizer)

    first_scan = scan_courses(root)[0]
    first = build_course(first_scan, AppConfig())
    assert first.state == "creata"
    assert first.output_path is not None and first.output_path.exists()
    ready = course_state(first_scan)
    assert ready.state == "pronta"
    assert (ready.new_files, ready.changed_files, ready.removed_files) == (0, 0, 0)
    assert calls == {"ingest": 1, "summarize": 1}

    second = build_course(scan_courses(root)[0], AppConfig())
    assert second.state == "riutilizzata"
    assert calls == {"ingest": 1, "summarize": 1}

    source.write_text("contenuto modificato", encoding="utf-8")
    changed_scan = scan_courses(root)[0]
    changed = course_state(changed_scan)
    assert changed.state == "modificata"
    assert (changed.new_files, changed.changed_files, changed.removed_files) == (0, 1, 0)

    third = build_course(changed_scan, AppConfig())
    assert third.state == "creata"
    assert calls == {"ingest": 2, "summarize": 2}
    assert course_state(changed_scan).state == "pronta"


def test_different_courses_get_different_workspace_databases(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    root = tmp_path / "SCIENZE MOTORIE"
    for name in ("ANATOMIA", "FISIOLOGIA", "BIOLOGIA"):
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "lezione.txt").write_text(name, encoding="utf-8")

    scans = scan_courses(root)
    db_paths = {workspace_db_path(scan) for scan in scans}

    assert len(scans) == 3
    assert len(db_paths) == 3
    assert all("workspaces" in path.parts for path in db_paths)
