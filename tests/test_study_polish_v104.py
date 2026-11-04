"""V10.4 pilot: exact coverage, source integrity and functional CLI output."""
import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import bc_science.study_polish_v104 as v104
from bc_science import cli, courses
from bc_science.courses import CourseScan
from bc_science.structural_compression import TITLES, _spans


def _document() -> str:
    titles = sorted([*TITLES, *(f"Capitolo esterno {i:02d}" for i in range(62))])
    index = "\n".join(f"- {title}" for title in titles)
    chapters = [
        f"## {name}\n\n### Sintesi progressiva\n\n"
        f"Riassunto V10.3 precedente della lezione {name}.\n"
        for name in titles
    ]
    return (
        "# Documento di studio\n\n## Indice degli argomenti\n"
        + index + "\n\n---\n\n" + "\n\n---\n\n".join(chapters)
    )


def _inputs(tmp_path, monkeypatch):
    scan_dir = tmp_path / "dispense"
    scan_dir.mkdir()
    raw_pdf = scan_dir / "lezione.pdf"
    raw_pdf.write_bytes(b"Original source evidence")
    scan = CourseScan("sample", scan_dir, (raw_pdf.resolve(),), 1, 0, 1)
    inputs = tmp_path / "source"
    inputs.mkdir()
    complete = inputs / "complete.md"
    old = inputs / "v103.md"
    previous = inputs / "v103-report.json"
    archive = inputs / "sources.zip"
    text = _document().encode("utf-8")
    complete.write_bytes(text)
    old.write_bytes(text)
    archive.write_bytes(b"Pinned source fixture")
    expected = {
        "version": "spark-v10.3-source-grounded-review-only",
        "publication_allowed": False,
        "scientifically_certified": False,
        "mandatory_fact_count": len(v104.FACTS),
        "unchanged_other_chapters": 62,
        "candidate_sha256": v104.sha(text),
        "complete_sha256": v104.sha(text),
        "source_archive_sha256": v104.sha(archive.read_bytes()),
        "after_audit": "WARN",
        "source_fingerprint": "fixed-fingerprint",
        "source_pdf_hashes": {"fixture.pdf": "abcdef"},
        "source_discrepancies": [],
    }
    previous.write_text(json.dumps(expected), encoding="utf-8")
    monkeypatch.setattr(v104, "COMPLETE_SHA256", v104.sha(text))
    monkeypatch.setattr(v104, "V103_CANDIDATE_SHA256", v104.sha(text))
    monkeypatch.setattr(
        v104, "V103_REPORT_SHA256", v104.sha(previous.read_bytes())
    )
    monkeypatch.setattr(
        v104, "SOURCE_ARCHIVE_SHA256", v104.sha(archive.read_bytes())
    )
    monkeypatch.setattr(
        v104, "_validated_manifest",
        lambda *_: ({"fingerprint": "fixed-fingerprint"}, {}),
    )
    monkeypatch.setattr(v104, "_fingerprint", lambda _: "fixed-fingerprint")
    monkeypatch.setattr(v104, "_snapshot", lambda _: [])
    monkeypatch.setattr(
        v104, "validate_original_pdfs",
        lambda *_: [
            {
                "ident": fact.ident,
                "chapter": fact.chapter,
                "pdf": fact.pdf,
                "page": fact.page,
                "witness": fact.witness,
            }
            for fact in v104.FACTS
        ],
    )
    monkeypatch.setattr(
        v104,
        "audit_fast_essential",
        lambda *_: SimpleNamespace(status="WARN"),
    )
    return scan, complete, old, previous, archive, tmp_path / "new-preview"


def test_30_original_fact_witnesses_survive_edits():
    template = v104.TEMPLATE_PATH.read_text(encoding="utf-8")
    candidate, changes = v104.apply_v104(_document(), template)
    assert len(changes) == 3
    assert len(v104.FACTS) == 30
    assert all("### Ripasso attivo" in candidate for _ in (0,))
    spans = _spans(candidate)
    for fact in v104.FACTS:
        assert candidate[slice(*spans[fact.chapter])].count(fact.witness) == 1


