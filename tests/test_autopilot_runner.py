"""Checkpointed, read-only three-chapter Spark pilot regression tests."""
import json

import pytest

import bc_science.autopilot_runner as runner
from bc_science.config import AppConfig
from bc_science.courses import CourseScan
from bc_science.scientific_autopilot import PRIORITY_CHAPTERS, AgentResult


def _files(tmp_path):
    source_dir = tmp_path / "FONDAMENTI DI BIOLOGIA E CHIMICA"
    source_dir.mkdir()
    pdf = source_dir / "biologia.pdf"
    pdf.write_bytes(b"fixture - PDF never opened by the mocked source loader")
    scan = CourseScan(
        "FONDAMENTI DI BIOLOGIA E CHIMICA", source_dir,
        (pdf.resolve(),), 1, 0, 1,
    )
    headings = list(PRIORITY_CHAPTERS)
    index = "\n".join("- " + name for name in headings)
    body = "\n\n---\n\n".join(
        f"## {name}\n\nLa cellula contiene una membrana che regola "
        "gli scambi con l'ambiente esterno." for name in headings
    )
    document = "# BIOLOGIA\n\n## Indice degli argomenti\n" + index
    document += "\n\n---\n\n" + body + "\n"
    complete = tmp_path / "complete.md"
    study = tmp_path / "essential.md"
    complete.write_text(document, encoding="utf-8")
    study.write_text(document, encoding="utf-8")
    return scan, complete, study, document


def _source_snapshot(monkeypatch, scan):
    files = [{
        "path": "biologia.pdf",
        "sha256": "valid-hash",
        "size": 12,
    }]
    manifest = {
        "schema": 3,
        "source": str(scan.path),
        "course": scan.name,
        "files": files,
        "fingerprint": "unchanged",
    }
    monkeypatch.setattr(runner, "_snapshot", lambda *_: files)
    monkeypatch.setattr(runner, "_fingerprint", lambda *_: "unchanged")
    monkeypatch.setattr(runner, "_load_manifest", lambda *_: manifest)
    monkeypatch.setattr(
        runner, "_coverage_map_from_manifest",
        lambda *_: {"biologia.pdf": PRIORITY_CHAPTERS[0]},
    )


def test_three_chapters_without_original_pages_are_blocked_without_model(
    tmp_path, monkeypatch,
):
    scan, complete, study, text = _files(tmp_path)
    _source_snapshot(monkeypatch, scan)
    monkeypatch.setattr(runner, "_original_evidence", lambda *_a: [])
    monkeypatch.setattr(
        runner, "Summarizer",
        lambda *_a, **_kw: pytest.fail("Model should never start without evidence"),
    )
    output = tmp_path / "pilot"
    result = runner.run_scientific_pilot(
        scan, complete, study, output, AppConfig(), max_attempts=3,
    )
    assert result == output.resolve()
    report = json.loads((output / "autopilot-report.json").read_text(encoding="utf-8"))
    assert [item["status"] for item in report["chapters"]] == [
        "BLOCKED_NO_ORIGINAL_EVIDENCE"
    ] * 3
    assert not report["scientifically_verified"]
    assert report["publication"] == "NOT_PERFORMED"
    assert report["status"] == "NO_CANDIDATE_REQUIRES_REVIEW"
    assert len(report["chapters"]) == 3
    assert all("diagnostics" in item for item in report["chapters"])
    assert (output / "autopilot-diagnostics.md").exists()
    dossier = (output / "autopilot-diagnostics.md").read_text(encoding="utf-8")
    assert "NON PUBBLICATO" in dossier
    assert "Possibili lacune" in dossier
    assert (output / "essenziale-autopilot-CANDIDATA.md").read_text(
        encoding="utf-8",
    ) == text
    assert complete.read_text(encoding="utf-8") == text
    assert study.read_text(encoding="utf-8") == text
    assert not (scan.path / "manifest.json").exists()


