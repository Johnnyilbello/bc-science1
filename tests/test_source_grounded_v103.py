"""Spark V10.3: regression of per-page evidence and 30 atomic fact blockers."""
import json
import zipfile
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

import pymupdf as fitz
import pytest

import bc_science.source_grounded_v103 as v103
from bc_science.courses import CourseScan
from bc_science.structural_compression import TITLES, _spans


def _document() -> str:
    all_titles = [*TITLES, "Capitolo non autorizzato"]
    index = "\n".join("- " + t for t in all_titles)
    sections = []
    for title in TITLES:
        paragraphs = [p.before for p in v103.PATCHES if p.chapter == title]
        sections.append(
            f"## {title}\n\n### Sintesi progressiva\n\n"
            + "\n\n".join(paragraphs)
            + "\nRichiamo conclusivo della lezione, da preservare.\n"
        )
    sections.append(
        "## Capitolo non autorizzato\n\nQuesta frase e 4 atomi devono rimanere invariati."
    )
    return "# Corso\n\n## Indice degli argomenti\n" + index + "\n\n---\n\n".join(
        ["", *sections]
    )


def _bundle(tmp_path, monkeypatch, newline="\n", bom=b""):
    course = tmp_path / "dispense"
    course.mkdir()
    grouped = defaultdict(lambda: defaultdict(list))
    for f in v103.FACTS:
        grouped[f.pdf][f.page].append(" ".join(f.source_terms))

    hashes = {}
    for filename in v103.SOURCE_HASHES:
        doc = fitz.open()
        pages = grouped[filename]
        max_page = max(pages, default=1)
        for number in range(1, max_page + 1):
            page = doc.new_page()
            for index, evidence in enumerate(pages[number]):
                page.insert_text((50, 70 + index * 25), evidence, fontsize=8)
        path = course / filename
        doc.save(path)
        doc.close()
        hashes[filename] = v103.sha(path.read_bytes())
    monkeypatch.setattr(v103, "SOURCE_HASHES", hashes)

    source_files = tuple(sorted(course.glob("*.pdf")))
    scan = CourseScan("source", course, source_files, len(source_files), 0, len(source_files))
    directory = tmp_path / "input"
    directory.mkdir()
    complete = directory / "complete.md"
    old = directory / "v102.md"
    previous_report = directory / "v102-report.json"
    bytes_in = bom + _document().replace("\n", newline).encode("utf-8")
    complete.write_bytes(bytes_in)
    old.write_bytes(bytes_in)
    report = {
        "version": "scientific-clarifications-v10.2-review-only",
        "publication_allowed": False,
        "scientifically_verified": False,
        "complete_sha256": v103.sha(bytes_in),
        "candidate_sha256": v103.sha(bytes_in),
        "unchanged_other_chapters": 62,
        "after_audit": "WARN",
        "source_fingerprint": "known-fingerprint",
    }
    previous_report.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(v103, "COMPLETE_SHA256", v103.sha(bytes_in))
    monkeypatch.setattr(v103, "V102_SHA256", v103.sha(bytes_in))
    monkeypatch.setattr(
        v103, "V102_REPORT_SHA256", v103.sha(previous_report.read_bytes())
    )
    monkeypatch.setattr(
        v103, "_validated_manifest",
        lambda *_: ({"fingerprint": "known-fingerprint"}, {}),
    )
    monkeypatch.setattr(v103, "_snapshot", lambda *_: [])
    monkeypatch.setattr(v103, "_fingerprint", lambda *_: "known-fingerprint")
    monkeypatch.setattr(
        v103,
        "audit_fast_essential",
        lambda *_: SimpleNamespace(status="WARN", missing_exam_number_chapters=[]),
    )
    return scan, complete, old, previous_report, tmp_path / "output"


