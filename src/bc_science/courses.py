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
from .course_audit import (
    StudyAuditResult,
    audit_report_is_current,
    audit_study_pair,
    restore_missing_audit_facts,
    write_audit_reports,
)
from .documents import SUPPORTED, file_sha256
from .finalizer import export_pdf
from .indexer import ingest_files
from .summarizer import (
    Summarizer,
    _extract_summary_chapters,
    group_course_files,
    topic_key,
)

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
    "-riassunto-studio",
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
class CourseCoverage:
    complete: bool
    document_set_verified: bool
    source_count: int
    covered_source_count: int
    expected_topics: tuple[str, ...]
    covered_topics: tuple[str, ...]
    missing_topics: tuple[str, ...]
    chapter_titles: tuple[str, ...]
    reason: str


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
    coverage_state: str = "manca"
    pdf_state: str = "manca"
    study_state: str = "manca"
    audit_state: str = "manca"
    existing_summary_path: Path | None = None


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
    coverage_complete: bool = False
    missing_topics: tuple[str, ...] = ()
    pdf_path: Path | None = None
    pdf_pages: int = 0
    pdf_generated: bool = False
    study_path: Path | None = None
    study_pdf_path: Path | None = None
    study_pdf_pages: int = 0
    study_generated: bool = False
    audit_status: str | None = None
    audit_path: Path | None = None


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

    return [
        scan_course(path)
        for path in sorted(base.iterdir(), key=lambda item: item.name.casefold())
        if path.is_dir() and not _is_hidden_or_ignored_dir(path.name)
    ]


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


def course_pdf_path(scan: CourseScan) -> Path:
    return app_home() / "outputs" / "courses" / scan.name / "riassunto-unico.pdf"


def course_study_output_path(scan: CourseScan) -> Path:
    return app_home() / "outputs" / "courses" / scan.name / "riassunto-studio.md"


def course_study_pdf_path(scan: CourseScan) -> Path:
    return app_home() / "outputs" / "courses" / scan.name / "riassunto-studio.pdf"


def course_audit_markdown_path(scan: CourseScan) -> Path:
    return app_home() / "outputs" / "courses" / scan.name / "audit.md"


def course_audit_json_path(scan: CourseScan) -> Path:
    return app_home() / "outputs" / "courses" / scan.name / "audit.json"


def _legacy_summary_path(scan: CourseScan) -> Path | None:
    root = app_home() / "outputs"
    exact = root / f"{scan.name}-riassunto-unico.md"
    if exact.exists():
        return exact

    if not root.exists():
        return None

    wanted = topic_key(scan.name)
    for candidate in root.glob("*-riassunto-unico.md"):
        stem = candidate.stem.removesuffix("-riassunto-unico")
        if topic_key(stem) == wanted:
            return candidate
    return None


def existing_course_summary_path(scan: CourseScan) -> Path | None:
    canonical = course_output_path(scan)
    if canonical.exists():
        return canonical
    return _legacy_summary_path(scan)


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


def _summary_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
    if not isinstance(data, dict) or data.get("schema") not in {1, 2, 3}:
        return None
    return data


def _coverage_map_from_manifest(manifest: dict | None) -> dict[str, str]:
    if not manifest:
        return {}
    rows = manifest.get("coverage")
    if not isinstance(rows, list):
        return {}

    result: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        source = row.get("source")
        chapter = row.get("chapter")
        if isinstance(source, str) and isinstance(chapter, str) and source and chapter:
            result[source] = chapter
    return result


def _relative_source(scan: CourseScan, source: Path | str) -> str:
    path = Path(source).expanduser().resolve()
    return path.relative_to(scan.path).as_posix()


def _chapter_titles(summary_text: str) -> list[str]:
    return [title for title, _chapter in _extract_summary_chapters(summary_text)]


def _full_build_coverage_map(scan: CourseScan, summary_text: str) -> dict[str, str]:
    chapter_titles = _chapter_titles(summary_text)
    chapter_by_key = {topic_key(title): title for title in chapter_titles}
    mapping: dict[str, str] = {}

    for group in group_course_files(list(scan.supported_files)):
        chapter = chapter_by_key.get(topic_key(group.title))
        if chapter is None:
            continue
        for path in group.files:
            mapping[path.relative_to(scan.path).as_posix()] = chapter
    return mapping


