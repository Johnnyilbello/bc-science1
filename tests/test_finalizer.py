import hashlib
import json
from pathlib import Path

import pymupdf

from bc_science.finalizer import (
    discover_passed_course_studies,
    finalize_course_bundle,
    finalize_file,
    finalize_markdown,
)


def test_finalize_markdown_preserves_source_and_adds_separate_notes():
    source = """# Corso

## GUSTO

La trasduzione olfattiva usa recettori specifici.

## olfatto

> Verifica materiale: il titolo non è supportato dal testo.

Il controllo nervoso dei movimenti usa feedback e feed-forward.

## Sistema cardiovascolare

Il sangue povero di ossigeno arriva al cuore tramite le arterie del corpo.

## Trasporto passivo e attivo

Entrambi i meccanismi passivi avvengono solo se esiste un gradiente di concentrazione ed è diretto verso la zona a maggiore concentrazione.

## Organizzazione organismo

Sistema endocrino (composto da ipotalamo, ipofisi, surrenali, tiroide, parotidi, timo e pancreas).

Gli organuli sono biomolecole specializzate.

## neuroni

L'uomo ha circa 100 milioni di neuroni. Rapporto: 1 neurone : 9 glie.

## Contrazione muscolare

L'accumulo di acido lattico nel tessuto muscolare è responsabile della sensazione di bruciore e dolore post-esercizio intenso.

## biomolecole

Esistono lipoproteine (proteine legate a lipidi), come l'emoglobina.
"""
    result, notes, fixes = finalize_markdown(source)

    assert "Il sangue povero di ossigeno arriva al cuore tramite le arterie del corpo." in result
    assert "Gli organuli sono biomolecole specializzate." in result
    assert notes == 7
    assert fixes == 1
    assert result.count("Nota scientifica aggiornata") == 7
    assert '## Controllo nervoso del movimento (materiale etichettato "olfatto")' in result
    assert "Nota organizzativa" in result


def test_finalize_file_creates_valid_pdf_docx_and_markdown(tmp_path: Path):
    source = tmp_path / "Materia-riassunto-rifinito.md"
    source.write_text(
        """# Materia - Riassunto rifinito

## Mappa della materia
- Capitolo.

## Ripasso globale
1. **Capitolo**: concetto di base e dettagli principali.

## Indice degli argomenti
- Capitolo

---

## Capitolo

### In parole semplici
Questo capitolo introduce un concetto di base.
La spiegazione parte dall'idea principale e poi aggiunge i dettagli.

### Parole chiave
- **Concetto** — idea centrale descritta nel capitolo.
- **Dettaglio** — informazione che completa l'idea principale.
- **Sequenza** — ordine con cui vengono presentati i passaggi.

### Spiegazione
Il testo usa frasi brevi e presenta una sola idea alla volta.
Le informazioni sono organizzate in modo progressivo.

### Da ricordare per l'esame
- Punto importante.
""",
        encoding="utf-8",
    )

    result = finalize_file(source, tmp_path / "final")

    assert result.markdown_path.exists()
    assert result.docx_path is not None and result.docx_path.exists()
    assert result.pdf_path is not None and result.pdf_path.exists()
    assert result.pdf_pages >= 1

    with pymupdf.open(result.pdf_path) as pdf:
        assert pdf.page_count == result.pdf_pages
        assert pdf.page_count >= 1


def test_finalize_markdown_is_idempotent_for_scientific_notes():
    source = """# Corso

## Trasporto

Il sangue povero di ossigeno arriva al cuore tramite le arterie del corpo.
"""
    first, notes_first, _ = finalize_markdown(source)
    second, notes_second, _ = finalize_markdown(first)

    assert notes_first == 1
    assert notes_second == 0
    assert second.count("Nota scientifica aggiornata") == 1


