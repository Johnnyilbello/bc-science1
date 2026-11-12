"""A model failure must not discard verified progress or fabricate outcomes."""
from __future__ import annotations

import json

import pytest

from bc_science.audit_checkpoint import AuditCheckpoint
from bc_science.autonomous_claim_audit import run_claim_audit
from bc_science.evidence_engine import OriginalEvidence
from bc_science.ollama_client import OllamaError


def _document():
    return (
        "# Materia\n\n## Indice degli argomenti\n- Storia\n\n---\n"
        "## Storia\n\n"
        "La spedizione raggiunse la costa nel 1500. "
        "La seconda spedizione raggiunse la costa nel 1600. "
        "La terza spedizione raggiunse la costa nel 1700."
    )


def _source(claim: str):
    return [OriginalEvidence(
        source_path="storia.txt", page=1, block=None,
        excerpt=claim, rank=1, provenance="cached_page",
    )]


def _metadata():
    return {
        "max_claims": 3, "model": "qwen3.5:4b",
        "document_sha256": "fixed-valid-digest", "subject": "Storia",
    }


def test_checkpoint_resume_only_recomputes_unfinished_claim(tmp_path):
    path = tmp_path / "run"
    checkpoint = AuditCheckpoint(path, _metadata())
    calls = []

    def failing_chat(_messages):
        calls.append(1)
        if len(calls) == 3:
            raise OllamaError("local HTTP 500")
        return json.dumps({
            "label": "UNVERIFIED", "source_ids": [],
            "reason": "Mancano le prove necessarie.",
        })

    with pytest.raises(OllamaError, match="500"):
        run_claim_audit(
            _document(), "Storia", max_claims=3,
            find_evidence=_source, chat=failing_chat,
            evidence_mode="spans", on_verdict=checkpoint.store,
        )
    assert len(checkpoint.claims) == 2
    assert len(json.loads(checkpoint.path.read_text())["claims"]) == 2
    assert not (path / "report.json").exists()

    resumed = AuditCheckpoint(path, _metadata(), resume=True)
    continuation_calls = []

    def resumed_chat(_messages):
        continuation_calls.append(1)
        return json.dumps({
            "label": "UNVERIFIED", "source_ids": [],
            "reason": "Mancano le prove necessarie.",
        })

    report = run_claim_audit(
        _document(), "Storia", max_claims=3,
        find_evidence=_source, chat=resumed_chat,
        evidence_mode="spans", prior_claims=resumed.claims,
        on_verdict=resumed.store,
    )
    assert len(continuation_calls) == 1
    assert report["claims_total"] == 3
    assert len(report["claims"]) == 3
    assert len({item["claim"]["claim_sha256"] for item in report["claims"]}) == 3
    assert report["published_modified"] is False
    assert report["complete_scientific_validation"] is False

    (path / "report.json").write_text(json.dumps(report), encoding="utf-8")
    resumed.finish()
    assert not resumed.path.exists()
    with pytest.raises(ValueError, match="Checkpoint"):
        AuditCheckpoint(path, _metadata(), resume=True)


def test_checkpoint_rejects_different_source_or_model(tmp_path):
    path = tmp_path / "run"
    checkpoint = AuditCheckpoint(path, _metadata())
    before = checkpoint.path.read_bytes()
    with pytest.raises(ValueError, match="incompatibile"):
        AuditCheckpoint(path, {**_metadata(), "model": "other"}, resume=True)
    with pytest.raises(ValueError, match="incompatibile"):
        AuditCheckpoint(
            path, {**_metadata(), "document_sha256": "changed"}, resume=True,
        )
    assert checkpoint.path.read_bytes() == before


def test_checkpoint_cannot_create_over_existing_directory(tmp_path):
    path = tmp_path / "run"
    path.mkdir()
    with pytest.raises(FileExistsError):
        AuditCheckpoint(path, _metadata())


def test_resume_rejects_modified_claim_order_before_running_chat(tmp_path):
    document = _document()
    checkpoint = AuditCheckpoint(tmp_path / "run", _metadata())
    checkpoint.store(1, 3, {
        "claim": {"text": "La frase è stata sostituita"},
        "status": "PROVISIONALLY_SUPPORTED",
    })
    restored = AuditCheckpoint(tmp_path / "run", _metadata(), resume=True)
    calls = []
    with pytest.raises(ValueError, match="Checkpoint incoerente"):
        run_claim_audit(
            document, "Storia", max_claims=3,
            find_evidence=_source,
            chat=lambda _messages: calls.append(1) or "{}",
            evidence_mode="spans", prior_claims=restored.claims,
            on_verdict=restored.store,
        )
    assert calls == []
