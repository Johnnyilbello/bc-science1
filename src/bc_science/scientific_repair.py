"""Conservative, read-only staging of a verified scientific repair.

Only the observed oxidative-phosphorylation error is supported. This module
cannot modify published summaries, source PDFs, or workspace manifests.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .courses import (
    CourseScan,
    _coverage_map_from_manifest,
    _load_manifest,
    _manifest_baseline_verified,
    _snapshot,
    _summary_sha256,
    audit_course_summary,
    course_audit_json_path,
    course_audit_markdown_path,
    course_output_path,
    course_pdf_path,
    course_study_output_path,
    course_study_pdf_path,
)
from .fast_audit import audit_fast_essential, write_fast_audit
from .finalizer import export_pdf
from .study_editions import FAST_STUDY_VERSION, make_fast_essential
from .structural_checks import find_structural_issues
from .summarizer import _extract_summary_chapters

CHAPTER = "Fosforilazione ossidativa"
REFERENCE_URLS = (
    "https://www.ncbi.nlm.nih.gov/books/NBK26904/",
    "https://www.ncbi.nlm.nih.gov/books/NBK9885/",
)
REPAIRS = (
    (
        "Durante la catena respiratoria, i complessi proteici pompano "
        'attivamente i protoni dallo spazio intermembrana alla matrice '
        '(o viceversa, a seconda della direzione del flusso descritto come "riflusso").',
        "Durante la catena respiratoria, i complessi proteici pompano "
        "i protoni dalla matrice mitocondriale allo spazio intermembrana. "
        "Il riflusso dei protoni verso la matrice avviene attraverso l'ATP sintetasi.",
    ),
    (
        "La catena respiratoria pompa protoni ($H^+$) dallo spazio "
        "intermembrana alla matrice mitocondriale",
        "La catena respiratoria pompa protoni ($H^+$) dalla matrice "
        "mitocondriale allo spazio intermembrana",
    ),
    (
        "L'**ADP** deve essere presente nella matrice per ricevere "
        "i protoni e formare ATP.",
        "L'**ADP** e il fosfato inorganico ($Pi$) sono i substrati "
        "da cui l'ATP sintetasi forma ATP sfruttando l'energia "
        "del flusso protonico verso la matrice.",
    ),
)


def repair_verified_chapter(complete_text: str) -> tuple[str, tuple[int, ...]]:
    """Return amended Complete in memory; fail on any unfamiliar source variant."""
    chapters = dict(_extract_summary_chapters(complete_text))
    if CHAPTER not in chapters:
        raise ValueError("Capitolo Fosforilazione ossidativa non trovato nella Completa.")
    original = chapters[CHAPTER]
    fixed = original
    counts = []
    for previous, revised in REPAIRS:
        occurrences = fixed.count(previous)
        if occurrences < 1:
            raise ValueError(
                "La Completa differisce dal dossier verificato: "
                "nessuna modifica automatica applicabile."
            )
        fixed = fixed.replace(previous, revised)
        counts.append(occurrences)
    remaining = {
        issue.category for issue in find_structural_issues(fixed)
        if issue.category in {"scientific_direction_risk", "scientific_mechanism_risk"}
    }
    if remaining:
        raise ValueError(f"Rischi scientifici residui nel capitolo: {sorted(remaining)}")
    if fixed == original:
        raise ValueError("Nessuna modifica prodotta.")
    if complete_text.count(original) != 1:
        raise ValueError("Il testo originale del capitolo non è univoco.")
    return complete_text.replace(original, fixed, 1), tuple(counts)


def _original_pdf_evidence(scan: CourseScan, manifest: dict) -> list[dict]:
    """Locate real page-level evidence in mapped original PDFs, without OCR."""
    import pymupdf

    mapping = _coverage_map_from_manifest(manifest)
    allowed = {p.resolve() for p in scan.supported_files}
    findings: list[dict] = []
    for relative, chapter in mapping.items():
        path = (scan.path / relative).resolve()
        if chapter != CHAPTER or path not in allowed or path.suffix.casefold() != ".pdf":
            continue
        with pymupdf.open(path) as document:
            for n, page in enumerate(document, 1):
                original = " ".join(page.get_text("text", sort=True).split())
                lowered = original.casefold()
                if all(term in lowered for term in ("proton", "matrice", "atp sintetasi")):
                    pos = lowered.find("proton")
                    findings.append({
                        "document": path.name,
                        "page": n,
                        "excerpt": original[max(0, pos - 170):pos + 520],
                    })
                    break
    if not findings:
        raise ValueError(
            "Nessuna pagina di PDF originale supporta il meccanismo "
            "protoni/matrice/ATP sintetasi: anteprima non generata."
        )
    return findings


def stage_mitochondrial_repair(scan: CourseScan, output_dir: Path) -> Path:
    """Create corrected previews/PDFs and a proposed manifest, never publish.

    No write goes to the course workspace, published output paths or original
    PDFs. A separate, previously nonexistent output directory is required.
    """
    if not scan.name.upper().startswith("FONDAMENTI"):
        raise ValueError("Riparazione limitata a Fondamenti di Biologia e Chimica.")
    root = output_dir.expanduser().resolve()
    if root == scan.path.resolve() or scan.path.resolve() in root.parents:
        raise ValueError("La cartella di anteprima non può trovarsi nelle dispense.")
    canonical = course_output_path(scan).parent.resolve()
    if root == canonical or canonical in root.parents:
        raise ValueError("La cartella di anteprima non può trovarsi negli output pubblicati.")
    if root.exists():
        raise ValueError("Cartella di anteprima già esistente: non sovrascrivo nulla.")

    complete_path = course_output_path(scan)
    manifest = _load_manifest(scan)
    if not complete_path.is_file() or not manifest:
        raise ValueError("Completa e manifest verificati necessari.")
    complete = complete_path.read_text(encoding="utf-8")
    snapshot = _snapshot(scan)
    if (
        not _manifest_baseline_verified(complete, manifest)
        or not audit_course_summary(
            scan, complete, manifest=manifest, snapshot=snapshot,
        ).complete
    ):
        raise ValueError("Manifest o PDF originali cambiati: non procedo.")

    evidence = _original_pdf_evidence(scan, manifest)
    repaired, counts = repair_verified_chapter(complete)
    essential, records = make_fast_essential(
        repaired, title=f"{scan.name} - Riassunto essenziale corretto (ANTEPRIMA)",
    )
    chap = dict(_extract_summary_chapters(essential))[CHAPTER]
    risks = {
        issue.category for issue in find_structural_issues(chap)
        if issue.category in {
            "scientific_direction_risk", "scientific_mechanism_risk",
            "detached_glossary_fragment",
        }
    }
    if risks:
        raise ValueError(f"Essenziale da rivedere, anteprima non pubblicata: {sorted(risks)}")
    report = audit_fast_essential(
        repaired, essential,
        source_documents=len(scan.supported_files),
        source_coverage_complete=True,
    )
    if report.status == "FAIL":
        raise ValueError("Audit della nuova Essenziale in FAIL: " + "; ".join(report.warnings))

    # Materialize only after all source and content checks above have passed.
    root.mkdir(parents=True, exist_ok=False)
    original_dir = root / "backup-originali"
    original_dir.mkdir()
    current_paths = (
        complete_path, course_pdf_path(scan),
        course_study_output_path(scan), course_study_pdf_path(scan),
        course_audit_json_path(scan), course_audit_markdown_path(scan),
    )
    for current in current_paths:
        if current.is_file():
            shutil.copy2(current, original_dir / current.name)

    from .courses import _manifest_path

    shutil.copy2(_manifest_path(scan), original_dir / "manifest.json")
    (root / "riassunto-unico.md").write_text(repaired, encoding="utf-8")
    (root / "riassunto-studio.md").write_text(essential, encoding="utf-8")
    complete_pages = export_pdf(
        repaired, root / "riassunto-unico.pdf", f"{scan.name} - Completa corretta",
    )
    study_pages = export_pdf(
        essential, root / "riassunto-studio.pdf", f"{scan.name} - Essenziale corretta",
    )
    write_fast_audit(report, root)

    # Preserve the original source-file hashes and coverage map. A proposed
    # manifest uses canonical target paths and is NOT written to workspace.
    proposed = json.loads(json.dumps(manifest))
    proposed["summary_sha256"] = _summary_sha256(repaired)
    proposed["pdf"] = {
        "path": str(course_pdf_path(scan)), "pages": complete_pages,
        "summary_sha256": _summary_sha256(repaired),
    }
    proposed["study"] = {
        "path": str(course_study_output_path(scan)),
        "pipeline": FAST_STUDY_VERSION,
        "source_summary_sha256": _summary_sha256(repaired),
        "summary_sha256": _summary_sha256(essential),
        "pdf": {
            "path": str(course_study_pdf_path(scan)), "pages": study_pages,
            "summary_sha256": _summary_sha256(essential),
        },
    }
    (root / "manifest-PROPOSTO.json").write_text(
        json.dumps(proposed, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    notes = {
        "status": "ANTEPRIMA_NON_PUBBLICATA",
        "course": scan.name,
        "chapter": CHAPTER,
        "original_complete_sha256": _summary_sha256(complete),
        "proposed_complete_sha256": _summary_sha256(repaired),
        "proposed_essential_sha256": _summary_sha256(essential),
        "source_fingerprint_unchanged": manifest.get("fingerprint"),
        "replacement_occurrences": counts,
        "chapter_count": len(records),
        "audit_status": report.status,
        "content_review_status": report.content_review_status,
        "pdf_original_evidence": evidence,
        "external_scientific_references": REFERENCE_URLS,
        "publication": "NON ESEGUITA. Verificare e approvare prima di applicare.",
    }
    (root / "riparazione-prove.json").write_text(
        json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return root
