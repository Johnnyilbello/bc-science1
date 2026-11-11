"""Non-destructive preview of shorter Essential editions from verified Complete files.

The preview NEVER publishes or overwrites the existing Essential, Complete, PDF,
manifest or audit. It is a draft requiring human review, not a certificate.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import app_home
from .course_audit import (
    AUDIT_VERSION,
    FAST_ESSENTIAL_AUDIT_VERSION,
    audit_report_is_current,
    audit_study_pair,
)
from .courses import (
    _load_manifest,
    _manifest_baseline_verified,
    _snapshot,
    audit_course_summary,
    course_audit_json_path,
    course_output_path,
    course_study_output_path,
    scan_courses,
)
from .fast_audit import audit_fast_essential
from .study_editions import FAST_STUDY_VERSION, make_fast_essential

_WORD = re.compile(r"\b[\wÀ-ÿ'+-]+\b")


@dataclass(frozen=True, slots=True)
class PreviewItem:
    subject: str
    state: str
    reason: str
    original_words: int
    existing_essential_words: int | None
    existing_audit_status: str | None
    existing_audit_version: str | None
    candidate_words: int | None
    candidate_compression_percent: float | None
    candidate_technical_status: str | None
    candidate_missing_numeric: int | None
    candidate_missing_exam: int | None
    candidate_fact_coverage: float | None
    candidate_chapters: int | None
    preview_path: str | None
    technical_warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PreviewRun:
    schema: str
    created_utc: str
    root: str
    report_dir: str
    subjects: tuple[PreviewItem, ...]


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _words(value: str) -> int:
    return len(_WORD.findall(value))


def _existing_audit(scan, complete: str, study: str | None, manifest: dict):
    if study is None:
        return None, None
    path = course_audit_json_path(scan)
    if not path.is_file():
        return None, None
    info = manifest.get("study")
    pipeline = info.get("pipeline") if isinstance(info, dict) else None
    expected = (
        FAST_ESSENTIAL_AUDIT_VERSION
        if pipeline == FAST_STUDY_VERSION else AUDIT_VERSION
    )
    try:
        current, status = audit_report_is_current(
            path, complete, study, expected_version=expected,
        )
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return "INVALID", None
    if not isinstance(data, dict):
        return "INVALID", None
    return (status if current else "STALE"), str(data.get("audit_version") or "")


def _safe_target(root: Path, output_dir: Path) -> Path:
    target = output_dir.expanduser().resolve()
    sources = root.expanduser().resolve()
    published = (app_home() / "outputs").resolve()
    if (
        target == sources or target.is_relative_to(sources)
        or sources.is_relative_to(target)
        or target == published or target.is_relative_to(published)
    ):
        raise ValueError(
            "Le anteprime devono essere fuori dalle dispense e dagli output pubblicati."
        )
    return target


def preview_essential_editions(
    root: Path,
    output_dir: Path,
    *,
    subject: str | None = None,
) -> PreviewRun:
    """Build only offline draft files under an isolated reports directory."""
    target = _safe_target(root, output_dir)
    scans = scan_courses(root)
    if subject is not None:
        scans = [scan for scan in scans if scan.name.casefold() == subject.casefold()]
        if not scans:
            raise ValueError(f"Materia non trovata: {subject}")
    if not scans:
        raise ValueError("Nessuna materia disponibile")

    # Never overwrite a previous diagnostic run.
    if target.exists() and any(target.iterdir()):
        raise ValueError("Directory anteprime già occupata: scegliere una nuova destinazione.")
    target.mkdir(parents=True, exist_ok=True)
    items: list[PreviewItem] = []

    for scan in scans:
        complete_path = course_output_path(scan)
        manifest = _load_manifest(scan)
        if not complete_path.is_file() or not isinstance(manifest, dict):
            items.append(PreviewItem(
                scan.name, "BLOCKED", "Completa o manifest mancante",
                0, None, None, None, None, None, None, None, None, None,
                None, None, (),
            ))
            continue
        complete = complete_path.read_text(encoding="utf-8")
        snapshot = _snapshot(scan)
        verified = (
            _manifest_baseline_verified(complete, manifest)
            and audit_course_summary(
                scan, complete, manifest=manifest, snapshot=snapshot,
            ).complete
        )
        if not verified:
            items.append(PreviewItem(
                scan.name, "BLOCKED", "Completa non verificata sulle fonti correnti",
                _words(complete), None, None, None, None, None,
                None, None, None, None, None, None, (),
            ))
            continue

        study_path = course_study_output_path(scan)
        study = study_path.read_text(encoding="utf-8") if study_path.is_file() else None
        audit_status, audit_version = _existing_audit(scan, complete, study, manifest)
        existing_words = _words(study) if study is not None else None

        try:
            candidate, chapters = make_fast_essential(
                complete, title=f"{scan.name} - Anteprima Essenziale (NON PUBBLICATA)",
            )
            technical = audit_fast_essential(
                complete, candidate,
                source_documents=len(scan.supported_files),
                source_coverage_complete=True,
            )
            facts = audit_study_pair(
                complete, candidate,
                source_documents=len(scan.supported_files),
                source_coverage_complete=True,
            )
        except (ValueError, TypeError, OSError) as exc:
            items.append(PreviewItem(
                scan.name, "BLOCKED", f"Anteprima non valida: {type(exc).__name__}: {exc}",
                _words(complete), existing_words, audit_status, audit_version,
                None, None, None, None, None, None, None, None, (),
            ))
            continue

        # A technical PASS only means automated checks passed, not scientific approval.
        state = "REVIEW" if technical.status != "FAIL" and (
            facts.missing_numeric_facts == 0
        ) else "BLOCKED"
        digest = hashlib.sha256(str(scan.path).encode("utf-8")).hexdigest()[:10]
        slug = re.sub(r"[^a-z0-9]+", "-", scan.name.casefold()).strip("-")[:45]
        preview_path = target / f"{slug}-{digest}-anteprima.md"
        preview_path.write_text(candidate, encoding="utf-8")

        items.append(PreviewItem(
            subject=scan.name,
            state=state,
            reason=(
                "Da controllare manualmente; nessun documento pubblicato"
                if state == "REVIEW" else
                "Gate numerico/tecnico non superato; nessun documento pubblicato"
            ),
            original_words=_words(complete),
            existing_essential_words=existing_words,
            existing_audit_status=audit_status,
            existing_audit_version=audit_version,
            candidate_words=_words(candidate),
            candidate_compression_percent=technical.compression_percent,
            candidate_technical_status=technical.status,
            candidate_missing_numeric=facts.missing_numeric_facts,
            candidate_missing_exam=facts.missing_exam_facts,
            candidate_fact_coverage=facts.weighted_fact_coverage,
            candidate_chapters=len(chapters),
            preview_path=str(preview_path),
            technical_warnings=tuple(technical.warnings),
        ))

    report = PreviewRun(
        schema="bc-science-essential-preview-v1",
        created_utc=datetime.now(timezone.utc).isoformat(),
        root=str(root.expanduser().resolve()),
        report_dir=str(target),
        subjects=tuple(items),
    )
    (target / "preview-report.json").write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# BC Science — Anteprime Essenziali (NON PUBBLICATE)",
        "",
        "File generati esclusivamente in questa cartella report. ",
        "Nessuna modifica alle dispense originali, Completa, Essenziale, PDF o manifest.",
        "Un PASS tecnico e una metrica di copertura lessicale non sono una",
        "certificazione della correttezza scientifica o della fedeltà semantica.",
        "Una sintesi breve può omettere intenzionalmente dettagli secondari.",
        "",
    ]
    for item in items:
        lines.extend([
            f"## {item.subject} — {item.state}",
            "",
            f"- Motivo: {item.reason}",
            f"- Parole Completa: {item.original_words}",
            f"- Parole Essenziale pubblicata: {item.existing_essential_words}",
            f"- Audit pubblicato: {item.existing_audit_status} ({item.existing_audit_version})",
            f"- Parole anteprima: {item.candidate_words}",
            f"- Compressione anteprima: {item.candidate_compression_percent}%",
            f"- Gate tecnico anteprima: {item.candidate_technical_status}",
            f"- Fatti numerici potenzialmente omessi: {item.candidate_missing_numeric}",
            f"- Punti d'esame potenzialmente omessi: {item.candidate_missing_exam}",
            f"- Copertura lessicale stimata: {item.candidate_fact_coverage}",
            f"- Capitoli: {item.candidate_chapters}",
            f"- File bozza: {item.preview_path}",
            "",
        ])
    (target / "preview-report.md").write_text("\n".join(lines), encoding="utf-8")
    return report
