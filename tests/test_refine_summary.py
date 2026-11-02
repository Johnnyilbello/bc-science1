from bc_science.summarizer import (
    _build_global_recap,
    _chapter_quality_issues,
    _chapter_title_supported,
    _compact_recap_source_sentence,
    _dedupe_exact_blocks,
    _extract_exam_points,
    _extract_exam_recap,
    _extract_summary_chapters,
    _normalize_refined_structure,
    _remove_identical_study_sentences,
    _preserves_exam_numbers,
    _preserves_existing_exam_points,
    _restore_exam_recap_from_source,
    _restore_missing_exam_points,
    preview_compact_global_recap,
    preview_compact_study,
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

def test_restore_missing_exam_points_repairs_semantic_and_numeric_loss():
    source = """## Pressione arteriosa

### In parole semplici
La pressione arteriosa viene descritta nelle dispense.

### Da ricordare per l'esame
- La pressione sistolica riportata è 120 mmHg.
- La pressione diastolica riportata è 80 mmHg.
"""
    candidate = """## Pressione arteriosa

### In parole semplici
La pressione arteriosa viene descritta nelle dispense.

### Concetti chiave
- La pressione sistolica è un valore importante.

### Spiegazione ordinata
Il capitolo distingue pressione sistolica e diastolica.

### Da ricordare per l'esame
- Ricordare la distinzione tra pressione sistolica e diastolica.
"""

    assert not _preserves_existing_exam_points(source, candidate)
    assert not _preserves_exam_numbers(source, candidate)

    repaired, restored = _restore_missing_exam_points(source, candidate)

    assert restored == 2
    assert "- La pressione sistolica riportata è 120 mmHg." in repaired
    assert "- La pressione diastolica riportata è 80 mmHg." in repaired
    assert _preserves_existing_exam_points(source, repaired)
    assert _preserves_exam_numbers(source, repaired)


def test_restore_missing_exam_points_is_idempotent_when_candidate_is_safe():
    source = """## ATP

### Da ricordare per l'esame
- Il valore riportato è 30 kJ/mol.
"""
    candidate = """## ATP

### In parole semplici
L'ATP è trattato nelle dispense.

### Da ricordare per l'esame
- Il valore riportato è 30 kJ/mol.
"""

    repaired, restored = _restore_missing_exam_points(source, candidate)

    assert restored == 0
    assert repaired == candidate

def test_structural_normalizer_canonicalizes_malformed_exam_recap_variants():
    chapter = """## Contrazione muscolare

Testo introduttivo abbastanza lungo da rappresentare un capitolo reale.

#### **Da ricordare per l’esame:**
- Primo gruppo di punti.

### Spiegazione ordinata
Contenuto intermedio.

**Da ricordare per l'esame**:
- Secondo gruppo di punti.
"""
    normalized, changed = _normalize_refined_structure(
        chapter,
        "Contrazione muscolare",
    )

    assert changed
    assert normalized.count("### Da ricordare per l'esame") == 1
    assert "### Punti chiave" in normalized
    assert "- Primo gruppo di punti." in normalized
    assert "- Secondo gruppo di punti." in normalized
    assert normalized.rfind("### Da ricordare per l'esame") > normalized.rfind(
        "### Spiegazione ordinata"
    )


def test_restore_exam_recap_from_source_when_model_omits_it():
    source = """## Contrazione muscolare

### In parole semplici
La contrazione muscolare è descritta nelle dispense.

### Da ricordare per l'esame
- Il calcio partecipa al processo descritto.
- L'ATP compare nel meccanismo riportato.
"""
    candidate = """## Contrazione muscolare

### In parole semplici
La contrazione muscolare è descritta nelle dispense.

### Concetti chiave
- Calcio — elemento citato nel processo.

### Spiegazione ordinata
Il capitolo presenta il processo in ordine progressivo.
"""

    repaired, changed = _restore_exam_recap_from_source(
        candidate,
        source,
        "Contrazione muscolare",
    )

    assert changed
    assert repaired.count("### Da ricordare per l'esame") == 1
    assert "- Il calcio partecipa al processo descritto." in repaired
    assert "- L'ATP compare nel meccanismo riportato." in repaired
    issues = _chapter_quality_issues(repaired, "Contrazione muscolare")
    assert not any("Da ricordare per l'esame" in issue for issue in issues)




def test_large_global_recap_is_bounded_and_uses_verbatim_source_only():
    import re

    chapters = []
    for i in range(65):
        title = f"Capitolo {i + 1}"
        fact = (
            f"Il capitolo {i + 1} descrive un esempio specifico "
            "che serve a comprendere i principali processi cellulari."
        )
        chapters.append((
            title,
            f"## {title}\n\n### Da ricordare per l'esame\n- {fact}\n",
        ))

    recap = _build_global_recap(chapters)
    word_count = len(re.findall(r"\b[\wÀ-ÿ'+-]+\b", recap))
    assert 300 <= word_count <= 500
    assert "## Ripasso globale" in recap
    assert len(recap) < 5000
    lines = [line for line in recap.splitlines() if re.match(r"^\d+\. \*\*", line)]
    assert len(lines) >= 12
    for line in lines:
        title = line.split("**:", 1)[0].split("**")[1]
        factual_sentence = line.split("**: ", 1)[1]
        source = dict(chapters)[title]
        assert factual_sentence in source
    # This is a study navigation aid; every chapter remains in the original.
    assert len(chapters) == 65


def test_compact_recap_skips_long_claim_instead_of_truncating():
    raw = (
        "La membrana cellulare potrebbe svolgere un ruolo specifico "
        "in numerose reazioni fisiologiche e in molte condizioni diverse "
        "che richiedono verifiche approfondite " + "confrontando ogni misura " * 20
        + "nel materiale originale."
    )
    assert _compact_recap_source_sentence(
        "## Membrana\n\n### Da ricordare per l'esame\n- " + raw
    ) == ""


def test_existing_recap_preview_changes_only_frontmatter_and_never_original():
    import re

    chapters = []
    for i in range(65):
        title = f"Capitolo {i + 1}"
        chapters.append((title, (
            f"## {title}\n\nLa cellula usa un meccanismo "
            f"descritto nella dispensa del capitolo {i + 1}."
        )))
    index = "\n".join(f"- {title}" for title, _ in chapters)
    body = "\n\n---\n\n".join(block for _title, block in chapters)
    original = (
        "# Essenziale\n\n## Ripasso globale\n"
        "Un ripasso tradizionale ripete tutte le informazioni senza selezione.\n\n"
        f"## Indice degli argomenti\n{index}\n\n---\n\n{body}\n"
    )
    candidate, notes = preview_compact_global_recap(original)
    assert original.startswith("# Essenziale\n\n## Ripasso globale\nUn ripasso")
    assert candidate != original
    assert _extract_summary_chapters(candidate) == _extract_summary_chapters(original)
    assert notes["preserved_chapters"] == 65
    assert notes["new_recap_words"] < 500
    assert notes["old_recap_words"] > 0
    assert candidate.endswith(body + "\n")
    assert re.search(r"(?m)^## Indice degli argomenti", candidate)


def test_preview_compact_recap_requires_original_recap_and_complete_statements():
    chapters = "\n".join(f"- Capitolo {i}" for i in range(10))
    blocks = "\n\n---\n\n".join(
        f"## Capitolo {i}\n\nEtichetta isolata"
        for i in range(10)
    )
    text = "# Test\n\n## Indice degli argomenti\n" + chapters + "\n\n---\n\n" + blocks
    import pytest

    with pytest.raises(ValueError, match="Ripasso globale"):
        preview_compact_global_recap(text)
    with pytest.raises(ValueError, match="Nessuna frase scientifica completa"):
        preview_compact_global_recap(
            "# Test\n\n## Ripasso globale\nLungo\n\n"
            + text.split("# Test\n\n", 1)[1]
        )



def test_exact_duplicate_sentence_only_removed_once_with_critical_numbers():
    fact = "La membrana cellulare contiene 3 componenti principali che regolano gli scambi."
    unique = "Il canale ionico può aprirsi durante le variazioni del potenziale elettrico."
    chapter = (
        "## Membrana\n\n" + fact
        + "\n\n### Concetti chiave\n\n" + fact
        + "\n\n" + unique
    )
    reduced, removed = _remove_identical_study_sentences(chapter)
    assert len(removed) == 1
    assert reduced.count(fact) == 1
    assert unique in reduced
    assert "3 componenti" in reduced
    assert "### Concetti chiave" in reduced


def test_negated_and_incomplete_study_statements_are_never_deduplicated():
    fact = "La proteina non può aprire il canale durante questa fase specifica."
    other = "La proteina può aprire il canale durante questa fase specifica."
    incomplete = "La proteina viene "
    chapter = (
        "## Canali\n\n" + fact + "\n\n" + other
        + "\n\n" + incomplete + "\n\n" + incomplete
    )
    result, removed = _remove_identical_study_sentences(chapter)
    assert not removed
    assert result == chapter


def test_compact_study_preview_only_edits_recap_and_literal_duplicates():
    chapters = []
    repeated = (
        "La cellula contiene 2 compartimenti che contribuiscono "
        "ai processi cellulari e al suo funzionamento."
    )
    for n in range(12):
        name = f"Capitolo {n}"
        body = (
            f"## {name}\n\n" + repeated
            + ("\n\n" + repeated if n == 4 else "")
        )
        chapters.append((name, body))
    index = "\n".join(f"- {name}" for name, _ in chapters)
    study = (
        "# Essenziale\n\n## Ripasso globale\n"
        "Questa frase introduttiva è molto lunga ma non rappresenta una dispensa.\n\n"
        f"## Indice degli argomenti\n{index}\n\n---\n\n"
        + "\n\n---\n\n".join(body for _, body in chapters) + "\n"
    )
    candidate, metrics = preview_compact_study(study)
    assert metrics["preserved_chapters"] == 12
    assert metrics["repeated_sentences_removed"] == 1
    assert metrics["deduplicated_chapters"] == ["Capitolo 4"]
    assert _extract_summary_chapters(candidate)[4][1].count(repeated) == 1
    for i, (_name, chapter) in enumerate(_extract_summary_chapters(candidate)):
        if i != 4:
            assert chapter == _extract_summary_chapters(study)[i][1]
    assert study.count(repeated) == 13
    assert candidate.count(repeated) == 12