def test_all_source_facts_present_and_ordered():
    doc = _document()
    candidate, edits = v103.apply_v103(doc)
    assert len(edits) == 5
    assert len(v103.FACTS) >= 25
    assert candidate != doc
    v103.validate_fact_coverage(candidate)
    old_spans = _spans(doc)
    new_spans = _spans(candidate)
    assert (
        candidate[slice(*new_spans["Capitolo non autorizzato"])]
        == doc[slice(*old_spans["Capitolo non autorizzato"])]
    )
    assert "12 ATP equivalenti" in candidate
    assert "10 ATP equivalenti" in candidate
    assert "isomeri strutturali" not in candidate or "non isomeri strutturali" in candidate


@pytest.mark.parametrize("fact", v103.FACTS, ids=lambda fact: fact.ident)
def test_every_mandatory_fact_is_fail_closed(fact):
    candidate, _ = v103.apply_v103(_document())
    assert candidate.count(fact.witness) == 1
    with pytest.raises(ValueError, match=fact.ident):
        v103.validate_fact_coverage(candidate.replace(fact.witness, "[OMESSO]", 1))


@pytest.mark.parametrize("patch", v103.PATCHES, ids=lambda p: p.ident)
def test_missing_or_duplicate_edit_source_blocked(patch):
    source = _document()
    with pytest.raises(ValueError, match="Ancoraggio"):
        v103.apply_v103(source.replace(patch.before, "[MANOMESSO]", 1))
    with pytest.raises(ValueError, match="Ancoraggio"):
        v103.apply_v103(source.replace(patch.before, patch.before + patch.before, 1))


def test_no_publication_and_source_integrity_with_bom_crlf(tmp_path, monkeypatch):
    args = _bundle(tmp_path, monkeypatch, newline="\r\n", bom=b"\xef\xbb\xbf")
    old_hashes = [v103.sha(p.read_bytes()) for p in args[1:4]]
    original_pdf_hashes = {
        p.name: v103.sha(p.read_bytes()) for p in args[0].path.glob("*.pdf")
    }
    output = v103.run_v103(*args)
    candidate = (
        output / "essenziale-v10.3-CANDIDATA-NON-PUBBLICATA.md"
    ).read_bytes()
    assert candidate.startswith(b"\xef\xbb\xbf")
    assert b"\n" not in candidate.replace(b"\r\n", b"")
    report = json.loads((output / "v10.3-report.json").read_text(encoding="utf-8"))
    assert report["publication_allowed"] is False
    assert report["scientifically_certified"] is False
    assert report["full_semantic_equivalence_verified"] is False
    assert report["original_pdfs_sha256_verified"] is True
    assert report["pdf_page_anchors_verified"] is True
    assert report["unchanged_other_chapters"] == 1
    assert report["mandatory_fact_count"] == len(v103.FACTS)
    assert len(report["chapters"]) == len(TITLES)
    assert [item["title"] for item in report["chapters"]] == list(TITLES)
    for chapter in report["chapters"]:
        assert chapter["complete_words"] > 0
        assert chapter["candidate_words"] > 0
        assert chapter["reduction_percent"] == round(
            100 * (1 - chapter["candidate_words"] / chapter["complete_words"]), 2
        )
        assert chapter["hard_compression_cap"] is False
    assert len(report["changes"]) == 5
    assert report["candidate_sha256"] == v103.sha(candidate)
    assert "BLOCKED_" in report["manual_review_gate"]
    assert [v103.sha(p.read_bytes()) for p in args[1:4]] == old_hashes
    assert {
        p.name: v103.sha(p.read_bytes()) for p in args[0].path.glob("*.pdf")
    } == original_pdf_hashes
    for name, digest in json.loads(
        (output / "SHA256SUMS.json").read_text()
    ).items():
        assert v103.sha((output / name).read_bytes()) == digest
    with pytest.raises(ValueError, match="output"):
        v103.run_v103(*args)


