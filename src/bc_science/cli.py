from __future__ import annotations

import time
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from .cache import CacheDB
from .clarity import audit_novice_document
from .config import AppConfig, app_home, resolve_ask_profile
from .courses import audit_course, build_course, course_state, scan_courses
from .documents import iter_source_files
from .finalizer import finalize_file
from .hardware import detect_hardware, select_model_plan
from .indexer import ingest as ingest_source
from .model_manager import ensure_model_with_progress
from .ollama_client import ChatResult, OllamaClient
from .qa import answer
from .scientific_research import (
    ResearchError,
    ScientificResearchBuilder,
    research_state,
)
from .summarizer import Summarizer

app = typer.Typer(
    name="bc-science",
    help="Assistente locale per lo studio di Scienze Motorie eCampus.",
    no_args_is_help=True,
)
models_app = typer.Typer(help="Gestione modelli locali.")
courses_app = typer.Typer(help="Gestione multi-materia da una cartella radice.")
research_app = typer.Typer(help="Approfondimenti scientifici separati oltre eCampus.")
app.add_typer(models_app, name="models")
app.add_typer(courses_app, name="courses")
app.add_typer(research_app, name="research")
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


@research_app.command("configure")
def research_configure(
    email: Annotated[
        str | None,
        typer.Option(
            "--email",
            help="Email usata nelle richieste NCBI E-utilities",
        ),
    ] = None,
    api_key: Annotated[
        str | None,
        typer.Option("--api-key", help="API key NCBI facoltativa"),
    ] = None,
    enabled: Annotated[
        bool,
        typer.Option("--enable/--disable", help="Aggiornamento automatico dopo courses build"),
    ] = True,
) -> None:
    """Configura la ricerca PubMed/NCBI per gli approfondimenti oltre eCampus."""
    config = _config()

    if email is not None:
        email = email.strip()
        if "@" not in email or " " in email:
            console.print("[red]Email NCBI non valida.[/]")
            raise typer.Exit(1)
        config.ncbi_email = email

    if enabled and not config.ncbi_email:
        console.print(
            "[red]Serve una email per usare le E-utilities NCBI.[/] "
            "Esegui: bc-science research configure --email nome@example.com"
        )
        raise typer.Exit(1)

    if api_key is not None:
        config.ncbi_api_key = api_key.strip()

    config.scientific_research_enabled = enabled
    path = config.save()

    state = "[green]ATTIVA[/]" if enabled else "[yellow]DISATTIVA[/]"
    console.print(f"Ricerca scientifica automatica: {state}")
    if config.ncbi_email:
        console.print(f"Email NCBI: {config.ncbi_email}")
    console.print(f"[dim]Configurazione: {path}[/]")


@research_app.command("status")
def research_status(
    root: Annotated[
        Path,
        typer.Argument(help="Cartella radice con una sottocartella per materia"),
    ],
    profile: Annotated[
        str,
        typer.Option("--profile", "-p", help="standard o quality"),
    ] = "standard",
) -> None:
    """Mostra quali approfondimenti scientifici sono pronti o da aggiornare."""
    config = _config()
    try:
        scans = scan_courses(root)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc

    table = Table(title=f"BC Science - Ricerca scientifica · {root.expanduser().resolve()}")
    table.add_column("Materia")
    table.add_column("Stato")

    for scan in scans:
        state = research_state(scan, config, profile=profile)
        style = {
            "pronta": "green",
            "da aggiornare": "yellow",
            "non configurata": "cyan",
            "manca riassunto": "dim",
        }.get(state, "white")
        table.add_row(scan.name, f"[{style}]{state}[/]")

    console.print(table)


