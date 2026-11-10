"""Regression checks for bounded, non-destructive scientific red flags."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from bc_science import cli, courses
from bc_science.scientific_claim_flags import inspect_scientific_claims


def _document(body: str) -> str:
    return (
        "# Anatomia\n\n## Indice degli argomenti\n"
        "- Argomento\n\n---\n\n## Argomento\n\n" + body
    )


def test_palmitate_mistaken_formula_flagged_without_changing_text():
    original = _document(
        "### Da ricordare per l'esame\n"
        "* La sintesi del palmitato richiede 7 molecole di acetil-CoA, "
        "14 di malonil-CoA, 14 di NADPH + H+ e 14 di ATP."
    )
    flags = inspect_scientific_claims(original)
    assert len(flags) == 1
    assert flags[0].rule == "PALMITATE_STOICHIOMETRY"
    assert "acetilcoa=7" in flags[0].issue
    assert "malonilcoa=14" in flags[0].issue
    assert "atp=14" in flags[0].issue
    assert "7 molecole" in flags[0].excerpt
    assert flags[0].reference.startswith("https://")
    assert flags[0].validation == "REVISIONE_UMANA_NECESSARIA"


def test_correct_palmitate_two_valid_stages_are_not_flagged():
    for line in (
        "La sintesi del palmitato richiede 1 acetil-CoA, "
        "7 malonil-CoA e 14 NADPH.",
        "La sintesi del palmitato richiede 8 acetil-CoA, "
        "7 ATP e 14 NADPH, producendo i 7 malonil-CoA intermedi.",
    ):
        assert inspect_scientific_claims(_document(line)) == ()


def test_known_anatomy_and_spiro_errors_flagged():
    statements = (
        "Esofago: tubo muscolare che trasporta il cibo "
        "dallo stomaco allo stomaco.\n\n"
        "Dotti epatici: canali che portano la bile "
        "verso l'esterno del corpo.\n\n"
        "Il volume corrente (VT) è circa 500 mL. "
        "Questo volume non può essere misurato dallo spirometro."
    )
    issues = {flag.rule for flag in inspect_scientific_claims(_document(statements))}
    assert issues == {
        "ESOPHAGUS_DIRECTION",
        "BILIARY_DIRECTION",
        "TIDAL_VOLUME_SPIROMETRY",
    }


def test_correct_technical_explanations_do_not_trigger():
    content = _document(
        "L'esofago trasporta il cibo dalla faringe allo stomaco. "
        "I dotti epatici portano la bile verso il coledoco e il duodeno.\n\n"
        "Il volume corrente si misura direttamente con lo spirometro. "
        "Il volume residuo non può essere misurato con la spirometria."
    )
    assert inspect_scientific_claims(content) == ()


def test_cli_readonly_json_output_and_existing_file_guard(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setenv("BC_SCIENCE_HOME", str(home))
    subject = tmp_path / "materie" / "BIOCHIMICA"
    subject.mkdir(parents=True)
    complete = home / "outputs" / "courses" / "BIOCHIMICA" / "riassunto-unico.md"
    complete.parent.mkdir(parents=True)
    original = _document(
        "La sintesi del palmitato richiede 7 molecole di acetil-CoA, "
        "14 di malonil-CoA, 14 di NADPH e 14 di ATP."
    )
    complete.write_text(original, encoding="utf-8")
    study = complete.with_name("riassunto-studio.md")
    study.write_text(original, encoding="utf-8")
    scan = SimpleNamespace(name="BIOCHIMICA")
    monkeypatch.setattr(cli, "scan_courses", lambda path: [scan])
    monkeypatch.setattr(courses, "course_output_path", lambda _: complete)
    monkeypatch.setattr(courses, "course_study_output_path", lambda _: study)
    target = tmp_path / "review" / "flags.json"

    args = [
        "courses", "inspect-scientific-flags", str(tmp_path / "materie"),
        "--output-json", str(target),
    ]
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    report = json.loads(target.read_text(encoding="ascii"))
    assert report["schema"] == "bc-science-known-scientific-flags-v1"
    assert report["scanned_files"] == 2
    assert [f["edition"] for f in report["flags"]] == ["Completa", "Essenziale"]
    assert all(f["rule"] == "PALMITATE_STOICHIOMETRY" for f in report["flags"])
    assert complete.read_text(encoding="utf-8") == original
    assert study.read_text(encoding="utf-8") == original

    duplicate = CliRunner().invoke(cli.app, args)
    assert duplicate.exit_code == 2
    assert json.loads(target.read_text(encoding="ascii")) == report
