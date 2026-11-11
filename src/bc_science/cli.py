from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from .agent_mesh import available_agents, plan_agents, run_agents, write_agent_reports
from .autonomous_claim_audit import run_claim_audit
from .cache import CacheDB
from .clarity import audit_novice_document
from .config import AppConfig, app_home, resolve_ask_profile
from .courses import (
    audit_course,
    build_course,
    course_state,
    inspect_original_evidence,
    scan_courses,
)
from .documents import iter_source_files
from .finalizer import finalize_course_bundle, finalize_file
from .hardware import detect_hardware, select_model_plan
from .indexer import QUERY_INSTRUCTION
from .indexer import ingest as ingest_source
from .model_manager import ensure_model_with_progress
from .ollama_client import ChatResult, OllamaClient
from .qa import answer
from .retrieval_audit import DEFAULT_RETRIEVAL_CASES, evaluate_retrieval
from .scientific_claim_flags import inspect_scientific_claims
from .scientific_patch_drafts import prepare_scientific_correction_drafts
from .scientific_research import (
    ResearchError,
    ScientificResearchBuilder,
    research_state,
)
from .study_preview import preview_essential_editions
from .summarizer import Summarizer
from .universal_audit import verify_courses, write_verification_reports

app = typer.Typer(
    name="bc-science",
    help="Assistente locale per lo studio di qualsiasi materia.",
    no_args_is_help=True,
)
models_app = typer.Typer(help="Gestione modelli locali.")
courses_app = typer.Typer(help="Gestione multi-materia da una cartella radice.")
research_app = typer.Typer(help="Approfondimenti scientifici separati oltre eCampus.")
agents_app = typer.Typer(help="Orchestrazione offline di 100 agenti verificatori specializzati.")
app.add_typer(models_app, name="models")
app.add_typer(courses_app, name="courses")
app.add_typer(research_app, name="research")
app.add_typer(agents_app, name="agents")
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


@agents_app.command("list")
def agents_list() -> None:
    """Elenca i 100 agenti registrati, senza modelli o connessione."""
    table = Table(title="BC Science — 100 verificatori specializzati")
    table.add_column("ID")
    table.add_column("Gruppo")
    table.add_column("Fase")
    table.add_column("Controllo")
    for item in available_agents():
        table.add_row(item.id, item.group, item.phase, item.title)
    console.print(table)


@agents_app.command("plan")
def agents_plan(
    task: Annotated[str, typer.Option("--task", help="ingest, summarize, study o publish")] = "study",
    limit: Annotated[int, typer.Option("--limit", help="Massimo agenti nella selezione")] = 24,
    all_agents: Annotated[bool, typer.Option("--all", help="Seleziona tutti i 100 agenti")] = False,
) -> None:
    """Mostra quali agenti verrebbero eseguiti senza avviarli."""
    try:
        selected = plan_agents(task, limit=limit, all_agents=all_agents)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(2) from exc
    console.print(f"[bold]{len(selected)}/100 agenti[/] per '{task}'")
    for item in selected:
        console.print(f"[dim]{item.id}[/] · {item.title}")


@agents_app.command("run")
def agents_run(
    root: Annotated[Path, typer.Argument(help="Cartella radice contenente le materie")],
    task: Annotated[str, typer.Option("--task", help="ingest, summarize, study o publish")] = "study",
    limit: Annotated[int, typer.Option("--limit", help="Numero di verificatori selezionati")] = 24,
    all_agents: Annotated[bool, typer.Option("--all", help="Esegui tutti i 100 verificatori")] = False,
    strict: Annotated[bool, typer.Option("--strict", help="Codice di errore se non PASS")] = False,
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", help="Directory per report JSON e Markdown"),
    ] = None,
) -> None:
    """Esegue verifiche offline read-only con report e stato INCOMPLETE."""
    try:
        report = run_agents(root, task=task, limit=limit, all_agents=all_agents)
        markdown_path, json_path = write_agent_reports(report, output_dir=output_dir)
    except (ValueError, OSError) as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(2) from exc

    table = Table(title=f"BC Science — Agent Mesh · {report.status}")
    table.add_column("Materia")
    table.add_column("Esito")
    table.add_column("PASS", justify="right")
    table.add_column("WARN", justify="right")
    table.add_column("FAIL", justify="right")
    table.add_column("SKIP", justify="right")
    table.add_column("N/A", justify="right")
    for subject in report.subjects:
        counts = {
            status: sum(item.status == status for item in subject.results)
            for status in ("PASS", "WARN", "FAIL", "SKIP", "N/A")
        }
        table.add_row(
            subject.subject, subject.status,
            *(str(counts[status]) for status in ("PASS", "WARN", "FAIL", "SKIP", "N/A")),
        )
    console.print(table)
    findings = [
        (subject.subject, item)
        for subject in report.subjects
        for item in subject.results
        if item.status in {"FAIL", "WARN"}
    ]
    if findings:
        console.print("[bold]Anomalie da verificare (nessun dato modificato):[/]")
        for subject_name, item in findings[:15]:
            console.print(
                f"  {subject_name} · {item.agent_id} [{item.status}] "
                f"{item.evidence}"
            )
        if len(findings) > 15:
            console.print(
                f"[dim]Altre {len(findings) - 15} anomalie nel report Markdown/JSON.[/]"
            )
    console.print(
        f"[dim]{report.selected_agents}/{report.registered_agents} agenti selezionati; "
        f"report: {markdown_path} · {json_path}[/]"
    )
    console.print(
        "[dim]Controlli strutturali: nessuna certificazione scientifica;"
        " SKIP non equivale a PASS.[/]"
    )
    if strict and report.status != "PASS":
        raise typer.Exit(1)


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