@research_app.command("build")
def research_build(
    root: Annotated[
        Path,
        typer.Argument(help="Cartella radice con una sottocartella per materia"),
    ],
    profile: Annotated[
        str,
        typer.Option("--profile", "-p", help="standard o quality"),
    ] = "standard",
    max_sources: Annotated[
        int | None,
        typer.Option(
            "--max-sources",
            min=1,
            max=5,
            help="Fonti PubMed massime per capitolo",
        ),
    ] = None,
) -> None:
    """Crea un approfondimento scientifico separato per ogni materia."""
    config = _config()
    if not config.ncbi_email:
        console.print(
            "[red]Ricerca non configurata.[/] "
            "Esegui prima 'bc-science research configure --email nome@example.com'."
        )
        raise typer.Exit(1)

    try:
        scans = scan_courses(root)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc

    _ensure_model(config, profile)
    builder = ScientificResearchBuilder(config, profile=profile)

    created = 0
    reused = 0
    missing = 0
    for index, scan in enumerate(scans, start=1):
        console.print()
        console.print(f"[bold][{index}/{len(scans)}] {scan.name}[/]")

        def show_progress(message: str, *, course_name: str = scan.name) -> None:
            console.print(f"[dim]{course_name}: {message}[/]")

        try:
            result = builder.build_course(
                scan,
                max_sources=max_sources,
                progress=show_progress,
            )
        except ResearchError as exc:
            console.print(f"[red]Ricerca fallita per {scan.name}:[/] {exc}")
            continue

        if result.state == "riutilizzata":
            reused += 1
            console.print(f"[green]Approfondimento già aggiornato:[/] {result.output_path}")
        elif result.state == "manca riassunto":
            missing += 1
            console.print("[yellow]Riassunto eCampus non trovato: materia saltata.[/]")
        else:
            created += 1
            console.print(f"[green]Approfondimento creato:[/] {result.output_path}")
            console.print(
                f"[dim]{result.chapters} capitoli · {result.sources} fonti PubMed[/]"
            )

    console.print()
    console.print(
        "[green]Ricerca scientifica completata.[/] "
        f"{created} create/aggiornate · {reused} riutilizzate · {missing} senza riassunto"
    )


@courses_app.command("list")
def courses_list(
    root: Annotated[
        Path,
        typer.Argument(help="Cartella radice con una sottocartella per materia"),
    ],
) -> None:
    """Scansiona velocemente le materie senza usare Ollama."""
    try:
        scans = scan_courses(root)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc

    table = Table(title=f"BC Science - Materie · {root.expanduser().resolve()}")
    table.add_column("Materia")
    table.add_column("Cartelle", justify="right")
    table.add_column("File", justify="right")
    table.add_column("Supportati", justify="right")
    table.add_column("Ignorati", justify="right")

    for scan in scans:
        table.add_row(
            scan.name,
            str(scan.folders_visited),
            str(scan.total_files),
            str(len(scan.supported_files)),
            str(scan.ignored_files),
        )

    console.print(table)
    console.print(
        f"[dim]{len(scans)} materie · "
        f"{sum(len(scan.supported_files) for scan in scans)} documenti supportati[/]"
    )


@courses_app.command("status")
def courses_status(
    root: Annotated[
        Path,
        typer.Argument(help="Cartella radice con una sottocartella per materia"),
    ],
) -> None:
    """Mostra cosa è pronto, modificato o ancora da generare."""
    try:
        states = [course_state(scan) for scan in scan_courses(root)]
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc

    table = Table(title=f"BC Science - Stato materie · {root.expanduser().resolve()}")
    table.add_column("Materia")
    table.add_column("Stato")
    table.add_column("Documenti", justify="right")
    table.add_column("Indicizzati", justify="right")
    table.add_column("Delta")
    table.add_column("Copertura")
    table.add_column("PDF")
    table.add_column("Studio")
    table.add_column("Audit")
    table.add_column("Output")

    state_style = {
        "pronta": "green",
        "modificata": "yellow",
        "da rivedere": "red",
        "da verificare": "yellow",
        "da creare": "cyan",
        "vuota": "dim",
    }
    for item in states:
        style = state_style.get(item.state, "white")
        delta = (
            f"+{item.new_files} ~{item.changed_files} -{item.removed_files}"
            if item.new_files or item.changed_files or item.removed_files
            else "—"
        )
        table.add_row(
            item.scan.name,
            f"[{style}]{item.state}[/]",
            str(len(item.scan.supported_files)),
            str(item.indexed_documents),
            delta,
            item.coverage_state,
            item.pdf_state,
            item.study_state,
            item.audit_state,
            str(item.existing_summary_path) if item.existing_summary_path else "—",
        )

    console.print(table)


