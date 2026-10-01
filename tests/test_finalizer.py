from pathlib import Path

import fitz

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

## Come studiare
1. Leggi.

## Capitolo
Testo semplice.

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

    with fitz.open(result.pdf_path) as pdf:
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
