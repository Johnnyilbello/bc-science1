from pathlib import Path

from bc_science import courses
from bc_science.config import AppConfig
from bc_science.courses import (
    audit_course_summary,
    build_course,
    course_pdf_path,
    course_state,
    existing_course_summary_path,
    scan_course,
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
            return (
                f"# {title}\n\n"
                "## Indice degli argomenti\n- lezione\n\n---\n\n"
                "## lezione\n### In parole semplici\nContenuto di base.\n"
                "### Da ricordare per l'esame\n- Punto di base.\n"
            )

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

def test_added_document_uses_incremental_course_update(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    root = tmp_path / "SCIENZE MOTORIE"
    subject = root / "FISIOLOGIA"
    subject.mkdir(parents=True)
    (subject / "Neuroni 1.txt").write_text("contenuto base", encoding="utf-8")

    calls = {"ingest": [], "summarize": 0, "incremental": 0}

    def fake_ingest(files, config, *, force=False, db_path=None, prune_missing=False):
        calls["ingest"].append(([path.name for path in files], prune_missing))
        return {
            "files_found": len(files),
            "indexed": len(files),
            "skipped": 0,
            "chunks": len(files),
            "removed": 0,
        }

    class FakeSummarizer:
        def __init__(self, config, profile=None, *, db_path=None):
            return None

        def summarize_course(self, files, title, *, progress=None):
            calls["summarize"] += 1
            return (
                f"# {title}\n\n"
                "## Indice degli argomenti\n- Neuroni\n\n---\n\n"
                "## Neuroni\n### In parole semplici\nBase.\n"
                "### Da ricordare per l'esame\n- Punto base.\n"
            )

        def incremental_update_course(
            self,
            existing_summary,
            new_files,
            title,
            *,
            progress=None,
        ):
            calls["incremental"] += 1
            assert [path.name for path in new_files] == ["Neuroni 2.txt"]
            return (
                existing_summary + "\nAggiornato incrementalmente.\n",
                1,
                0,
                {str(new_files[0].resolve()): "Neuroni"},
            )

        def close(self):
            return None

    monkeypatch.setattr(courses, "ingest_files", fake_ingest)
    monkeypatch.setattr(courses, "Summarizer", FakeSummarizer)

    first = build_course(scan_courses(root)[0], AppConfig())
    assert first.state == "creata"
    assert calls["summarize"] == 1
    assert calls["incremental"] == 0

    (subject / "Neuroni 2.txt").write_text("nuove informazioni", encoding="utf-8")
    status = course_state(scan_courses(root)[0])
    assert (status.new_files, status.changed_files, status.removed_files) == (1, 0, 0)

    second = build_course(scan_courses(root)[0], AppConfig())

    assert second.incremental_files == 1
    assert second.updated_chapters == 1
    assert second.new_chapters == 0
    assert second.fallback_full_rebuild is False
    assert calls["summarize"] == 1
    assert calls["incremental"] == 1
    assert calls["ingest"] == [
        (["Neuroni 1.txt"], True),
        (["Neuroni 2.txt"], False),
    ]
    assert second.output_path is not None
    assert "Aggiornato incrementalmente" in second.output_path.read_text(encoding="utf-8")
    assert course_state(scan_courses(root)[0]).state == "pronta"

def test_incremental_failure_falls_back_to_full_course_rebuild(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    root = tmp_path / "SCIENZE MOTORIE"
    subject = root / "ANATOMIA"
    subject.mkdir(parents=True)
    (subject / "Ossa 1.txt").write_text("base", encoding="utf-8")

    calls = {"summarize": 0, "incremental": 0}

    def fake_ingest(files, config, *, force=False, db_path=None, prune_missing=False):
        return {
            "files_found": len(files),
            "indexed": len(files),
            "skipped": 0,
            "chunks": len(files),
            "removed": 0,
        }

    class FakeSummarizer:
        def __init__(self, config, profile=None, *, db_path=None):
            return None

        def summarize_course(self, files, title, *, progress=None):
            calls["summarize"] += 1
            return (
                f"# {title}\n\n"
                "## Indice degli argomenti\n- Ossa\n\n---\n\n"
                "## Ossa\n### In parole semplici\nOssa.\n"
                "### Da ricordare per l'esame\n- Ossa.\n"
            )

        def incremental_update_course(
            self,
            existing_summary,
            new_files,
            title,
            *,
            progress=None,
        ):
            calls["incremental"] += 1
            raise ValueError("gate anti-perdita")

        def close(self):
            return None

    monkeypatch.setattr(courses, "ingest_files", fake_ingest)
    monkeypatch.setattr(courses, "Summarizer", FakeSummarizer)

    build_course(scan_courses(root)[0], AppConfig())
    (subject / "Ossa 2.txt").write_text("nuovo", encoding="utf-8")
    result = build_course(scan_courses(root)[0], AppConfig())

    assert result.incremental_files == 1
    assert result.fallback_full_rebuild is True
    assert calls == {"summarize": 2, "incremental": 1}

def test_existing_current_summary_generates_missing_pdf_without_resummarizing(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    root = tmp_path / "SCIENZE MOTORIE"
    subject = root / "BIOLOGIA"
    subject.mkdir(parents=True)
    (subject / "Cellula 1.txt").write_text("contenuto", encoding="utf-8")

    calls = {"summarize": 0, "pdf": 0}

    def fake_ingest(files, config, *, force=False, db_path=None, prune_missing=False):
        return {
            "files_found": len(files),
            "indexed": len(files),
            "skipped": 0,
            "chunks": len(files),
            "removed": 0,
        }

    class FakeSummarizer:
        def __init__(self, config, profile=None, *, db_path=None):
            return None

        def summarize_course(self, files, title, *, progress=None):
            calls["summarize"] += 1
            return (
                f"# {title}\n\n"
                "## Indice degli argomenti\n- Cellula\n\n---\n\n"
                "## Cellula\n### In parole semplici\nLa cellula è trattata nelle dispense.\n"
                "### Da ricordare per l'esame\n- Punto cellula.\n"
            )

        def close(self):
            return None

    def fake_pdf(markdown, destination, title):
        calls["pdf"] += 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"%PDF-test")
        return 7

    monkeypatch.setattr(courses, "ingest_files", fake_ingest)
    monkeypatch.setattr(courses, "Summarizer", FakeSummarizer)
    monkeypatch.setattr(courses, "export_pdf", fake_pdf)

    scan = scan_courses(root)[0]
    first = build_course(scan, AppConfig())
    assert first.coverage_complete is True
    assert first.pdf_pages == 7
    assert calls == {"summarize": 1, "pdf": 1}

    course_pdf_path(scan).unlink()
    second = build_course(scan_courses(root)[0], AppConfig())

    assert second.state == "riutilizzata"
    assert second.pdf_generated is True
    assert second.pdf_pages == 7
    assert calls == {"summarize": 1, "pdf": 2}

def test_legacy_summary_without_verified_manifest_is_rebuilt(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    subject = tmp_path / "SCIENZE MOTORIE" / "ANATOMIA"
    subject.mkdir(parents=True)
    (subject / "Ossa 1.txt").write_text("ossa", encoding="utf-8")
    scan = scan_course(subject)

    legacy = tmp_path / "home" / "outputs" / "ANATOMIA-riassunto-unico.md"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(
        "# Vecchio\n\n## Indice degli argomenti\n- Ossa\n\n---\n\n## Ossa\nVecchio.",
        encoding="utf-8",
    )
    assert existing_course_summary_path(scan) == legacy

    calls = {"summarize": 0}

    def fake_ingest(files, config, *, force=False, db_path=None, prune_missing=False):
        return {
            "files_found": len(files),
            "indexed": len(files),
            "skipped": 0,
            "chunks": len(files),
            "removed": 0,
        }

    class FakeSummarizer:
        def __init__(self, config, profile=None, *, db_path=None):
            return None

        def summarize_course(self, files, title, *, progress=None):
            calls["summarize"] += 1
            return (
                f"# {title}\n\n"
                "## Indice degli argomenti\n- Ossa\n\n---\n\n"
                "## Ossa\n### In parole semplici\nOssa.\n"
                "### Da ricordare per l'esame\n- Ossa.\n"
            )

        def close(self):
            return None

    monkeypatch.setattr(courses, "ingest_files", fake_ingest)
    monkeypatch.setattr(courses, "Summarizer", FakeSummarizer)
    monkeypatch.setattr(
        courses,
        "export_pdf",
        lambda markdown, destination, title: (
            destination.parent.mkdir(parents=True, exist_ok=True),
            destination.write_bytes(b"%PDF-test"),
            3,
        )[-1],
    )

    result = build_course(scan, AppConfig())

    assert calls["summarize"] == 1
    assert result.coverage_complete is True
    assert result.output_path is not None and result.output_path != legacy
    assert result.pdf_path is not None and result.pdf_path.exists()

def test_coverage_audit_detects_missing_topic_even_with_summary_file(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    subject = tmp_path / "SCIENZE MOTORIE" / "FISIOLOGIA"
    subject.mkdir(parents=True)
    (subject / "Neuroni 1.txt").write_text("neuroni", encoding="utf-8")
    (subject / "Muscoli 1.txt").write_text("muscoli", encoding="utf-8")
    scan = scan_course(subject)

    incomplete = """# FISIOLOGIA - Riassunto completo

## Indice degli argomenti
- Neuroni

---

## Neuroni
### In parole semplici
Neuroni.
### Da ricordare per l'esame
- Neuroni.
"""
    coverage = audit_course_summary(
        scan,
        incomplete,
        source_verified=True,
    )

    assert coverage.complete is False
    assert coverage.covered_source_count == 1
    assert "Muscoli" in coverage.missing_topics

def test_course_state_reports_coverage_and_pdf_status(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    subject = tmp_path / "SCIENZE MOTORIE" / "ANATOMIA"
    subject.mkdir(parents=True)
    (subject / "Ossa 1.txt").write_text("ossa", encoding="utf-8")
    scan = scan_course(subject)

    calls = {"summary": 0}

    def fake_ingest(files, config, *, force=False, db_path=None, prune_missing=False):
        return {
            "files_found": len(files),
            "indexed": len(files),
            "skipped": 0,
            "chunks": len(files),
            "removed": 0,
        }

    class FakeSummarizer:
        def __init__(self, config, profile=None, *, db_path=None):
            return None

        def summarize_course(self, files, title, *, progress=None):
            calls["summary"] += 1
            return (
                f"# {title}\n\n"
                "## Indice degli argomenti\n- Ossa\n\n---\n\n"
                "## Ossa\n### In parole semplici\nOssa.\n"
                "### Da ricordare per l'esame\n- Ossa.\n"
            )

        def close(self):
            return None

    def fake_pdf(markdown, destination, title):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"%PDF-test")
        return 4

    monkeypatch.setattr(courses, "ingest_files", fake_ingest)
    monkeypatch.setattr(courses, "Summarizer", FakeSummarizer)
    monkeypatch.setattr(courses, "export_pdf", fake_pdf)

    build_course(scan, AppConfig())
    state = course_state(scan)

    assert state.state == "pronta"
    assert state.coverage_state == "completa"
    assert state.pdf_state == "aggiornato"
