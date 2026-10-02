from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .cache import CacheDB
from .config import AppConfig, app_home
from .documents import SUPPORTED, file_sha256
from .indexer import ingest_files
from .summarizer import Summarizer

IGNORED_DIR_NAMES = {
    ".git",
    ".idea",
    ".vscode",
    "__pycache__",
    "node_modules",
    "outputs",
    "output",
    "final",
    ".venv",
    "venv",
}
IGNORED_FILE_SUFFIXES = {".tmp", ".bak", ".part", ".crdownload"}
GENERATED_MARKERS = (
    "-riassunto-unico",
    "-riassunto-rifinito",
    "-dispensa-finale",
)


@dataclass(frozen=True, slots=True)
class CourseScan:
    name: str
    path: Path
    supported_files: tuple[Path, ...]
    total_files: int
    ignored_files: int
    folders_visited: int


@dataclass(frozen=True, slots=True)
class CourseState:
    scan: CourseScan
    state: str
    indexed_documents: int
    output_path: Path
    workspace_path: Path
    new_files: int = 0
    changed_files: int = 0
    removed_files: int = 0


@dataclass(frozen=True, slots=True)
class CourseBuildResult:
    scan: CourseScan
    state: str
    output_path: Path | None
    indexed: int = 0
    reused_index: int = 0
    removed_index: int = 0
    incremental_files: int = 0
    updated_chapters: int = 0
    new_chapters: int = 0
    fallback_full_rebuild: bool = False


def _is_hidden_or_ignored_dir(name: str) -> bool:
    return name.startswith(".") or name.casefold() in IGNORED_DIR_NAMES


def _is_ignored_file(path: Path) -> bool:
    name = path.name
    lowered = name.casefold()
    if name.startswith((".", "~$")):
        return True
    if path.suffix.casefold() in IGNORED_FILE_SUFFIXES:
        return True
    return any(marker in lowered for marker in GENERATED_MARKERS)


def scan_course(path: Path) -> CourseScan:
    course = path.expanduser().resolve()
    if not course.is_dir():
        raise ValueError(f"Cartella materia non valida: {course}")

    supported: list[Path] = []
    total_files = 0
    ignored_files = 0
    folders_visited = 0

    for current, dirs, files in os.walk(course):
        dirs[:] = sorted(
            name for name in dirs if not _is_hidden_or_ignored_dir(name)
        )
        folders_visited += 1

        current_path = Path(current)
        for filename in sorted(files):
            total_files += 1
            candidate = current_path / filename
            if _is_ignored_file(candidate):
                ignored_files += 1
                continue
            if candidate.suffix.casefold() in SUPPORTED:
                supported.append(candidate.resolve())
            else:
                ignored_files += 1

    return CourseScan(
        name=course.name,
        path=course,
        supported_files=tuple(sorted(supported)),
        total_files=total_files,
        ignored_files=ignored_files,
        folders_visited=folders_visited,
    )


def scan_courses(root: Path) -> list[CourseScan]:
    base = root.expanduser().resolve()
    if not base.is_dir():
        raise ValueError(f"Cartella radice non valida: {base}")

    courses = [
        scan_course(path)
        for path in sorted(base.iterdir(), key=lambda item: item.name.casefold())
        if path.is_dir() and not _is_hidden_or_ignored_dir(path.name)
    ]
    return courses


def _workspace_slug(scan: CourseScan) -> str:
    readable = re.sub(r"[^A-Za-z0-9._-]+", "-", scan.name).strip("-._")
    if not readable:
        readable = "corso"
    digest = hashlib.sha256(str(scan.path).encode("utf-8")).hexdigest()[:10]
    return f"{readable[:60]}-{digest}"


def workspace_path(scan: CourseScan) -> Path:
    return app_home() / "workspaces" / "courses" / _workspace_slug(scan)


def workspace_db_path(scan: CourseScan) -> Path:
    return workspace_path(scan) / "bc_science.db"


def course_output_path(scan: CourseScan) -> Path:
    return app_home() / "outputs" / "courses" / scan.name / "riassunto-unico.md"


def _snapshot(scan: CourseScan) -> list[dict[str, str | int]]:
    rows: list[dict[str, str | int]] = []
    for path in scan.supported_files:
        stat = path.stat()
        rows.append(
            {
                "path": path.relative_to(scan.path).as_posix(),
                "sha256": file_sha256(path),
                "size": stat.st_size,
            }
        )
    return rows


def _fingerprint(snapshot: list[dict[str, str | int]]) -> str:
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _manifest_path(scan: CourseScan) -> Path:
    return workspace_path(scan) / "manifest.json"


def _load_manifest(scan: CourseScan) -> dict | None:
    path = _manifest_path(scan)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("schema") == 1 else None


def _write_manifest(
    scan: CourseScan,
    snapshot: list[dict[str, str | int]],
    output_path: Path,
) -> None:
    target = _manifest_path(scan)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": 1,
        "course": scan.name,
        "source": str(scan.path),
        "fingerprint": _fingerprint(snapshot),
        "files": snapshot,
        "output": str(output_path),
    }
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(target)


def _source_changes(
    scan: CourseScan,
    snapshot: list[dict[str, str | int]],
    manifest: dict | None,
) -> tuple[list[Path], int, int]:
    if manifest is None:
        return list(scan.supported_files), 0, 0

    previous_rows = manifest.get("files")
    previous_files = {
        str(row["path"]): str(row["sha256"])
        for row in previous_rows
        if isinstance(row, dict) and "path" in row and "sha256" in row
    } if isinstance(previous_rows, list) else {}

    current_files = {
        str(row["path"]): str(row["sha256"])
        for row in snapshot
    }
    by_relative = {
        path.relative_to(scan.path).as_posix(): path
        for path in scan.supported_files
    }

    added = [
        by_relative[path]
        for path in sorted(current_files.keys() - previous_files.keys())
    ]
    changed = sum(
        1
        for path in current_files.keys() & previous_files.keys()
        if current_files[path] != previous_files[path]
    )
    removed = len(previous_files.keys() - current_files.keys())
    return added, changed, removed


