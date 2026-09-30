from bc_science.summarizer import (
    _chapter_quality_issues,
    _chapter_title_supported,
    _dedupe_exact_blocks,
    _extract_exam_recap,
    _extract_summary_chapters,
)


def test_dedupe_exact_blocks_keeps_first_occurrence():
    text = """## Capitolo

Blocco unico.

### Da ricordare
- Punto A

### Da ricordare
- Punto A

Blocco finale.
"""
    cleaned = _dedupe_exact_blocks(text)
    assert cleaned.count("### Da ricordare") == 1
    assert cleaned.count("- Punto A") == 1
    assert "Blocco unico." in cleaned
    assert "Blocco finale." in cleaned


def test_extract_summary_chapters_uses_deterministic_index():
    text = """# Corso

## Indice degli argomenti
- Primo
- Secondo

---

## Primo

Testo primo.

---

## Secondo

Testo secondo.
"""
    chapters = _extract_summary_chapters(text)
    assert [title for title, _ in chapters] == ["Primo", "Secondo"]
    assert "Testo primo." in chapters[0][1]
    assert "Testo secondo." in chapters[1][1]


def test_quality_gate_rejects_duplicate_heading_and_truncated_end():
    chapter = """## Tatto

Testo completo.

## Tatto
### Da ricordare per l'esame
Il testo termina con tatto/
"""
    issues = _chapter_quality_issues(chapter, "Tatto")
    assert any("titolo H2" in item for item in issues)
    assert any("frase incompleta" in item for item in issues)


def test_quality_gate_accepts_well_formed_chapter():
    chapter = """## Tatto

Spiegazione semplice e completa.

### Da ricordare per l'esame
- Punto importante.
"""
    assert _chapter_quality_issues(chapter, "Tatto") == []


def test_title_support_ignores_heading_itself():
    mismatch = """## Olfatto

Il controllo motorio usa feedback e feed-forward.

### Da ricordare per l'esame
- I movimenti possono essere volontari.
"""
    assert not _chapter_title_supported("Olfatto", mismatch)

    supported = """## Olfatto

I recettori olfattivi si trovano nella cavita nasale.

### Da ricordare per l'esame
- L'olfatto usa recettori specifici.
"""
    assert _chapter_title_supported("Olfatto", supported)


def test_extract_exam_recap_reads_final_exam_section():
    chapter = """## Membrana

Testo.

### Da ricordare per l'esame
- Il trasporto passivo segue il gradiente.
- Il trasporto attivo richiede energia.
"""
    recap = _extract_exam_recap(chapter)
    assert "trasporto passivo" in recap
    assert "trasporto attivo" in recap