def test_original_page_word_removed_blocks(tmp_path, monkeypatch):
    args = _bundle(tmp_path, monkeypatch)
    filename = v103.KREBS_PDF
    path = args[0].path / filename
    with fitz.open(path) as doc:
        page = doc[3]
        # Clear page 4 source terms so the text witness cannot be established.
        rect = page.rect
        page.add_redact_annot(rect, fill=(1, 1, 1))
        page.apply_redactions()
        doc.save(str(path) + ".new")
    replacement = Path(str(path) + ".new")
    replacement.replace(path)
    changed = dict(v103.SOURCE_HASHES)
    changed[filename] = v103.sha(path.read_bytes())
    monkeypatch.setattr(v103, "SOURCE_HASHES", changed)
    with pytest.raises(ValueError, match="Fonte non verificabile"):
        v103.run_v103(*args)
    assert not args[4].exists()


def test_pdf_sha_tamper_blocks(tmp_path, monkeypatch):
    args = _bundle(tmp_path, monkeypatch)
    path = args[0].path / v103.KREBS_PDF
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="PDF originale"):
        v103.run_v103(*args)
    assert not args[4].exists()


def test_original_report_tamper_blocks(tmp_path, monkeypatch):
    args = _bundle(tmp_path, monkeypatch)
    old = json.loads(args[3].read_text())
    old["publication_allowed"] = True
    args[3].write_text(json.dumps(old))
    monkeypatch.setattr(
        v103, "V102_REPORT_SHA256", v103.sha(args[3].read_bytes())
    )
    with pytest.raises(ValueError, match="non autorizza"):
        v103.run_v103(*args)
    assert not args[4].exists()


def test_audit_regression_still_blocks(tmp_path, monkeypatch):
    args = _bundle(tmp_path, monkeypatch)
    states = iter([
        SimpleNamespace(status="WARN", missing_exam_number_chapters=[]),
        SimpleNamespace(status="FAIL", missing_exam_number_chapters=[TITLES[0]]),
    ])
    monkeypatch.setattr(v103, "audit_fast_essential", lambda *_: next(states))
    with pytest.raises(ValueError, match="Nuovo FAIL"):
        v103.run_v103(*args)
    assert not args[4].exists()


def test_pdf_fingerprint_change_in_flight_blocks(tmp_path, monkeypatch):
    args = _bundle(tmp_path, monkeypatch)
    monkeypatch.setattr(v103, "_fingerprint", lambda _: "changed-fingerprint")
    with pytest.raises(ValueError, match="cambiati"):
        v103.run_v103(*args)
    assert not args[4].exists()


def _make_archive(args, tmp_path, monkeypatch):
    """Fixture ZIP with a manifest equivalent to the original Windows export."""
    archive = tmp_path / "pinned-sources.zip"
    records = []
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for index, (filename, digest) in enumerate(
            v103.SOURCE_HASHES.items(), start=1
        ):
            data = (args[0].path / filename).read_bytes()
            assert v103.sha(data) == digest
            records.append({
                "Capitolo": "Source",
                "File": filename,
                "SHA256": digest.upper(),
                "DimensioneBytes": len(data),
            })
            z.writestr(f"Source\\{index:02d}-{filename}", data)
        z.writestr("manifest.json", json.dumps(records))
    monkeypatch.setattr(
        v103, "SOURCE_ARCHIVE_SHA256", v103.sha(archive.read_bytes())
    )
    return archive


def test_exact_zip_mode_with_duplicate_course_pdf(tmp_path, monkeypatch):
    args = _bundle(tmp_path, monkeypatch)
    archive = _make_archive(args, tmp_path, monkeypatch)
    # The course contains two identical file names on separate paths. Folder
    # mode fails closed, whereas the pinned archive identifies one source.
    extra = args[0].path / "archive-shadow"
    extra.mkdir()
    filename = v103.CHEM_PDF
    (extra / filename).write_bytes((args[0].path / filename).read_bytes())
    with pytest.raises(ValueError, match="Più copie identiche"):
        v103.validate_original_pdfs(args[0].path)
    evidence = v103.validate_original_pdfs(args[0].path, archive)
    assert len(evidence) == len(v103.FACTS)
    result_dir = v103.run_v103(*args, source_archive=archive)
    report = json.loads(
        (result_dir / "v10.3-report.json").read_text(encoding="utf-8")
    )
    assert report["source_validation_mode"] == "sha256_pinned_zip"
    assert report["source_archive_sha256"] == v103.sha(archive.read_bytes())
    assert report["original_pdfs_sha256_verified"]
    assert report["pdf_page_anchors_verified"]
    assert report["publication_allowed"] is False
    assert report["scientifically_certified"] is False


