"""V10.1 regression: pinned inputs, scoped amendments and manual review gates."""
import json
from types import SimpleNamespace

import pytest

import bc_science.scientific_clarifications_v101 as v101
from bc_science.courses import CourseScan
from bc_science.structural_compression import TITLES, _spans


def _document() -> str:
    names = [*TITLES, "Capitolo estraneo"]
    index = "\n".join("- " + name for name in names)
    sections = []
    for title in TITLES:
        items = [p.before for p in v101.AMENDMENTS if p.title == title]
        sections.append(
            f"## {title}\n\n### Sintesi progressiva\n\n" + "\n\n".join(items)
        )
    sections.append(
        "## Capitolo estraneo\n\nUn testo intatto. Non alterare 22 ATP.\n"
    )
    return (
        "# Biologia\n\n## Indice degli argomenti\n"
        + index + "\n\n---\n\n"
        + "\n\n---\n\n".join(sections)
    )


def _bundle(tmp_path, monkeypatch, *, newline="\n", bom=b""):
    course = tmp_path / "corso"
    course.mkdir()
    pdf = course / "documento.pdf"
    pdf.write_bytes(b"simulated original PDF; no editing")
    scan = CourseScan(course.name, course, (pdf.resolve(),), 1, 0, 1)
    sources = tmp_path / "file_originali"
    sources.mkdir()
    complete = sources / "completa.md"
    prior = sources / "essenziale-v10.md"
    report_file = sources / "v10-report.json"
    text = _document()
    payload = bom + text.replace("\n", newline).encode("utf-8")
    complete.write_bytes(payload)
    prior.write_bytes(payload)
    report = {
        "version": "structural-compression-v10",
        "status": "PREVIEW_UNPUBLISHED_MANUAL_REVIEW_REQUIRED",
        "publication_allowed": False,
        "complete_sha256": v101.sha(payload),
        "candidate_sha256": v101.sha(payload),
        "source_fingerprint": "unchanged-source",
        "chapters": [{"title": title} for title in TITLES],
    }
    report_file.write_text(json.dumps(report), encoding="utf-8")

    monkeypatch.setattr(v101, "EXPECTED_COMPLETE_SHA256", v101.sha(payload))
    monkeypatch.setattr(v101, "EXPECTED_V10_CANDIDATE_SHA256", v101.sha(payload))
    monkeypatch.setattr(
        v101, "EXPECTED_V10_REPORT_SHA256", v101.sha(report_file.read_bytes())
    )
    monkeypatch.setattr(
        v101, "_validated_manifest",
        lambda _scan, _complete: ({"fingerprint": "unchanged-source"}, {}),
    )
    monkeypatch.setattr(v101, "_snapshot", lambda _scan: [])
    monkeypatch.setattr(v101, "_fingerprint", lambda _snapshot: "unchanged-source")
    monkeypatch.setattr(
        v101, "audit_fast_essential",
        lambda *_args: SimpleNamespace(status="WARN"),
    )
    return scan, complete, prior, report_file, tmp_path / "v101-review"


def test_all_seven_clarifications_scoped_and_reversible():
    study = _document()
    proposed, records = v101.apply_clarifications(study)
    assert len(records) == 7
    assert len({record["ident"] for record in records}) == len(records)
    assert proposed != study
    for patch in v101.AMENDMENTS:
        section = proposed[slice(*_spans(proposed)[patch.title])]
        assert section.count(patch.before) == 0
        assert section.count(patch.after) == 1
    assert (
        proposed[slice(*_spans(proposed)["Capitolo estraneo"])]
        == study[slice(*_spans(study)["Capitolo estraneo"])]
    )
    assert "### Sintesi progressiva" in proposed


@pytest.mark.parametrize("patch", v101.AMENDMENTS, ids=lambda p: p.ident)
def test_single_changed_source_word_blocks_whole_preview(patch):
    before = _document()
    modified = before.replace(patch.before, patch.before + " Alterata.", 1)
    # The complete literal remains present but its appended qualifier should
    # not be considered semantic equivalence. Our matcher is exact substring,
    # so mutate a content word inside the anchor itself instead.
    modified = before.replace(
        patch.before, "ALTERATO " + patch.before[1:], 1,
    )
    with pytest.raises(ValueError, match="Ancoraggio"):
        v101.apply_clarifications(modified)


def test_duplicate_anchored_sentence_blocks():
    before = _document()
    patch = v101.AMENDMENTS[0]
    doubled = before.replace(
        patch.before, patch.before + "\n\n" + patch.before, 1,
    )
    with pytest.raises(ValueError, match="Ancoraggio"):
        v101.apply_clarifications(doubled)


