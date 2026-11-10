"""Science correction proposals must be isolated, SHA-validated and review-only."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from bc_science import cli, scientific_patch_drafts
from bc_science.scientific_claim_flags import inspect_scientific_claims
from bc_science.scientific_patch_drafts import prepare_scientific_correction_drafts


def _doc(chapter: str, body: str) -> str:
    return (
        "# Scienze\n\n## Indice degli argomenti\n"
        "- " + chapter + "\n\n---\n\n## " + chapter + "\n\n" + body
    )


def _setup(monkeypatch, tmp_path: Path):
    home = tmp_path / "home"
    monkeypatch.setenv("BC_SCIENCE_HOME", str(home))
    source = tmp_path / "materie"
    source.mkdir()
    names = ["ANATOMIA", "BIOLOGIA", "FISIOLOGIA"]
    scans = [SimpleNamespace(name=name) for name in names]
    monkeypatch.setattr(scientific_patch_drafts, "scan_courses", lambda _root: scans)
    monkeypatch.setattr(
        scientific_patch_drafts, "course_output_path",
        lambda scan: home / "outputs" / "courses" / scan.name / "riassunto-unico.md",
    )
    monkeypatch.setattr(
        scientific_patch_drafts, "course_study_output_path",
        lambda scan: home / "outputs" / "courses" / scan.name / "riassunto-studio.md",
    )
    anatomy = _doc(
        "Faringe ed esofago",
        "Esofago: tubo muscolare lungo che trasporta il cibo dallo stomaco "
        "allo stomaco. Dotti epatici: canali attraverso cui la bile viaggia "
        "verso l'esterno del corpo.",
    )
    biology = _doc(
        "BIOSINTESI DEGLI ACIDI GRASSI",
        "La sintesi del palmitato richiede 7 molecole di acetil-CoA, "
        "14 di malonil-CoA, 14 di NADPH + H+ e 14 di ATP.",
    )
    anatomy_path = home / "outputs" / "courses" / "ANATOMIA" / "riassunto-unico.md"
    bio_path = home / "outputs" / "courses" / "BIOLOGIA" / "riassunto-studio.md"
    for path, value in ((anatomy_path, anatomy), (bio_path, biology)):
        path.parent.mkdir(parents=True)
        path.write_text(value, encoding="utf-8")
    return source, anatomy_path, bio_path


def _report(root, tmp_path: Path, *paths: Path):
    flags = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        subject = path.parent.name
        edition = "Completa" if path.name == "riassunto-unico.md" else "Essenziale"
        for issue in inspect_scientific_claims(text):
            flags.append({
                "subject": subject,
                "edition": edition,
                "path": str(path.resolve()),
                "document_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "rule": issue.rule,
                "chapter": issue.chapter,
                "excerpt": issue.excerpt,
            })
    report = tmp_path / "audit.json"
    report.write_text(
        json.dumps({
            "schema": "bc-science-known-scientific-flags-v1",
            "scanned_files": len(paths),
            "flags": flags,
        }), encoding="utf-8",
    )
    return report


def test_drafts_fix_only_known_phrases_and_leave_live_bytes_identical(
    monkeypatch, tmp_path,
):
    root, anatomy, bio = _setup(monkeypatch, tmp_path)
    originals = {str(path): path.read_bytes() for path in (anatomy, bio)}
    audit = _report(root, tmp_path, anatomy, bio)
    output = tmp_path / "review" / "session1"
    plan = prepare_scientific_correction_drafts(root, audit, output)
    assert len(plan["proposals"]) == 2
    assert all(row["state"] == "REVIEW" for row in plan["proposals"])
    assert len(list(output.glob("proposta-*.md"))) == 2
    assert len(list(output.glob("proposta-*.diff"))) == 2
    for row in plan["proposals"]:
        draft = Path(row["draft_path"]).read_text(encoding="utf-8")
        assert inspect_scientific_claims(draft) == ()
        assert row["revised_sha256"] == hashlib.sha256(draft.encode("utf-8")).hexdigest()
        diff = Path(row["diff_path"]).read_text(encoding="utf-8")
        assert diff.startswith("--- ")
        assert "+++" in diff
    assert "dalla faringe allo stomaco" in Path(
        plan["proposals"][0]["draft_path"]
    ).read_text(encoding="utf-8")
    assert originals == {str(path): path.read_bytes() for path in (anatomy, bio)}
    assert plan["scientifically_verified"] is False
    assert plan["published_modified"] is False


def test_sha_mismatch_blocks_draft_and_never_overwrites(monkeypatch, tmp_path):
    root, anatomy, bio = _setup(monkeypatch, tmp_path)
    audit = _report(root, tmp_path, anatomy, bio)
    anatomy.write_text(
        anatomy.read_text(encoding="utf-8") + "\nAltra modifica locale.",
        encoding="utf-8",
    )
    result = prepare_scientific_correction_drafts(
        root, audit, tmp_path / "review" / "session2",
    )
    assert result["proposals"][0]["state"] == "BLOCKED"
    assert "modificato dopo" in result["proposals"][0]["reason"]
    assert result["proposals"][0]["draft_path"] is None
    assert result["proposals"][1]["state"] == "REVIEW"


def test_no_replacing_old_report_and_no_writes_to_live(monkeypatch, tmp_path):
    root, anatomy, _ = _setup(monkeypatch, tmp_path)
    audit = _report(root, tmp_path, anatomy)
    for forbidden in (
        tmp_path / "materie" / "nested",
        tmp_path / "home" / "outputs" / "courses" / "ANATOMIA",
    ):
        with pytest.raises(ValueError, match="non isolata"):
            prepare_scientific_correction_drafts(root, audit, forbidden)
    target = tmp_path / "existing"
    target.mkdir()
    sentinel = target / "keep.txt"
    sentinel.write_text("important", encoding="utf-8")
    with pytest.raises(ValueError, match="già esistente"):
        prepare_scientific_correction_drafts(root, audit, target)
    assert sentinel.read_text(encoding="utf-8") == "important"


def test_tampered_report_cannot_read_an_unapproved_file(monkeypatch, tmp_path):
    root, anatomy, _ = _setup(monkeypatch, tmp_path)
    audit = _report(root, tmp_path, anatomy)
    obj = json.loads(audit.read_text(encoding="utf-8"))
    private = tmp_path / "unapproved.md"
    private.write_text("Private notes", encoding="utf-8")
    obj["flags"][0]["path"] = str(private)
    audit.write_text(json.dumps(obj), encoding="utf-8")
    result = prepare_scientific_correction_drafts(
        root, audit, tmp_path / "review" / "tampered",
    )
    assert result["proposals"][0]["state"] == "BLOCKED"
    assert "non ammesso" in result["proposals"][0]["reason"]
    assert not list((tmp_path / "review" / "tampered").glob("proposta-*.md"))


def test_cli_outputs_review_only_report(monkeypatch, tmp_path):
    root, anatomy, bio = _setup(monkeypatch, tmp_path)
    # CLI calls the module's real function; no Ollama or PDF generation.
    audit = _report(root, tmp_path, anatomy, bio)
    out = tmp_path / "review" / "cli"
    result = CliRunner().invoke(
        cli.app,
        [
            "courses", "draft-scientific-corrections", str(root),
            "--audit-json", str(audit),
            "--output-dir", str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    report = json.loads((out / "review-report.json").read_text(encoding="utf-8"))
    assert len(report["proposals"]) == 2
    assert all(item["state"] == "REVIEW" for item in report["proposals"])
