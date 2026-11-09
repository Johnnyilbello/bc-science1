from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import app_home
from .courses import (
    CourseScan,
    _coverage_map_from_manifest,
    _load_manifest,
    _snapshot,
    audit_course_summary,
    course_output_path,
    course_state,
    scan_courses,
)
from .summarizer import group_course_files, topic_key

REPORT_VERSION = "universal-structural-audit-v1"


@dataclass(frozen=True, slots=True)
class SourceTrace:
    source: str
    sha256: str
    topic: str
    chapter: str | None
    status: str


@dataclass(frozen=True, slots=True)
class CourseVerification:
    name: str
    status: str
    workspace_state: str
    source_count: int
    mapped_sources: int
    coverage: str
    pdf: str
    study: str
    study_audit: str
    issues: tuple[str, ...]
    missing_topics: tuple[str, ...]
    sources: tuple[SourceTrace, ...]


@dataclass(frozen=True, slots=True)
class UniversalVerification:
    version: str
    root: str
    status: str
    subjects: tuple[CourseVerification, ...]


def _verify_subject(scan: CourseScan) -> CourseVerification:
    state = course_state(scan)
    manifest = _load_manifest(scan)
    snapshot = _snapshot(scan)
    mapping = _coverage_map_from_manifest(manifest)
    old_hashes = {
        row["path"]: row["sha256"]
        for row in ((manifest or {}).get("files") or [])
        if isinstance(row, dict)
        and isinstance(row.get("path"), str)
        and isinstance(row.get("sha256"), str)
    }
    topics: dict[str, str] = {}
    for group in group_course_files(list(scan.supported_files)):
        for path in group.files:
            topics[path.relative_to(scan.path).as_posix()] = group.title

    summary = course_output_path(scan)
    coverage = None
    chapter_keys: set[str] = set()
    if summary.is_file():
        summary_text = summary.read_text(encoding="utf-8")
        coverage = audit_course_summary(
            scan,
            summary_text,
            manifest=manifest,
            snapshot=snapshot,
        )
        chapter_keys = {topic_key(title) for title in coverage.chapter_titles}

    traces: list[SourceTrace] = []
    for row in snapshot:
        source = str(row["path"])
        sha = str(row["sha256"])
        chapter = mapping.get(source)
        if chapter is None or topic_key(chapter) not in chapter_keys:
            source_status = "NON_COLLEGATO"
        elif source not in old_hashes:
            source_status = "NUOVO"
        elif sha != old_hashes[source]:
            source_status = "MODIFICATO"
        else:
            source_status = "OK"
        traces.append(
            SourceTrace(
                source=source,
                sha256=sha,
                topic=topics.get(source, ""),
                chapter=chapter,
                status=source_status,
            )
        )

    errors: list[str] = []
    warnings: list[str] = []
    if not snapshot:
        warnings.append("Materia senza documenti supportati")
    elif coverage is None:
        errors.append("Riassunto completo non ancora creato")
    elif not coverage.complete:
        errors.append(f"Copertura delle fonti non verificata: {coverage.reason}")

    if snapshot and coverage is not None:
        if state.pdf_state != "aggiornato":
            warnings.append("PDF completo mancante o da aggiornare")
        if state.study_state != "aggiornato":
            warnings.append("Versione Essenziale mancante o da aggiornare")
        if state.audit_state == "FAIL":
            errors.append("Audit versione studio in FAIL")
        elif state.audit_state != "PASS":
            warnings.append(f"Audit versione studio: {state.audit_state}")

    status = "FAIL" if errors else ("WARN" if warnings else "PASS")
    return CourseVerification(
        name=scan.name,
        status=status,
        workspace_state=state.state,
        source_count=len(snapshot),
        mapped_sources=sum(trace.status == "OK" for trace in traces),
        coverage=("completa" if coverage and coverage.complete else "incompleta"),
        pdf=state.pdf_state,
        study=state.study_state,
        study_audit=state.audit_state,
        issues=tuple(errors + warnings),
        missing_topics=(coverage.missing_topics if coverage else ()),
        sources=tuple(traces),
    )


def verify_courses(root: Path) -> UniversalVerification:
    scans = scan_courses(root)
    subjects = tuple(_verify_subject(scan) for scan in scans)
    if not subjects or any(subject.status == "FAIL" for subject in subjects):
        status = "FAIL"
    elif any(subject.status == "WARN" for subject in subjects):
        status = "WARN"
    else:
        status = "PASS"
    return UniversalVerification(
        version=REPORT_VERSION,
        root=str(root.expanduser().resolve()),
        status=status,
        subjects=subjects,
    )


def write_verification_reports(
    result: UniversalVerification,
    output_dir: Path | None = None,
) -> tuple[Path, Path]:
    destination = output_dir or (app_home() / "reports" / "courses-verify")
    destination.mkdir(parents=True, exist_ok=True)
    json_path = destination / "verification.json"
    md_path = destination / "verification.md"
    json_path.write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# BC Science — Verifica strutturale multi-materia",
        "",
        f"**Esito:** {result.status} · **Schema:** {result.version}",
        "",
        "Questo controllo verifica hash, collegamenti documento→argomento→capitolo",
        "e stato degli output. Non certifica la correttezza scientifica o semantica.",
        "",
        "| Materia | Stato | Fonti collegate | Copertura | PDF | Essenziale | Audit |",
        "| --- | --- | ---: | --- | --- | --- | --- |",
    ]
    for item in result.subjects:
        safe_name = item.name.replace("|", "\\|")
        lines.append(
            f"| {safe_name} | {item.status} | {item.mapped_sources}/{item.source_count} "
            f"| {item.coverage} | {item.pdf} | {item.study} | {item.study_audit} |"
        )
    if not result.subjects:
        lines.append("| Nessuna materia trovata | FAIL | 0/0 | — | — | — | — |")

    for item in result.subjects:
        lines.extend(["", f"## {item.name}", ""])
        for issue in item.issues:
            lines.append(f"- {issue}")
        if not item.issues:
            lines.append("- Nessuna anomalia strutturale rilevata.")
        lines.extend([
            "",
            "| Documento | Argomento | Capitolo | SHA256 attuale | Stato |",
            "| --- | --- | --- | --- | --- |",
        ])
        for source in item.sources:
            cells = (
                source.source, source.topic, source.chapter or "—",
                source.sha256, source.status,
            )
            lines.append("| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path, json_path
