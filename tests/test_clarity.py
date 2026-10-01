from bc_science.clarity import (
    audit_novice_document,
    normalize_novice_layout,
    novice_audit,
    readability_metrics,
)


def _novice_chapter(title: str = "Membrana") -> str:
    return f"""## {title}

### In parole semplici
La membrana separa l'interno della cellula dall'ambiente esterno.
Controlla quali sostanze possono entrare o uscire.
Per capire il trasporto bisogna prima distinguere movimento passivo e attivo.

### Parole chiave
- **Membrana** — barriera che separa due ambienti.
- **Gradiente** — differenza di concentrazione tra due zone.
- **Trasporto passivo** — movimento che non richiede energia cellulare.

### Spiegazione
Nel trasporto passivo le sostanze seguono il gradiente descritto nelle dispense.
Il trasporto attivo richiede invece energia.
I due processi permettono alla cellula di regolare gli scambi.

### Da ricordare per l'esame
- La membrana separa ambiente interno ed esterno.
- Il trasporto passivo non richiede energia.
"""


def test_readability_metrics_reward_beginner_structure():
    metrics = readability_metrics(_novice_chapter())
    assert metrics.has_simple_intro
    assert metrics.has_keywords
    assert metrics.average_sentence_words <= 20
    assert metrics.score >= 80


def test_novice_audit_rejects_dense_unstructured_text():
    text = """## Capitolo

Questo paragrafo contiene una spiegazione molto lunga che continua senza una vera introduzione per il lettore e usa molti concetti tutti insieme rendendo difficile capire quale sia l'idea principale prima di arrivare ai dettagli tecnici che vengono presentati senza una struttura progressiva e senza definizioni iniziali. Questo secondo periodo continua ad aggiungere altre informazioni nello stesso blocco senza offrire parole chiave o una guida per chi non ha mai studiato l'argomento e quindi aumenta ulteriormente il carico di lettura.
"""
    audit = novice_audit(text)
    assert not audit.passed
    assert any("In parole semplici" in issue for issue in audit.issues)


def test_document_audit_checks_every_indexed_chapter():
    chapter_a = _novice_chapter("Primo")
    chapter_b = _novice_chapter("Secondo")
    text = f"""# Corso

## Mappa della materia
- Primo e Secondo.

## Ripasso globale
1. **Primo**: concetto principale.
2. **Secondo**: concetto principale.

## Indice degli argomenti
- Primo
- Secondo

---

{chapter_a}

---

{chapter_b}
"""
    passed, results = audit_novice_document(text)
    assert passed
    assert len(results) == 3
    assert all(audit.passed for _, audit in results)


def test_document_audit_detects_missing_chapter():
    text = f"""# Corso

## Indice degli argomenti
- Primo
- Secondo

---

{_novice_chapter("Primo")}
"""
    passed, results = audit_novice_document(text)
    assert not passed
    second = next(audit for title, audit in results if title == "Secondo")
    assert any("assente" in issue for issue in second.issues)


def test_layout_normalizer_splits_seven_sentence_paragraph_without_rewriting():
    sentences = [f"Frase numero {index}." for index in range(1, 8)]
    text = """## Capitolo

### In parole semplici
""" + " ".join(sentences) + """

### Parole chiave
- **Termine** — definizione semplice.

### Da ricordare per l'esame
- Punto importante.
"""
    normalized, fixes = normalize_novice_layout(text)

    assert fixes == 1
    assert all(sentence in normalized for sentence in sentences)
    assert normalized.index(sentences[0]) < normalized.index(sentences[-1])
    assert novice_audit(normalized).metrics.max_paragraph_sentences <= 5


def test_list_under_heading_is_not_counted_as_one_seven_sentence_paragraph():
    text = """## Capitolo

### In parole semplici
La cellula usa diversi tipi di molecole.
Ogni gruppo ha una funzione specifica.

### Parole chiave
- **Glucidi** — molecole usate anche come fonte di energia.
- **Lipidi** — molecole che possono avere funzione energetica o strutturale.
- **Proteine** — molecole formate da amminoacidi.
- **Enzimi** — proteine che accelerano reazioni.
- **ATP** — molecola coinvolta negli scambi di energia.
- **DNA** — molecola che contiene informazione genetica.
- **RNA** — molecola coinvolta nell'espressione dell'informazione genetica.

### Da ricordare per l'esame
- Le biomolecole hanno funzioni diverse.
"""

    audit = novice_audit(text)

    assert audit.metrics.max_paragraph_sentences <= 5
    assert not any("troppe frasi" in issue for issue in audit.issues)


def test_novice_audit_allows_keyword_overload_but_rejects_inline_heading():
    keywords = "\n".join(
        f"- **Termine {index}** — definizione semplice."
        for index in range(1, 10)
    )
    text = f"""## Capitolo

### In parole semplici
Introduzione semplice per chi parte da zero.

### Parole chiave
{keywords}

### Spiegazione
Frase semplice. ### Applicazione

Altra frase.

### Da ricordare per l'esame
- Punto.
"""
    audit = novice_audit(text)

    assert not audit.passed
    assert not any("3 a 8 voci" in issue for issue in audit.issues)
    assert any("heading Markdown" in issue for issue in audit.issues)


def test_document_audit_rejects_incomplete_global_recap_frontmatter():
    text = f"""# Corso

## Mappa della materia
- Primo

## Ripasso globale
1. **Primo**: rivedi il capitolo.

## Indice degli argomenti
- Primo

---

{_novice_chapter("Primo")}
"""
    passed, results = audit_novice_document(text)

    assert not passed
    front = next(audit for title, audit in results if title == "Front matter")
    assert any("rivedi il capitolo" in issue for issue in front.issues)


def test_novice_audit_can_pass_without_keyword_section():
    text = """## Capitolo

### In parole semplici
Questo capitolo introduce l'argomento con parole semplici.
La spiegazione parte dall'idea generale.
Il lettore non deve conoscere gia la materia.

### Spiegazione
Il primo concetto viene presentato in modo progressivo.
Ogni frase contiene una sola idea.
I dettagli vengono aggiunti dopo il quadro generale.

### Da ricordare per l'esame
- Il concetto principale resta chiaro.
- I dettagli vengono dopo l'introduzione.
"""

    audit = novice_audit(text)

    assert audit.passed
    assert not audit.metrics.has_keywords


def test_layout_normalizer_handles_mixed_prose_boundaries_and_inline_heading():
    dense = " ".join(f"Frase {index} descrive un concetto." for index in range(1, 8))
    text = f"""## Capitolo

### In parole semplici
{dense}
> Nota separata che non deve impedire la correzione del paragrafo.

### Spiegazione
Una frase completa. ### Applicazione pratica
Testo applicativo semplice.

### Da ricordare per l'esame
- Punto importante.
"""

    normalized, fixes = normalize_novice_layout(text)
    audit = novice_audit(normalized)

    assert fixes >= 1
    assert "frase completa. ###" not in normalized.casefold()
    assert "\n### Applicazione pratica\n" in normalized
    assert audit.metrics.max_paragraph_sentences <= 5
    assert not any("heading Markdown" in issue for issue in audit.issues)
    assert not any("troppe frasi" in issue for issue in audit.issues)
