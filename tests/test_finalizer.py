from pathlib import Path

import pymupdf

from bc_science.finalizer import finalize_file, finalize_markdown


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
14. **olfatto**: controllo motorio e feedback.

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
    assert 'gusto/tatto/udito/vista + controllo nervoso del movimento (file "olfatto")' in result
    assert f"## {label}" in result
    assert fixes >= 5

    note_pos = result.index("Nota scientifica aggiornata")
    index_pos = result.index("## Indice degli argomenti")
    chapter_pos = result.index("## neuroni")
    assert notes == 1
    assert note_pos > index_pos
    assert note_pos > chapter_pos


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
