"""V10.2 regression suite: scoped edits, pinned provenance, no publications."""
import json
from types import SimpleNamespace

import pytest

import bc_science.scientific_clarifications_v102 as v102
from bc_science.courses import CourseScan
from bc_science.structural_compression import TITLES, _spans


def _document() -> str:
    names = [*TITLES, "Capitolo invariato"]
    index = "\n".join(f"- {title}" for title in names)
    chapters = []
    for title in TITLES:
        text = "\n\n".join(
            edit.before for edit in v102.REVISIONS if edit.title == title
        )
        chapters.append(f"## {title}\n\n### Sintesi progressiva\n\n{text}")
    chapters.append("## Capitolo invariato\n\nNon modificare 4 atomi o 22 ATP.")
    return (
        "# Corso\n\n## Indice degli argomenti\n"
        + index + "\n\n---\n\n"
        + "\n\n---\n\n".join(chapters)
    )


def _bundle(tmp_path, monkeypatch, *, newline="\n", bom=b""):
    course = tmp_path / "originali"
    course.mkdir()
    pdf = course / "originale.pdf"
    pdf.write_bytes(b"Fake original course PDF for integrity only")
    scan = CourseScan(course.name, course, (pdf.resolve(),), 1, 0, 1)
    inputs = tmp_path / "file_source"
    inputs.mkdir()
    complete = inputs / "completa.md"
    prior = inputs / "v101.md"
    report_file = inputs / "v101-report.json"
    payload = bom + _document().replace("\n", newline).encode("utf-8")
    complete.write_bytes(payload)
    prior.write_bytes(payload)
    report_file.write_text(json.dumps({
        "version": "scientific-clarifications-v10.1-review-only",
        "status": "PREVIEW_UNPUBLISHED_SCIENCE_REVIEW_REQUIRED",
        "publication_allowed": False,
        "scientifically_verified": False,
        "complete_sha256": v102.sha(payload),
        "candidate_sha256": v102.sha(payload),
        "source_fingerprint": "same-pdfs",
        "unchanged_other_chapters": 62,
        "after_audit": "WARN",
    }), encoding="utf-8")
    monkeypatch.setattr(v102, "EXPECTED_COMPLETE_SHA256", v102.sha(payload))
    monkeypatch.setattr(v102, "EXPECTED_V101_CANDIDATE_SHA256", v102.sha(payload))
    monkeypatch.setattr(
        v102, "EXPECTED_V101_REPORT_SHA256", v102.sha(report_file.read_bytes())
    )
    monkeypatch.setattr(
        v102, "_validated_manifest",
        lambda *_args: ({"fingerprint": "same-pdfs"}, {}),
    )
    monkeypatch.setattr(v102, "_snapshot", lambda _scan: [])
    monkeypatch.setattr(v102, "_fingerprint", lambda _: "same-pdfs")
    monkeypatch.setattr(
        v102, "audit_fast_essential",
        lambda *_args: SimpleNamespace(
            status="WARN", missing_exam_number_chapters=()
        ),
    )
    return scan, complete, prior, report_file, tmp_path / "preview"


def test_five_exact_amendments_scoped_and_reversible():
    original = _document()
    candidate, entries = v102.apply_v102(original)
    assert len(entries) == 5
    assert len({entry["ident"] for entry in entries}) == 5
    for patch in v102.REVISIONS:
        chapter = candidate[slice(*_spans(candidate)[patch.title])]
        assert patch.after in chapter
        assert patch.before not in chapter
    assert (
        candidate[slice(*_spans(candidate)["Capitolo invariato"])]
        == original[slice(*_spans(original)["Capitolo invariato"])]
    )
    assert "La Completa indica" not in candidate
    assert "Nota di verifica" not in candidate
    assert "l'effetto idrofobico" in candidate.casefold()
    assert "succinato deidrogenasi" in candidate
    assert "H2O" in candidate
    assert "12 ATP" in candidate and "10 ATP" in candidate


@pytest.mark.parametrize("patch", v102.REVISIONS, ids=lambda p: p.ident)
def test_single_word_mutation_rejected(patch):
    original = _document()
    damaged = original.replace(
        patch.before, "ALTERATO" + patch.before[1:], 1
    )
    with pytest.raises(ValueError, match="Ancoraggio"):
        v102.apply_v102(damaged)


def test_duplicate_exact_claim_rejected():
    original = _document()
    patch = v102.REVISIONS[2]
    bad = original.replace(
        patch.before, patch.before + "\n\n" + patch.before, 1
    )
    with pytest.raises(ValueError, match="Ancoraggio"):
        v102.apply_v102(bad)


