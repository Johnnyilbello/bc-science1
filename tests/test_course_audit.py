from pathlib import Path

from bc_science.course_audit import (
    audit_report_is_current,
    audit_study_pair,
    extract_atomic_facts,
    write_audit_reports,
)


def _document(body: str, title: str = "Energia") -> str:
    return f"""# Corso

## Mappa della materia
- {title}.

## Ripasso globale
1. **{title}**: punto principale.

## Indice degli argomenti
- {title}

---

## {title}
### In parole semplici
Introduzione semplice all'argomento.
{body}
"""


def test_atomic_fact_audit_passes_when_exam_numbers_survive():
    complete = _document(
        """### Spiegazione ordinata
L'ATP ha un valore di riferimento di 30 kJ/mol.
La fosfocreatina rigenera ATP durante il lavoro muscolare.
### Da ricordare per l'esame
- L'ATP ha un valore di riferimento di 30 kJ/mol.
- La fosfocreatina rigenera ATP durante il lavoro muscolare.
"""
    )
    study = _document(
        """### Concetti chiave
- ATP — composto trattato nel materiale.
### Spiegazione ordinata
Durante il lavoro muscolare la fosfocreatina rigenera ATP.
Il valore di riferimento dell'ATP è 30 kJ/mol.
### Da ricordare per l'esame
- ATP: valore di riferimento 30 kJ/mol.
- La fosfocreatina rigenera ATP nel lavoro muscolare.
"""
    )

    result = audit_study_pair(
        complete,
        study,
        source_documents=4,
        source_coverage_complete=True,
    )

    assert result.status in {"PASS", "WARN"}
    assert result.missing_numeric_facts == 0
    assert result.weighted_fact_coverage >= 0.90
    assert result.source_documents == 4


def test_atomic_fact_audit_fails_when_numeric_fact_disappears():
    complete = _document(
        """### Spiegazione ordinata
La concentrazione riportata è 30 mM.
### Da ricordare per l'esame
- La concentrazione riportata è 30 mM.
"""
    )
    study = _document(
        """### Concetti chiave
- Concentrazione — valore trattato nelle dispense.
### Spiegazione ordinata
La concentrazione è importante per l'argomento.
### Da ricordare per l'esame
- Ricordare il concetto di concentrazione.
"""
    )

    result = audit_study_pair(
        complete,
        study,
        source_documents=1,
        source_coverage_complete=True,
    )

    assert result.status == "FAIL"
    assert result.missing_numeric_facts >= 1
    assert result.weighted_fact_coverage < 1.0


def test_atomic_fact_extraction_reads_progressive_explanation():
    complete = _document(
        """### Meccanismo
Il segnale raggiunge la membrana e attiva il processo cellulare.
Successivamente il secondo passaggio modifica la risposta.
### Da ricordare per l'esame
- Il segnale attiva il processo cellulare.
"""
    )

    facts = extract_atomic_facts(complete)
    texts = [fact.text for fact in facts]

    assert any("raggiunge la membrana" in text for text in texts)
    assert any("secondo passaggio" in text for text in texts)


def test_audit_reports_track_input_hashes(tmp_path: Path):
    complete = _document(
        """### Spiegazione ordinata
L'acqua partecipa al processo.
### Da ricordare per l'esame
- L'acqua partecipa al processo.
"""
    )
    study = _document(
        """### Concetti chiave
- Acqua — partecipa al processo.
### Spiegazione ordinata
L'acqua partecipa al processo.
### Da ricordare per l'esame
- L'acqua partecipa al processo.
"""
    )
    result = audit_study_pair(complete, study)

    _md, json_path = write_audit_reports(result, tmp_path)

    current, status = audit_report_is_current(json_path, complete, study)
    stale, stale_status = audit_report_is_current(
        json_path,
        complete + "\nModifica.",
        study,
    )

    assert current is True
    assert status == result.status
    assert stale is False
    assert stale_status is None