def test_finalize_updates_frontmatter_and_places_notes_in_full_chapters():
    source = """# FISIOLOGIA UMANA E DELLO SPORT - Riassunto rifinito
> Versione rifinita dell'ultimo riassunto BC Science; non sostituisce la verifica sui PDF originali.

## Mappa della materia
- Sistema nervoso: gusto/tatto/olfatto/udito/vista.

## Ripasso globale
14. **olfatto (titolo da verificare)**: controllo motorio e feedback.

## Indice degli argomenti
- GUSTO
- olfatto
- neuroni

---

## GUSTO
La trasduzione olfattiva usa recettori specifici.

---

## olfatto
> Verifica materiale: il titolo del capitolo non è supportato chiaramente dal testo sorgente.

Il controllo nervoso dei movimenti usa feedback e feed-forward.

---

## neuroni
L'uomo ha circa 100 milioni di neuroni. Rapporto: 1 neurone : 9 glie.
"""
    result, notes, fixes = finalize_markdown(source)

    assert result.startswith("# FISIOLOGIA UMANA E DELLO SPORT - Dispensa finale")
    assert "Dispensa finale generata da BC Science." in result
    label = 'Controllo nervoso del movimento (materiale etichettato "olfatto")'
    assert f"- {label}" in result
    assert f"**{label}**:" in result
    assert "**olfatto (titolo da verificare)**:" not in result
    assert 'gusto/tatto/udito/vista + controllo nervoso del movimento (file "olfatto")' in result
    assert f"## {label}" in result
    assert fixes >= 5

    note_pos = result.index("Nota scientifica aggiornata")
    index_pos = result.index("## Indice degli argomenti")
    chapter_pos = result.index("## neuroni")
    assert notes == 1
    assert note_pos > index_pos
    assert note_pos > chapter_pos


def test_finalize_applies_conservative_proofreading_and_sensory_title():
    source = """# Corso

## Ripasso globale
5. **GUSTO**: gusto e olfatto.

## Indice degli argomenti
- GUSTO

---

## GUSTO

### In parole semplici
L'olfatto permette di percepire gli odori.
Il gusto permette di percepire i sapori.
Le biomolecole sono le "mattoni" fondamentali.
In secondo luogo, questa legame attiva il segnale.
Il segnale passa attraverso una sinapsia.
Questa enzima aumenta il segnale.
La trasduzione converte l'energia fisica del stimolo.
I meccanocettori mediano tatto, propriocezione e equilibrio.

### Da ricordare per l'esame
- Gusto e olfatto sono sistemi sensoriali.
"""
    result, notes, fixes = finalize_markdown(source)

    assert notes == 0
    assert fixes == 9
    assert "## GUSTO E OLFATTO" in result
    assert "- GUSTO E OLFATTO" in result
    assert "**GUSTO E OLFATTO**:" in result
    assert 'Le biomolecole sono i "mattoni" fondamentali.' in result
    assert "questo legame attiva il segnale" in result
    assert "attraverso una sinapsi" in result
    assert "Questo enzima aumenta il segnale" in result
    assert "energia fisica dello stimolo" in result
    assert "propriocezione ed equilibrio" in result
    assert 'le "mattoni"' not in result
    assert "questa legame" not in result
    assert "sinapsia" not in result
    assert "Questa enzima" not in result


def test_finalize_proofreading_is_idempotent():
    source = """# Corso

## GUSTO
L'olfatto e il gusto sono trattati insieme.
Le biomolecole sono le "mattoni".
"""
    first, _notes_first, fixes_first = finalize_markdown(source)
    second, _notes_second, fixes_second = finalize_markdown(first)

    assert fixes_first > 0
    assert fixes_second == 0
    assert second == first


def test_finalize_file_rejects_non_novice_ready_summary(tmp_path: Path):
    source = tmp_path / "Materia-riassunto-rifinito.md"
    source.write_text(
        """# Materia - Riassunto rifinito

## Indice degli argomenti
- Capitolo

---

## Capitolo
Testo tecnico senza introduzione o parole chiave.

### Da ricordare per l'esame
- Punto.
""",
        encoding="utf-8",
    )

    try:
        finalize_file(source, tmp_path / "final")
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("finalize_file doveva bloccare un riassunto non novice-ready")

    assert "comprensibilita per principianti" in message
    assert "bc-science refine" in message

