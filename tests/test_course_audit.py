from pathlib import Path

from bc_science.course_audit import (
    audit_report_is_current,
    audit_study_pair,
    extract_atomic_facts,
    restore_missing_audit_facts,
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

def test_restore_missing_audit_facts_makes_numeric_failure_recoverable():
    complete = _document(
        """### Spiegazione ordinata
La concentrazione riportata è 30 mM.
La durata riportata è 45 secondi.
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

    before = audit_study_pair(
        complete,
        study,
        source_documents=1,
        source_coverage_complete=True,
    )
    assert before.status == "FAIL"
    assert before.missing_numeric_facts >= 1

    repaired, restored = restore_missing_audit_facts(
        study,
        before.missing_facts,
    )
    after = audit_study_pair(
        complete,
        repaired,
        source_documents=1,
        source_coverage_complete=True,
    )

    assert restored >= 1
    assert "### Dettagli recuperati dall'audit" in repaired
    assert "30 mM" in repaired
    assert "45 secondi" in repaired
    assert after.missing_numeric_facts == 0
    assert after.weighted_fact_coverage >= before.weighted_fact_coverage
    assert after.status != "FAIL"


def test_restore_missing_audit_facts_keeps_exam_recap_last():
    complete = _document(
        """### Spiegazione ordinata
Il valore riportato è 70 bpm.
### Da ricordare per l'esame
- Il valore riportato è 70 bpm.
"""
    )
    study = _document(
        """### Spiegazione ordinata
Il valore viene discusso.
### Da ricordare per l'esame
- Ricordare il valore.
"""
    )
    before = audit_study_pair(complete, study)
    repaired, restored = restore_missing_audit_facts(study, before.missing_facts)

    assert restored >= 1
    assert repaired.rfind("### Da ricordare per l'esame") > repaired.rfind(
        "### Dettagli recuperati dall'audit"
    )

def test_restore_missing_audit_fact_even_if_text_exists_only_in_non_atomic_intro():
    complete = _document(
        """### Spiegazione ordinata
La pressione sistolica riportata è 120 mmHg.
### Da ricordare per l'esame
- La pressione sistolica riportata è 120 mmHg.
"""
    )
    study = _document(
        """### La pressione sistolica riportata è 120 mmHg.
Il capitolo introduce il concetto senza riportare il valore in un fatto atomico.
### Da ricordare per l'esame
- Ricordare la pressione sistolica.
"""
    )
    before = audit_study_pair(complete, study)
    assert before.missing_numeric_facts >= 1

    repaired, restored = restore_missing_audit_facts(study, before.missing_facts)
    after = audit_study_pair(complete, repaired)

    assert restored >= 1
    assert after.missing_numeric_facts == 0
    assert after.weighted_fact_coverage > before.weighted_fact_coverage


def test_restore_missing_audit_fact_maps_numbered_merged_chapter_title():
    complete = """# Corso

## Indice degli argomenti
- Sistema linfatico e linfociti B e T 2

---

## Sistema linfatico e linfociti B e T 2
### Spiegazione ordinata
Il valore riportato è 42 unità.
### Da ricordare per l'esame
- Il valore riportato è 42 unità.
"""
    study = """# Corso studio

## Indice degli argomenti
- Sistema linfatico e linfociti B e T

---

## Sistema linfatico e linfociti B e T
### Spiegazione ordinata
Il valore non è riportato.
### Da ricordare per l'esame
- Ricordare il sistema linfatico.
"""
    before = audit_study_pair(complete, study)
    repaired, restored = restore_missing_audit_facts(study, before.missing_facts)
    after = audit_study_pair(complete, repaired)

    assert restored >= 1
    assert "42 unità" in repaired
    assert after.missing_numeric_facts == 0

