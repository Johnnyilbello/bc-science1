"""Dual local + Nemotron repair is fail-closed and never edits originals."""
from __future__ import annotations

import json

import pytest

from bc_science.dual_agent_autofix import (
    NemotronNvidia,
    _cloud_evidence,
    _normalise_judgment,
    run_dual_repair,
    save_dual_repair,
)
from bc_science.evidence_engine import OriginalEvidence


def _document(value: str) -> str:
    return "# Materia\n\n## Geografia\n\n" + value + "\n\n## Altri\n\nNessuna modifica.\n"


def _original(value: str) -> OriginalEvidence:
    return OriginalEvidence(
        source_path="original.pdf", page=2, block=None,
        excerpt=value, rank=1, provenance="cached_page",
    )


def _response(label: str, excerpt: str, replacement: str = "") -> str:
    return json.dumps({
        "verdict": label, "reason": "Il documento sostiene una conclusione precisa",
        "replacement": replacement,
        "quotes": [{"source_id": 1, "quote": excerpt}],
    })


def test_dual_agent_stages_exact_patch_and_preserves_sources(tmp_path):
    wrong = "Il treno va da Genova a Genova."
    corrected = "Il treno va da Genova a Livorno."
    doc = _document(wrong)
    excerpt = "Il treno va da Genova a Livorno."
    evidence = [_original("Nel documento originale: " + excerpt)]
    answers = iter([
        _response("SUSPECT", excerpt),
        _response("SUPPORTED", excerpt),
    ])
    received = []

    def cloud(messages):
        received.append(messages)
        return next(answers)

    result = run_dual_repair(
        doc, "Geografia", max_claims=1, max_rounds=2,
        find_evidence=lambda _claim: evidence,
        local_chat=lambda _messages: _response("SUSPECT", excerpt, corrected),
        cloud_chat=cloud,
    )
    assert result["results"][0]["status"] == "DRAFT_REVIEW"
    assert len(received) == 2
    assert "original.pdf" not in str(received)
    assert result["published_modified"] is False
    out = tmp_path / "review"
    save_dual_repair(
        result, doc, out,
        source_root=tmp_path / "private-materials",
        published_root=tmp_path / "live-output",
    )
    assert doc == _document(wrong)
    assert (out / "report.json").is_file()
    saved = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert "staged_drafts" not in saved
    assert corrected in (out / "proposta-01.md").read_text(encoding="utf-8")


def test_missing_source_proof_and_hallucinated_source_block_proposal():
    excerpt = "La profondità del lago è di circa 120 metri."
    evidence = [_original(excerpt)]
    claim = "Il lago misura 130 metri di profondità."
    assert not _normalise_judgment(
        json.dumps({
            "verdict": "SUSPECT", "reason": "Motivazione",
            "replacement": "Il lago è profondo 120 metri.", "quotes": [],
        }), evidence,
    )["valid"]
    assert not _normalise_judgment(
        _response("SUSPECT", "Il lago è profondo 130 metri."),
        evidence,
    )["valid"]
    result = run_dual_repair(
        _document(claim), "Geografia", max_claims=1,
        find_evidence=lambda _c: evidence,
        local_chat=lambda _m: _response(
            "SUSPECT", "Il lago è profondo 130 metri.",
            "Il lago è profondo 120 metri.",
        ),
        cloud_chat=lambda _m: _response("SUSPECT", excerpt),
    )
    assert result["results"][0]["status"] == "UNVERIFIED"
    assert result["staged_drafts"] == []


def test_disagreement_and_provider_stop_fail_closed():
    doc = _document("La città conta 30.000 abitanti.")
    excerpt = "La città conta 30.000 abitanti."
    ev = [_original(excerpt)]
    disagreement = run_dual_repair(
        doc, "Geografia", max_claims=1,
        find_evidence=lambda _c: ev,
        local_chat=lambda _m: _response("SUSPECT", excerpt, "La città conta 35.000 abitanti."),
        cloud_chat=lambda _m: _response("SUPPORTED", excerpt),
    )
    assert disagreement["results"][0]["status"] == "DISAGREEMENT"
    assert not disagreement["staged_drafts"]

    calls = []

    def offline(_messages):
        calls.append(1)
        raise RuntimeError("cloud offline")

    stopped = run_dual_repair(
        doc, "Geografia", max_claims=1,
        find_evidence=lambda _c: ev,
        local_chat=lambda _m: _response("SUSPECT", excerpt),
        cloud_chat=offline,
    )
    assert calls == [1]
    assert stopped["results"][0]["status"] == "CLOUD_STOPPED"
    assert not stopped["staged_drafts"]


def test_redaction_and_request_cap():
    source = _original(
        "L'email segreta è test@example.com. "
        "Contattare +39 333 123 4567 prima dell'esame.",
    )
    text = str(_cloud_evidence([source]))
    assert "test@example.com" not in text
    assert "333 123 4567" not in text
    with pytest.raises(ValueError, match="NVIDIA_API_KEY"):
        NemotronNvidia("", max_calls=1)
    with pytest.raises(ValueError, match="Limite"):
        NemotronNvidia("nvapi-test", max_calls=31)


def test_destination_in_live_and_existing_dir_refused(tmp_path):
    doc = _document("Una prova dura 40 minuti.")
    report = {
        "source_sha256": __import__("hashlib").sha256(
            doc.encode("utf-8")
        ).hexdigest(),
        "results": [],
        "staged_drafts": [],
        "claims_processed": 0,
    }
    private = tmp_path / "original"
    live = tmp_path / "live"
    with pytest.raises(ValueError, match="non isolata"):
        save_dual_repair(
            report, doc, live / "nested",
            source_root=private, published_root=live,
        )
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(ValueError, match="già esistente"):
        save_dual_repair(
            report, doc, existing,
            source_root=private, published_root=live,
        )
