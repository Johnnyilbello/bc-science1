from __future__ import annotations

from .config import AppConfig
from .indexer import retrieve
from .knowledge import classify_domain, system_prompt
from .ollama_client import OllamaClient


def answer(
    question: str,
    config: AppConfig,
    profile: str | None = None,
) -> tuple[str, list[dict]]:
    hits = retrieve(question, config)
    if not hits:
        return (
            "Non ci sono ancora materiali indicizzati. Esegui prima il comando ingest.",
            [],
        )

    context = "\n\n".join(
        f"[Fonte: {hit['source']} | blocco {hit['chunk_index'] + 1}]\n{hit['text']}"
        for hit in hits
    )
    _, domain = classify_domain(context)
    system = system_prompt(domain)
    prompt = f"""Rispondi alla domanda dello studente usando prima di tutto le FONTI riportate.
Se la risposta non è contenuta nelle fonti, dichiaralo chiaramente. Spiega in modo semplice,
ma conserva terminologia scientifica, passaggi causali e differenze importanti.

DOMANDA:
{question}

FONTI:
{context}

Concludi con una sezione "Da ricordare" di massimo 5 punti.
"""
    client = OllamaClient(config.ollama_url)
    response = client.chat(
        config.model_for_profile(profile),
        [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        num_ctx=config.num_ctx,
        num_predict=1200,
        keep_alive=config.keep_alive,
    )
    return response, hits
