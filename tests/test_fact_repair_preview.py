"""Review-only, two-fact V8 regression and source-provenance checks."""
import hashlib
import json
from dataclasses import replace

import pytest

import bc_science.fact_repair_preview as repair
from bc_science.courses import CourseScan


def _documents(tmp_path):
    source_dir = tmp_path / "FONDAMENTI DI BIOLOGIA  E CHIMICA"
    source_dir.mkdir()
    pdf = source_dir / "lezione.pdf"
    pdf.write_bytes(b"source fixture; fake PDF reader is used for unit tests")
    scan = CourseScan(
        source_dir.name, source_dir, (pdf.resolve(),), 1, 0, 1,
    )
    titles = [item.title for item in repair.CASES] + ["Capitolo immutato"]
    index = "\n".join("- " + title for title in titles)
    head = "# Biologia\n\n## Indice degli argomenti\n" + index + "\n\n---\n\n"
    complete = head + "\n\n---\n\n".join(
        f"## {item.title}\n\n{item.complete_anchor}" for item in repair.CASES
    )
    complete += "\n\n---\n\n## Capitolo immutato\n\nConcetto non modificabile.\n"
    study = head + "\n\n---\n\n".join(
        f"## {item.title}\n\n{item.old}" for item in repair.CASES
    )
    study += "\n\n---\n\n## Capitolo immutato\n\nConcetto non modificabile.\n"
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    complete_file = input_dir / "complete.md"
    study_file = input_dir / "study.md"
    complete_file.write_text(complete, encoding="utf-8")
    study_file.write_text(study, encoding="utf-8")
    return scan, complete_file, study_file, complete, study


def _mock_valid_source(monkeypatch):
    monkeypatch.setattr(
        repair, "_validated_manifest",
        lambda *_args: ({"fingerprint": "stable"}, {"lezione.pdf": repair.CASES[0].title}),
    )
    monkeypatch.setattr(repair, "_snapshot", lambda *_args: [])
    monkeypatch.setattr(repair, "_fingerprint", lambda *_args: "stable")
    monkeypatch.setattr(
        repair, "_source_fact_evidence",
        lambda _scan, _mapping, case: {
            "source_path": "lezione.pdf",
            "page": 3 if case == repair.CASES[0] else 7,
            "block": None,
            "excerpt": "Estratto originale per revisione umana.",
            "provenance": "mapped_original_pdf_or_docx",
            "interpretation": "SOURCE_CONTEXT_ONLY_NOT_SCIENTIFICALLY_CERTIFIED",
        },
    )


def test_v8_two_exact_edits_preserve_everything_else_and_never_publish(
    tmp_path, monkeypatch,
):
    scan, complete_file, study_file, complete, study = _documents(tmp_path)
    _mock_valid_source(monkeypatch)
    destination = tmp_path / "new-preview"
    original_sha = hashlib.sha256(study_file.read_bytes()).hexdigest()
    repair.stage_two_fact_preview(
        scan, complete_file, study_file, destination,
    )
    proposed = (
        destination / "essenziale-v8-CANDIDATA-NON-PUBBLICATA.md"
    ).read_text(encoding="utf-8")
    expected = study
    for item in repair.CASES:
        expected = expected.replace(item.old, item.revised, 1)
    assert proposed == expected
    assert "Capitolo immutato\n\nConcetto non modificabile" in proposed
    assert complete_file.read_text(encoding="utf-8") == complete
    assert hashlib.sha256(study_file.read_bytes()).hexdigest() == original_sha
    report = json.loads((destination / "v8-report.json").read_text(encoding="utf-8"))
    assert report["candidate_count"] == 2
    assert report["publication"] == "NOT_PERFORMED"
    assert report["model_used"] is False
    assert report["scientifically_verified"] is False
    assert all(item["status"] == "CANDIDATE_UNVERIFIED" for item in report["proposals"])
    assert "NON PUBBLICATO" in (
        destination / "v8-dossier.md"
    ).read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="Percorsi"):
        repair.stage_two_fact_preview(
            scan, complete_file, study_file, destination,
        )


def test_missing_original_source_blocks_candidate_without_changing_input(
    tmp_path, monkeypatch,
):
    scan, complete_file, study_file, _complete, study = _documents(tmp_path)
    _mock_valid_source(monkeypatch)
    monkeypatch.setattr(repair, "_source_fact_evidence", lambda *_args: None)
    destination = tmp_path / "preview"
    repair.stage_two_fact_preview(scan, complete_file, study_file, destination)
    report = json.loads((destination / "v8-report.json").read_text(encoding="utf-8"))
    assert report["candidate_count"] == 0
    assert all(
        item["status"] == "BLOCKED_ORIGINAL_SOURCE_CONTEXT_MISSING"
        for item in report["proposals"]
    )
    assert (
        destination / "essenziale-v8-CANDIDATA-NON-PUBBLICATA.md"
    ).read_text(encoding="utf-8") == study


def test_repair_refuses_unfamiliar_text_and_unrelated_number_changes():
    case = repair.CASES[0]
    header = (
        "# Biologia\n\n## Indice degli argomenti\n"
        "- Il ciclo di Krebs\n\n---\n\n"
    )
    study = header + "## Il ciclo di Krebs\n\n" + case.old
    complete = header + "## Il ciclo di Krebs\n\n" + case.complete_anchor
    evidence = {"source_path": "actual.pdf", "page": 8}
    modified = study.replace("rientrano", "ritornano")
    proposed, reason = repair._validated_scoped_edit(
        modified, complete, case, evidence,
    )
    assert proposed == modified
    assert reason == "BLOCKED_ESSENTIAL_ANCHOR_NOT_UNIQUE"

    numbered = replace(case, revised=case.revised + " Il valore cambia a 5.")
    with pytest.raises(ValueError, match="Quantità"):
        repair._validated_scoped_edit(study, complete, numbered, evidence)


def test_source_page_requires_all_factual_anchors(tmp_path, monkeypatch):
    scan, *_ = _documents(tmp_path)
    case = repair.CASES[1]
    text = (
        "I fosfolipidi formano un doppio strato; le teste idrofile "
        "sono rivolte verso l'esterno e le code idrofobiche verso l'interno."
    )

    class FakePage:
        def __init__(self, body):
            self.body = body

        def get_text(self, mode, *, sort):
            assert mode == "text" and sort
            return self.body

    class FakePDF:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return [FakePage(self.body)]

        def __exit__(self, *_args):
            pass

    import pymupdf

    monkeypatch.setattr(pymupdf, "open", lambda _path: FakePDF(text))
    hit = repair._source_fact_evidence(scan, {"lezione.pdf": case.title}, case)
    assert hit and hit["page"] == 1
    assert hit["source_path"] == str(scan.supported_files[0])
    assert "teste idrofile" in hit["excerpt"]
    assert "NOT_SCIENTIFICALLY_CERTIFIED" in hit["interpretation"]

    monkeypatch.setattr(
        pymupdf, "open",
        lambda _path: FakePDF(text.replace("code idrofobiche", "code lipidiche")),
    )
    assert repair._source_fact_evidence(
        scan, {"lezione.pdf": case.title}, case,
    ) is None


def test_source_retrieval_refuses_path_outside_course(tmp_path):
    scan, *_ = _documents(tmp_path)
    case = repair.CASES[0]
    with pytest.raises(ValueError, match="non autorizzato"):
        repair._source_fact_evidence(
            scan, {"../../outside.pdf": case.title}, case,
        )
