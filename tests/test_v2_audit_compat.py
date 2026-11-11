"""V2 audit compatibility with existing source-grounded course reports."""
from __future__ import annotations

from bc_science.agent_checks import _numeric_evidence
from bc_science.course_audit import (
    AUDIT_VERSION,
    FAST_ESSENTIAL_AUDIT_VERSION,
    audit_report_is_current,
    audit_study_pair,
    extract_atomic_facts,
    write_audit_reports,
)


def _simple_doc(body: str) -> str:
    return (
        "# Anatomia\n\n## Indice degli argomenti\n"
        "- Apparato locomotore\n\n---\n\n"
        "## Apparato locomotore\n"
        "### Da ricordare per l'esame\n"
        + body + "\n"
    )


def test_v2_qualifier_is_a_separate_atomic_fact_category():
    source = _simple_doc("- Questo studio potrebbe indicare una associazione tra i fattori.")
    facts = extract_atomic_facts(source)
    assert facts
    assert any(item.category == "qualificatore" for item in facts)
    assert AUDIT_VERSION == "atomic-fact-v2-source-qualifiers"


def test_v2_reports_with_matching_hashes_are_current(tmp_path):
    complete = _simple_doc("- Le ossa hanno proprietà e funzioni diverse.")
    study = _simple_doc("- Le ossa hanno proprietà e funzioni diverse.")
    result = audit_study_pair(complete, study)
    assert result.audit_version == AUDIT_VERSION
    _, saved = write_audit_reports(result, tmp_path)
    current, status = audit_report_is_current(
        saved, complete, study, expected_version=AUDIT_VERSION,
    )
    assert current is True
    assert status == result.status

    current, status = audit_report_is_current(
        saved, complete, study + " una frase cambiata",
        expected_version=AUDIT_VERSION,
    )
    assert current is False
    assert status is None

    current, _ = audit_report_is_current(
        saved, complete, study,
        expected_version=FAST_ESSENTIAL_AUDIT_VERSION,
    )
    assert current is False


def test_v2_long_uncompressed_edition_is_not_approved():
    sentences = [
        f"- La parte scheletrica numero {i} presenta una connessione anatomica distinta."
        for i in range(45)
    ]
    complete = _simple_doc("\n".join(sentences))
    report = audit_study_pair(complete, complete)
    assert report.complete_words >= 250
    assert report.compression_percent == 0.0
    assert report.status != "PASS"
    assert any("Compressione" in issue for issue in report.warnings)


def test_numeric_examples_are_separate_from_exam_examples():
    report = {
        "missing_numeric_facts": 3,
        "missing_exam_facts": 309,
        "missing_facts": [
            {"category": "esame", "chapter": "Ossa", "text": "Le ossa hanno una funzione."},
            {"category": "numero", "chapter": "Vertebre", "text": "La misura è di 12 cm."},
        ],
    }
    message = _numeric_evidence(report)
    assert "Esempi NUMERICI: Vertebre" in message
    assert "Esempi D'ESAME: Ossa" in message
    assert message.index("Esempi NUMERICI") < message.index("Esempi D'ESAME")