def test_archive_bit_tamper_is_blocked_without_output(tmp_path, monkeypatch):
    args = _bundle(tmp_path, monkeypatch)
    archive = _make_archive(args, tmp_path, monkeypatch)
    archive.write_bytes(archive.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="ZIP delle fonti differente"):
        v103.run_v103(*args, source_archive=archive)
    assert not args[4].exists()


def test_archive_manifest_conflict_is_blocked_even_if_zip_pinned(
    tmp_path, monkeypatch,
):
    args = _bundle(tmp_path, monkeypatch)
    archive = _make_archive(args, tmp_path, monkeypatch)
    with zipfile.ZipFile(archive, "r") as z:
        contents = {name: z.read(name) for name in z.namelist()}
    manifest = json.loads(contents["manifest.json"].decode())
    manifest[0]["SHA256"] = "0" * 64
    contents["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(archive, "w") as z:
        for name, data in contents.items():
            z.writestr(name, data)
    monkeypatch.setattr(
        v103, "SOURCE_ARCHIVE_SHA256", v103.sha(archive.read_bytes())
    )
    with pytest.raises(ValueError, match="SHA manifest incoerente"):
        v103.run_v103(*args, source_archive=archive)
    assert not args[4].exists()


def test_missing_pdf_file_has_actionable_diagnostic(tmp_path, monkeypatch):
    args = _bundle(tmp_path, monkeypatch)
    (args[0].path / v103.CHEM_PDF).unlink()
    with pytest.raises(ValueError, match="--source-archive"):
        v103.validate_original_pdfs(args[0].path)


def test_locked_manifest_hashes_are_valid_and_chemistry_matches_original():
    """Protect the independently verified seven-PDF manifest from typos."""
    assert len(v103.SOURCE_HASHES) == 7
    assert all(
        len(digest) == 64 and all(char in "0123456789abcdef" for char in digest)
        for digest in v103.SOURCE_HASHES.values()
    )
    assert v103.SOURCE_HASHES["CHIMICA ORGANICA e carboidrati 2.pdf"] == (
        "43343aeb466dcd7dc16634cf27b1d0bcc153aa35f6df362f2ce9e0f33bec7efd"
    )


def test_source_grounded_cli_displays_all_three_chapters(tmp_path, monkeypatch):
    """End-to-end contract: CLI must consume the freshly written V10.3 report."""
    from typer.testing import CliRunner

    from bc_science import cli, courses

    args = _bundle(tmp_path, monkeypatch)
    monkeypatch.setattr(courses, "scan_course", lambda _: args[0])

    invoked = CliRunner().invoke(cli.app, [
        "courses",
        "source-grounded-preview",
        str(args[0].path),
        "--complete", str(args[1]),
        "--v102-candidate", str(args[2]),
        "--v102-report", str(args[3]),
        "--output-dir", str(args[4]),
    ])
    assert invoked.exit_code == 0, (
        f"{invoked.stdout}\n{type(invoked.exception).__name__}: "
        f"{invoked.exception}"
    )
    assert "V10.3: candidata non pubblicata" in invoked.stdout
    assert "Fatti da fonti verificati:" in invoked.stdout
    assert "Origine PDF: course_folder" in invoked.stdout
    assert "Altri capitoli invariati: 1" in invoked.stdout
    assert "Audit: WARN → WARN" in invoked.stdout
    for title in TITLES:
        assert title in invoked.stdout
    assert (args[4] / "v10.3-report.json").is_file()