def audit_course_summary(
    scan: CourseScan,
    summary_text: str,
    *,
    manifest: dict | None = None,
    coverage_map: dict[str, str] | None = None,
    source_verified: bool = False,
    snapshot: list[dict[str, str | int]] | None = None,
) -> CourseCoverage:
    current_snapshot = snapshot if snapshot is not None else _snapshot(scan)
    current_fingerprint = _fingerprint(current_snapshot)
    current_sources = {str(row["path"]) for row in current_snapshot}
    chapter_titles = _chapter_titles(summary_text)
    chapter_keys = {topic_key(title) for title in chapter_titles}

    mapping = (
        dict(coverage_map)
        if coverage_map is not None
        else _coverage_map_from_manifest(manifest)
    )
    if not mapping:
        mapping = _full_build_coverage_map(scan, summary_text)

    mapped_sources = {
        source
        for source, chapter in mapping.items()
        if source in current_sources and topic_key(chapter) in chapter_keys
    }

    expected_groups = group_course_files(list(scan.supported_files))
    expected_topics = tuple(group.title for group in expected_groups)
    covered_topics: list[str] = []
    missing_topics: list[str] = []
    for group in expected_groups:
        group_sources = {
            path.relative_to(scan.path).as_posix()
            for path in group.files
        }
        if group_sources and group_sources <= mapped_sources:
            covered_topics.append(group.title)
        else:
            missing_topics.append(group.title)

    manifest_verified = False
    if manifest is not None and manifest.get("schema") in {2, 3}:
        manifest_verified = (
            manifest.get("fingerprint") == current_fingerprint
            and manifest.get("summary_sha256") == _summary_sha256(summary_text)
            and set(_coverage_map_from_manifest(manifest)) == current_sources
        )

    document_set_verified = source_verified or manifest_verified
    complete = (
        document_set_verified
        and bool(chapter_titles)
        and len(mapped_sources) == len(current_sources)
        and not missing_topics
    )

    if not chapter_titles:
        reason = "il file non contiene un vero indice/capitoli BC Science"
    elif not document_set_verified:
        reason = "non esiste una prova hash che il riassunto usi tutte le dispense attuali"
    elif missing_topics:
        reason = "mancano argomenti o documenti associati a capitoli"
    elif len(mapped_sources) != len(current_sources):
        reason = "non tutti i documenti correnti risultano rappresentati"
    else:
        reason = "copertura completa verificata"

    return CourseCoverage(
        complete=complete,
        document_set_verified=document_set_verified,
        source_count=len(current_sources),
        covered_source_count=len(mapped_sources),
        expected_topics=expected_topics,
        covered_topics=tuple(covered_topics),
        missing_topics=tuple(missing_topics),
        chapter_titles=tuple(chapter_titles),
        reason=reason,
    )


def _manifest_baseline_verified(summary_text: str, manifest: dict | None) -> bool:
    if not manifest or manifest.get("schema") not in {2, 3}:
        return False
    if manifest.get("summary_sha256") != _summary_sha256(summary_text):
        return False

    files = manifest.get("files")
    if not isinstance(files, list):
        return False
    previous_sources = {
        str(row["path"])
        for row in files
        if isinstance(row, dict) and isinstance(row.get("path"), str)
    }
    mapping = _coverage_map_from_manifest(manifest)
    if set(mapping) != previous_sources:
        return False

    chapter_keys = {topic_key(title) for title in _chapter_titles(summary_text)}
    return bool(chapter_keys) and all(
        topic_key(chapter) in chapter_keys
        for chapter in mapping.values()
    )


def _pdf_is_current(scan: CourseScan, manifest: dict | None, summary_text: str) -> bool:
    pdf = course_pdf_path(scan)
    if not pdf.exists() or not manifest or manifest.get("schema") not in {2, 3}:
        return False
    pdf_info = manifest.get("pdf")
    return (
        isinstance(pdf_info, dict)
        and pdf_info.get("summary_sha256") == _summary_sha256(summary_text)
        and pdf_info.get("path") == str(pdf)
    )


def _study_is_current(
    scan: CourseScan,
    manifest: dict | None,
    summary_text: str,
) -> bool:
    if not manifest or manifest.get("schema") != 3:
        return False
    study = manifest.get("study")
    if not isinstance(study, dict):
        return False

    markdown = course_study_output_path(scan)
    pdf = course_study_pdf_path(scan)
    if not markdown.exists() or not pdf.exists():
        return False

    try:
        study_text = markdown.read_text(encoding="utf-8")
    except OSError:
        return False

    pdf_info = study.get("pdf")
    return (
        study.get("source_summary_sha256") == _summary_sha256(summary_text)
        and study.get("summary_sha256") == _summary_sha256(study_text)
        and study.get("path") == str(markdown)
        and isinstance(pdf_info, dict)
        and pdf_info.get("path") == str(pdf)
        and pdf_info.get("summary_sha256") == _summary_sha256(study_text)
    )


