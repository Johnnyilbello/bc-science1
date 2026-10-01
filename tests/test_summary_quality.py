from pathlib import Path

from bc_science.config import AppConfig
from bc_science.ollama_client import ChatResult
from bc_science.summarizer import (
    Summarizer,
    _normalize_chapter_heading,
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