@courses_app.command("audit")
def courses_audit(
    root: Annotated[
        Path,
        typer.Argument(help="Cartella radice con una sottocartella per materia"),
    ],
    strict: Annotated[
        bool,
        typer.Option(
            "--strict/--no-strict",
            help="Restituisce errore se almeno una materia è FAIL",
        ),
    ] = False,
) -> None:
    """Misura copertura fatti, compressione, ridondanza e chiarezza della versione studio."""
    try:
        scans = scan_courses(root)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc

    table = Table(title=f"BC Science - Audit studio · {root.expanduser().resolve()}")
    table.add_column("Materia")
    table.add_column("Stato")
    table.add_column("Fatti", justify="right")
    table.add_column("Fact coverage", justify="right")
    table.add_column("Compressione", justify="right")
    table.add_column("Duplicati", justify="right")
    table.add_column("Chiarezza", justify="right")
    table.add_column("Warning", justify="right")

    failures = 0
    missing = 0
    for scan in scans:
        try:
            result = audit_course(scan)
        except ValueError as exc:
            missing += 1
            table.add_row(
                scan.name,
                "[dim]N/D[/]",
                "—",
                "—",
                "—",
                "—",
                "—",
                str(exc),
            )
            continue

        if result.status == "FAIL":
            failures += 1
            status = "[red]FAIL[/]"
        elif result.status == "WARN":
            status = "[yellow]WARN[/]"
        else:
            status = "[green]PASS[/]"

        table.add_row(
            scan.name,
            status,
            f"{result.covered_facts}/{result.source_facts}",
            f"{result.weighted_fact_coverage:.1%}",
            f"{result.compression_percent:.1f}%",
            f"{result.duplicate_rate:.1%}",
            f"{result.clarity_score:.0f}/100",
            str(len(result.warnings)),
        )

    console.print(table)
    console.print(
        "[dim]Report completi: "
        "%LOCALAPPDATA%\\BCScience\\outputs\\courses\\<MATERIA>\\audit.md "
        "e audit.json[/]"
    )
    if strict and failures:
        console.print(f"[red]{failures} materie in FAIL.[/]")
        raise typer.Exit(1)
    if missing:
        console.print(f"[yellow]{missing} materie senza output auditabile.[/]")