@courses_app.command("verify")
def courses_verify(
    root: Annotated[
        Path,
        typer.Argument(help="Cartella radice con sottocartelle per materia"),
    ],
    strict: Annotated[
        bool,
        typer.Option(
            "--strict/--no-strict",
            help="Esce con errore se una materia è WARN/FAIL o non ci sono materie",
        ),
    ] = False,
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", help="Directory per report Markdown e JSON"),
    ] = None,
) -> None:
    """Verifica offline fonti, matrice documenti/capitoli e stato degli output."""
    try:
        result = verify_courses(root)
    except (ValueError, OSError) as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc

    markdown_path, json_path = write_verification_reports(result, output_dir)
    table = Table(title=f"BC Science — Verifica multi-materia · {result.status}")
    table.add_column("Materia")
    table.add_column("Esito")
    table.add_column("Fonti collegate", justify="right")
    table.add_column("Copertura")
    table.add_column("Studio")
    table.add_column("Audit")
    for subject in result.subjects:
        table.add_row(
            subject.name,
            subject.status,
            f"{subject.mapped_sources}/{subject.source_count}",
            subject.coverage,
            subject.study,
            subject.study_audit,
        )
    console.print(table)
    console.print(f"[dim]Report: {markdown_path} · {json_path}[/]")
    console.print(
        "[dim]Verifica strutturale: non certifica la correttezza scientifica.[/]"
    )
    if strict and result.status != "PASS":
        raise typer.Exit(1)


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


