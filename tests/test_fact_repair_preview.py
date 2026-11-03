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



@pytest.mark.parametrize("case", repair.CASES)
def test_v8_accepts_exact_diagnostic_statement_in_markdown_list(case):
    """The V7 dossier strips markdown; original source text often does not."""
    label = case.old.split(":", 1)[0] + ":"
    full_label = case.complete_anchor.split(":", 1)[0] + ":"
    head = (
        "# Biologia\n\n## Indice degli argomenti\n"
        + "- " + case.title + "\n\n---\n\n"
        + "## " + case.title + "\n\n"
    )
    complete = (
        head + "- **" + full_label + "** "
        + case.complete_anchor[len(full_label):].lstrip()
    )
    study = (
        head + "- **" + label + "** "
        + case.old[len(label):].lstrip()
    )
    evidence = {"source_path": "actual.pdf", "page": 8}
    proposed, status = repair._validated_scoped_edit(
        study, complete, case, evidence,
    )
    assert status == "CANDIDATE_UNVERIFIED"
    assert proposed != study
    assert "- **" + label + "** " in proposed
    assert case.revised.split(":", 1)[1].strip() in proposed
    assert proposed[:len(head)] == head
    assert proposed.startswith(head + "- **" + label + "** ")


def test_v8_does_not_match_different_scientific_content():
    case = repair.CASES[0]
    head = (
        "# Biologia\n\n## Indice degli argomenti\n"
        "- Il ciclo di Krebs\n\n---\n\n## Il ciclo di Krebs\n\n"
    )
    study = head + case.old
    evidence = {"source_path": "actual.pdf", "page": 8}
    # A missing qualifier, a changed word, or a second occurrence MUST block.
    source = head + case.complete_anchor.replace("rientrare", "non rientrare")
    unchanged, reason = repair._validated_scoped_edit(
        study, source, case, evidence,
    )
    assert unchanged == study
    assert reason == "BLOCKED_COMPLETE_ANCHOR_MISSING"
    doubled = head + case.complete_anchor + "\n\n" + case.complete_anchor
    unchanged, reason = repair._validated_scoped_edit(
        study, doubled, case, evidence,
    )
    assert unchanged == study
    assert reason == "BLOCKED_COMPLETE_ANCHOR_MISSING"


def test_v8_does_not_match_altered_essential_even_when_complete_matches():
    case = repair.CASES[1]
    head = (
        "# Biologia\n\n## Indice degli argomenti\n"
        "- La struttura terziaria e quaternaria delle proteine ed i lipidi\n"
        "\n---\n\n"
        "## La struttura terziaria e quaternaria delle proteine ed i lipidi\n\n"
    )
    complete = head + case.complete_anchor
    changed = head + case.old.replace("anfipatici", "esclusivamente polari")
    unchanged, reason = repair._validated_scoped_edit(
        changed, complete, case, {"source_path": "actual.pdf", "page": 7},
    )
    assert unchanged == changed
    assert reason == "BLOCKED_ESSENTIAL_ANCHOR_NOT_UNIQUE"



@pytest.mark.parametrize("case", repair.CASES)
def test_other_chapter_mentions_do_not_suppress_local_fact_proposal(case):
    """The real V8.1 false skip: a term elsewhere is NOT in the target."""
    header = (
        "# Biologia\n\n## Indice degli argomenti\n- "
        + case.title + "\n\n---\n\n## " + case.title + "\n\n"
    )
    complete = header + case.complete_anchor
    other = (
        "Nota separata sugli stessi argomenti: "
        + "; ".join(case.already_present) + " vengono citati qui."
    )
    study = header + case.old + "\n\n" + other
    evidence = {"source_path": "dispensa.pdf", "page": 8}
    proposed, state = repair._validated_scoped_edit(
        study, complete, case, evidence,
    )
    assert state == "CANDIDATE_UNVERIFIED"
    assert proposed == study.replace(case.old, case.revised, 1)
    assert proposed.endswith(other)
    mentions = repair._elsewhere_term_mentions(
        dict(repair._extract_summary_chapters(study))[case.title],
        case,
    )
    assert mentions
    assert set(mentions[0]["terms"]) == set(case.already_present)
    assert mentions[0]["status"] == "CONTEXT_ONLY_NOT_SEMANTICALLY_VERIFIED"


@pytest.mark.parametrize("case", repair.CASES)
def test_elsewhere_terms_cannot_override_absent_original_evidence(case):
    header = (
        "# Biologia\n\n## Indice degli argomenti\n- "
        + case.title + "\n\n---\n\n## " + case.title + "\n\n"
    )
    complete = header + case.complete_anchor
    study = (
        header + case.old + "\n\nAltro paragrafo: "
        + " e ".join(case.already_present) + "."
    )
    proposed, state = repair._validated_scoped_edit(
        study, complete, case, None,
    )
    assert proposed == study
    assert state == "BLOCKED_ORIGINAL_SOURCE_CONTEXT_MISSING"


def test_mismatched_target_is_never_replaced_based_on_other_chapter_terms():
    case = repair.CASES[0]
    header = (
        "# Biologia\n\n## Indice degli argomenti\n- "
        "Il ciclo di Krebs\n\n---\n\n## Il ciclo di Krebs\n\n"
    )
    complete = header + case.complete_anchor
    study = (
        header + case.old.replace("rientrano", "non rientrano")
        + "\n\nNota: le reazioni anaplerotiche sono importanti."
    )
    proposed, state = repair._validated_scoped_edit(
        study, complete, case, {"source_path": "reale.pdf", "page": 8},
    )
    assert proposed == study
    assert state == "BLOCKED_ESSENTIAL_ANCHOR_NOT_UNIQUE"