def test_independent_shortened_target_still_blocks_duplicate():
    original = _document()
    patch = v102.REVISIONS[-1]
    assert patch.after in patch.before
    unsafe = original.replace(
        patch.before, patch.before + "\n\n" + patch.after, 1
    )
    with pytest.raises(ValueError, match="Ancoraggio"):
        v102.apply_v102(unsafe)


def test_real_exam_number_convention_is_still_present():
    from bc_science.summarizer import _preserves_exam_numbers

    new, _records = v102.apply_v102(_document())
    chapter = new[slice(*_spans(new)[TITLES[0]])]
    source = (
        "## Il ciclo di Krebs\n"
        "### Da ricordare per l'esame\n"
        "- Il ciclo produce 3 NADH, 1 FADH2 e 1 GTP.\n"
        "- La fosforilazione ossidativa genera circa 3 ATP per NADH "
        "e 2 ATP per FADH2, portando a un totale di 12 ATP per acetil-CoA.\n"
    )
    assert _preserves_exam_numbers(source, chapter)
    assert not _preserves_exam_numbers(
        source, chapter.replace("12 ATP", "dodici ATP", 1)
    )


def test_preview_hash_guard_and_unchanged_content(tmp_path, monkeypatch):
    args = _bundle(tmp_path, monkeypatch, newline="\r\n", bom=b"\xef\xbb\xbf")
    originals = [p.read_bytes() for p in args[1:4]]
    pdf_bytes = (args[0].path / "originale.pdf").read_bytes()
    folder = v102.run_v102(*args)
    candidate = (folder / "essenziale-v10.2-CANDIDATA-NON-PUBBLICATA.md").read_bytes()
    report = json.loads((folder / "v10.2-report.json").read_text(encoding="utf-8"))
    assert candidate.startswith(b"\xef\xbb\xbf")
    assert b"\n" not in candidate.replace(b"\r\n", b"")
    assert report["publication_allowed"] is False
    assert report["scientifically_verified"] is False
    assert report["original_pdfs_individually_verified"] is False
    assert report["manual_review_gate"] == "BLOCKED_UNTIL_SOURCE_PDF_AND_SCIENCE_REVIEW"
    assert len(report["changes"]) == 5
    assert report["unchanged_other_chapters"] == 1
    assert report["outside_pilot_byte_content_preserved"]
    assert report["candidate_sha256"] == v102.sha(candidate)
    assert [p.read_bytes() for p in args[1:4]] == originals
    assert (args[0].path / "originale.pdf").read_bytes() == pdf_bytes
    for name, digest in json.loads((folder / "SHA256SUMS.json").read_text()).items():
        assert v102.sha((folder / name).read_bytes()) == digest
    with pytest.raises(ValueError, match="già esistente"):
        v102.run_v102(*args)


@pytest.mark.parametrize("bad", [
    "complete", "candidate", "report", "source", "published", "out_in_course",
])
def test_bad_provenance_fail_closed(tmp_path, monkeypatch, bad):
    args = list(_bundle(tmp_path, monkeypatch))
    if bad == "complete":
        args[1].write_bytes(args[1].read_bytes() + b"tampered")
    elif bad == "candidate":
        args[2].write_bytes(args[2].read_bytes() + b"tampered")
    elif bad in {"report", "source", "published"}:
        data = json.loads(args[3].read_text(encoding="utf-8"))
        if bad == "source":
            data["source_fingerprint"] = "other-pdfs"
        elif bad == "published":
            data["publication_allowed"] = True
        else:
            data["version"] = "tampered"
        args[3].write_text(json.dumps(data), encoding="utf-8")
        if bad != "report":
            monkeypatch.setattr(
                v102, "EXPECTED_V101_REPORT_SHA256",
                v102.sha(args[3].read_bytes()),
            )
    else:
        args[4] = args[0].path / "output"
    with pytest.raises(ValueError):
        v102.run_v102(*args)
    assert not args[4].exists()


def test_audit_warn_to_fail_is_hard_block_with_chapter(tmp_path, monkeypatch):
    args = _bundle(tmp_path, monkeypatch)
    states = iter([
        SimpleNamespace(status="WARN", missing_exam_number_chapters=()),
        SimpleNamespace(
            status="FAIL",
            missing_exam_number_chapters=("Il ciclo di Krebs",),
        ),
    ])
    monkeypatch.setattr(v102, "audit_fast_essential", lambda *_: next(states))
    with pytest.raises(ValueError, match="Nuovo FAIL.*Il ciclo di Krebs"):
        v102.run_v102(*args)
    assert not args[4].exists()


def test_changed_originals_in_flight_are_rejected(tmp_path, monkeypatch):
    args = _bundle(tmp_path, monkeypatch)
    monkeypatch.setattr(v102, "_fingerprint", lambda *_: "source-mutation")
    with pytest.raises(ValueError, match="modificati"):
        v102.run_v102(*args)
    assert not args[4].exists()
