from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from .cache import CacheDB
from .config import AppConfig, app_home
from .documents import extract_document, iter_source_files
from .hardware import detect_hardware, select_model_plan
from .indexer import ingest as ingest_source
from .ollama_client import OllamaClient
from .qa import answer
from .summarizer import Summarizer

app = typer.Typer(
    name="bc-science",
    help="Assistente locale per lo studio di Scienze Motorie eCampus.",
    no_args_is_help=True,
)
models_app = typer.Typer(help="Gestione modelli locali.")
app.add_typer(models_app, name="models")
console = Console()


def _config() -> AppConfig:
    return AppConfig.load()


def _require_ollama(config: AppConfig) -> OllamaClient:
    client = OllamaClient(config.ollama_url)
    if not client.available():
        console.print("[red]Ollama non risponde.[/] Avvia Ollama e riprova.")
        raise typer.Exit(2)
    return client


def _ensure_model(config: AppConfig, profile: str) -> str:
    client = _require_ollama(config)
    model = config.model_for_profile(profile)
    if model not in client.models() and f"{model}:latest" not in client.models():
        console.print(f"Scarico il modello richiesto [cyan]{model}[/]...")
        client.ensure_model(model)
    return model


@app.command()
def doctor() -> None:
    """Controlla hardware, Ollama, modelli e cache."""
    config = _config()
    hw = detect_hardware()
    plan = select_model_plan(hw)
    client = OllamaClient(config.ollama_url)

    table = Table(title="BC Science - Diagnostica")
    table.add_column("Voce")
    table.add_column("Stato")
    table.add_row("Home", str(app_home()))
    table.add_row("Hardware", hw.summary)
    table.add_row("Profilo consigliato", plan.recommended_profile)
    table.add_row("Modello standard", config.generation_model)
    table.add_row("Embedding", config.embedding_model)
    table.add_row("Ollama", "[green]OK[/]" if client.available() else "[red]NON RAGGIUNGIBILE[/]")

    if client.available():
        installed = sorted(client.models())
        table.add_row("Modelli installati", ", ".join(installed) if installed else "nessuno")

    db = CacheDB()
    stats = db.stats()
    db.close()
    table.add_row(
        "Indice",
        f"{stats['documents']} documenti / {stats['chunks']} blocchi / "
        f"{stats['summaries']} risultati in cache",
    )
    console.print(table)


@app.command()
def ingest(
    source: Annotated[Path, typer.Argument(help="PDF, DOCX, TXT, ZIP o cartella")],
    force: Annotated[bool, typer.Option("--force", help="Reindicizza anche file invariati")] = False,
) -> None:
    """Indicizza semanticamente i materiali di studio."""
    config = _config()
    client = _require_ollama(config)
    if config.embedding_model not in client.models():
        console.print(f"Scarico [cyan]{config.embedding_model}[/]...")
        client.ensure_model(config.embedding_model)
    result = ingest_source(source, config, force=force)
    console.print(
        f"[green]Indicizzazione completata.[/] Trovati {result['files_found']} file, "
        f"indicizzati {result['indexed']}, invariati {result['skipped']}, "
        f"nuovi blocchi {result['chunks']}."
    )


@app.command()
def summarize(
    source: Annotated[Path, typer.Argument(help="PDF, DOCX, TXT, ZIP o cartella")],
    profile: Annotated[str, typer.Option("--profile", "-p", help="turbo, standard o quality")] = "standard",
    single: Annotated[bool, typer.Option("--single/--separate", help="Un unico riassunto o uno per file")] = True,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="File/cartella di output")] = None,
) -> None:
    """Genera riassunti semplici, completi e orientati all'esame."""
    config = _config()
    _ensure_model(config, profile)
    files = iter_source_files(source)
    if not files:
        console.print("[red]Nessun file supportato trovato.[/]")
        raise typer.Exit(1)

    summarizer = Summarizer(config, profile)
    try:
        if single:
            combined: list[str] = []
            for path in files:
                console.print(f"Leggo [cyan]{path.name}[/]...")
                doc = extract_document(path)
                combined.append(f"\n\n# Documento: {path.stem}\n\n{doc.text}")
            title = source.stem if source.is_file() else source.name
            console.print(
                f"Genero il riassunto con [cyan]{summarizer.model}[/]. "
                "I blocchi già elaborati vengono letti dalla cache."
            )
            result = summarizer.summarize_text("\n".join(combined), title=title)
            destination = output or (app_home() / "outputs" / f"{title}-riassunto.md")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(result, encoding="utf-8")
            console.print(f"[green]Creato:[/] {destination}")
            return

        destination_dir = output or (app_home() / "outputs" / source.stem)
        destination_dir.mkdir(parents=True, exist_ok=True)
        for path in files:
            console.print(f"Riassumo [cyan]{path.name}[/]...")
            result = summarizer.summarize_file(path)
            target = destination_dir / f"{path.stem}-riassunto.md"
            target.write_text(result, encoding="utf-8")
            console.print(f"  [green]{target.name}[/]")
    finally:
        summarizer.close()


@app.command()
def ask(
    question: Annotated[str, typer.Argument(help="Domanda sui materiali indicizzati")],
    profile: Annotated[str, typer.Option("--profile", "-p")] = "standard",
    sources: Annotated[bool, typer.Option("--sources/--no-sources")] = True,
) -> None:
    """Interroga localmente i materiali indicizzati."""
    config = _config()
    _ensure_model(config, profile)
    client = _require_ollama(config)
    if config.embedding_model not in client.models():
        client.ensure_model(config.embedding_model)
    response, hits = answer(question, config, profile)
    console.print(response)
    if sources and hits:
        console.print("\n[dim]Fonti recuperate:[/]")
        for hit in hits:
            console.print(
                f"[dim]- {Path(hit['source']).name}, blocco {hit['chunk_index'] + 1} "
                f"(similarità {hit['score']:.3f})[/]"
            )


@models_app.command("list")
def models_list() -> None:
    config = _config()
    client = _require_ollama(config)
    for model in sorted(client.models()):
        console.print(model)


@models_app.command("install")
def models_install(
    profile: Annotated[str, typer.Argument(help="turbo, standard o quality")] = "standard",
) -> None:
    config = _config()
    model = _ensure_model(config, profile)
    console.print(f"[green]Pronto:[/] {model}")


if __name__ == "__main__":
    app()