def test_other_62_chapters_exactly_preserved():
    text = _document()
    updated, changes = v104.apply_v104(
        text, v104.TEMPLATE_PATH.read_text(encoding="utf-8")
    )
    assert len(changes) == 3
    old_spans = _spans(text)
    updated_spans = _spans(updated)
    for title in old_spans:
        if title not in TITLES:
            assert (
                text[slice(*old_spans[title])]
                == updated[slice(*updated_spans[title])]
            )


@pytest.mark.parametrize("fact", v104.FACTS, ids=lambda f: f.ident)
def test_any_mandatory_fact_removed_blocks_generation(fact):
    template = v104.TEMPLATE_PATH.read_text(encoding="utf-8")
    assert template.count(fact.witness) == 1
    damaged = template.replace(fact.witness, "FATTO OMESSO", 1)
    with pytest.raises(ValueError, match=fact.ident):
        v104.apply_v104(_document(), damaged)


@pytest.mark.parametrize("modifier", [
    lambda s: s.replace("## CHIMICA ORGANICA e carboidrati", "## Altro"),
    lambda s: s.replace("### Ripasso attivo", "### Ripasso passivo", 1),
    lambda s: s + "\n## Il ciclo di Krebs\n\n---\n",
])
def test_unapproved_template_changes_rejected(modifier):
    template = v104.TEMPLATE_PATH.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        v104.apply_v104(_document(), modifier(template))


def test_local_preview_has_report_diff_hashes_and_no_publication(
    tmp_path, monkeypatch,
):
    args = _inputs(tmp_path, monkeypatch)
    before = [p.read_bytes() for p in args[1:5]]
    result = v104.run_v104(*args)
    assert result == args[-1]
    data = json.loads((result / "v10.4-report.json").read_text("utf-8"))
    candidate = (result / "essenziale-v10.4-CANDIDATA-NON-PUBBLICATA.md").read_bytes()
    assert data["publication_allowed"] is False
    assert data["scientifically_certified"] is False
    assert data["full_semantic_equivalence_verified"] is False
    assert data["mandatory_fact_count"] == 30
    assert len(data["atomic_fact_matrix"]) == 30
    assert all(f["candidate_witness_occurrences"] == 1 for f in data["atomic_fact_matrix"])
    assert data["unchanged_other_chapters"] == 62
    assert len(data["chapters"]) == 3
    assert data["candidate_sha256"] == v104.sha(candidate)
    assert data["manual_review_gate"].startswith("BLOCKED_")
    assert [p.read_bytes() for p in args[1:5]] == before
    assert (result / "v10.4-vs-v10.3.diff").is_file()
    for filename, digest in json.loads(
        (result / "SHA256SUMS.json").read_text("utf-8")
    ).items():
        assert v104.sha((result / filename).read_bytes()) == digest
    with pytest.raises(ValueError, match="output"):
        v104.run_v104(*args)


def test_cli_end_to_end_writes_candidate_and_displays_metrics(
    tmp_path, monkeypatch,
):
    args = _inputs(tmp_path, monkeypatch)
    monkeypatch.setattr(courses, "scan_course", lambda _: args[0])
    result = CliRunner().invoke(cli.app, [
        "courses", "study-polish-preview", str(args[0].path),
        "--complete", str(args[1]),
        "--v103-candidate", str(args[2]),
        "--v103-report", str(args[3]),
        "--source-archive", str(args[4]),
        "--output-dir", str(args[5]),
    ])
    assert result.exit_code == 0, f"{result.stdout}\n{result.exception}"
    assert "V10.4: candidata NON pubblicata" in result.stdout
    assert "Fatti preservati: 30" in result.stdout
    for name in TITLES:
        assert name in result.stdout
    assert (args[5] / "v10.4-report.json").is_file()


def test_audit_regression_is_fail_closed(tmp_path, monkeypatch):
    args = _inputs(tmp_path, monkeypatch)
    sequence = iter([
        SimpleNamespace(status="WARN"),
        SimpleNamespace(status="FAIL"),
    ])
    monkeypatch.setattr(v104, "audit_fast_essential", lambda *_: next(sequence))
    with pytest.raises(ValueError, match="Nuovo FAIL"):
        v104.run_v104(*args)
    assert not args[-1].exists()


def test_modified_input_hash_blocks_before_output(tmp_path, monkeypatch):
    args = _inputs(tmp_path, monkeypatch)
    args[2].write_bytes(args[2].read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="Hash"):
        v104.run_v104(*args)
    assert not args[-1].exists()