def test_v101_run_no_publication_exact_hashes_and_crlf(tmp_path, monkeypatch):
    args = _bundle(tmp_path, monkeypatch, newline="\r\n", bom=b"\xef\xbb\xbf")
    inputs = [p.read_bytes() for p in args[1:4]]
    original_pdf = (args[0].path / "documento.pdf").read_bytes()
    folder = v101.run_scientific_clarifications(*args)
    new = (folder / "essenziale-v10.1-CANDIDATA-NON-PUBBLICATA.md").read_bytes()
    assert new.startswith(b"\xef\xbb\xbf")
    assert b"\n" not in new.replace(b"\r\n", b"")
    report = json.loads((folder / "v10.1-report.json").read_text(encoding="utf-8"))
    assert not report["publication_allowed"]
    assert not report["scientifically_verified"]
    assert not report["source_pdf_checked_for_every_amendment"]
    assert report["unchanged_other_chapters"] == 1
    assert report["manual_review_gate"] == "BLOCKED_UNTIL_ORIGINAL_PDF_AND_SCIENCE_REVIEW"
    assert len(report["changes"]) == 7
    assert report["candidate_sha256"] == v101.sha(new)
    assert any(c["numeric_change_disclosed"] for c in report["changes"])
    assert "10 ATP equivalenti" in new.decode("utf-8-sig")
    assert "12 ATP equivalenti" in new.decode("utf-8-sig")
    assert "Nel promemoria:" not in new.decode("utf-8-sig")
    assert "Il ciclo è aperto, non chiuso" not in new.decode("utf-8-sig")
    assert [
        c["reduction_target_met"] for c in report["chapters"]
    ] == [False, False, False]  # Synthetic fixture is short, never a publish gate.
    assert [p.read_bytes() for p in args[1:4]] == inputs
    assert (args[0].path / "documento.pdf").read_bytes() == original_pdf
    for label, digest in json.loads(
        (folder / "SHA256SUMS.json").read_text()
    ).items():
        assert v101.sha((folder / label).read_bytes()) == digest
    with pytest.raises(ValueError, match="cartella"):
        v101.run_scientific_clarifications(*args)


@pytest.mark.parametrize("error", [
    "complete_hash", "candidate_hash", "report_hash", "source_fingerprint",
    "published_report", "inside_source",
])
def test_invalid_provenance_blocks_without_output(tmp_path, monkeypatch, error):
    args = list(_bundle(tmp_path, monkeypatch))
    if error == "complete_hash":
        args[1].write_bytes(args[1].read_bytes() + b"\n")
    elif error == "candidate_hash":
        args[2].write_bytes(args[2].read_bytes() + b"\n")
    elif error in {"report_hash", "source_fingerprint", "published_report"}:
        obj = json.loads(args[3].read_text())
        if error == "source_fingerprint":
            obj["source_fingerprint"] = "wrong"
        elif error == "published_report":
            obj["publication_allowed"] = True
        else:
            obj["version"] = "tampered"
        args[3].write_text(json.dumps(obj))
        if error != "report_hash":
            monkeypatch.setattr(
                v101, "EXPECTED_V10_REPORT_SHA256",
                v101.sha(args[3].read_bytes()),
            )
    else:
        args[4] = args[0].path / "preview"
    with pytest.raises(ValueError):
        v101.run_scientific_clarifications(*args)
    assert not args[4].exists()


def test_detect_source_changed_during_operation(tmp_path, monkeypatch):
    args = _bundle(tmp_path, monkeypatch)
    monkeypatch.setattr(v101, "_fingerprint", lambda _: "source-changed")
    with pytest.raises(ValueError, match="Dispense"):
        v101.run_scientific_clarifications(*args)
    assert not args[4].exists()


def test_new_audit_fail_closed(tmp_path, monkeypatch):
    args = _bundle(tmp_path, monkeypatch)
    states = iter(["WARN", "FAIL"])
    monkeypatch.setattr(
        v101, "audit_fast_essential",
        lambda *_args: SimpleNamespace(status=next(states)),
    )
    with pytest.raises(ValueError, match="nuovo FAIL"):
        v101.run_scientific_clarifications(*args)
    assert not args[4].exists()


def test_real_style_exam_point_four_groups_survives_chirality_clarification():
    """Regression for local V10.1: '4 gruppi' must not become 'quattro gruppi'."""
    from bc_science.summarizer import _preserves_exam_numbers

    candidate, records = v101.apply_clarifications(_document())
    chapter = candidate[slice(*_spans(candidate)[TITLES[2]])]
    complete = (
        "## CHIMICA ORGANICA e carboidrati\n\n"
        "### Da ricordare per l'esame\n"
        "9. Ricordare che gli enantiomeri esistono solo se il carbonio "
        "è legato a 4 gruppi diversi; sono immagini speculari "
        "non sovrapponibili.\n"
    )
    assert _preserves_exam_numbers(complete, chapter)
    assert not _preserves_exam_numbers(
        complete, chapter.replace("4 gruppi", "quattro gruppi", 1),
    )
    change = next(
        record for record in records
        if record["ident"] == "CHIRALITY_NO_ONLY_CARBON"
    )
    assert "4 gruppi" in change["after"]
    assert change["numeric_change_disclosed"] is False


def test_real_style_new_audit_fail_explains_missing_chapter(tmp_path, monkeypatch):
    args = _bundle(tmp_path, monkeypatch)
    states = iter([
        SimpleNamespace(status="WARN", missing_exam_number_chapters=()),
        SimpleNamespace(
            status="FAIL",
            missing_exam_number_chapters=("CHIMICA ORGANICA e carboidrati",),
        ),
    ])
    monkeypatch.setattr(
        v101, "audit_fast_essential",
        lambda *_args: next(states),
    )
    with pytest.raises(ValueError, match="Numeri d'esame non preservati"):
        v101.run_scientific_clarifications(*args)
    assert not args[4].exists()
