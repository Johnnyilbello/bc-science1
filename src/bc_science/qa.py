from __future__ import annotations

from collections.abc import Callable

from .config import AppConfig
from .indexer import retrieve
from .knowledge import classify_domain, system_prompt
from .ollama_client import ChatResult, OllamaClient


def answer(
    question: str,
    config: AppConfig,
    profile: str | None = None,
    *,
    deep: bool = False,
    on_token: Callable[[str], None] | None = None,
) -> tuple[ChatResult, list[dict]]:
    context_limit = config.context_chunks if deep else min(4, config.context_chunks)
    hits = retrieve(question, config, limit=context_limit)
    if not hits:
        return (
            ChatResult(
                content="Non ci sono ancora materiali indicizzati. Esegui prima il comando ingest."
            ),
            [],
        )

    context = "\n\n".join(
        f"[Fonte: {hit['source']} | blocco {hit['chunk_index'] + 1}]\n{hit['text']}"
        for hit in hits
    )
    _, domain = classify_domain(context)
    system = system_prompt(domain)

    selected_profile = (profile or config.profile).lower()
    turbo_guard = ""
    if selected_profile == "turbo":
        turbo_guard = """
VINCOLO TURBO:
- Usa soltanto informazioni esplicitamente supportate dalle FONTI.
- Non aggiungere conoscenza generale, deduzioni, esempi, classificazioni o giudizi non presenti.
- Non rendere una frase più forte della fonte: evita parole come "tossico", "sempre",
  "principale", "predominante" o equivalenti se la fonte non le sostiene esplicitamente.
- Se un dettaglio non è nelle fonti, omettilo invece di completarlo a memoria.
"""

    mode = (
        "Approfondita: includi tutti i dettagli rilevanti presenti nelle fonti."
        if deep
        else (
            "Rapida: sii completo sui concetti fondamentali ma compatto. "
            "Punta a circa 450-650 parole e non ripetere lo stesso concetto."
        )
    )
    prompt = f"""Rispondi alla domanda dello studente usando prima di tutto le FONTI riportate.
Se la risposta non è contenuta nelle fonti, dichiaralo chiaramente.
Spiega in italiano semplice ma conserva terminologia scientifica, numeri,
passaggi causali, differenze e definizioni importanti.
Correggi evidenti refusi OCR o lessicali senza alterare il contenuto scientifico.
Non inventare dettagli per riempire sezioni.

MODALITA:
{mode}
{turbo_guard}

DOMANDA:
{question}

FONTI:
{context}

Struttura la risposta in sezioni brevi quando aiuta la comprensione.
Concludi con "Da ricordare" in massimo 5 punti.
"""
    client = OllamaClient(config.ollama_url)
    result = client.chat_stream(
        config.model_for_profile(profile),
        [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        on_token=on_token,
        num_ctx=config.num_ctx,
        num_predict=1500 if deep else (1100 if selected_profile == "turbo" else 1400),
        keep_alive=config.keep_alive,
    )
    return result, hits
