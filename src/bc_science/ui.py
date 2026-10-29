"""Local desktop shell. The CLI and course pipeline remain the application backend."""
from __future__ import annotations

import argparse
import os
import re
import shutil
import tempfile
import threading
from pathlib import Path

from markdown_it import MarkdownIt

from .cache import CacheDB
from .config import AppConfig
from .courses import (
    _load_manifest,
    course_output_path,
    course_pdf_path,
    course_study_output_path,
    course_study_pdf_path,
    scan_courses,
    workspace_db_path,
)
from .indexer import QUERY_INSTRUCTION
from .ollama_client import OllamaClient
from .summarizer import PROMPT_VERSION, _extract_summary_chapters, _summary_system_prompt

DEFAULT_FOLDER = Path.home() / "Desktop" / "SCIENZE MOTORIE 2026"


def render_markdown(text: str) -> str:
    # Source documents are data: raw HTML and embedded remote media are never executed.
    md = MarkdownIt("commonmark", {"html": False, "linkify": False})
    md.disable(["image", "link", "autolink"])
    return md.render(text)


class DesktopAPI:
    def __init__(self, root: Path) -> None:
        self._root = root.expanduser().resolve()
        self._window = None
        self._lock = threading.Lock()
        self._ai_lock = threading.Lock()
        self._job = {"running": False, "lines": [], "error": "", "done": False}

    def _course(self, name: str):
        for course in scan_courses(self._root):
            if course.name == name:
                return course
        raise ValueError("Materia non trovata. Aggiorna la cartella delle dispense.")

    def snapshot(self):
        courses = scan_courses(self._root)
        return {"root": str(self._root), "courses": [
            {"name": c.name, "files": len(c.supported_files),
             "complete": course_output_path(c).is_file(),
             "essential": course_study_output_path(c).is_file()}
            for c in courses]}

    def choose_folder(self):
        import webview
        selected = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if selected:
            candidate = Path(selected[0]).resolve()
            scan_courses(candidate)
            self._root = candidate
        return self.snapshot()

    def documents(self, name: str):
        course = self._course(name)
        return [{"id": p.relative_to(course.path).as_posix(), "name": p.relative_to(course.path).as_posix(), "size": p.stat().st_size} for p in course.supported_files]

    def open_source(self, name: str, filename: str):
        course = self._course(name)
        source = next((p for p in course.supported_files if p.relative_to(course.path).as_posix() == filename), None)
        if source is None:
            raise ValueError("Documento non trovato.")
        os.startfile(source)

    def import_documents(self, name: str):
        import webview
        course = self._course(name)
        selected = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True,
            file_types=("Dispense (*.pdf;*.docx;*.txt;*.md)",))
        copied, duplicates = 0, []
        for value in selected or []:
            source = Path(value)
            if source.suffix.casefold() not in {".pdf", ".docx", ".txt", ".md"}:
                continue
            target = course.path / source.name
            temp_path = None
            try:
                if target.exists():
                    duplicates.append(source.name)
                    continue
                with tempfile.NamedTemporaryFile(dir=course.path, suffix=".tmp", delete=False) as outgoing:
                    temp_path = Path(outgoing.name)
                    with source.open("rb") as incoming:
                        shutil.copyfileobj(incoming, outgoing)
                temp_path.rename(target)
                copied += 1
            except FileExistsError:
                duplicates.append(source.name)
            finally:
                if temp_path is not None:
                    temp_path.unlink(missing_ok=True)

        return {"copied": copied, "duplicates": duplicates}

    def read(self, name: str, mode: str):
        course = self._course(name)
        path = course_study_output_path(course) if mode == "essential" else course_output_path(course)
        if not path.is_file():
            return {"chapters": [], "ready": False}
        text = path.read_text(encoding="utf-8")
        chapters = _extract_summary_chapters(text)
        if not chapters:
            chapters = [(course.name, text)]
        manifest = _load_manifest(course) or {}
        return {"ready": True, "legacy": manifest.get("summary_pipeline") != PROMPT_VERSION, "chapters": [{"title": title, "html": render_markdown(body),
            "terms": list({term.casefold(): term for term in reversed(re.findall(r"\*\*([^*\n]{2,70})\*\*", body))}.values())[:6]}
            for title, body in chapters]}

    def export(self, name: str, mode: str):
        course = self._course(name)
        path = course_study_pdf_path(course) if mode == "essential" else course_pdf_path(course)
        if not path.is_file():
            raise ValueError("PDF non ancora disponibile. Genera prima i riassunti.")
        os.startfile(path)

    def model_status(self):
        config = AppConfig.load()
        client = OllamaClient(config.ollama_url)
        online = client.available()
        models = sorted(client.models()) if online else []
        return {"online": online, "model": config.generation_model, "models": models}

    def start_build(
        self, name: str, profile: str = "standard", mode: str = "complete",
    ):
        self._course(name)
        if mode not in {"complete", "essential"}:
            raise ValueError("Modalita di generazione non valida.")
        if profile not in {"standard", "quality", "turbo"}:
            raise ValueError("Profilo non valido.")
        if not self._ai_lock.acquire(blocking=False):
            raise ValueError("Il modello locale è già impegnato. Attendi il completamento.")
        with self._lock:
            if self._job["running"]:
                self._ai_lock.release()
                raise ValueError("Un’elaborazione è già in corso.")
            self._job = {"running": True, "lines": [], "error": "", "done": False}
        def worker():
            try:
                # Reuse the same model preparation and build operations as the CLI.
                from rich.console import Console

                from .cli import _ensure_model, _require_ollama
                from .courses import build_course, build_essential_only
                from .model_manager import ensure_model_with_progress
                config = AppConfig.load()
                self._line("Preparazione dei modelli locali… Il primo download può richiedere tempo.")
                _ensure_model(config, profile)
                client = _require_ollama(config)
                ensure_model_with_progress(client, config.embedding_model, Console())
                self._line(
                    "Operazione: solo Essenziale, dalla Completa esistente."
                    if mode == "essential"
                    else "Operazione: Completa e aggiornamento Essenziale."
                )
                builder = build_essential_only if mode == "essential" else build_course
                result = builder(
                    self._course(name), config, profile=profile, progress=self._line,
                )
                self._line(
                    "Operazione terminata. Audit: "
                    + (result.audit_status or "non disponibile")
                )
                with self._lock:
                    self._job["done"] = True
            except Exception as exc:  # noqa: BLE001 - UI worker: mostra gli errori senza interrompere l'interfaccia
                with self._lock:
                    self._job["error"] = str(exc) or "Preparazione del modello non riuscita. Controlla Ollama."
            finally:
                with self._lock:
                    self._job["running"] = False
                self._ai_lock.release()
        threading.Thread(target=worker, daemon=True).start()
        return True

    def _line(self, text: str):
        with self._lock:
            self._job["lines"].append(text)
            self._job["lines"] = self._job["lines"][-100:]

    def progress(self):
        with self._lock:
            return {**self._job, "lines": list(self._job["lines"])}

    def create_subject(self, name: str):
        name = name.strip()
        if not name or name.startswith(".") or name.casefold() in {"outputs", "output", "final", "node_modules", "venv"} or len(name) > 80 or any(c in name for c in '<>:"/\\|?*') or name.endswith((".", " ")) or name in {".", ".."} or name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
            raise ValueError("Usa un nome di materia valido, senza simboli riservati.")
        self._root.mkdir(parents=True, exist_ok=True)
        (self._root / name).mkdir(exist_ok=False)
        return True

    def ask(self, name: str, question: str):
        if not self._ai_lock.acquire(blocking=False):
            raise ValueError("Il modello locale è già impegnato. Attendi il completamento.")
        try:
            return self._answer(name, question)
        finally:
            self._ai_lock.release()

    def _answer(self, name: str, question: str):
        question = question.strip()
        if not question or len(question) > 4000:
            raise ValueError("Scrivi una domanda tra 1 e 4000 caratteri.")
        course = self._course(name)
        db_path = workspace_db_path(course)
        if not db_path.is_file():
            raise ValueError("Genera prima i materiali della materia per indicizzare le fonti.")
        config = AppConfig.load()
        client = OllamaClient(config.ollama_url)
        vector = client.embed(config.embedding_model, [QUERY_INSTRUCTION + question])[0]
        db = CacheDB(db_path)
        try:
            hits = db.search(vector, limit=4)
        finally:
            db.close()
        if not hits:
            raise ValueError("Nessuna fonte indicizzata per questa materia.")
        context = "\n\n".join(f"Fonte: {Path(h['source']).name}\n{h['text']}" for h in hits)
        result = client.chat_stream(config.generation_model,
            [{"role": "system", "content": _summary_system_prompt(name)},
             {"role": "user", "content": f"Rispondi solo in base alle fonti. Se mancano informazioni dichiaralo.\nDOMANDA: {question}\nFONTI:\n{context}"}],
            num_ctx=config.num_ctx, num_predict=1500, keep_alive=config.keep_alive)
        text = result.content
        if result.done_reason == "length":
            text += "\n\nLa risposta ha raggiunto il limite: prova una domanda più specifica."
        return {"html": render_markdown(text), "sources": list(dict.fromkeys(Path(h['source']).name for h in hits))}


def main():
    import webview
    parser = argparse.ArgumentParser(description="BC Science · The Cognitive Lab")
    parser.add_argument("--folder", type=Path, default=DEFAULT_FOLDER)
    args = parser.parse_args()
    api = DesktopAPI(args.folder)
    assets = Path(__file__).parent / "desktop" / "index.html"
    window = webview.create_window("BC Science · The Cognitive Lab", str(assets),
        js_api=api, width=1480, height=960, min_size=(760, 600), background_color="#f8fafb")
    api._window = window
    def closing():
        if api.progress()["running"]:
            return window.create_confirmation_dialog("Elaborazione in corso", "Chiudendo interrompi la generazione. Vuoi chiudere?")
    window.events.closing += closing
    webview.start(debug=False)


if __name__ == "__main__":
    main()
