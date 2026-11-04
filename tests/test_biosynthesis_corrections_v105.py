"""V10.5: source SHA gates, exact four edits, one-chapter scope and CLI."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import bc_science.biosynthesis_corrections_v105 as v105
from bc_science import cli
from bc_science.structural_compression import TITLES, _spans


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _document(plan: dict, *, multiline: bool = False) -> bytes:
    names = sorted(
        [v105.TITLE, *TITLES, *(f"External chapter {i:02d}" for i in range(61))]
    )
    index = "\n".join(f"- {name}" for name in names)
    chapter_texts = []
    for name in names:
        if name == v105.TITLE:
            source = [item["old"] for item in plan["fixes"]]
            if multiline:
                source[1] = source[1].replace(
                    ". Il resto del NADPH", ".\nIl resto del NADPH"
                )
            body = "\n\n".join(source)
        else:
            body = f"Materiale invariato {name}."
        chapter_texts.append(f"## {name}\n\n{body}")
    recap = (
        "## Ripasso globale\n\n1. **BIOSINTESI DEGLI ACIDI GRASSI**: "
        + plan["global_recap_fix"]["old"] + "\n\n"
        if multiline else ""
    )
    content = (
        "# Ecampus test\n\n" + recap + "## Indice degli argomenti\n"
        + index + "\n\n---\n\n"
        + "\n\n---\n\n".join(chapter_texts)
        + "\n"
    )
    return content.replace("\n", "\r\n").encode("utf-8")


def _patched_chapter_source_sha(data: bytes, plan: dict, kind: str) -> None:
    text = data.decode("utf-8")
    found = v105.CHAPTER_RE.search(text)
    assert found
    chapter = found.group(0).strip()
    plan["chapter_original_sha256"][kind] = sha(chapter.encode("utf-8"))
    revised = chapter
    for item in plan["fixes"]:
        expression = re.escape(item["old"]).replace(r"\ ", r"\s+")
        revised, count = re.subn(
            expression, lambda _m, fix=item: fix["replacement"], revised
        )
        assert count == 1
    plan["chapter_expected_corrected_sha256"][kind] = sha(
        revised.encode("utf-8")
    )


def _fixture(tmp_path, monkeypatch):
    plan = copy.deepcopy(v105._plan())
    source = tmp_path / "BCSciencePreviews"
    source.mkdir()
    complete = source / "riassunto-unico.md"
    essential = source / "essenziale-v10.4.md"
    report_path = source / "v10.4-report.json"
    archive = source / "FONTI-BIOSINTESI-PER-AUDIT.zip"
    complete.write_bytes(_document(plan))
    essential.write_bytes(_document(plan, multiline=True))
    archive.write_bytes(b"fake zipped material in tests only")
    _patched_chapter_source_sha(complete.read_bytes(), plan, "complete")
    _patched_chapter_source_sha(essential.read_bytes(), plan, "essential")
    plan["complete_sha256"] = sha(complete.read_bytes())
    plan["v104_candidate_sha256"] = sha(essential.read_bytes())
    metadata = {
        "publication_allowed": False,
        "scientifically_certified": False,
        "total_guarded_fact_count": 35,
        "unchanged_other_chapters": 62,
        "candidate_sha256": plan["v104_candidate_sha256"],
        "after_audit": "WARN",
    }
    report_path.write_text(json.dumps(metadata), encoding="utf-8")
    plan["v104_report_sha256"] = sha(report_path.read_bytes())
    monkeypatch.setattr(v105, "_plan", lambda: plan)
    monkeypatch.setattr(v105, "_verify_archive", lambda *_: None)
    monkeypatch.setattr(v105, "_validate_essential_witnesses", lambda *_: None)
    monkeypatch.setattr(
        v105, "audit_fast_essential",
        lambda *_: SimpleNamespace(status="WARN"),
    )
    output = source / "spark-v105-corrected-fresh"
    return plan, (complete, essential, report_path, archive, output)


def test_source_manifest_pins_eight_pdfs_and_four_reviewed_corrections():
    plan = v105._plan()
    assert len(plan["pdf_sources"]) == 8
    assert tuple(fix["id"] for fix in plan["fixes"]) == v105.EXPECTED_FIX_IDS
    for entry in plan["pdf_sources"].values():
        assert len(entry["sha256"]) == 64
    for fix in plan["fixes"]:
        assert fix["old"] != fix["replacement"]
    assert plan["fixes"][0]["source_page"] == 11


def test_real_archived_excerpts_and_review_proposal_have_pinned_sha256s():
    """Protect the corrected chapter texts from changing independently."""
    plan = v105._plan()
    assert plan["chapter_original_sha256"]["complete"] == (
        "8ade835154ce2e6fb13dec13239cb027e5e1002e55a4b96cb7741b75a6d085df"
    )
    assert plan["chapter_original_sha256"]["essential"] == (
        "cfdab31dcf4d41343af3e9a15a7612f9e08baf1861508d82f6a9f4ff1e648709"
    )
    assert plan["chapter_expected_corrected_sha256"]["complete"] == (
        "c7e9225b634c4e41cb4256255f9e70aad35d19383e76807c69afce3325a4cc57"
    )
    assert plan["chapter_expected_corrected_sha256"]["essential"] == (
        "537466d3d8c84ad759809b2058e2ccf09cd55b4a74d05de218b09495ce1e9245"
    )


def test_edits_apply_exactly_four_times_in_each_document(tmp_path, monkeypatch):
    plan, args = _fixture(tmp_path, monkeypatch)
    for kind, path in (("complete", args[0]), ("essential", args[1])):
        result, changes = v105._replace_single_chapter(
            path.read_bytes(), plan, kind
        )
        assert changes == list(v105.EXPECTED_FIX_IDS)
        text = result.decode("utf-8")
        if kind == "essential":
            assert plan["global_recap_fix"]["old"] not in text
            assert text.count(plan["global_recap_fix"]["replacement"]) == 1
        else:
            assert plan["global_recap_fix"]["replacement"] not in text
        assert "**8 acetil-CoA**" in text
        assert "**7 ATP**" in text
        assert "**14 NADPH**" in text
        assert "**malato consumando NADH**" in text
        assert "**produce NADPH**" in text
        assert "rallenta il flusso del ciclo di Krebs" in text
        assert "**sintesi de novo del palmitato**" in text
        assert "14 di malonil-CoA, 14 di NADPH" not in text
        spans_before = _spans(path.read_bytes().decode("utf-8").replace("\r\n", "\n"))
        spans_after = _spans(text.replace("\r\n", "\n"))
        assert len(spans_before) == len(spans_after) == 65
        for title in spans_before:
            if title != v105.TITLE:
                before_text = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
                after_text = text.replace("\r\n", "\n")
                assert (
                    before_text[slice(*spans_before[title])]
                    == after_text[slice(*spans_after[title])]
                )
        assert result.count(b"\r\n") == result.count(b"\n")


def test_preview_creates_two_nonpublished_docs_and_checksums(
    tmp_path, monkeypatch,
):
    plan, args = _fixture(tmp_path, monkeypatch)
    previous = [p.read_bytes() for p in args[:4]]
    result = v105.run_v105(*args)
    assert result == args[-1]
    report = json.loads((result / "v10.5-report.json").read_text("utf-8"))
    assert report["publication_allowed"] is False
    assert report["scientifically_certified"] is False
    assert report["corrected_chapters_per_document"] == 1
    assert report["unchanged_chapters_per_document"] == 64
    assert report["global_recap_updates"] == 1
    assert report["global_recap_sync"]["id"] == "B05_RECAP_SYNC"
    essential = (
        result / "essenziale-v10.5-BIOSINTESI-CANDIDATA-NON-PUBBLICATA.md"
    ).read_text("utf-8")
    assert plan["global_recap_fix"]["old"] not in essential
    assert essential.count(plan["global_recap_fix"]["replacement"]) == 1
    assert report["v104_three_pilot_chapters_unchanged"] is True
    assert report["before_audit"] == report["after_audit"] == "WARN"
    assert [row["id"] for row in report["corrections"]] == [
        "B01", "B02", "B03", "B04"
    ]
    sums = json.loads((result / "SHA256SUMS.json").read_text("utf-8"))
    assert len(sums) == 6
    for filename, digest in sums.items():
        assert sha((result / filename).read_bytes()) == digest
    assert [p.read_bytes() for p in args[:4]] == previous
    with pytest.raises(ValueError, match="output V10.5"):
        v105.run_v105(*args)


def test_preview_blocks_corrupted_input_without_creating_folder(
    tmp_path, monkeypatch,
):
    _, args = _fixture(tmp_path, monkeypatch)
    args[1].write_bytes(args[1].read_bytes() + b"x")
    with pytest.raises(ValueError, match="Hash input non valido"):
        v105.run_v105(*args)
    assert not args[-1].exists()


def test_preview_blocks_existing_parent_as_output(tmp_path, monkeypatch):
    _, args = _fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="output V10.5"):
        v105.run_v105(*args[:4], args[0].parent)


def test_missing_one_original_claim_fails_without_fuzzy_substitution(
    tmp_path, monkeypatch,
):
    plan, args = _fixture(tmp_path, monkeypatch)
    raw = args[0].read_bytes()
    modified = raw.replace(
        plan["fixes"][0]["old"].encode(),
        b"Wrong unreviewed palmitate values",
    )
    plan["chapter_original_sha256"]["complete"] = sha(
        v105.CHAPTER_RE.search(modified.decode()).group(0).strip().encode()
    )
    with pytest.raises(ValueError, match="B01"):
        v105._replace_single_chapter(modified, plan, "complete")


def test_zip_bytes_must_match_pinned_archive(tmp_path):
    plan = v105._plan()
    path = tmp_path / "fake.zip"
    path.write_bytes(b"not original")
    with pytest.raises(ValueError, match="ZIP"):
        v105._verify_archive(path, plan)


def test_new_fail_audit_still_blocks(tmp_path, monkeypatch):
    _, args = _fixture(tmp_path, monkeypatch)
    statuses = iter(["WARN", "FAIL"])
    monkeypatch.setattr(
        v105, "audit_fast_essential",
        lambda *_: SimpleNamespace(status=next(statuses)),
    )
    with pytest.raises(ValueError, match="Audit V10.5 bloccato"):
        v105.run_v105(*args)
    assert not args[-1].exists()


def test_cli_completes_for_four_edits_and_reports_paths(tmp_path, monkeypatch):
    _, args = _fixture(tmp_path, monkeypatch)
    result = CliRunner().invoke(cli.app, [
        "courses", "biosynthesis-source-preview",
        "--complete", str(args[0]),
        "--v104-candidate", str(args[1]),
        "--v104-report", str(args[2]),
        "--source-archive", str(args[3]),
        "--output-dir", str(args[4]),
    ])
    assert result.exit_code == 0, f"{result.stdout}\n{result.exception}"
    assert "V10.5: DUE candidate NON pubblicate" in result.stdout
    assert "64 capitoli" in result.stdout



def test_missing_global_recap_claim_blocks_without_editing(tmp_path, monkeypatch):
    plan, args = _fixture(tmp_path, monkeypatch)
    old = plan["global_recap_fix"]["old"].encode()
    altered = args[1].read_bytes().replace(old, b"Frase assente", 1)
    assert b"Frase assente" in altered.split(b"## Indice degli argomenti", 1)[0]
    with pytest.raises(ValueError, match="richiamo globale obsoleto"):
        v105._replace_single_chapter(altered, plan, "essential")
    assert not args[-1].exists()