def course_state(scan: CourseScan) -> CourseState:
    output = course_output_path(scan)
    workspace = workspace_path(scan)
    manifest = _load_manifest(scan)

    indexed_documents = 0
    db_path = workspace_db_path(scan)
    if db_path.exists():
        db = CacheDB(db_path)
        try:
            indexed_documents = db.stats()["documents"]
        finally:
            db.close()

    new_files = 0
    changed_files = 0
    removed_files = 0

    if not scan.supported_files:
        state = "vuota"
    elif manifest is None:
        state = "da creare"
        new_files = len(scan.supported_files)
    else:
        snapshot = _snapshot(scan)
        added_paths, changed_files, removed_files = _source_changes(
            scan,
            snapshot,
            manifest,
        )
        new_files = len(added_paths)

        current = _fingerprint(snapshot)
        if manifest.get("fingerprint") == current and output.exists():
            state = "pronta"
        else:
            state = "modificata"

    return CourseState(
        scan=scan,
        state=state,
        indexed_documents=indexed_documents,
        output_path=output,
        workspace_path=workspace,
        new_files=new_files,
        changed_files=changed_files,
        removed_files=removed_files,
    )


def build_course(
    scan: CourseScan,
    config: AppConfig,
    *,
    profile: str = "standard",
    progress: Callable[[str], None] | None = None,
) -> CourseBuildResult:
    if not scan.supported_files:
        return CourseBuildResult(scan=scan, state="vuota", output_path=None)

    snapshot = _snapshot(scan)
    fingerprint = _fingerprint(snapshot)
    output = course_output_path(scan)
    manifest = _load_manifest(scan)

    if (
        manifest is not None
        and manifest.get("fingerprint") == fingerprint
        and output.exists()
    ):
        return CourseBuildResult(
            scan=scan,
            state="riutilizzata",
            output_path=output,
        )

    workspace = workspace_path(scan)
    workspace.mkdir(parents=True, exist_ok=True)
    db_path = workspace_db_path(scan)
    added_files, changed_files, removed_files = _source_changes(
        scan,
        snapshot,
        manifest,
    )

    incremental_candidate = (
        manifest is not None
        and output.exists()
        and bool(added_files)
        and changed_files == 0
        and removed_files == 0
    )

    if incremental_candidate:
        if progress:
            progress(
                f"Aggiornamento incrementale: analizzo solo {len(added_files)} "
                f"{'nuovo documento' if len(added_files) == 1 else 'nuovi documenti'}."
            )

        index_result = ingest_files(
            added_files,
            config,
            db_path=db_path,
            prune_missing=False,
        )
        summarizer = Summarizer(config, profile, db_path=db_path)
        try:
            existing_summary = output.read_text(encoding="utf-8")
            try:
                summary, updated_chapters, new_chapters = (
                    summarizer.incremental_update_course(
                        existing_summary,
                        added_files,
                        title=f"{scan.name} - Riassunto completo",
                        progress=progress,
                    )
                )
            except ValueError as exc:
                if progress:
                    progress(
                        f"Incrementale non sicuro ({exc}); eseguo rebuild completo."
                    )
                summary = summarizer.summarize_course(
                    list(scan.supported_files),
                    title=f"{scan.name} - Riassunto completo",
                    progress=progress,
                )
                updated_chapters = 0
                new_chapters = 0
                fallback = True
            else:
                fallback = False
        finally:
            summarizer.close()

        if not summary.strip():
            raise ValueError(f"Nessun contenuto riassumibile per {scan.name}.")

        temporary = output.with_name(output.name + ".tmp")
        temporary.write_text(summary, encoding="utf-8")
        temporary.replace(output)
        _write_manifest(scan, snapshot, output)

        return CourseBuildResult(
            scan=scan,
            state="creata",
            output_path=output,
            indexed=index_result["indexed"],
            reused_index=index_result["skipped"],
            removed_index=0,
            incremental_files=len(added_files),
            updated_chapters=updated_chapters,
            new_chapters=new_chapters,
            fallback_full_rebuild=fallback,
        )

    index_result = ingest_files(
        list(scan.supported_files),
        config,
        db_path=db_path,
        prune_missing=True,
    )

    summarizer = Summarizer(config, profile, db_path=db_path)
    try:
        summary = summarizer.summarize_course(
            list(scan.supported_files),
            title=f"{scan.name} - Riassunto completo",
            progress=progress,
        )
    finally:
        summarizer.close()

    if not summary.strip():
        raise ValueError(f"Nessun contenuto riassumibile per {scan.name}.")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_text(summary, encoding="utf-8")
    temporary.replace(output)
    _write_manifest(scan, snapshot, output)

    return CourseBuildResult(
        scan=scan,
        state="creata",
        output_path=output,
        indexed=index_result["indexed"],
        reused_index=index_result["skipped"],
        removed_index=index_result["removed"],
    )


def build_courses(
    root: Path,
    config: AppConfig,
    *,
    profile: str = "standard",
    progress: Callable[[str], None] | None = None,
) -> list[CourseBuildResult]:
    return [
        build_course(scan, config, profile=profile, progress=progress)
        for scan in scan_courses(root)
    ]