def test_checkpoint_resumes_only_remaining_chapters_without_rewriting_input(
    tmp_path, monkeypatch,
):
    scan, complete, study, text = _files(tmp_path)
    _source_snapshot(monkeypatch, scan)
    monkeypatch.setattr(runner, "_original_evidence", lambda *_a: [
        type("Evidence", (), {
            "to_dict": lambda self: {
                "source_path": "originale.pdf",
                "page": 1,
                "block": None,
                "excerpt": "La cellula regola gli scambi.",
                "rank": 1,
                "provenance": "cached_page",
            }
        })()
    ])

    class FakeModel:
        def close(self):
            pass

    monkeypatch.setattr(runner, "Summarizer", lambda *_a, **_kw: FakeModel())
    calls = []

    def interrupt_second(title, source, original, config, **kwargs):
        del source, config, kwargs
        calls.append(title)
        if len(calls) == 2:
            raise KeyboardInterrupt()
        return AgentResult(
            title, "BLOCKED_FOR_MANUAL_REVIEW", original, (), 1,
        )

    monkeypatch.setattr(runner, "review_with_agents", interrupt_second)
    output = tmp_path / "pilot"
    with pytest.raises(KeyboardInterrupt):
        runner.run_scientific_pilot(
            scan, complete, study, output, AppConfig(),
        )
    checkpoint = json.loads(
        (output / "autopilot-checkpoint.json").read_text(encoding="utf-8")
    )
    assert len(checkpoint["completed"]) == 1
    monkeypatch.setattr(
        runner, "review_with_agents",
        lambda title, _src, original, _cfg, **_kw: (
            calls.append(title),
            AgentResult(title, "BLOCKED_FOR_MANUAL_REVIEW", original, (), 1),
        )[1],
    )
    runner.run_scientific_pilot(
        scan, complete, study, output, AppConfig(), resume=True,
    )
    assert len(calls) == 4
    assert calls[:2] == list(PRIORITY_CHAPTERS[:2])
    assert calls[2:] == list(PRIORITY_CHAPTERS[1:])
    assert study.read_text(encoding="utf-8") == text


def test_resume_denies_modified_essential_and_refuses_existing_dir(
    tmp_path, monkeypatch,
):
    scan, complete, study, _text = _files(tmp_path)
    _source_snapshot(monkeypatch, scan)
    monkeypatch.setattr(runner, "_original_evidence", lambda *_a: [])
    output = tmp_path / "pilot"
    runner.run_scientific_pilot(
        scan, complete, study, output, AppConfig(),
    )
    with pytest.raises(ValueError, match="nuova cartella"):
        runner.run_scientific_pilot(
            scan, complete, study, output, AppConfig(),
        )
    study.write_text(study.read_text(encoding="utf-8") + "modificato", encoding="utf-8")
    with pytest.raises(ValueError, match="Checkpoint"):
        runner.run_scientific_pilot(
            scan, complete, study, output, AppConfig(), resume=True,
        )


def test_original_pdf_source_page_provenance_is_not_invented(tmp_path, monkeypatch):
    scan, complete, _study, _text = _files(tmp_path)
    body = (
        "La membrana cellulare regola il passaggio delle sostanze e "
        "contiene un doppio strato fosfolipidico."
    )

    class FakePage:
        def get_text(self, _format, *, sort):
            assert sort
            return body

    class FakePDF:
        def __enter__(self):
            return [FakePage()]

        def __exit__(self, *_args):
            pass

    import pymupdf

    monkeypatch.setattr(pymupdf, "open", lambda _path: FakePDF())
    evidence = runner._original_evidence(
        scan, {"biologia.pdf": PRIORITY_CHAPTERS[0]},
        PRIORITY_CHAPTERS[0],
        complete.read_text(encoding="utf-8"),
    )
    assert len(evidence) == 1
    assert evidence[0].page == 1
    assert evidence[0].block is None
    assert evidence[0].source_path == str(scan.supported_files[0])
    assert evidence[0].excerpt == body


def test_original_snapshot_mismatch_fails_before_creating_output(tmp_path, monkeypatch):
    scan, complete, study, _text = _files(tmp_path)
    _source_snapshot(monkeypatch, scan)
    monkeypatch.setattr(runner, "_fingerprint", lambda *_: "changed")
    output = tmp_path / "pilot"
    with pytest.raises(ValueError, match="mappatura cambiate"):
        runner.run_scientific_pilot(scan, complete, study, output, AppConfig())
    assert not output.exists()
