from pathlib import Path

from bc_science.config import AppConfig
from bc_science.ollama_client import ChatResult
from bc_science.summarizer import (
    Summarizer,
    _build_global_recap,
    _chapter_quality_issues,
    _normalize_chapter_heading,
    _normalize_refined_structure,
    _summary_system_prompt,
)


def test_summary_prompt_forbids_external_clarifications():
    prompt = _summary_system_prompt("Fisiologia umana e dello sport")
    assert "NON aggiungere conoscenza generale" in prompt
    assert "NON correggere scientificamente" in prompt
    assert "non ha mai studiato la materia" in prompt
    assert "Una frase deve esprimere preferibilmente una sola idea" in prompt


def test_chapter_normalization_removes_internal_rules_and_obvious_typos():
    raw = "# Titolo generato\n\nIl calcio viene ricucinato.\n\n---\n\nFine."
    cleaned = _normalize_chapter_heading(raw, "Contrazione muscolare")
    assert cleaned.startswith("## Contrazione muscolare")
    assert "ricaptato" in cleaned
    assert "\n---\n" not in cleaned


def test_cached_chat_retries_whole_answer_after_length_stop(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    summarizer = Summarizer(AppConfig())
    responses = iter(
        [
            ChatResult(
                content="La frase continua",
                done_reason="length",
                eval_count=10,
                eval_duration=1_000_000_000,
                total_duration=1_200_000_000,
            ),
            ChatResult(
                content="La frase continua correttamente.",
                done_reason="stop",
                eval_count=5,
                eval_duration=500_000_000,
                total_duration=700_000_000,
            ),
        ]
    )

    monkeypatch.setattr(
        summarizer.client,
        "chat_stream",
        lambda *args, **kwargs: next(responses),
    )

    try:
        result = summarizer._cached_chat(
            "test-continuation",
            "system",
            "user",
            num_predict=20,
        )
    finally:
        summarizer.close()

    assert result == "La frase continua correttamente."
    assert summarizer.stats.continuation_calls == 1
    assert summarizer.stats.generated_tokens == 15


def test_refine_keeps_self_healing_until_novice_gate_passes(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    summarizer = Summarizer(AppConfig())

    source = """# Corso

## Indice degli argomenti
- Capitolo

---

## Capitolo

Testo sorgente semplice ma abbastanza lungo da essere rifinito.
Il capitolo contiene informazioni che devono restare presenti nel risultato finale.
Questa frase serve soltanto a dare contenuto sufficiente al test.
Le informazioni sono organizzate in più frasi complete.
Il testo sorgente non richiede conoscenze esterne.

### Da ricordare per l'esame
- Punto importante.
"""

    initial = """## Capitolo

Questa prima versione conserva le informazioni ma non ha ancora la struttura per principianti.
""" + ("Una frase breve mantiene il contenuto del capitolo. " * 12) + """

### Da ricordare per l'esame
- Punto importante.
"""

    still_bad = """## Capitolo

### In parole semplici
Il capitolo introduce il concetto principale con parole semplici.
Il lettore parte dall'idea generale prima dei dettagli.

Questa parte mantiene altre informazioni già presenti nella fonte.
Le frasi restano complete e ordinate.
Il contenuto non viene ampliato con nozioni esterne.
Le informazioni utili all'esame restano nel capitolo.
Il testo continua a usare una struttura progressiva.

### Da ricordare per l'esame
- Punto importante.
"""

    good = """## Capitolo

### In parole semplici
Il capitolo introduce il concetto principale con parole semplici.
Il lettore parte dall'idea generale prima dei dettagli.
Le informazioni vengono presentate in ordine progressivo.

### Parole chiave
- **Concetto** — idea centrale descritta nel capitolo.
- **Dettaglio** — informazione che completa il concetto.
- **Sequenza** — ordine con cui sono presentati i passaggi.

### Spiegazione
Questa parte mantiene le informazioni già presenti nella fonte.
Le frasi restano complete e ordinate.
Il contenuto non viene ampliato con nozioni esterne.

Un secondo paragrafo conserva altri dettagli utili.
La struttura resta semplice da seguire.
Ogni blocco tratta un'idea principale.

### Da ricordare per l'esame
- Punto importante.
- Il capitolo mantiene il contenuto sorgente.
"""

    novice_attempts = 0

    def fake_cached_chat(_cache_key, _system, user, *, num_predict=None):
        nonlocal novice_attempts
        if "LETTORE PRINCIPIANTE ASSOLUTO" in user:
            novice_attempts += 1
            return still_bad if novice_attempts == 1 else good
        if "Crea SOLO la sezione Markdown" in user:
            return "## Mappa della materia\n- Capitolo"
        return initial

    monkeypatch.setattr(summarizer, "_cached_chat", fake_cached_chat)

    try:
        result = summarizer.refine_summary(source, "Corso - Riassunto rifinito")
    finally:
        summarizer.close()

    assert novice_attempts == 2
    assert "### Parole chiave" in result
    assert summarizer.stats.novice_repairs == 2
    assert summarizer.stats.novice_chapters == 1


def test_normalizer_detaches_inline_markdown_heading():
    raw = """## Fibre

### Spiegazione
Una frase completa. ### Applicazione pratica

Testo applicativo.

### Da ricordare per l'esame
- Punto.
"""
    normalized, changed = _normalize_refined_structure(raw, "Fibre")

    assert changed
    assert "Una frase completa.\n### Applicazione pratica" in normalized
    assert "frase completa. ###" not in normalized


def test_quality_gate_rejects_too_many_keywords_and_fake_scientific_certification():
    keywords = "\n".join(
        f"- **Termine {index}** — definizione semplice."
        for index in range(1, 11)
    )
    chapter = f"""## Capitolo

### Verifica materiale
Il materiale e scientificamente corretto e non richiede verifiche.

### In parole semplici
Introduzione semplice.

### Parole chiave
{keywords}

### Da ricordare per l'esame
- Punto conclusivo.
"""
    issues = _chapter_quality_issues(
        chapter,
        "Capitolo",
        require_source_warning=False,
    )

    assert any("Verifica materiale non richiesta" in issue for issue in issues)
    assert any("certificazione scientifica" in issue for issue in issues)
    assert any("3 a 8 voci" in issue for issue in issues)


def test_global_recap_never_uses_review_chapter_placeholder():
    chapter = """## Capitolo

### In parole semplici
Questo capitolo spiega il concetto di base con parole semplici.
Mostra poi i dettagli necessari per comprenderlo.

### Parole chiave
- **Concetto** — idea principale.
- **Dettaglio** — informazione aggiuntiva.
- **Sequenza** — ordine dei passaggi.

### Spiegazione
Testo del capitolo.

### Da ricordare per l'esame

"""
    recap = _build_global_recap([("Capitolo", chapter)])

    assert "rivedi il capitolo" not in recap
    assert "concetto di base" in recap


def test_global_recap_truncates_long_prose_exam_section():
    long_point = " ".join(["informazione"] * 120) + "."
    chapter = f"""## Capitolo

### In parole semplici
Introduzione semplice.

### Parole chiave
- **Uno** — definizione.
- **Due** — definizione.
- **Tre** — definizione.

### Da ricordare per l'esame
{long_point}
"""
    recap = _build_global_recap([("Capitolo", chapter)])
    recap_line = recap.splitlines()[1]

    assert len(recap_line) < 360
    assert "rivedi il capitolo" not in recap_line


def test_refine_retries_quality_gate_until_structure_is_clean(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    summarizer = Summarizer(AppConfig())

    source = """# Corso

## Indice degli argomenti
- Capitolo

---

## Capitolo

Contenuto sorgente sufficiente per il test.
Mantiene informazioni che non devono essere inventate o perse.
La struttura iniziale deve essere migliorata.

### Da ricordare per l'esame
- Punto importante.
"""

    too_many_10 = "\n".join(
        f"- **Termine {index}** — definizione semplice."
        for index in range(1, 11)
    )
    too_many_9 = "\n".join(
        f"- **Termine {index}** — definizione semplice."
        for index in range(1, 10)
    )
    good_3 = "\n".join(
        f"- **Termine {index}** — definizione semplice."
        for index in range(1, 4)
    )

    def chapter(keywords: str) -> str:
        return f"""## Capitolo

### In parole semplici
Questo capitolo introduce il contenuto in modo semplice.
La spiegazione parte dall'idea generale.

### Parole chiave
{keywords}

### Spiegazione
Le informazioni restano quelle della fonte.
La struttura viene resa più leggibile.
Il testo non aggiunge conoscenze esterne.

### Da ricordare per l'esame
- Punto importante.
- Conserva il contenuto della fonte.
"""

    quality_attempts = 0

    def fake_cached_chat(_cache_key, _system, user, *, num_predict=None):
        nonlocal quality_attempts
        if "La precedente generazione non ha superato il controllo qualita" in user:
            quality_attempts += 1
            return chapter(too_many_9 if quality_attempts == 1 else good_3)
        if "Crea SOLO la sezione Markdown" in user:
            return "## Mappa della materia\n- Capitolo"
        return chapter(too_many_10)

    monkeypatch.setattr(summarizer, "_cached_chat", fake_cached_chat)

    try:
        result = summarizer.refine_summary(source, "Corso - Riassunto rifinito")
    finally:
        summarizer.close()

    assert quality_attempts == 2
    assert "Termine 9" not in result
    assert summarizer.stats.quality_repairs == 2
