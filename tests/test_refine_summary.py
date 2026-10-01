from bc_science.summarizer import (
    _chapter_quality_issues,
    _chapter_title_supported,
    _dedupe_exact_blocks,
    _build_global_recap,
    _extract_exam_points,
    _extract_exam_recap,
    _extract_summary_chapters,
    _normalize_refined_structure,
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

""" + ("Spiegazione semplice e completa del sistema somatosensoriale. " * 10) + """
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


def test_structural_normalizer_collapses_duplicate_exam_headings():
    chapter = """## LA CORTECCIA MOTORIA

Testo del capitolo abbastanza lungo da rappresentare contenuto reale.

### Da ricordare per l'esame
- Primo gruppo di punti.

### Area motoria
Contenuto intermedio.

### Da ricordare per l’esame
- Secondo gruppo di punti.
"""
    normalized, changed = _normalize_refined_structure(
        chapter,
        "LA CORTECCIA MOTORIA",
    )
    assert changed
    assert normalized.count("### Da ricordare per l'esame") == 1
    assert "### Punti chiave" in normalized
    assert "- Primo gruppo di punti." in normalized
    assert "- Secondo gruppo di punti." in normalized


def test_structural_normalizer_removes_duplicate_title_h2():
    chapter = """## il tatto

Testo iniziale.

## Il tatto
### Da ricordare per l'esame
- Punto finale.
"""
    normalized, changed = _normalize_refined_structure(chapter, "il tatto")
    assert changed
    assert len([line for line in normalized.splitlines() if line.startswith("## ")]) == 1
    assert "### Da ricordare per l'esame" in normalized


def test_structural_normalizer_merges_duplicate_h3_and_moves_recap_last():
    chapter = """## LA CORTECCIA MOTORIA

Introduzione.

### Definizioni e termini
- Prima definizione.

### Sequenze / meccanismi
- Prima sequenza.

### Da ricordare per l'esame
- Primo recap.

### Definizioni e termini
- Seconda definizione.

### Sequenze / meccanismi
- Seconda sequenza.

### Attenzione all'esame
- Nota finale.
"""
    normalized, changed = _normalize_refined_structure(
        chapter,
        "LA CORTECCIA MOTORIA",
    )
    assert changed
    assert normalized.count("### Definizioni e termini") == 1
    assert normalized.count("### Sequenze / meccanismi") == 1
    assert normalized.count("### Da ricordare per l'esame") == 1
    assert "- Prima definizione." in normalized
    assert "- Seconda definizione." in normalized
    assert "- Prima sequenza." in normalized
    assert "- Seconda sequenza." in normalized
    assert normalized.rfind("### Da ricordare per l'esame") > normalized.rfind("### Attenzione all'esame")


def test_extract_exam_points_reads_bullets():
    chapter = """## Membrana

Testo.

### Da ricordare per l'esame
- Il trasporto passivo segue il gradiente.
- Il trasporto attivo richiede energia.
"""
    assert _extract_exam_points(chapter) == [
        "Il trasporto passivo segue il gradiente.",
        "Il trasporto attivo richiede energia.",
    ]


def test_global_recap_has_one_item_per_chapter():
    chapters = [
        (
            "Membrana",
            """## Membrana

Testo.

### Da ricordare per l'esame
- Il trasporto passivo segue il gradiente.
- Il trasporto attivo richiede energia.
""",
        ),
        (
            "Muscolo",
            """## Muscolo

Testo.

### Da ricordare per l'esame
- Il calcio lega la troponina.
""",
        ),
    ]
    recap = _build_global_recap(chapters)
    assert recap.startswith("## Ripasso globale")
    assert recap.count("\n1. **Membrana**:") == 1
    assert recap.count("\n2. **Muscolo**:") == 1
    assert "Il trasporto passivo segue il gradiente." in recap
    assert "Il calcio lega la troponina." in recap
