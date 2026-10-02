from pathlib import Path

from bc_science.config import AppConfig
from bc_science.summarizer import Summarizer


def test_incremental_summary_updates_only_matching_chapter(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    summarizer = Summarizer(AppConfig())

    existing = """# FISIOLOGIA - Riassunto completo

> Riassunto unico costruito dalle dispense importate.

## Come studiare questo riassunto
- Studia un capitolo alla volta.

## Mappa della materia
- Sistema nervoso: Neuroni.
- Movimento: Muscoli.

## Ripasso globale
1. **Neuroni**: I neuroni trasmettono segnali.
2. **Muscoli**: I muscoli generano forza.

## Indice degli argomenti
- Neuroni
- Muscoli

---

## Neuroni
### In parole semplici
I neuroni trasmettono segnali nel sistema nervoso.
### Dettagli
La membrana permette la trasmissione del segnale.
### Da ricordare per l'esame
- I neuroni trasmettono segnali.
- La membrana partecipa alla trasmissione.

---

## Muscoli
### In parole semplici
I muscoli generano forza.
### Dettagli
La contrazione produce movimento.
### Da ricordare per l'esame
- I muscoli generano forza.
"""

    new_file = tmp_path / "Neuroni 2.txt"
    new_file.write_text("nuovo materiale", encoding="utf-8")

    def fake_topic(group, *, progress=None):
        assert group.title == "Neuroni"
        assert group.files == [new_file]
        return """## Neuroni
### In parole semplici
I neuroni comunicano anche tramite sinapsi.
### Da ricordare per l'esame
- Le sinapsi permettono la comunicazione tra neuroni.
"""

    merged = """## Neuroni
### In parole semplici
I neuroni trasmettono segnali nel sistema nervoso e comunicano tramite sinapsi.
### Dettagli
La membrana permette la trasmissione del segnale.
Le sinapsi permettono la comunicazione tra neuroni.
Il capitolo mantiene le informazioni precedenti e integra il nuovo materiale in modo progressivo.
La spiegazione distingue il ruolo della membrana dalla comunicazione sinaptica e conserva i concetti gia presenti.
Questa integrazione permette di studiare il nuovo contenuto senza perdere il quadro generale costruito dal materiale precedente.
I termini vengono mantenuti nello stesso contesto del capitolo per evitare duplicazioni e frammentazione.
### Da ricordare per l'esame
- I neuroni trasmettono segnali.
- La membrana partecipa alla trasmissione.
- Le sinapsi permettono la comunicazione tra neuroni.
"""

    monkeypatch.setattr(summarizer, "_summarize_topic", fake_topic)

    def fake_cached(cache_key, system, user, *, num_predict=None):
        assert "CAPITOLO ESISTENTE" in user
        assert "NUOVO MATERIALE" in user
        return merged

    monkeypatch.setattr(summarizer, "_cached_chat", fake_cached)

    try:
        result, updated, created = summarizer.incremental_update_course(
            existing,
            [new_file],
            "FISIOLOGIA - Riassunto completo",
        )
    finally:
        summarizer.close()

    assert updated == 1
    assert created == 0
    assert "Le sinapsi permettono la comunicazione tra neuroni." in result
    assert "La membrana partecipa alla trasmissione." in result
    assert "## Muscoli" in result
    assert "La contrazione produce movimento." in result
    assert result.count("## Neuroni") == 1
    assert "## Mappa della materia\n- Sistema nervoso: Neuroni." in result


def test_incremental_summary_adds_new_topic_and_regenerates_map(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    summarizer = Summarizer(AppConfig())

    existing = """# ANATOMIA - Riassunto completo

## Come studiare questo riassunto
- Segui la mappa.

## Mappa della materia
- Ossa.

## Ripasso globale
1. **Ossa**: Le ossa sostengono il corpo.

## Indice degli argomenti
- Ossa

---

## Ossa
### In parole semplici
Le ossa sostengono il corpo.
### Da ricordare per l'esame
- Le ossa sostengono il corpo.
"""

    new_file = tmp_path / "Articolazioni 1.txt"
    new_file.write_text("articolazioni", encoding="utf-8")

    def fake_topic(group, *, progress=None):
        return """## Articolazioni
### In parole semplici
Le articolazioni collegano segmenti ossei.
### Da ricordare per l'esame
- Le articolazioni collegano segmenti ossei.
"""

    monkeypatch.setattr(summarizer, "_summarize_topic", fake_topic)

    calls = {"route": 0, "map": 0}

    def fake_cached(cache_key, system, user, *, num_predict=None):
        if "Decidi dove appartiene il NUOVO materiale" in user:
            calls["route"] += 1
            return "NEW"
        assert "## Mappa della materia" in user
        calls["map"] += 1
        return "## Mappa della materia\n- Ossa e articolazioni."

    monkeypatch.setattr(summarizer, "_cached_chat", fake_cached)

    try:
        result, updated, created = summarizer.incremental_update_course(
            existing,
            [new_file],
            "ANATOMIA - Riassunto completo",
        )
    finally:
        summarizer.close()

    assert updated == 0
    assert created == 1
    assert calls == {"route": 1, "map": 1}
    assert "- Articolazioni" in result
    assert "## Articolazioni" in result
    assert "Ossa e articolazioni" in result


def test_incremental_summary_routes_ambiguous_filename_by_content(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    summarizer = Summarizer(AppConfig())

    existing = """# FISIOLOGIA - Riassunto completo

## Mappa della materia
- Sistema nervoso.

## Ripasso globale
1. **Neuroni**: I neuroni trasmettono segnali.

## Indice degli argomenti
- Neuroni

---

## Neuroni
### In parole semplici
I neuroni trasmettono segnali.
### Da ricordare per l'esame
- I neuroni trasmettono segnali.
"""

    new_file = tmp_path / "Lezione speciale.txt"
    new_file.write_text("sinapsi e neuroni", encoding="utf-8")

    new_material = """## Lezione speciale
### In parole semplici
I neuroni comunicano attraverso sinapsi.
### Da ricordare per l'esame
- I neuroni comunicano attraverso sinapsi.
"""
    merged = """## Neuroni
### In parole semplici
I neuroni trasmettono segnali e comunicano attraverso sinapsi.
### Dettagli
Il capitolo conserva il concetto di trasmissione dei segnali e aggiunge la comunicazione sinaptica come nuova informazione.
La spiegazione resta organizzata in modo progressivo, mantenendo il contenuto precedente e integrando il nuovo materiale senza sostituirlo.
Le informazioni vengono presentate nello stesso capitolo perché riguardano chiaramente i neuroni e il loro modo di comunicare.
Il testo mantiene separati i concetti distinti ed evita di trasformare il nuovo documento in un capitolo duplicato.
Questa struttura permette di studiare insieme le informazioni precedenti e quelle aggiunte successivamente.
### Da ricordare per l'esame
- I neuroni trasmettono segnali.
- I neuroni comunicano attraverso sinapsi.
"""

    monkeypatch.setattr(
        summarizer,
        "_summarize_topic",
        lambda group, progress=None: new_material,
    )

    calls = {"route": 0, "merge": 0}

    def fake_cached(cache_key, system, user, *, num_predict=None):
        if "Decidi dove appartiene il NUOVO materiale" in user:
            calls["route"] += 1
            return "MATCH: Neuroni"
        calls["merge"] += 1
        return merged

    monkeypatch.setattr(summarizer, "_cached_chat", fake_cached)

    try:
        result, updated, created = summarizer.incremental_update_course(
            existing,
            [new_file],
            "FISIOLOGIA - Riassunto completo",
        )
    finally:
        summarizer.close()

    assert calls == {"route": 1, "merge": 1}
    assert updated == 1
    assert created == 0
    assert result.count("## Neuroni") == 1
    assert "comunicano attraverso sinapsi" in result
