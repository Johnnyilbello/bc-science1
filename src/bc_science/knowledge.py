from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml


def _knowledge_path() -> Path:
    packaged = Path(__file__).parent / "knowledge" / "scienze_motorie.yaml"
    if packaged.exists():
        return packaged
    return Path(__file__).resolve().parents[2] / "knowledge" / "scienze_motorie.yaml"


@lru_cache(maxsize=1)
def load_knowledge() -> dict:
    path = _knowledge_path()
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def classify_domain(text: str) -> tuple[str, str]:
    data = load_knowledge()
    lowered = text.lower()
    best_key = "generale"
    best_label = "Scienze Motorie"
    best_score = 0
    for key, item in data.get("domains", {}).items():
        score = sum(lowered.count(keyword.lower()) for keyword in item.get("keywords", []))
        if score > best_score:
            best_key = key
            best_label = item.get("label", key)
            best_score = score
    return best_key, best_label


def system_prompt(domain_label: str) -> str:
    principles = load_knowledge().get("principles", [])
    rules = "\n".join(f"- {item}" for item in principles)
    return f"""Sei BC Science, tutor universitario locale specializzato in Scienze Motorie.
Materia rilevata: {domain_label}.

REGOLE OBBLIGATORIE:
{rules}

Scrivi in italiano semplice e preciso. Mantieni la terminologia scientifica corretta,
ma spiega ogni termine difficile in modo comprensibile. Non inventare informazioni
mancanti dalla fonte e non trasformare ipotesi in fatti.
"""