@courses_app.command("build")
def courses_build(
    root: Annotated[
        Path,
        typer.Argument(help="Cartella radice con una sottocartella per materia"),
    ],
    profile: Annotated[
        str,
        typer.Option("--profile", "-p", help="standard o quality"),
    ] = "standard",
) -> None:
    """Genera o aggiorna un riassunto unico per ogni materia necessaria."""
    try:
        scans = scan_courses(root)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc

    supported_total = sum(len(scan.supported_files) for scan in scans)
    ignored_total = sum(scan.ignored_files for scan in scans)
    console.print(
        f"[bold]Preflight:[/] {len(scans)} materie · "
        f"{supported_total} documenti supportati · "
        f"{ignored_total} file ignorati"
    )
    if not scans or supported_total == 0:
        console.print("[red]Nessun documento supportato trovato nelle materie.[/]")
        raise typer.Exit(1)

    config = _config()
    _ensure_model(config, profile)
    client = _require_ollama(config)
    ensure_model_with_progress(client, config.embedding_model, console)

    created = 0
    reused = 0
    empty = 0
    for index, scan in enumerate(scans, start=1):
        console.print()
        console.print(
            f"[bold][{index}/{len(scans)}] {scan.name}[/] · "
            f"{len(scan.supported_files)} documenti"
        )

        def show_progress(message: str, *, course_name: str = scan.name) -> None:
            console.print(f"[dim]{course_name}: {message}[/]")

        try:
            result = build_course(
                scan,
                config,
                profile=profile,
                progress=show_progress,
            )
        except ValueError as exc:
            console.print(f"[red]{exc}[/]")
            raise typer.Exit(1) from exc

        if result.state == "riutilizzata":
            reused += 1
            console.print(f"[green]Riassunto unico verificato:[/] {result.output_path}")
            if result.pdf_generated:
                console.print(
                    f"[green]PDF unico generato:[/] {result.pdf_path} "
                    f"({result.pdf_pages} pagine)"
                )
            elif result.pdf_path is not None:
                console.print(
                    f"[dim]PDF unico già aggiornato: {result.pdf_path} "
                    f"({result.pdf_pages} pagine)[/]"
                )
            if result.study_generated and result.study_pdf_path is not None:
                console.print(
                    f"[green]Versione studio generata:[/] {result.study_pdf_path} "
                    f"({result.study_pdf_pages} pagine)"
                )
            elif result.study_pdf_path is not None:
                console.print(
                    f"[dim]Versione studio già aggiornata: {result.study_pdf_path} "
                    f"({result.study_pdf_pages} pagine)[/]"
                )
            if result.audit_status:
                style = "green" if result.audit_status == "PASS" else (
                    "yellow" if result.audit_status == "WARN" else "red"
                )
                console.print(
                    f"[{style}]Audit studio: {result.audit_status}[/] · {result.audit_path}"
                )
        elif result.state == "vuota":
            empty += 1
            console.print("[yellow]Nessun documento supportato: materia saltata.[/]")
        else:
            created += 1
            console.print(f"[green]Riassunto unico creato:[/] {result.output_path}")
            console.print(
                "[dim]"
                f"Indice: {result.indexed} nuovi/modificati · "
                f"{result.reused_index} invariati · "
                f"{result.removed_index} rimossi"
                "[/]"
            )
            if result.incremental_files:
                if result.fallback_full_rebuild:
                    console.print(
                        "[yellow]Incrementale:[/] "
                        f"{result.incremental_files} nuovi documenti rilevati, "
                        "ma il merge non ha superato il gate: rebuild completo eseguito."
                    )
                else:
                    console.print(
                        "[green]Incrementale:[/] "
                        f"analizzati solo {result.incremental_files} nuovi documenti · "
                        f"{result.updated_chapters} capitoli aggiornati · "
                        f"{result.new_chapters} capitoli nuovi"
                    )
            if result.coverage_complete:
                console.print("[green]Copertura:[/] tutti i documenti attuali sono rappresentati.")
            if result.pdf_path is not None:
                console.print(
                    f"[green]PDF unico:[/] {result.pdf_path} "
                    f"({result.pdf_pages} pagine)"
                )
            if result.study_pdf_path is not None:
                console.print(
                    f"[green]PDF studio:[/] {result.study_pdf_path} "
                    f"({result.study_pdf_pages} pagine)"
                )
            if result.audit_status:
                style = "green" if result.audit_status == "PASS" else (
                    "yellow" if result.audit_status == "WARN" else "red"
                )
                console.print(
                    f"[{style}]Audit studio: {result.audit_status}[/] · {result.audit_path}"
                )

        if config.scientific_research_enabled and result.output_path is not None:
            console.print("[dim]Aggiorno l'approfondimento scientifico separato...[/]")
            try:
                research_result = ScientificResearchBuilder(
                    config,
                    profile=profile,
                ).build_course(
                    scan,
                    progress=show_progress,
                )
            except (ResearchError, ValueError) as exc:
                console.print(
                    "[yellow]Approfondimento scientifico non aggiornato:[/] "
                    f"{exc}. Il riassunto eCampus resta valido."
                )
            else:
                if research_result.state == "riutilizzata":
                    console.print("[dim]Approfondimento scientifico già aggiornato.[/]")
                elif research_result.output_path is not None:
                    console.print(
                        f"[green]Approfondimento scientifico:[/] "
                        f"{research_result.output_path}"
                    )

    console.print()
    console.print(
        "[green]Multi-materia completato.[/] "
        f"{created} create/aggiornate · {reused} riutilizzate · {empty} vuote"
    )


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
    checkpoint_path = destination.with_name(destination.name + ".checkpoint.json")

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
                checkpoint_path=checkpoint_path,
            )
        except ValueError as exc:
            console.print(f"[red]{exc}[/]")
            if checkpoint_path.exists():
                console.print(
                    "[yellow]Checkpoint conservato:[/] "
                    f"{checkpoint_path}\n"
                    "Rilancia 'bc-science refine' per riprendere dai capitoli gia completati."
                )
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
        novice_avg = (
            summarizer.stats.novice_score_total / summarizer.stats.novice_chapters
            if summarizer.stats.novice_chapters
            else 0.0
        )
        console.print(
            "[dim]"
            f"Tempo totale: {int(minutes)}m {seconds:.1f}s · "
            f"generazioni: {summarizer.stats.generated_calls} · "
            f"cache: {summarizer.stats.cache_hits} · "
            f"token generati: {summarizer.stats.generated_tokens} · "
            f"media: {avg_tps:.1f} token/s · "
            f"retry anti-troncamento: {summarizer.stats.continuation_calls} · "
            f"riparazioni qualita: {summarizer.stats.quality_repairs} · "
            f"riparazioni principiante: {summarizer.stats.novice_repairs} · "
            f"fix layout: {summarizer.stats.novice_layout_fixes} · "
            f"chiarezza media: {novice_avg:.0f}/100 · "
            f"avvisi materiale: {summarizer.stats.source_warnings} · "
            f"fix struttura: {summarizer.stats.structural_fixes}"
            "[/]"
        )
        console.print(
            "[yellow]Nota:[/] la rifinitura puo correggere struttura e duplicati, "
            "ma non recupera informazioni eventualmente assenti dal riassunto sorgente."
        )
    finally:
        summarizer.close()


