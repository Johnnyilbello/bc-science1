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
    assert any("Parole chiave" in issue for issue in audit.issues)


def test_document_audit_checks_every_indexed_chapter():
    chapter_a = _novice_chapter("Primo")
    chapter_b = _novice_chapter("Secondo")
    text = f"""# Corso

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
    assert len(results) == 2
    assert all(audit.metrics.score >= 80 for _, audit in results)


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
