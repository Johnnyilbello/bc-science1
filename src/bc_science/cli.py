from __future__ import annotations

import time
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from .cache import CacheDB
from .config import AppConfig, app_home, resolve_ask_profile
from .documents import iter_source_files
from .hardware import detect_hardware, select_model_plan
from .indexer import ingest as ingest_source
from .model_manager import ensure_model_with_progress
from .ollama_client import ChatResult, OllamaClient
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
    ensure_model_with_progress(client, model, console)
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
    ensure_model_with_progress(client, config.embedding_model, console)
    result = ingest_source(source, config, force=force)
    console.print(
        f"[green]Indicizzazione completata.[/] Trovati {result['files_found']} file, "
        f"indicizzati {result['indexed']}, invariati {result['skipped']}, "
        f"nuovi blocchi {result['chunks']}."
    )


@app.command()
def summarize(
    source: Annotated[Path, typer.Argument(help="PDF, DOCX, TXT, ZIP o cartella")],
    profile: Annotated[
        str,
        typer.Option("--profile", "-p", help="turbo, standard o quality"),
    ] = "standard",
    single: Annotated[
        bool,
        typer.Option("--single/--separate", help="Un unico riassunto o uno per file"),
    ] = True,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="File/cartella di output"),
    ] = None,
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
            title = source.stem if source.is_file() else source.name
            console.print(
                f"Creo un unico riassunto del corso con [cyan]{summarizer.model}[/]."
            )
            console.print(
                "[dim]Lezioni duplicate vengono fuse per argomento; "
                "i risultati gia elaborati vengono letti dalla cache.[/]"
            )

            def show_progress(message: str) -> None:
                console.print(f"[dim]{message}[/]")

            started = time.perf_counter()
            result = summarizer.summarize_course(
                files,
                title=f"{title} - Riassunto completo",
                progress=show_progress,
            )
            elapsed = time.perf_counter() - started

            destination = output or (
                app_home() / "outputs" / f"{title}-riassunto-unico.md"
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(result, encoding="utf-8")

            minutes, seconds = divmod(elapsed, 60)
            avg_tps = (
                summarizer.stats.generated_tokens / summarizer.stats.eval_seconds
                if summarizer.stats.eval_seconds > 0
                else 0.0
            )

            console.print()
            console.print(f"[green]Riassunto unico creato:[/] {destination}")
            console.print(
                "[dim]"
                f"Copertura: {summarizer.stats.source_files} documenti -> "
                f"{summarizer.stats.topic_groups} argomenti · "
                f"{summarizer.stats.generated_calls} generazioni · "
                f"{summarizer.stats.cache_hits} risultati dalla cache"
                "[/]"
            )
            console.print(
                "[dim]"
                f"Tempo totale: {int(minutes)}m {seconds:.1f}s · "
                f"tempo Ollama: {summarizer.stats.ollama_seconds:.1f}s · "
                f"token generati: {summarizer.stats.generated_tokens} · "
                f"media generazione: {avg_tps:.1f} token/s · "
                f"continuazioni anti-troncamento: "
                f"{summarizer.stats.continuation_calls}"
                "[/]"
            )
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
def refine(
    source: Annotated[
        Path | None,
        typer.Option(
            "--input",
            "-i",
            help="Riassunto Markdown da rifinire; se omesso usa l'ultimo riassunto unico",
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="File Markdown di output"),
    ] = None,
    profile: Annotated[
        str,
        typer.Option("--profile", "-p", help="standard o quality"),
    ] = "standard",
) -> None:
    """Rifinisce rapidamente l'ultimo riassunto senza rileggere tutti i PDF."""
    config = _config()
    _ensure_model(config, profile)

    if source is None:
        output_dir = app_home() / "outputs"
        candidates = sorted(
            output_dir.glob("*-riassunto-unico.md"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            console.print(
                "[red]Nessun riassunto unico trovato.[/] "
                "Generane prima uno con 'bc-science summarize ... --single'."
            )
            raise typer.Exit(1)
        source = candidates[0]

    source = source.expanduser().resolve()
    if not source.exists() or source.suffix.lower() != ".md":
        console.print(f"[red]Riassunto non valido:[/] {source}")
        raise typer.Exit(1)

    text = source.read_text(encoding="utf-8")
    title = source.stem
    for suffix in ("-riassunto-unico", "-riassunto-rifinito"):
        if title.endswith(suffix):
            title = title[: -len(suffix)]
            break
    title = f"{title} - Riassunto rifinito"

    destination = output or source.with_name(
        source.stem.removesuffix("-riassunto-unico") + "-riassunto-rifinito.md"
    )
    destination = destination.expanduser().resolve()

    summarizer = Summarizer(config, profile)
    try:
        console.print(f"Rifinisco [cyan]{source.name}[/] con {summarizer.model}.")
        console.print(
            "[dim]Modalita rapida: usa il riassunto esistente come unica fonte; "
            "non rilegge i PDF originali.[/]"
        )

        def show_progress(message: str) -> None:
            console.print(f"[dim]{message}[/]")

        started = time.perf_counter()
        try:
            result = summarizer.refine_summary(
                text,
                title=title,
                progress=show_progress,
            )
        except ValueError as exc:
            console.print(f"[red]{exc}[/]")
            raise typer.Exit(1) from exc
        elapsed = time.perf_counter() - started

        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(result, encoding="utf-8")

        minutes, seconds = divmod(elapsed, 60)
        avg_tps = (
            summarizer.stats.generated_tokens / summarizer.stats.eval_seconds
            if summarizer.stats.eval_seconds > 0
            else 0.0
        )
        console.print()
        console.print(f"[green]Riassunto rifinito creato:[/] {destination}")
        console.print(
            "[dim]"
            f"Tempo totale: {int(minutes)}m {seconds:.1f}s · "
            f"generazioni: {summarizer.stats.generated_calls} · "
            f"cache: {summarizer.stats.cache_hits} · "
            f"token generati: {summarizer.stats.generated_tokens} · "
            f"media: {avg_tps:.1f} token/s · "
            f"retry anti-troncamento: {summarizer.stats.continuation_calls} · "
            f"riparazioni qualita: {summarizer.stats.quality_repairs} · "
            f"avvisi materiale: {summarizer.stats.source_warnings}"
            "[/]"
        )
        console.print(
            "[yellow]Nota:[/] la rifinitura puo correggere struttura e duplicati, "
            "ma non recupera informazioni eventualmente assenti dal riassunto sorgente."
        )
    finally:
        summarizer.close()


@app.command()
def ask(
    question: Annotated[str, typer.Argument(help="Domanda sui materiali indicizzati")],
    profile: Annotated[
        str,
        typer.Option("--profile", "-p", help="auto, turbo, standard o quality"),
    ] = "auto",
    sources: Annotated[bool, typer.Option("--sources/--no-sources")] = True,
    deep: Annotated[
        bool,
        typer.Option("--deep", help="Usa più fonti e una risposta più lunga"),
    ] = False,
) -> None:
    """Interroga localmente i materiali indicizzati."""
    config = _config()
    try:
        selected_profile = resolve_ask_profile(profile, deep=deep)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--profile") from exc

    model = _ensure_model(config, selected_profile)
    client = _require_ollama(config)
    ensure_model_with_progress(client, config.embedding_model, console)

    route = f"{profile}->{selected_profile}" if profile == "auto" else selected_profile
    console.print(
        f"[dim]BC Science · {model} · {route} · "
        f"{'approfondita' if deep else 'rapida'}[/]"
    )
    streamed = False

    def emit(piece: str) -> None:
        nonlocal streamed
        streamed = True
        console.print(piece, end="", markup=False, highlight=False)

    result, hits = answer(
        question,
        config,
        selected_profile,
        deep=deep,
        on_token=emit,
    )
    if streamed:
        console.print()
    elif result.content:
        console.print(result.content)

    console.print(
        "\n[dim]"
        f"Performance: {result.tokens_per_second:.1f} token/s · "
        f"{result.eval_count} token generati · "
        f"{result.prompt_eval_count} token prompt · "
        f"load {result.load_seconds:.2f}s · "
        f"prompt {result.prompt_seconds:.2f}s · "
        f"totale Ollama {result.total_seconds:.2f}s"
        "[/]"
    )
    if result.done_reason == "length":
        console.print(
            "[yellow]Risposta fermata dal limite token. "
            "Riprova con --deep per una risposta più lunga.[/]"
        )

    if sources and hits:
        console.print("\n[dim]Fonti recuperate:[/]")
        for hit in hits:
            console.print(
                f"[dim]- {Path(hit['source']).name}, blocco {hit['chunk_index'] + 1} "
                f"(similarita {hit['score']:.3f})[/]"
            )


@app.command()
def benchmark(
    question: Annotated[str, typer.Argument(help="Domanda usata per confrontare i modelli")],
) -> None:
    """Confronta Turbo e Standard sulla stessa domanda e sulle stesse dispense."""
    config = _config()
    client = _require_ollama(config)
    ensure_model_with_progress(client, config.embedding_model, console)

    profiles = ("turbo", "standard")
    results: list[tuple[str, str, ChatResult, list[dict]]] = []

    for profile in profiles:
        model = _ensure_model(config, profile)
        console.print(f"\n[cyan]Benchmark {profile}: {model}[/]")
        with console.status("Genero risposta di benchmark..."):
            result, hits = answer(
                question,
                config,
                profile,
                deep=False,
                on_token=None,
            )
        results.append((profile, model, result, hits))

    table = Table(title="BC Science - Benchmark locale")
    table.add_column("Profilo")
    table.add_column("Modello")
    table.add_column("Token/s", justify="right")
    table.add_column("Token", justify="right")
    table.add_column("Prompt", justify="right")
    table.add_column("Totale", justify="right")
    table.add_column("Stop")

    for profile, model, result, _hits in results:
        table.add_row(
            profile,
            model,
            f"{result.tokens_per_second:.1f}",
            str(result.eval_count),
            str(result.prompt_eval_count),
            f"{result.total_seconds:.2f}s",
            result.done_reason or "-",
        )
    console.print()
    console.print(table)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    destination = app_home() / "benchmarks" / f"benchmark-{stamp}.md"
    destination.parent.mkdir(parents=True, exist_ok=True)

    sections = [f"# BC Science benchmark\n\nDomanda: {question}\n"]
    for profile, model, result, hits in results:
        sections.append(
            f"\n## {profile.title()} - {model}\n\n"
            f"- Token/s: {result.tokens_per_second:.1f}\n"
            f"- Token generati: {result.eval_count}\n"
            f"- Token prompt: {result.prompt_eval_count}\n"
            f"- Tempo totale Ollama: {result.total_seconds:.2f}s\n"
            f"- Stop: {result.done_reason or '-'}\n\n"
            f"{result.content}\n\n"
            "### Fonti\n"
            + "\n".join(
                f"- {Path(hit['source']).name} "
                f"(similarita {hit['score']:.3f})"
                for hit in hits
            )
            + "\n"
        )

    destination.write_text("\n".join(sections), encoding="utf-8")
    console.print(f"[green]Confronto completo salvato:[/] {destination}")


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