def _write_course_study(
    courses_dir: Path,
    subject: str,
    *,
    audit_status: str = "PASS",
) -> Path:
    subject_dir = courses_dir / subject
    subject_dir.mkdir(parents=True)
    study = subject_dir / "riassunto-studio.md"
    study.write_text(
        f"""# {subject} - Riassunto studio

## Mappa della materia
- Capitolo.

## Ripasso globale
1. **Capitolo**: concetto centrale.

## Indice degli argomenti
- Capitolo

---

## Capitolo

### In parole semplici
Questo capitolo presenta il concetto centrale con parole semplici.
La spiegazione procede gradualmente verso i dettagli.

### Parole chiave
- **Concetto** — idea principale.
- **Dettaglio** — informazione di supporto.
- **Esame** — punto da ricordare.

### Spiegazione ordinata
Il concetto viene spiegato in modo progressivo.
Ogni frase aggiunge un dettaglio utile senza cambiare argomento.

### Da ricordare per l'esame
- Ricordare il concetto centrale.
""",
        encoding="utf-8",
    )
    normalized_text = study.read_text(encoding="utf-8")
    digest = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
    (subject_dir / "audit.json").write_text(
        json.dumps(
            {
                "status": audit_status,
                "study_sha256": digest,
                "source_coverage_complete": True,
            }
        ),
        encoding="utf-8",
    )
    return study


def test_finalize_course_bundle_creates_one_output_from_all_pass_subjects(tmp_path: Path):
    courses_dir = tmp_path / "outputs" / "courses"
    _write_course_study(courses_dir, "ANATOMIA")
    _write_course_study(courses_dir, "FISIOLOGIA UMANA E DELLO SPORT")
    _write_course_study(courses_dir, "FONDAMENTI DI BIOLOGIA E CHIMICA")

    result = finalize_course_bundle(courses_dir, tmp_path / "outputs" / "final")
    markdown = result.markdown_path.read_text(encoding="utf-8")

    assert "Parte 1 — ANATOMIA" in markdown
    assert "Parte 2 — FISIOLOGIA UMANA E DELLO SPORT" in markdown
    assert "Parte 3 — FONDAMENTI DI BIOLOGIA E CHIMICA" in markdown
    assert result.markdown_path.name == (
        "SCIENZE-MOTORIE-eCampus-2026-2027-dispensa-finale.md"
    )
    assert result.docx_path is not None and result.docx_path.exists()
    assert result.pdf_path is not None and result.pdf_path.exists()
    with pymupdf.open(result.pdf_path) as pdf:
        assert pdf.page_count == result.pdf_pages
        assert pdf.page_count >= 3


def test_discover_course_bundle_rejects_non_pass_subject_instead_of_omitting_it(
    tmp_path: Path,
):
    courses_dir = tmp_path / "outputs" / "courses"
    _write_course_study(courses_dir, "ANATOMIA")
    _write_course_study(courses_dir, "FISIOLOGIA", audit_status="WARN")

    try:
        discover_passed_course_studies(courses_dir)
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("Una materia WARN non deve essere omessa dal bundle finale.")

    assert "FISIOLOGIA: audit WARN" in message



def test_discover_course_bundle_accepts_windows_crlf_with_text_hash(tmp_path: Path):
    courses_dir = tmp_path / "outputs" / "courses"
    subject_dir = courses_dir / "ANATOMIA"
    subject_dir.mkdir(parents=True)
    study = subject_dir / "riassunto-studio.md"

    logical_text = """# ANATOMIA - Riassunto studio

## Mappa della materia
- Capitolo.

## Ripasso globale
1. **Capitolo**: concetto centrale.

## Indice degli argomenti
- Capitolo

---

## Capitolo

### In parole semplici
Testo semplice.

### Da ricordare per l'esame
- Punto chiave.
"""
    # Simulate Windows physical CRLF storage while audit hashes normalized text.
    study.write_bytes(logical_text.replace("\n", "\r\n").encode("utf-8"))
    normalized_hash = hashlib.sha256(logical_text.encode("utf-8")).hexdigest()
    (subject_dir / "audit.json").write_text(
        json.dumps(
            {
                "status": "PASS",
                "study_sha256": normalized_hash,
                "source_coverage_complete": True,
            }
        ),
        encoding="utf-8",
    )

    discovered = discover_passed_course_studies(courses_dir)

    assert discovered == [("ANATOMIA", study)]


def test_discover_course_bundle_rejects_stale_audit(tmp_path: Path):
    courses_dir = tmp_path / "outputs" / "courses"
    study = _write_course_study(courses_dir, "ANATOMIA")
    study.write_text(study.read_text(encoding="utf-8") + "\nModifica successiva.\n", encoding="utf-8")

    try:
        discover_passed_course_studies(courses_dir)
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("Un audit obsoleto non deve essere accettato.")

    assert "audit obsoleto" in message

