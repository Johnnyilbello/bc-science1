from __future__ import annotations

from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from .ollama_client import OllamaClient


def ensure_model_with_progress(
    client: OllamaClient,
    model: str,
    console: Console,
) -> bool:
    installed = client.models()
    if model in installed or f"{model}:latest" in installed:
        console.print(f"  [green]gia presente[/]: {model}")
        return False

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    ) as progress:
        task_id = progress.add_task(f"Scarico {model}", total=None)

        def on_progress(payload: dict) -> None:
            status = str(payload.get("status", "download"))
            total = payload.get("total")
            completed = payload.get("completed")

            update: dict = {"description": f"{model}: {status}"}
            if isinstance(total, int) and total > 0:
                update["total"] = total
            if isinstance(completed, int) and completed >= 0:
                update["completed"] = completed
            progress.update(task_id, **update)

        downloaded = client.ensure_model(model, progress_callback=on_progress)

    if downloaded:
        console.print(f"  [green]pronto[/]: {model}")
    return downloaded
