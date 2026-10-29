"""Local-only study benchmark. No uploaded files or external LLM APIs."""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from bc_science.config import AppConfig
from bc_science.ollama_client import OllamaClient
from bc_science.study_quality import assess_essential


def main() -> None:
    parser = argparse.ArgumentParser(description="BC Science: qualità Essenziale / Completa")
    parser.add_argument("--complete", required=True, type=Path, help="Markdown Completa")
    parser.add_argument("--essential", required=True, type=Path, help="Markdown Essenziale")
    parser.add_argument(
        "--ab-thinking", action="store_true",
        help="Confronta tempi e output token del 4B (richiede Ollama in esecuzione).",
    )
    args = parser.parse_args()
    complete = args.complete.read_text(encoding="utf-8")
    essential = args.essential.read_text(encoding="utf-8")
    report = assess_essential(complete, essential)
    print(json.dumps(asdict(report), ensure_ascii=False, indent=2))

    if not args.ab_thinking:
        return

    config = AppConfig.load()
    client = OllamaClient(config.ollama_url)
    sample = complete[:4000]
    prompt = (
        "Spiega in italiano semplice e fedele alle fonti i concetti essenziali "
        "di questo estratto. Non inventare dettagli e conserva i numeri importanti.\n\n"
        + sample
    )
    for thinking in (False, True):
        try:
            result = client.chat_stream(
                config.generation_model,
                [{"role": "user", "content": prompt}],
                think=thinking,
                num_ctx=config.num_ctx,
                num_predict=1600,
                keep_alive="15m",
            )
            print(json.dumps({
                "thinking": thinking,
                "model": config.generation_model,
                "done_reason": result.done_reason,
                "generated_tokens": result.eval_count,
                "tokens_per_second": round(result.tokens_per_second, 2),
                "total_seconds": round(result.total_seconds, 2),
                "load_seconds": round(result.load_seconds, 2),
                "output_words": len(result.content.split()),
            }, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001 - benchmark: report per-mode failures
            print(json.dumps({"thinking": thinking, "error": str(exc)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