@app.command()
def clarity(
    source: Annotated[
        Path | None,
        typer.Option(
            "--input",
            "-i",
            help="Riassunto rifinito da controllare; se omesso usa l'ultimo disponibile",
        ),
    ] = None,
) -> None:
    """Misura quanto il riassunto e comprensibile per un principiante assoluto."""
    outputs = app_home() / "outputs"
    if source is None:
        candidates = sorted(
            outputs.glob("*-riassunto-rifinito.md"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            console.print(
                "[red]Nessun riassunto rifinito trovato.[/] "
                "Esegui prima 'bc-science refine'."
            )
            raise typer.Exit(1)
        source = candidates[0]

    source = source.expanduser().resolve()
    if not source.exists() or source.suffix.lower() != ".md":
        console.print(f"[red]Riassunto non valido:[/] {source}")
        raise typer.Exit(1)

    passed, results = audit_novice_document(source.read_text(encoding="utf-8"))

    table = Table(title=f"BC Science - Chiarezza principiante · {source.name}")
    table.add_column("Capitolo")
    table.add_column("Score", justify="right")
    table.add_column("Parole/frase", justify="right")
    table.add_column("Paragrafo max", justify="right")
    table.add_column("Stato")

    total = 0
    for title, audit in results:
        metrics = audit.metrics
        total += metrics.score
        status = "[green]PASS[/]" if audit.passed else "[red]FAIL[/]"
        if audit.issues:
            status += " · " + "; ".join(audit.issues)
        table.add_row(
            title,
            f"{metrics.score}/100",
            f"{metrics.average_sentence_words:.1f}",
            str(metrics.max_paragraph_words),
            status,
        )

    console.print(table)
    average = total / len(results) if results else 0.0
    console.print(
        f"[{'green' if passed else 'red'}]"
        f"{'PASS' if passed else 'FAIL'}[/] · "
        f"chiarezza media {average:.0f}/100 · "
        f"{sum(1 for _title, audit in results if audit.passed)}/{len(results)} sezioni conformi"
    )
    if not passed:
        raise typer.Exit(1)


@app.command()
def finalize(
    source: Annotated[
        Path | None,
        typer.Option(
            "--input",
            "-i",
            help="Riassunto rifinito Markdown; se omesso usa l'ultimo disponibile",
        ),
    ] = None,
    output_dir: Annotated[
        Path | None,
        typer.Option(
            "--output-dir",
            "-o",
            help="Cartella di destinazione; default: outputs/final",
        ),
    ] = None,
    pdf: Annotated[
        bool,
        typer.Option("--pdf/--no-pdf", help="Genera il PDF finale"),
    ] = True,
    docx: Annotated[
        bool,
        typer.Option("--docx/--no-docx", help="Genera anche il DOCX modificabile"),
    ] = True,
) -> None:
    """Crea la dispensa finale da un riassunto rifinito, senza rileggere i PDF."""
    outputs = app_home() / "outputs"

    if source is None:
        candidates = sorted(
            outputs.glob("*-riassunto-rifinito.md"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            console.print(
                "[red]Nessun riassunto rifinito trovato.[/] "
                "Esegui prima 'bc-science refine'."
            )
            raise typer.Exit(1)
        source = candidates[0]

    source = source.expanduser().resolve()
    destination = (output_dir or (outputs / "final")).expanduser().resolve()

    console.print(f"Finalizzo [cyan]{source.name}[/].")
    console.print(
        "[dim]Il testo eCampus resta invariato; eventuali precisazioni scientifiche "
        "vengono aggiunte come note separate e citate.[/]"
    )

    started = time.perf_counter()
    try:
        result = finalize_file(
            source,
            destination,
            create_docx=docx,
            create_pdf=pdf,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    elapsed = time.perf_counter() - started

    console.print()
    console.print(f"[green]Dispensa finale creata:[/] {result.markdown_path}")
    if result.docx_path:
        console.print(f"[green]DOCX:[/] {result.docx_path}")
    if result.pdf_path:
        console.print(
            f"[green]PDF:[/] {result.pdf_path} "
            f"[dim]({result.pdf_pages} pagine)[/]"
        )
    console.print(
        "[dim]"
        f"Tempo: {elapsed:.1f}s · "
        f"note scientifiche: {result.scientific_notes} · "
        f"fix editoriali/organizzativi: {result.organization_fixes}"
        "[/]"
    )
    console.print(
        "[yellow]Nota:[/] finalize non sostituisce il testo delle dispense: "
        "le precisazioni scientifiche restano visivamente separate."
    )


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
