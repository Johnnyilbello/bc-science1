"""Regression tests for actionable findings, not-applicable agents and audit gates."""
from __future__ import annotations

from pathlib import Path

from bc_science.agent_checks import execute_check, make_context
from bc_science.agent_mesh import _aggregate
from bc_science.courses import scan_courses


def _context(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    root = tmp_path / "courses"
    anatomy = root / "ANATOMIA"
    anatomy.mkdir(parents=True)
    (anatomy / "lezione-01.txt").write_text("Il cuore ha quattro camere.", encoding="utf-8")
    (anatomy / "lezione-02.txt").write_text("Il cuore ha quattro camere.", encoding="utf-8")
    scan = scan_courses(root)[0]
    return make_context(scan)


def test_aggregate_handles_optional_and_subject_incomplete():
    assert _aggregate(["PASS", "N/A"]) == "PASS"
    assert _aggregate(["N/A", "N/A"]) == "INCOMPLETE"
    assert _aggregate(["PASS", "SKIP"]) == "INCOMPLETE"
    assert _aggregate(["PASS", "WARN"]) == "INCOMPLETE"
    assert _aggregate(["PASS", "INCOMPLETE"]) == "INCOMPLETE"
    assert _aggregate(["PASS", "FAIL"]) == "FAIL"
    assert _aggregate(["PASS", "invalido"]) == "FAIL"


def test_duplicate_source_warning_lists_actual_files(tmp_path: Path, monkeypatch):
    context = _context(tmp_path, monkeypatch)
    status, evidence = execute_check(context, "SRC", 6)
    assert status == "WARN"
    assert "lezione-01.txt" in evidence
    assert "lezione-02.txt" in evidence
    assert "SHA256" in evidence


def test_numeric_omissions_are_actionable_and_fail_closed(tmp_path: Path, monkeypatch):
    context = _context(tmp_path, monkeypatch)
    context.audit = {
        "status": "FAIL",
        "audit_version": "not-important-here",
        "missing_numeric_facts": 2,
        "missing_exam_facts": 1,
        "missing_facts": [
            {
                "chapter": "Apparato digerente",
                "category": "numero",
                "text": "Il documento indica 25 unità.",
            },
            {
                "chapter": "Colonna vertebrale",
                "category": "esame",
                "text": "La sorgente elenca tre passaggi.",
            },
        ],
        "warnings": ["Due fatti numerici da controllare"],
    }
    for group, rule in (("STD", 9), ("AUD", 8)):
        status, evidence = execute_check(context, group, rule)
        assert status == "FAIL"
        assert "Fatti numerici non coperti: 2" in evidence
        assert "Apparato digerente" in evidence
        assert "Colonna vertebrale" in evidence

    status, evidence = execute_check(context, "AUD", 9)
    assert status == "FAIL"
    assert "Due fatti numerici da controllare" in evidence

    status, evidence = execute_check(context, "AUD", 2)
    assert status == "WARN"
    assert "obsoleta" in evidence


def test_optional_research_n_a_is_distinct_from_missing_enabled_research(
    tmp_path: Path, monkeypatch,
):
    context = _context(tmp_path, monkeypatch)
    for group, rule in (("RES", 0), ("RES", 9), ("SEC", 8)):
        status, evidence = execute_check(context, group, rule)
        assert status == "N/A"
        assert evidence

    context.research_enabled = True
    assert execute_check(context, "RES", 0)[0] == "FAIL"
    assert execute_check(context, "RES", 9)[0] == "SKIP"
    assert execute_check(context, "SEC", 8)[0] == "SKIP"


def test_warning_audit_has_reason_and_invalid_status_fails(
    tmp_path: Path, monkeypatch,
):
    context = _context(tmp_path, monkeypatch)
    context.audit = {
        "status": "WARN",
        "warnings": [
            "Copertura fatti 96.0%: sotto il target del 97%.",
            "Ridondanza globale stimata elevata",
        ],
    }
    status, evidence = execute_check(context, "AUD", 9)
    assert status == "WARN"
    assert "Copertura fatti 96.0%" in evidence
    context.audit["status"] = "unexpected"
    assert execute_check(context, "AUD", 9)[0] == "FAIL"
