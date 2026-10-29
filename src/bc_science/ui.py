"""Local desktop shell. The CLI and course pipeline remain the application backend."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
from pathlib import Path

from markdown_it import MarkdownIt

from .cache import CacheDB
from .config import AppConfig
from .course_audit import audit_report_is_current
from .courses import (
    _load_manifest,
    _study_is_current,
    course_audit_json_path,
    course_output_path,
    course_pdf_path,
    course_study_output_path,
    course_study_pdf_path,
    read_essential_chapter_previews,
    scan_courses,
    workspace_db_path,
)
from .indexer import QUERY_INSTRUCTION
from .ollama_client import OllamaClient
from .study_editions import (
    FAST_STUDY_VERSION,
    readable_complete_chapter,
    readable_complete_document,
)
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
        path = (
            course_study_output_path(course)
            if mode == "essential" else course_output_path(course)
        )
        complete_path = course_output_path(course)
        if not path.is_file() and (mode != "essential" or not complete_path.is_file()):
            return {"chapters": [], "ready": False}

        manifest = _load_manifest(course) or {}
        complete_legacy = manifest.get("summary_pipeline") != PROMPT_VERSION
        complete_text = (
            complete_path.read_text(encoding="utf-8")
            if complete_path.is_file() else ""
        )

        if path.is_file():
            text = path.read_text(encoding="utf-8")
            chapters = _extract_summary_chapters(text)
            if not chapters:
                chapters = [(course.name, text)]
        else:
            # Do not display the Complete as an Essential: show a clearly
            # marked placeholder until the requested chapter is generated.
            chapters = [
                (
                    title,
                    (f"## {title}\n\n> Ripasso non ancora generato. "
                     "Seleziona Genera questo argomento per prepararlo."),
                )
                for title, _ in _extract_summary_chapters(complete_text)
            ]

        previews = (
            read_essential_chapter_previews(course, complete_text)
            if mode == "essential" and complete_text else {}
        )
        study_current = (
            _study_is_current(course, manifest, complete_text)
            if mode == "essential" and complete_text else False
        )
        fast_current = (
            study_current
            and (manifest.get("study") or {}).get("pipeline") == FAST_STUDY_VERSION
        )
        if fast_current:
            # The unified fast edition supersedes old per-chapter AI previews.
            previews = {}
        entries: list[tuple[str, str, str]] = []
        if mode == "essential":
            seen: set[str] = set()
            for title, body in chapters:
                seen.add(title)
                status = (
                    "updated" if title in previews or study_current
                    else ("previous" if path.is_file() else "pending")
                )
                entries.append((title, previews.get(title, body), status))
            # A partial Essential may omit topics: keep missing topics visible
            # with honest placeholders so single-chapter generation works.
            for title, _source_body in _extract_summary_chapters(complete_text):
                if title not in seen:
                    body = previews.get(
                        title,
                        f"## {title}\n\n> Ripasso non ancora generato. "
                        "Seleziona Genera questo argomento per prepararlo.",
                    )
                    entries.append(
                        (title, body, "updated" if title in previews else "pending")
                    )
        else:
            entries = [
                (
                    title,
                    readable_complete_chapter(title, body),
                    "updated" if not complete_legacy else "previous",
                )
                for title, body in chapters
            ]

        audit_detail = {}
        if mode == "essential" and complete_text and path.is_file():
            audit_file = course_audit_json_path(course)
            current, _status = audit_report_is_current(
                audit_file, complete_text, path.read_text(encoding="utf-8"),
            )
            if current:
                try:
                    data = json.loads(audit_file.read_text(encoding="utf-8"))
                    if "content_review_status" in data:
                        audit_detail = data
                except (OSError, ValueError, TypeError):
                    pass
        reviews = {
            x["title"]: x
            for x in audit_detail.get("chapter_signals", [])
            if isinstance(x, dict) and isinstance(x.get("title"), str)
        }

        return {
            "ready": True,
            "legacy": complete_legacy,
            "quality_status": audit_detail.get(
                "content_review_status", "NON_VALUTATO"
            ),
            "technical_status": audit_detail.get("status", "NON_VALUTATO"),
            "linguistic_status": audit_detail.get(
                "linguistic_status", "NON_VALUTATO"
            ),
            "chapters_with_language_issues": audit_detail.get(
                "chapters_with_language_issues", 0
            ),
            "chapters_needing_review": audit_detail.get(
                "chapters_needing_review", 0
            ),
            "complete_legacy": complete_legacy,
            "chapters": [
                {
                    "title": title,
                    "html": render_markdown(body),
                    "edition_status": status,
                    "quality": reviews.get(title, {}),
                    "terms": list({
                        term.casefold(): term
                        for term in reversed(re.findall(
                            r"\*\*([^*\n]{2,70})\*\*", body,
                        ))
                    }.values())[:6],
                }
                for title, body, status in entries
            ],
        }

    def export(self, name: str, mode: str):
        course = self._course(name)
        if mode == "complete":
            # Present the Complete PDF in the same readable narrative format
            # as the desktop reader; keep the archived verified PDF untouched.
            from .finalizer import export_pdf

            source_path = course_output_path(course)
            if not source_path.is_file():
                raise ValueError("Versione Completa non disponibile.")
            source = source_path.read_text(encoding="utf-8")
            digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]
            path = course_pdf_path(course).with_name(
                f"riassunto-completo-leggibile-{digest}.pdf"
            )
            if not path.is_file():
                formatted = readable_complete_document(
                    source, title=f"{course.name} - Riassunto completo",
                )
                export_pdf(formatted, path, f"{course.name} - Riassunto completo")
        else:
            path = course_study_pdf_path(course)
            if not path.is_file():
                raise ValueError("PDF Essenziale non ancora disponibile.")
        os.startfile(path)

    def model_status(self):
        config = AppConfig.load()
        client = OllamaClient(config.ollama_url)
        online = client.available()
        models = sorted(client.models()) if online else []
        return {"online": online, "model": config.generation_model, "models": models}

    def start_build(
        self, name: str, profile: str = "standard", mode: str = "complete",
        chapter_title: str = "",
    ):
        self._course(name)
        if mode not in {"complete", "essential", "chapter", "review"}:
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
                from .courses import (
                    build_course,
                    build_essential_chapter,
                    build_fast_essential_only,
                    review_essential_chapter,
                )
                from .model_manager import ensure_model_with_progress
                config = AppConfig.load()
                if mode == "essential":
                    self._line(
                        "Essenziale rapido: utilizzo la Completa già verificata. "
                        "Non avvio Ollama né rileggo i PDF."
                    )
                else:
                    self._line(
                        "Preparazione dei modelli locali… "
                        "Il primo download può richiedere tempo."
                    )
                    _ensure_model(config, profile)
                    client = _require_ollama(config)
                    ensure_model_with_progress(
                        client, config.embedding_model, Console(),
                    )
                self._line(
                    "Operazione: solo Essenziale, dalla Completa esistente."
                    if mode == "essential" else (
                        "Operazione: revisione mirata di fedelta e comprensibilita."
                        if mode == "review" else (
                            "Operazione: un solo capitolo Essenziale."
                            if mode == "chapter"
                            else "Operazione: Completa e aggiornamento Essenziale."
                        )
                    )
                )
                if mode == "review":
                    if not chapter_title.strip():
                        raise ValueError("Seleziona un argomento da controllare.")
                    result = review_essential_chapter(
                        self._course(name), config, chapter_title,
                        profile=profile, progress=self._line,
                    )
                    self._line(
                        f"Revisione terminata: {result.state}. "
                        "Lo stato scientifico richiede comunque controllo umano."
                    )
                elif mode == "chapter":
                    if not chapter_title.strip():
                        raise ValueError("Seleziona un argomento da generare.")
                    build_essential_chapter(
                        self._course(name), config, chapter_title,
                        profile=profile, progress=self._line,
                    )
                    self._line("Capitolo richiesto completato.")
                else:
                    builder = build_fast_essential_only if mode == "essential" else build_course
                    result = builder(
                        self._course(name), config,
                        profile=profile, progress=self._line,
                    )
                    self._line(
                        "Operazione terminata. Audit: "
                        + (result.audit_status or "non disponibile")
                    )
                with self._lock:
                    self._job["done"] = True
            except Exception as exc:  # noqa: BLE001 - UI worker: mostra gli errori senza interrompere l'interfaccia
                message = str(exc) or "Preparazione del modello non riuscita. Controlla Ollama."
                self._line(f"ERRORE: {message}")
                with self._lock:
                    self._job["error"] = message
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
        # Keep the desktop activity panel and PowerShell in sync.
        # flush=True is essential when Qwen or OCR is running for several minutes.
        print(f"[BC Science] {text}", flush=True)

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
