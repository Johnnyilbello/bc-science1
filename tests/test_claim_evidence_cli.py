"""Claim inspection must use original cached evidence, not LLM paraphrases."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from bc_science import cli
from bc_science.courses import inspect_original_evidence


def test_inspect_claim_validates_query_before_opening_files():
    fake_scan = SimpleNamespace(name="ANATOMIA")
    with pytest.raises(ValueError, match="1-500"):
        inspect_original_evidence(
            fake_scan, "Midollo spinale", edition="complete", claim="",
        )
    with pytest.raises(ValueError, match="1-500"):
        inspect_original_evidence(
            fake_scan, "Midollo spinale", edition="complete", claim="A" * 501,
        )


def test_cli_claim_uses_original_evidence_without_model(monkeypatch, tmp_path):
    root = tmp_path / "materie"
    root.mkdir()
    fake_scan = SimpleNamespace(name="ANATOMIA")
    calls = []

    monkeypatch.setattr(cli, "scan_courses", lambda path: [fake_scan])

    def fake_evidence(scan, chapter, *, edition, claim):
        calls.append((scan.name, chapter, edition, claim))
        return {
            "claim": claim,
            "status": "PROVE_ORIGINALI_RECUPERATE_DA_VERIFICARE",
            "evidence": [
                {
                    "source_path": "/local/dispensa1.pdf",
                    "page": 4,
                    "block": None,
                    "excerpt": "Il midollo spinale emette 31 paia di nervi.",
                }
            ],
        }

    monkeypatch.setattr(cli, "inspect_original_evidence", fake_evidence)
    result = CliRunner().invoke(cli.app, [
        "courses", "inspect-claim", str(root),
        "--subject", "ANATOMIA",
        "--chapter", "Midollo spinale",
        "--claim", "31 paia di nervi spinali",
    ])
    assert result.exit_code == 0, result.output
    assert "dispensa1.pdf" in result.output
    assert "31 paia" in result.output
    assert calls == [
        ("ANATOMIA", "Midollo spinale", "complete", "31 paia di nervi spinali")
    ]


def test_cli_claim_unknown_subject_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(
        cli, "scan_courses",
        lambda path: [SimpleNamespace(name="ANATOMIA")],
    )
    result = CliRunner().invoke(cli.app, [
        "courses", "inspect-claim", str(tmp_path),
        "--subject", "IGNOTA",
        "--chapter", "Midollo spinale",
        "--claim", "31 paia",
    ])
    assert result.exit_code == 2
    assert "Materia non trovata" in result.output
