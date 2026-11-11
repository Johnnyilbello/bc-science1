"""Regression: an already source-hash-verified Complete must not be silently rewritten."""
from __future__ import annotations

import json
from pathlib import Path

from bc_science import courses
from bc_science.config import AppConfig
from bc_science.courses import build_course, scan_courses, workspace_path


def test_prompt_upgrade_does_not_rebuild_verified_original(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "bc_home"))
    root = tmp_path / "subject-root"
    subject = root / "MATEMATICA"
    subject.mkdir(parents=True)
    (subject / "lezione.txt").write_text("Uno piu uno fa due.", encoding="utf-8")
    scan = scan_courses(root)[0]
    calls = {"summarize": 0, "ingest": 0}

    def fake_ingest(files, config, *, force=False, db_path=None, prune_missing=False):
        calls["ingest"] += 1
        return {"indexed": 1, "skipped": 0, "removed": 0}

    class StubSummarizer:
        def __init__(self, config, profile="standard", *, db_path=None):
            pass

        def summarize_course(self, files, title, *, progress=None):
            calls["summarize"] += 1
            return (
                "# MATEMATICA\n\n## Indice degli argomenti\n"
                "- lezione\n\n---\n\n## lezione\n"
                "### In parole semplici\nUno piu uno fa due.\n"
                "### Da ricordare per l'esame\n- Addizione di base.\n"
            )

        def close(self):
            pass

    monkeypatch.setattr(courses, "ingest_files", fake_ingest)
    monkeypatch.setattr(courses, "Summarizer", StubSummarizer)
    first = build_course(scan, AppConfig())
    assert first.state == "creata"
    original = first.output_path.read_bytes()
    manifest_path = workspace_path(scan) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["summary_pipeline"] = "legacy-v13-verified"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    second = build_course(scan, AppConfig())
    assert second.state == "riutilizzata"
    assert second.output_path.read_bytes() == original
    assert calls == {"summarize": 1, "ingest": 1}
    updated = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert updated["summary_pipeline"] == "legacy-v13-verified"


def test_source_change_still_requires_rebuild(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "bc_home"))
    root = tmp_path / "subjects"
    folder = root / "MATEMATICA"
    folder.mkdir(parents=True)
    source = folder / "lezione.txt"
    source.write_text("Vecchia dispensa.", encoding="utf-8")
    scan = scan_courses(root)[0]
    calls = {"summarize": 0}

    def fake_ingest(files, config, *, force=False, db_path=None, prune_missing=False):
        return {"indexed": 1, "skipped": 0, "removed": 0}

    class StubSummarizer:
        def __init__(self, config, profile="standard", *, db_path=None):
            pass

        def summarize_course(self, files, title, *, progress=None):
            calls["summarize"] += 1
            return (
                "# MATEMATICA\n\n## Indice degli argomenti\n"
                "- lezione\n\n---\n\n## lezione\n"
                "### In parole semplici\nNuova descrizione.\n"
                "### Da ricordare per l'esame\n- Addizione.\n"
            )

        def close(self):
            pass

    monkeypatch.setattr(courses, "ingest_files", fake_ingest)
    monkeypatch.setattr(courses, "Summarizer", StubSummarizer)
    build_course(scan, AppConfig())
    source.write_text("Nuova dispensa, con dati modificati.", encoding="utf-8")
    result = build_course(scan, AppConfig())
    assert result.state == "creata"
    assert calls["summarize"] == 2
