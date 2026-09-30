from __future__ import annotations

import hashlib
from pathlib import Path

from .cache import CacheDB
from .config import AppConfig
from .documents import chunk_text, extract_document
from .knowledge import classify_domain, system_prompt
from .ollama_client import OllamaClient

PROMPT_VERSION = "summary-v1"


def _key(*parts: str) -> str:
    payload = "\x1f".join(parts).encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()


def _chunk_prompt(text: str, domain: str) -> str:
    return f"""Trasforma il seguente estratto di {domain} in appunti da esame eCampus.

OBIETTIVO:
- massima comprensibilità;
- nessuna perdita di concetti potenzialmente valutabili;
- elimina solo ripetizioni e frasi decorative;
- conserva definizioni, classificazioni, passaggi causali, eccezioni e numeri;
- spiega sigle e termini tecnici alla prima occorrenza.

FORMATO:
### Spiegazione semplice
### Concetti da ricordare
### Definizioni e termini
### Sequenze / meccanismi
### Attenzione all'esame

FONTE:
{text}
"""


def _final_prompt(notes: str, domain: str, title: str) -> str:
    return f"""Crea il riassunto finale di studio per "{title}" ({domain}) usando ESCLUSIVAMENTE
gli appunti intermedi sotto. Unifica i duplicati ma non eliminare informazioni diverse.

Struttura obbligatoria:
# {title}
## Spiegazione semplice
## Concetti fondamentali
## Definizioni da sapere
## Meccanismi e sequenze
## Confronti e differenze importanti
## Errori da evitare
## Domande di ripasso
Formula quesiti basati sui contenuti senza presentarli come domande ufficiali eCampus.
## Ripasso lampo
Chiudi con 5-10 punti telegrafici.

APPUNTI INTERMEDI:
{notes}
"""


class Summarizer:
    def __init__(self, config: AppConfig, profile: str | None = None) -> None:
        self.config = config
        self.model = config.model_for_profile(profile)
        self.client = OllamaClient(config.ollama_url)
        self.db = CacheDB()

    def close(self) -> None:
        self.db.close()

    def _cached_chat(self, cache_key: str, system: str, user: str) -> str:
        hit = self.db.get_summary(cache_key)
        if hit:
            return hit
        result = self.client.chat(
            self.model,
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            num_ctx=self.config.num_ctx,
            num_predict=self.config.num_predict,
            keep_alive=self.config.keep_alive,
        )
        self.db.set_summary(cache_key, result)
        return result

    def summarize_text(self, text: str, title: str = "Riassunto") -> str:
        _, domain = classify_domain(text)
        system = system_prompt(domain)
        chunks = chunk_text(
            text,
            chunk_chars=self.config.chunk_chars,
            overlap=self.config.chunk_overlap,
        )
        if not chunks:
            return ""

        notes: list[str] = []
        for chunk in chunks:
            cache_key = _key(PROMPT_VERSION, self.model, "chunk", domain, chunk)
            notes.append(self._cached_chat(cache_key, system, _chunk_prompt(chunk, domain)))

        round_number = 0
        while sum(map(len, notes)) > 26000 and len(notes) > 1:
            reduced: list[str] = []
            for start in range(0, len(notes), 4):
                group = "\n\n".join(notes[start:start + 4])
                prompt = (
                    "Fondi questi appunti in un unico blocco completo. Rimuovi solo duplicati, "
                    "non perdere definizioni, meccanismi, eccezioni o numeri.\n\n" + group
                )
                cache_key = _key(
                    PROMPT_VERSION, self.model, f"reduce-{round_number}", domain, group
                )
                reduced.append(self._cached_chat(cache_key, system, prompt))
            notes = reduced
            round_number += 1

        merged = "\n\n".join(notes)
        final_key = _key(PROMPT_VERSION, self.model, "final", domain, title, merged)
        return self._cached_chat(final_key, system, _final_prompt(merged, domain, title))

    def summarize_file(self, path: Path) -> str:
        extracted = extract_document(path)
        return self.summarize_text(extracted.text, title=path.stem)