def _write_manifest(
    scan: CourseScan,
    snapshot: list[dict[str, str | int]],
    output_path: Path,
    summary_text: str,
    coverage_map: dict[str, str],
    *,
    pdf_path: Path | None,
    pdf_pages: int,
    study_path: Path | None = None,
    study_text: str | None = None,
    study_pdf_path: Path | None = None,
    study_pdf_pages: int = 0,
) -> None:
    target = _manifest_path(scan)
    target.parent.mkdir(parents=True, exist_ok=True)
    chapter_titles = _chapter_titles(summary_text)
    payload = {
        "schema": 3,
        "course": scan.name,
        "source": str(scan.path),
        "fingerprint": _fingerprint(snapshot),
        "files": snapshot,
        "output": str(output_path),
        "summary_sha256": _summary_sha256(summary_text),
        "chapters": chapter_titles,
        "coverage": [
            {"source": source, "chapter": chapter}
            for source, chapter in sorted(coverage_map.items())
        ],
        "pdf": (
            {
                "path": str(pdf_path),
                "pages": pdf_pages,
                "summary_sha256": _summary_sha256(summary_text),
            }
            if pdf_path is not None
            else None
        ),
        "study": (
            {
                "path": str(study_path),
                "source_summary_sha256": _summary_sha256(summary_text),
                "summary_sha256": _summary_sha256(study_text),
                "pdf": {
                    "path": str(study_pdf_path),
                    "pages": study_pdf_pages,
                    "summary_sha256": _summary_sha256(study_text),
                },
            }
            if (
                study_path is not None
                and study_text is not None
                and study_pdf_path is not None
            )
            else None
        ),
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


def _save_summary(output: Path, summary_text: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_text(summary_text, encoding="utf-8")
    temporary.replace(output)


def _ensure_pdf(
    scan: CourseScan,
    summary_text: str,
    *,
    manifest: dict | None,
) -> tuple[Path, int, bool]:
    pdf = course_pdf_path(scan)
    if _pdf_is_current(scan, manifest, summary_text):
        pdf_info = manifest.get("pdf") if manifest else {}
        pages = int(pdf_info.get("pages", 0)) if isinstance(pdf_info, dict) else 0
        return pdf, pages, False

    pages = export_pdf(
        summary_text,
        pdf,
        f"{scan.name} - Riassunto unico",
    )
    return pdf, pages, True


def _ensure_study(
    scan: CourseScan,
    summary_text: str,
    config: AppConfig,
    profile: str,
    *,
    db_path: Path,
    manifest: dict | None,
    progress: Callable[[str], None] | None,
) -> tuple[Path | None, str | None, Path | None, int, bool]:
    def repair_audit_gaps(study_text: str) -> tuple[str, int]:
        current = study_text
        total_restored = 0
        max_passes = 3

        for pass_number in range(1, max_passes + 1):
            before = audit_study_pair(
                summary_text,
                current,
                source_documents=len(scan.supported_files),
                source_coverage_complete=True,
            )
            if before.status != "FAIL" or not before.missing_facts:
                break

            repaired, restored = restore_missing_audit_facts(
                current,
                before.missing_facts,
            )
            if not restored:
                if progress:
                    progress(
                        "Versione studio: audit ancora FAIL ma nessun altro fatto "
                        "recuperabile automaticamente "
                        f"(copertura {before.weighted_fact_coverage:.1%}, "
                        f"numerici mancanti {before.missing_numeric_facts}, "
                        f"fatti mancanti {len(before.missing_facts)})."
                    )
                break

            after = audit_study_pair(
                summary_text,
                repaired,
                source_documents=len(scan.supported_files),
                source_coverage_complete=True,
            )
            improved = (
                after.weighted_fact_coverage >= before.weighted_fact_coverage
                and after.missing_numeric_facts <= before.missing_numeric_facts
                and len(after.missing_facts) < len(before.missing_facts)
            )
            if not improved:
                break

            current = repaired
            total_restored += restored
            if progress:
                progress(
                    "Versione studio: audit automatico passaggio "
                    f"{pass_number}/{max_passes}, recuperati {restored} fatti "
                    f"({before.status} -> {after.status}; "
                    f"copertura {after.weighted_fact_coverage:.1%}; "
                    f"numerici mancanti {after.missing_numeric_facts}; "
                    f"fatti mancanti {len(after.missing_facts)})."
                )

            if after.status != "FAIL":
                break

        return current, total_restored


    if _study_is_current(scan, manifest, summary_text):
        study_path = course_study_output_path(scan)
        study_text = study_path.read_text(encoding="utf-8")
        repaired_text, restored = repair_audit_gaps(study_text)
        if restored:
            _save_summary(study_path, repaired_text)
            pdf_path = course_study_pdf_path(scan)
            pages = export_pdf(
                repaired_text,
                pdf_path,
                f"{scan.name} - Riassunto studio",
            )
            return study_path, repaired_text, pdf_path, pages, True

        study_info = manifest.get("study") if manifest else {}
        pdf_info = study_info.get("pdf") if isinstance(study_info, dict) else {}
        pages = int(pdf_info.get("pages", 0)) if isinstance(pdf_info, dict) else 0
        return study_path, study_text, course_study_pdf_path(scan), pages, False

    summarizer = Summarizer(config, profile, db_path=db_path)
    try:
        builder = getattr(summarizer, "build_study_summary", None)
        if not callable(builder):
            return None, None, None, 0, False

        checkpoint = workspace_path(scan) / "study.checkpoint.json"
        study_text = builder(
            summary_text,
            title=f"{scan.name} - Riassunto studio",
            progress=progress,
            checkpoint_path=checkpoint,
        )
    finally:
        summarizer.close()

    if not study_text.strip():
        raise ValueError(f"La versione studio di {scan.name} risulta vuota.")

    study_text, _restored = repair_audit_gaps(study_text)
    study_path = course_study_output_path(scan)
    _save_summary(study_path, study_text)
    pdf_path = course_study_pdf_path(scan)
    pages = export_pdf(
        study_text,
        pdf_path,
        f"{scan.name} - Riassunto studio",
    )
    return study_path, study_text, pdf_path, pages, True


def course_state(scan: CourseScan) -> CourseState:
    output = course_output_path(scan)
    existing = existing_course_summary_path(scan)
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
    coverage_state = "manca"
    pdf_state = "manca"
    study_state = "manca"
    audit_state = "manca"

    if not scan.supported_files:
        state = "vuota"
    elif existing is None:
        state = "da creare"
        new_files = len(scan.supported_files)
    elif existing != output:
        state = "da verificare"
        coverage_state = "non verificata"
    else:
        snapshot = _snapshot(scan)
        added_paths, changed_files, removed_files = _source_changes(
            scan,
            snapshot,
            manifest,
        )
        new_files = len(added_paths)
        summary_text = output.read_text(encoding="utf-8")
        coverage = audit_course_summary(
            scan,
            summary_text,
            manifest=manifest,
            snapshot=snapshot,
        )
        coverage_state = "completa" if coverage.complete else "incompleta"

        current = _fingerprint(snapshot)
        if (
            manifest is not None
            and manifest.get("fingerprint") == current
            and coverage.complete
        ):
            state = "pronta"
        else:
            state = "modificata"

        if course_pdf_path(scan).exists():
            pdf_state = (
                "aggiornato"
                if _pdf_is_current(scan, manifest, summary_text)
                else "da aggiornare"
            )

        if callable(getattr(Summarizer, "build_study_summary", None)):
            if _study_is_current(scan, manifest, summary_text):
                study_state = "aggiornato"
            elif course_study_output_path(scan).exists():
                study_state = "da aggiornare"
            else:
                study_state = "manca"
            if state == "pronta" and study_state != "aggiornato":
                state = "modificata"

        study_path = course_study_output_path(scan)
        audit_json = course_audit_json_path(scan)
        if study_path.exists():
            try:
                study_text = study_path.read_text(encoding="utf-8")
            except OSError:
                audit_state = "manca"
            else:
                audit_current, audit_status = audit_report_is_current(
                    audit_json,
                    summary_text,
                    study_text,
                )
                if audit_current and audit_status:
                    audit_state = audit_status
                    if audit_status == "FAIL" and state == "pronta":
                        state = "da rivedere"
                elif audit_json.exists():
                    audit_state = "obsoleto"

    return CourseState(
        scan=scan,
        state=state,
        indexed_documents=indexed_documents,
        output_path=output,
        workspace_path=workspace,
        new_files=new_files,
        changed_files=changed_files,
        removed_files=removed_files,
        coverage_state=coverage_state,
        pdf_state=pdf_state,
        study_state=study_state,
        audit_state=audit_state,
        existing_summary_path=existing,
    )


def _ensure_course_audit_status(
    scan: CourseScan,
    complete_text: str,
    study_text: str,
    *,
    source_coverage_complete: bool,
) -> tuple[str, Path]:
    json_path = course_audit_json_path(scan)
    current, status = audit_report_is_current(
        json_path,
        complete_text,
        study_text,
    )
    if current and status:
        return status, course_audit_markdown_path(scan)

    result = audit_study_pair(
        complete_text,
        study_text,
        source_documents=len(scan.supported_files),
        source_coverage_complete=source_coverage_complete,
    )
    markdown_path, _json_path = write_audit_reports(
        result,
        course_output_path(scan).parent,
    )
    return result.status, markdown_path


def audit_course(scan: CourseScan) -> StudyAuditResult:
    complete_path = course_output_path(scan)
    study_path = course_study_output_path(scan)
    if not complete_path.exists():
        raise ValueError(f"Riassunto completo mancante per {scan.name}.")
    if not study_path.exists():
        raise ValueError(f"Versione studio mancante per {scan.name}.")

    complete_text = complete_path.read_text(encoding="utf-8")
    study_text = study_path.read_text(encoding="utf-8")
    manifest = _load_manifest(scan)
    coverage = audit_course_summary(
        scan,
        complete_text,
        manifest=manifest,
    )
    result = audit_study_pair(
        complete_text,
        study_text,
        source_documents=len(scan.supported_files),
        source_coverage_complete=coverage.complete,
    )
    write_audit_reports(result, complete_path.parent)
    return result


def _full_rebuild(
    scan: CourseScan,
    config: AppConfig,
    profile: str,
    *,
    db_path: Path,
    progress: Callable[[str], None] | None,
) -> tuple[str, dict[str, str], dict[str, int]]:
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

    coverage_map = _full_build_coverage_map(scan, summary)
    return summary, coverage_map, index_result


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
    existing = existing_course_summary_path(scan)

    if existing is not None and existing != output and progress:
        progress(
            f"Riassunto unico precedente rilevato: {existing}. "
            "Manca un manifest verificabile per le dispense correnti: lo ricostruisco."
        )

    if output.exists():
        existing_text = output.read_text(encoding="utf-8")
        existing_coverage = audit_course_summary(
            scan,
            existing_text,
            manifest=manifest,
            snapshot=snapshot,
        )
        if (
            manifest is not None
            and manifest.get("fingerprint") == fingerprint
            and existing_coverage.complete
        ):
            pdf, pages, pdf_generated = _ensure_pdf(
                scan,
                existing_text,
                manifest=manifest,
            )
            coverage_map = _coverage_map_from_manifest(manifest)
            (
                study_path,
                study_text,
                study_pdf,
                study_pages,
                study_generated,
            ) = _ensure_study(
                scan,
                existing_text,
                config,
                profile,
                db_path=workspace_db_path(scan),
                manifest=manifest,
                progress=progress,
            )
            _write_manifest(
                scan,
                snapshot,
                output,
                existing_text,
                coverage_map,
                pdf_path=pdf,
                pdf_pages=pages,
                study_path=study_path,
                study_text=study_text,
                study_pdf_path=study_pdf,
                study_pdf_pages=study_pages,
            )
            if study_text is not None:
                audit_status, audit_path = _ensure_course_audit_status(
                    scan,
                    existing_text,
                    study_text,
                    source_coverage_complete=True,
                )
            else:
                audit_status, audit_path = None, None
            return CourseBuildResult(
                scan=scan,
                state="riutilizzata",
                output_path=output,
                coverage_complete=True,
                pdf_path=pdf,
                pdf_pages=pages,
                pdf_generated=pdf_generated,
                study_path=study_path,
                study_pdf_path=study_pdf,
                study_pdf_pages=study_pages,
                study_generated=study_generated,
                audit_status=audit_status,
                audit_path=audit_path,
            )

    workspace = workspace_path(scan)
    workspace.mkdir(parents=True, exist_ok=True)
    db_path = workspace_db_path(scan)
    added_files, changed_files, removed_files = _source_changes(
        scan,
        snapshot,
        manifest,
    )

    baseline_text = output.read_text(encoding="utf-8") if output.exists() else ""
    incremental_candidate = (
        bool(baseline_text)
        and _manifest_baseline_verified(baseline_text, manifest)
        and bool(added_files)
        and changed_files == 0
        and removed_files == 0
    )

    incremental_files = 0
    updated_chapters = 0
    new_chapters = 0
    fallback = False

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
            try:
                (
                    summary,
                    updated_chapters,
                    new_chapters,
                    new_source_mapping,
                ) = summarizer.incremental_update_course(
                    baseline_text,
                    added_files,
                    title=f"{scan.name} - Riassunto completo",
                    progress=progress,
                )
            except ValueError as exc:
                if progress:
                    progress(
                        f"Incrementale non sicuro ({exc}); eseguo rebuild completo."
                    )
                fallback = True
                summary = ""
                new_source_mapping = {}
        finally:
            summarizer.close()

        incremental_files = len(added_files)
        coverage_map = _coverage_map_from_manifest(manifest)
        for source, chapter in new_source_mapping.items():
            coverage_map[_relative_source(scan, source)] = chapter

        if not fallback:
            coverage = audit_course_summary(
                scan,
                summary,
                coverage_map=coverage_map,
                source_verified=True,
                snapshot=snapshot,
            )
            if not coverage.complete:
                fallback = True
                if progress:
                    missing = ", ".join(coverage.missing_topics[:5]) or coverage.reason
                    progress(
                        "L'aggiornamento incrementale non copre tutte le dispense "
                        f"({missing}); eseguo rebuild completo."
                    )

        if fallback:
            summary, coverage_map, index_result = _full_rebuild(
                scan,
                config,
                profile,
                db_path=db_path,
                progress=progress,
            )
            updated_chapters = 0
            new_chapters = 0
    else:
        if output.exists() and progress:
            current_text = output.read_text(encoding="utf-8")
            current_coverage = audit_course_summary(
                scan,
                current_text,
                manifest=manifest,
                snapshot=snapshot,
            )
            if not current_coverage.complete:
                missing = ", ".join(current_coverage.missing_topics[:5])
                detail = missing or current_coverage.reason
                progress(
                    "Riassunto unico esistente non verificato/completo "
                    f"({detail}); rigenero sui documenti attuali."
                )

        summary, coverage_map, index_result = _full_rebuild(
            scan,
            config,
            profile,
            db_path=db_path,
            progress=progress,
        )

    coverage = audit_course_summary(
        scan,
        summary,
        coverage_map=coverage_map,
        source_verified=True,
        snapshot=snapshot,
    )
    if not coverage.complete:
        missing = ", ".join(coverage.missing_topics) or coverage.reason
        raise ValueError(
            f"Il riassunto unico di {scan.name} non supera il gate di copertura: {missing}."
        )

    _save_summary(output, summary)
    pdf, pages, pdf_generated = _ensure_pdf(
        scan,
        summary,
        manifest=None,
    )
    (
        study_path,
        study_text,
        study_pdf,
        study_pages,
        study_generated,
    ) = _ensure_study(
        scan,
        summary,
        config,
        profile,
        db_path=db_path,
        manifest=None,
        progress=progress,
    )
    _write_manifest(
        scan,
        snapshot,
        output,
        summary,
        coverage_map,
        pdf_path=pdf,
        pdf_pages=pages,
        study_path=study_path,
        study_text=study_text,
        study_pdf_path=study_pdf,
        study_pdf_pages=study_pages,
    )
    if study_text is not None:
        audit_status, audit_path = _ensure_course_audit_status(
            scan,
            summary,
            study_text,
            source_coverage_complete=True,
        )
    else:
        audit_status, audit_path = None, None

    return CourseBuildResult(
        scan=scan,
        state="creata",
        output_path=output,
        indexed=index_result["indexed"],
        reused_index=index_result["skipped"],
        removed_index=index_result["removed"],
        incremental_files=incremental_files,
        updated_chapters=updated_chapters,
        new_chapters=new_chapters,
        fallback_full_rebuild=fallback,
        coverage_complete=True,
        missing_topics=(),
        pdf_path=pdf,
        pdf_pages=pages,
        pdf_generated=pdf_generated,
        study_path=study_path,
        study_pdf_path=study_pdf,
        study_pdf_pages=study_pages,
        study_generated=study_generated,
        audit_status=audit_status,
        audit_path=audit_path,
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