@courses_app.command("dual-agent-autofix")
def courses_dual_agent_autofix(
    root: Annotated[Path, typer.Argument(help="Radice dispense originali")],
    subject: Annotated[str, typer.Option("--subject", help="Materia esatta")],
    chapter: Annotated[str, typer.Option("--chapter", help="Titolo capitolo esatto")],
    edition: Annotated[
        str, typer.Option("--edition", help="Completa, Essenziale o Anteprima"),
    ] = "Completa",
    max_claims: Annotated[
        int, typer.Option("--max-claims", help="Affermazioni da verificare (1-8)"),
    ] = 4,
    local_model: Annotated[
        str, typer.Option("--local-model", help="Diagnostico locale; MiniCPM5-2B di default"),
    ] = "openbmb/minicpm5-2b",
    max_rounds: Annotated[
        int, typer.Option("--max-rounds", help="Iterazioni per proposta (1 o 2)"),
    ] = 2,
    preview_dir: Annotated[
        Path | None, typer.Option("--preview-dir", help="Manifest anteprime autorizzate"),
    ] = None,
    output_dir: Annotated[
        Path | None, typer.Option("--output-dir", help="NUOVA directory di revisione"),
    ] = None,
    allow_cloud_excerpts: Annotated[
        bool,
        typer.Option(
            "--allow-cloud-excerpts",
            help="Consenti di inviare a Nemotron brevi estratti didattici NVIDIA",
        ),
    ] = False,
) -> None:
    """MiniCPM/Qwen locale + Nemotron NVIDIA, proposte isolate non pubblicate."""
    from datetime import datetime, timezone
    from urllib.parse import urlsplit

    from .dual_agent_autofix import (
        NemotronNvidia,
        nvidia_key_from_environment,
        run_dual_repair,
        save_dual_repair,
    )
    from .scientific_patch_drafts import _allowed_documents

    if not allow_cloud_excerpts:
        console.print(
            "[red]Non eseguito: --allow-cloud-excerpts è obbligatorio.[/]"
        )
        console.print(
            "[dim]Nemotron riceverà affermazioni ed estratti ridotti delle "
            "dispense (non PDF completi), dopo consenso esplicito.[/]"
        )
        raise typer.Exit(2)
    if (
        edition not in {"Completa", "Essenziale", "Anteprima"}
        or not subject.strip() or not chapter.strip()
        or not 1 <= max_claims <= 8
        or max_rounds not in {1, 2}
        or (edition == "Anteprima" and preview_dir is None)
    ):
        console.print("[red]Edizione, materia, capitolo o limiti non validi.[/]")
        raise typer.Exit(2)
    config = _config()
    endpoint = urlsplit(config.ollama_url)
    if (
        endpoint.scheme != "http"
        or endpoint.hostname not in {"localhost", "127.0.0.1", "::1"}
        or endpoint.username is not None or endpoint.password is not None
    ):
        console.print("[red]Il modello deve essere eseguito solo via Ollama locale.[/]")
        raise typer.Exit(2)
    client = OllamaClient(config.ollama_url)
    if not client.available():
        console.print("[red]Ollama non disponibile in localhost.[/]")
        raise typer.Exit(2)
    model = local_model
    if not model or len(model) > 128 or any(
        char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:/"
        for char in model
    ):
        console.print("[red]Modello locale non valido.[/]")
        raise typer.Exit(2)
    try:
        installed = client.models()
        if model not in installed and f"{model}:latest" not in installed:
            raise ValueError(f"Modello locale non installato: {model}; usare ollama pull")
        # Only the fixed NVIDIA Developer Program endpoint is allowed.
        reviewer = NemotronNvidia(nvidia_key_from_environment(), max_calls=16)
        scans = [scan for scan in scan_courses(root) if scan.name == subject]
        if len(scans) != 1:
            raise ValueError("Materia assente o non univoca")
        scan = scans[0]
        documents = _allowed_documents(root, preview_dir)
        source = documents.get((subject, edition))
        if source is None or not source.is_file():
            raise ValueError("Documento della materia non disponibile")
        document = source.read_text(encoding="utf-8")
        if output_dir is None:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
            output_dir = (
                app_home() / "reports" / "local-nemotron" / stamp
            )

        def lookup(claim: str) -> list:
            original = inspect_original_evidence(
                scan, chapter,
                edition=(
                    "essential" if edition == "Essenziale" else "complete"
                ),
                claim=claim,
            )
            from .evidence_engine import OriginalEvidence

            return [OriginalEvidence(**item) for item in original["evidence"]]

        def local_chat(messages: list[dict[str, str]]) -> str:
            return client.chat(
                model, messages, num_ctx=min(config.num_ctx, 8192),
                num_predict=900, temperature=0.0, think=False,
            )

        def progress(index: int, total: int, state: str) -> None:
            console.print(f"[dim]{index}/{total} · {state}[/]")

        report = run_dual_repair(
            document, chapter, max_claims=max_claims, max_rounds=max_rounds,
            find_evidence=lookup, local_chat=local_chat, cloud_chat=reviewer,
            progress=progress,
        )
        report.update({
            "subject": subject,
            "edition": edition,
            "model_local": model,
            "model_reviewer": "nvidia/nemotron-3-super-120b-a12b",
            "nemotron_requests": reviewer.used_calls,
            "source_path": str(source),
            "cloud_consent": allow_cloud_excerpts,
            "independent_scientific_verification": False,
        })
        destination = save_dual_repair(
            report, document, output_dir,
            source_root=root,
            published_root=app_home() / "outputs",
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        console.print(f"[red]Review doppio non completato: {exc}[/]")
        raise typer.Exit(2) from exc
    console.print(
        f"[green]Qwen + Nemotron · report:[/] {destination}"
    )
    console.print(
        f"[yellow]Richieste Nemotron: {reviewer.used_calls}. "
        f"Proposte isolate: {len(report['proposals'])}. "
        "Nessuna modifica live, nessuna pubblicazione.[/]"
    )


@courses_app.command("auto-audit-claims")
def courses_auto_audit_claims(
    root: Annotated[Path, typer.Argument(help="Cartella radice delle materie")],
    subject: Annotated[str, typer.Option("--subject", help="Materia esatta")],
    chapter: Annotated[str, typer.Option("--chapter", help="Titolo esatto del capitolo")],
    edition: Annotated[
        str, typer.Option("--edition", help="Completa, Essenziale o Anteprima"),
    ] = "Completa",
    max_claims: Annotated[
        int, typer.Option("--max-claims", help="Affermazioni campione (1-30)"),
    ] = 8,
    model_override: Annotated[
        str | None,
        typer.Option("--model", help="Modello locale Ollama, es. openbmb/minicpm5-2b"),
    ] = None,
    preview_dir: Annotated[
        Path | None,
        typer.Option("--preview-dir", help="Manifest della bozza se edizione Anteprima"),
    ] = None,
    output_dir: Annotated[
        Path | None, typer.Option("--output-dir", help="Nuovo report isolato"),
    ] = None,
) -> None:
    """Esamina le affermazioni con un modello Ollama locale configurabile."""
    from datetime import datetime, timezone
    from urllib.parse import urlsplit

    from .evidence_engine import OriginalEvidence
    from .scientific_patch_drafts import _allowed_documents

    if edition not in {"Completa", "Essenziale", "Anteprima"}:
        console.print("[red]Edizione non valida.[/]")
        raise typer.Exit(2)
    if not 1 <= max_claims <= 30 or not chapter.strip():
        console.print("[red]Capitolo o quantità di affermazioni non validi.[/]")
        raise typer.Exit(2)
    if edition == "Anteprima" and preview_dir is None:
        console.print("[red]Anteprima richiede --preview-dir.[/]")
        raise typer.Exit(2)

    config = _config()
    endpoint = urlsplit(config.ollama_url)
    if (
        endpoint.scheme != "http"
        or endpoint.hostname not in {"localhost", "127.0.0.1", "::1"}
        or endpoint.username is not None or endpoint.password is not None
    ):
        console.print("[red]Audit consentito solo con Ollama in localhost.[/]")
        raise typer.Exit(2)
    client = OllamaClient(config.ollama_url)
    if not client.available():
        console.print("[red]Ollama locale non disponibile. Nessun modello cloud usato.[/]")
        raise typer.Exit(2)
    model = model_override or config.generation_model
    if not model or len(model) > 128 or any(
        char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:/"
        for char in model
    ):
        console.print("[red]Modello locale non valido.[/]")
        raise typer.Exit(2)
    try:
        installed = client.models()
    except (OSError, ValueError) as exc:
        console.print(f"[red]Impossibile controllare Ollama: {exc}[/]")
        raise typer.Exit(2) from exc
    if model not in installed and f"{model}:latest" not in installed:
        console.print(f"[red]Modello locale non installato: {model}[/]")
        raise typer.Exit(2)

    try:
        matches = [scan for scan in scan_courses(root) if scan.name == subject]
        if len(matches) != 1:
            raise ValueError("Materia non trovata in modo univoco")
        scan = matches[0]
        documents = _allowed_documents(root, preview_dir)
        source = documents.get((subject, edition))
        if source is None or not source.is_file():
            raise ValueError("Riassunto non disponibile per questa edizione")
        document = source.read_text(encoding="utf-8")

        def retrieve(claim: str) -> list[OriginalEvidence]:
            source_report = inspect_original_evidence(
                scan, chapter,
                edition="complete" if edition in {"Completa", "Anteprima"} else "essential",
                claim=claim,
            )
            return [
                OriginalEvidence(**item) for item in source_report["evidence"]
            ]

        def query(messages: list[dict[str, str]]) -> str:
            is_critic = (
                bool(messages)
                and "PAROLE_NON_COPERTE_DALLE_CITAZIONI_PRECEDENTI:"
                in messages[-1].get("content", "")
            )
            return client.chat(
                model, messages,
                num_ctx=min(config.num_ctx, 8192),
                num_predict=1100 if is_critic else 500,
                temperature=0.0, think=False,
            )

        def progress(index: int, total: int, state: str) -> None:
            console.print(f"[dim]{index}/{total} · {state}[/]")

        report = run_claim_audit(
            document, chapter, max_claims=max_claims,
            find_evidence=retrieve, chat=query, progress=progress,
        )
        report.update({
            "subject": subject, "edition": edition, "source_path": str(source),
            "model": model, "source_kind": "original_course_cache",
            "independent_scientific_verification": False,
        })
        if output_dir is None:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
            output_dir = app_home() / "reports" / "autonomous-claims" / stamp
        target = output_dir.expanduser().resolve()
        sources = root.expanduser().resolve()
        live = (app_home() / "outputs").resolve()
        preview = preview_dir.expanduser().resolve() if preview_dir else None
        if (
            target == sources or target.is_relative_to(sources)
            or sources.is_relative_to(target)
            or target == live or target.is_relative_to(live)
            or live.is_relative_to(target)
            or (preview is not None and (
                target == preview or target.is_relative_to(preview)
                or preview.is_relative_to(target)
            ))
        ):
            raise ValueError("La destinazione deve essere esterna a fonti e output live")
        target.mkdir(parents=True, exist_ok=False)
        (target / "report.json").write_text(
            json.dumps(report, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
        lines = [
            "# BC Science — audit autonomo SPERIMENTALE", "",
            f"Materia: {subject} · {edition} · {chapter}", "",
            "Verdetti del modello non sono una certificazione scientifica.", "",
        ]
        for item in report["claims"]:
            critic = item.get("independent_challenge") or {}
            lines.extend([
                f"## {item['status']}",
                f"Affermazione: {item['claim']['text']}",
                f"Motivo: {item['rationale']}",
                f"Citazioni validate: {len(item['quotes'])}",
                (
                    "Parafrasi candidata (NON approvata): "
                    f"{bool(critic.get('semantic_support_candidate', False))}"
                ),
                f"Componenti semantiche: {len(critic.get('facets', []))}",
                f"Revisore diagnostica: {critic.get('diagnostic', 'N/A')}",
                f"Lunghezza risposta revisore: {critic.get('response_chars', 0)}",
                "",
            ])
        (target / "report.md").write_text("\n".join(lines), encoding="utf-8")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        console.print(f"[red]Audit autonomo non completato: {exc}[/]")
        raise typer.Exit(2) from exc
    console.print(
        f"[dim]Parafrasi semantiche candidate, non approvate: "
        f"{report['semantic_support_candidates']}[/]"
    )
    console.print(f"[green]Report audit autonomo:[/] {target}")
    console.print(
        "[yellow]Nessun riassunto corretto o pubblicato. "
        "Le prove sono tratte dalle dispense, che possono contenere errori.[/]"
    )


@courses_app.command("draft-scientific-corrections")
def courses_draft_scientific_corrections(
    root: Annotated[Path, typer.Argument(help="Radice delle materie")],
    audit_json: Annotated[
        Path, typer.Option("--audit-json", help="Report inspect-scientific-flags esistente"),
    ],
    output_dir: Annotated[
        Path | None, typer.Option("--output-dir", help="Nuova cartella revisioni isolata"),
    ] = None,
    preview_dir: Annotated[
        Path | None,
        typer.Option("--preview-dir", help="Cartella anteprime autorizzate dal manifest"),
    ] = None,
) -> None:
    """Crea bozze scientifiche con SHA256 verificato; non pubblica nulla."""
    from datetime import datetime, timezone

    if output_dir is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        output_dir = app_home() / "reports" / "scientific-correction-drafts" / stamp
    try:
        report = prepare_scientific_correction_drafts(
            root, audit_json, output_dir, preview_dir=preview_dir,
        )
    except (OSError, ValueError, TypeError, UnicodeError) as exc:
        console.print(f"[red]Impossibile creare bozze: {exc}[/]")
        raise typer.Exit(2) from exc
    counts: dict[str, int] = {}
    for item in report["proposals"]:
        counts[item["state"]] = counts.get(item["state"], 0) + 1
        console.print(
            f"  {item['subject']} · {item['edition']}: "
            f"{item['state']} ({item['reason']})"
        )
    console.print(
        f"[yellow]Bozze da controllare: {counts.get('REVIEW', 0)} · "
        f"Bloccate: {counts.get('BLOCKED', 0)}[/]"
    )
    console.print(f"[green]Report e differenze:[/] {report['report_dir']}")
    console.print(
        "[dim]Nessuna modifica alle dispense, alle due versioni pubblicate, "
        "ai PDF o ai manifest. Non equivale a verifica scientifica.[/]"
    )


@courses_app.command("inspect-scientific-flags")
def courses_inspect_scientific_flags(
    root: Annotated[Path, typer.Argument(help="Cartella con le materie")],
    output_json: Annotated[
        Path | None,
        typer.Option("--output-json", help="Nuovo report JSON ASCII-safe, fuori dagli output live"),
    ] = None,
    preview_dir: Annotated[
        Path | None,
        typer.Option("--preview-dir", help="Includi le bozze da un preview-report.json esistente"),
    ] = None,
) -> None:
    """Segnala un piccolo insieme di anomalie scientifiche note; non corregge."""
    import hashlib
    from dataclasses import asdict
    from datetime import datetime, timezone

    from .courses import course_output_path, course_study_output_path

    records = []
    total_files = 0
    try:
        scans = scan_courses(root)
        preview_paths: dict[str, Path] = {}
        if preview_dir is not None:
            base = preview_dir.expanduser().resolve()
            source = base / "preview-report.json"
            preview_report = json.loads(source.read_text(encoding="utf-8"))
            if (
                not isinstance(preview_report, dict)
                or preview_report.get("schema") != "bc-science-essential-preview-v1"
                or not isinstance(preview_report.get("root"), str)
                or Path(preview_report["root"]).resolve() != root.expanduser().resolve()
                or not isinstance(preview_report.get("report_dir"), str)
                or Path(preview_report["report_dir"]).resolve() != base
                or not isinstance(preview_report.get("subjects"), list)
            ):
                raise ValueError("Manifest anteprime non valido o relativo a materie diverse.")
            subjects = {scan.name.casefold() for scan in scans}
            for item in preview_report["subjects"]:
                if not isinstance(item, dict):
                    raise TypeError("Riga anteprima non valida.")
                subject = item.get("subject")
                filename = item.get("preview_path")
                if not isinstance(subject, str) or subject.casefold() not in subjects:
                    raise ValueError("Materia anteprima sconosciuta.")
                if not filename:
                    continue  # Materia senza bozza: BLOCKED senza file
                if not isinstance(filename, str):
                    raise TypeError("Percorso bozza non valido.")
                draft = Path(filename).resolve()
                if (
                    not draft.is_relative_to(base)
                    or draft.suffix.casefold() != ".md"
                    or not draft.is_file()
                ):
                    raise ValueError("Bozza esterna o mancante: lettura non consentita.")
                if subject.casefold() in preview_paths:
                    raise ValueError("Materia duplicata nel manifest anteprime.")
                preview_paths[subject.casefold()] = draft

        for scan in scans:
            editions = [
                ("Completa", course_output_path(scan)),
                ("Essenziale", course_study_output_path(scan)),
            ]
            draft = preview_paths.get(scan.name.casefold())
            if draft is not None:
                editions.append(("Anteprima", draft))
            for edition, path in editions:
                if not path.is_file():
                    continue
                content = path.read_text(encoding="utf-8")
                total_files += 1
                digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                for finding in inspect_scientific_claims(content):
                    records.append({
                        "subject": scan.name,
                        "edition": edition,
                        "path": str(path),
                        "document_sha256": digest,
                        **asdict(finding),
                    })
    except (ValueError, OSError, UnicodeError, TypeError) as exc:
        console.print(f"[red]Analisi non eseguibile: {exc}[/]")
        raise typer.Exit(2) from exc

    if output_json is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        output_json = app_home() / "reports" / "scientific-flags" / (
            stamp + ".json"
        )
    try:
        target = output_json.expanduser().resolve()
        source_root = root.expanduser().resolve()
        live_root = (app_home() / "outputs").resolve()
        if (
            target == source_root or target.is_relative_to(source_root)
            or target == live_root or target.is_relative_to(live_root)
        ):
            raise ValueError("Il report deve essere fuori dalle dispense e dagli output live.")
        if target.exists():
            raise ValueError("Il report esiste già: sovrascrittura vietata.")
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": "bc-science-known-scientific-flags-v1",
            "scope": "regole note documentate, nessuna certificazione completa",
            "scanned_files": total_files,
            "flags": records,
        }
        with target.open("x", encoding="ascii") as stream:
            stream.write(json.dumps(payload, ensure_ascii=True, indent=2))
            stream.write("\n")
    except (OSError, ValueError) as exc:
        console.print(f"[red]Impossibile salvare report: {exc}[/]")
        raise typer.Exit(2) from exc

    console.print(
        f"[yellow]Possibili anomalie scientifiche documentate: {len(records)}[/]"
        f" · documenti esaminati: {total_files}"
    )
    counts: dict[tuple[str, str], int] = {}
    for row in records:
        key = (row["subject"], row["edition"])
        counts[key] = counts.get(key, 0) + 1
    for (subject, edition), count in sorted(counts.items()):
        console.print(f"  {subject} · {edition}: {count}")
    console.print(f"[green]Report JSON:[/] {target}")
    console.print(
        "[dim]Un risultato senza anomalie non equivale a correttezza scientifica. "
        "Queste sono soltanto regole mirate e documentate. Nessun testo modificato.[/]"
    )


@courses_app.command("inspect-claim")
def courses_inspect_claim(
    root: Annotated[Path, typer.Argument(help="Cartella con le materie")],
    subject: Annotated[str, typer.Option("--subject", help="Nome esatto della materia")],
    chapter: Annotated[str, typer.Option("--chapter", help="Titolo del capitolo")],
    claim: Annotated[str, typer.Option("--claim", help="Affermazione da verificare (testo, non una domanda)")],
    output_json: Annotated[
        Path | None,
        typer.Option("--output-json", help="Salva evidenze complete in JSON UTF-8 ASCII-safe"),
    ] = None,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Non stampa gli estratti originali nella console"),
    ] = False,
) -> None:
    """Ricerca prova originale locale per un'affermazione; non certifica la verità."""
    try:
        scans = scan_courses(root)
        matches = [scan for scan in scans if scan.name.casefold() == subject.casefold()]
        if len(matches) != 1:
            raise ValueError(f"Materia non trovata univocamente: {subject}")
        report = inspect_original_evidence(
            matches[0], chapter, edition="complete", claim=claim,
        )
    except (ValueError, OSError) as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(2) from exc
    if quiet and output_json is None:
        console.print("[red]--quiet richiede --output-json per non perdere i risultati.[/]")
        raise typer.Exit(2)
    if output_json is not None:
        try:
            target = output_json.expanduser().resolve()
            source_root = root.expanduser().resolve()
            live_root = (app_home() / "outputs").resolve()
            if (
                target == source_root or target.is_relative_to(source_root)
                or target == live_root or target.is_relative_to(live_root)
            ):
                raise ValueError(
                    "Il report JSON deve essere fuori dalle fonti e dagli output pubblicati."
                )
            if target.exists():
                raise ValueError("Report JSON già esistente: nessuna sovrascrittura.")
            target.parent.mkdir(parents=True, exist_ok=True)
            pending = target.with_name(target.name + ".pending")
            if pending.exists():
                raise ValueError("File temporaneo già presente: nessuna sovrascrittura.")
            payload = json.dumps(report, ensure_ascii=True, indent=2) + "\n"
            try:
                with pending.open("x", encoding="ascii") as stream:
                    stream.write(payload)
                pending.replace(target)
            finally:
                pending.unlink(missing_ok=True)
        except (OSError, ValueError) as exc:
            console.print(f"[red]Impossibile salvare evidenze JSON: {exc}[/]")
            raise typer.Exit(2) from exc
    if quiet:
        return
    console.print(f"[bold]Affermazione:[/] {report['claim']}")
    console.print(f"[bold]Materia e capitolo:[/] {subject} · {chapter}")
    console.print(f"[bold]Esito ricerca:[/] {report['status']}")
    evidence = report["evidence"]
    if not evidence:
        console.print(
            "[yellow]Nessun passaggio trovato nella cache originale: "
            "non equivale a prova dell'assenza né a conferma dell'errore.[/]"
        )
    for item in evidence:
        place = (
            f"pagina {item['page']}" if item.get("page") is not None
            else f"blocco {item['block']}" if item.get("block") is not None
            else "posizione non disponibile"
        )
        console.print(f"\\n[bold]{Path(item['source_path']).name} · {place}[/]")
        console.print(item["excerpt"])
    console.print(
        "[dim]Risultato di recupero lessicale dalle fonti originali con "
        "hash verificato. Un passaggio simile NON valida la sua correttezza "
        "scientifica né dimostra che supporti l'affermazione.[/]"
    )


@courses_app.command("preview-essential")
def courses_preview_essential(
    root: Annotated[
        Path,
        typer.Argument(help="Cartella radice con le materie"),
    ],
    subject: Annotated[
        str | None,
        typer.Option("--subject", help="Materia singola; omettere per tutte"),
    ] = None,
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", help="Cartella isolata anteprime; mai output live"),
    ] = None,
) -> None:
    """Crea solo bozze estrattive, senza alterare gli output pubblicati."""
    from datetime import datetime, timezone

    if output_dir is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        output_dir = app_home() / "reports" / "essential-preview" / stamp
    try:
        result = preview_essential_editions(
            root, output_dir, subject=subject,
        )
    except (ValueError, OSError) as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(2) from exc

    table = Table(title="BC Science — Anteprime Essenziali NON PUBBLICATE")
    table.add_column("Materia")
    table.add_column("Stato")
    table.add_column("Parole oggi", justify="right")
    table.add_column("Parole bozza", justify="right")
    table.add_column("Compress.", justify="right")
    table.add_column("Numerici?", justify="right")
    table.add_column("Esame?", justify="right")
    for item in result.subjects:
        table.add_row(
            item.subject, item.state,
            str(item.existing_essential_words or "—"),
            str(item.candidate_words or "—"),
            (
                f"{item.candidate_compression_percent:.1f}%"
                if item.candidate_compression_percent is not None else "—"
            ),
            (
                str(item.candidate_missing_numeric)
                if item.candidate_missing_numeric is not None else "—"
            ),
            (
                str(item.candidate_missing_exam)
                if item.candidate_missing_exam is not None else "—"
            ),
        )
    console.print(table)
    console.print(
        f"[dim]Anteprime e diagnostica: {result.report_dir}[/]"
    )
    console.print(
        "[yellow]NON pubblicate. Nessun file live modificato. "
        "Fatti e comprensione da validare manualmente sulle dispense.[/]"
    )


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
        f"nuovi blocchi {result['chunks']}, artefatti rimossi {result['removed']}."
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

    destination = (output_dir or (outputs / "final")).expanduser().resolve()
    courses_dir = outputs / "courses"
    multi_course = source is None and any(courses_dir.glob("*/riassunto-studio.md"))

    if source is None and not multi_course:
        candidates = sorted(
            outputs.glob("*-riassunto-rifinito.md"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            console.print(
                "[red]Nessun output pronto da finalizzare.[/] "
                "Esegui prima 'bc-science courses build <cartella-radice>' "
                "oppure 'bc-science refine'."
            )
            raise typer.Exit(1)
        source = candidates[0]

    if multi_course:
        console.print("Finalizzo [cyan]tutte le materie multi-materia con audit PASS[/].")
    else:
        source = source.expanduser().resolve()
        console.print(f"Finalizzo [cyan]{source.name}[/].")
    console.print(
        "[dim]Il testo eCampus resta invariato; eventuali precisazioni scientifiche "
        "vengono aggiunte come note separate e citate.[/]"
    )

    started = time.perf_counter()
    try:
        if multi_course:
            result = finalize_course_bundle(
                courses_dir,
                destination,
                create_docx=docx,
                create_pdf=pdf,
            )
        else:
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


@app.command("retrieval-audit")
def retrieval_audit(
    limit: Annotated[
        int,
        typer.Option("--limit", "-k", min=1, max=20, help="Numero di blocchi recuperati per domanda"),
    ] = 6,
    strict: Annotated[
        bool,
        typer.Option("--strict", help="Restituisce errore se il retrieval non raggiunge PASS"),
    ] = False,
) -> None:
    """Misura Hit@K, Top-1 e MRR del retrieval sul corpus indicizzato."""
    config = _config()
    client = _require_ollama(config)
    ensure_model_with_progress(client, config.embedding_model, console)

    cases = list(DEFAULT_RETRIEVAL_CASES)
    prompts = [QUERY_INSTRUCTION + case.question for case in cases]
    vectors = client.embed(config.embedding_model, prompts)

    db = CacheDB()
    try:
        hits_by_question = [db.search(vector, limit=limit) for vector in vectors]
    finally:
        db.close()

    report = evaluate_retrieval(cases, hits_by_question)

    table = Table(title=f"BC Science - Retrieval audit · {report['status']}")
    table.add_column("Domanda", overflow="fold")
    table.add_column("Rank", justify="right")
    table.add_column("Prima fonte", overflow="fold")

    for row in report["results"]:
        first_source = row["top_sources"][0] if row["top_sources"] else "-"
        table.add_row(
            row["question"],
            str(row["rank"]) if row["rank"] is not None else "-",
            first_source,
        )
    console.print(table)
    console.print(
        "[bold]"
        f"Hit@{limit}: {report['hit_at_k']:.0%} · "
        f"Top-1: {report['top1']:.0%} · "
        f"MRR: {report['mrr']:.3f} · "
        f"Stato: {report['status']}"
        "[/]"
    )

    stamp = time.strftime("%Y%m%d-%H%M%S")
    directory = app_home() / "benchmarks"
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"retrieval-audit-{stamp}.json"
    md_path = directory / f"retrieval-audit-{stamp}.md"

    payload = {
        "limit": limit,
        **report,
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    lines = [
        "# BC Science - Retrieval audit",
        "",
        f"- Stato: **{report['status']}**",
        f"- Casi: {report['cases']}",
        f"- Hit@{limit}: {report['hit_at_k']:.1%}",
        f"- Top-1: {report['top1']:.1%}",
        f"- MRR: {report['mrr']:.3f}",
        "",
        "| Domanda | Rank | Top fonti |",
        "| --- | ---: | --- |",
    ]
    for row in report["results"]:
        sources = ", ".join(row["top_sources"]) or "-"
        rank = row["rank"] if row["rank"] is not None else "-"
        lines.append(f"| {row['question']} | {rank} | {sources} |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    console.print(f"[dim]Report JSON: {json_path}[/]")
    console.print(f"[dim]Report Markdown: {md_path}[/]")

    if strict and report["status"] != "PASS":
        raise typer.Exit(1)


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
