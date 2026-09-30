from __future__ import annotations

import argparse
import sys

from rich.console import Console

from .config import AppConfig
from .hardware import detect_hardware, select_model_plan
from .ollama_client import OllamaClient

console = Console()


def bootstrap(pull: bool = True) -> AppConfig:
    hw = detect_hardware()
    plan = select_model_plan(hw)
    config = AppConfig(
        generation_model=plan.standard,
        turbo_model=plan.turbo,
        quality_model=plan.quality,
        profile=plan.recommended_profile,
    )
    config.save()

    console.print(f"[bold]Hardware:[/] {hw.summary}")
    console.print(
        f"[bold]Profilo:[/] {plan.recommended_profile}  "
        f"[bold]Modello:[/] {config.generation_model}"
    )
    if plan.warning:
        console.print(f"[yellow]{plan.warning}[/]")

    if pull:
        client = OllamaClient(config.ollama_url)
        if not client.available():
            raise RuntimeError(
                "Ollama non risponde su localhost:11434. Avvialo e riprova il bootstrap."
            )

        required = [config.generation_model, config.embedding_model]
        for model in dict.fromkeys(required):
            console.print(f"Verifico modello [cyan]{model}[/]...")
            downloaded = client.ensure_model(model)
            console.print("  scaricato." if downloaded else "  già presente.")

    return config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-pull", action="store_true")
    parser.add_argument("--print-model", action="store_true")
    args = parser.parse_args()

    config = bootstrap(pull=not args.no_pull and not args.print_model)
    if args.print_model:
        print(config.generation_model)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        console.print(f"[red]Bootstrap fallito:[/] {exc}")
        sys.exit(1)
