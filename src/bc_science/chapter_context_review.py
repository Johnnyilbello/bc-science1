"""V9 read-only whole-chapter triage and a narrowly scoped grammar preview.

This is not semantic entailment, automatic deduplication, or scientific approval.
All chapter reports and original-document excerpts are for human review only.
Never alters input summaries, PDFs, manifests, or published course files.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

from .autopilot_runner import _validated_manifest
from .courses import CourseScan, _fingerprint, _snapshot
from .fact_repair_preview import (
    CASES,
    _canonical_with_offsets,
    _exact_statement_exists,
    _source_fact_evidence,
    _unique_study_span,
)
from .fast_audit import _ordered_title_match, _title_spelling_key, audit_fast_essential
from .italian_proofreading import grammar_findings
from .quality_signals import _lines
from .summarizer import _extract_summary_chapters

VERSION = "whole-chapter-review-v9-20261103"


def _exact_duplicates(chapter: str, *, max_examples: int = 5) -> dict:
    """Report *identical* whole statements, not loosely similar claims.

    Do not delete duplicates automatically: repeated examples can be useful
    pedagogically, even when the exact wording is duplicated.
    """
    groups: dict[str, list[str]] = defaultdict(list)
    for statement in _lines(chapter):
        canonical = _canonical_with_offsets(statement)[0].strip()
        if len(canonical) < 45 or len(canonical.split()) < 7:
            continue
        groups[canonical].append(statement)
    duplicates = [item for item in groups.values() if len(item) > 1]
    duplicates.sort(key=lambda items: (-len(items), items[0].casefold()))
    return {
        "duplicate_groups_total": len(duplicates),
        "excess_exact_occurrences_total": sum(len(items) - 1 for items in duplicates),
        "examples": [
            {"text": items[0][:600], "occurrences": len(items),
             "status": "EXACT_DUPLICATE_REVIEW_ONLY"}
            for items in duplicates[:max_examples]
        ],
    }


def _coverage_in_other_statements(chapter: str, *, kind: str, target: str) -> list[str]:
    """Conservative context-specific lexical patterns, not scientific proof.

    Require a single non-target sentence to contain a meaningful group of
    related concepts. This is a review signal, not certification that all
    details from the original PDF have been preserved.
    """
    matches = []
    target_normalized = _canonical_with_offsets(target)[0].strip()
    for statement in _lines(chapter):
        normalized = _canonical_with_offsets(statement)[0].strip()
        if target_normalized in normalized:
            continue
        if kind == "krebs":
            yes = (
                "anaplerotich" in normalized
                and "intermed" in normalized
                and any(
                    term in normalized for term in (
                        "reintrodu", "riprist", "riforn", "sottratt", "biosintetic",
                    )
                )
            )
        elif kind == "fosfolipidi":
            yes = (
                "test" in normalized and "polar" in normalized
                and "cod" in normalized and "apolar" in normalized
                and "acqua" in normalized and "intern" in normalized
                and any(term in normalized for term in ("membran", "doppio strato"))
            )
        else:
            raise ValueError("Regola di concetto sconosciuta.")
        if yes and normalized not in {
            _canonical_with_offsets(item)[0].strip() for item in matches
        }:
            matches.append(statement[:650])
    return matches[:5]


def _grammar_preview(
    study: str, complete: str, chapter: str,
) -> tuple[str, dict]:
    """Only change 'rientrano'->'rientrino' in one validated Krebs definition."""
    case = CASES[0]
    complete_body = dict(_extract_summary_chapters(complete)).get(case.title)
    if not complete_body or not _exact_statement_exists(
        complete_body, case.complete_anchor,
    ):
        return study, {
            "status": "BLOCKED_COMPLETE_ANCHOR_MISSING",
            "before": None, "after": None,
        }
    span = _unique_study_span(study, chapter, case.old)
    if span is None:
        return study, {
            "status": "BLOCKED_ESSENTIAL_ANCHOR_NOT_UNIQUE",
            "before": None, "after": None,
        }
    start, stop = span
    segment = study[start:stop]
    if segment.count("rientrano") != 1:
        return study, {
            "status": "BLOCKED_GRAMMAR_TARGET_NOT_UNIQUE",
            "before": None, "after": None,
        }
    revised = segment.replace("rientrano", "rientrino", 1)
    proposal = study[:start] + revised + study[stop:]
    if (
        proposal[:start] != study[:start]
        or proposal[start + len(revised):] != study[stop:]
        or proposal[:start] + segment + proposal[start + len(revised):] != study
    ):
        raise ValueError("Anteprima grammaticale fuori dall'ambito ammesso.")
    old_chapters = _extract_summary_chapters(study)
    new_chapters = _extract_summary_chapters(proposal)
    if (
        [name for name, _ in old_chapters] != [name for name, _ in new_chapters]
        or any(
            before != after for (name, before), (_, after) in
            zip(old_chapters, new_chapters, strict=True)
            if _title_spelling_key(name) != _title_spelling_key(case.title)
        )
    ):
        raise ValueError("Modifica imprevedibile fuori dal capitolo Krebs.")
    number_pattern = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?")
    if number_pattern.findall(study) != number_pattern.findall(proposal):
        raise ValueError("Numeri di esame modificati.")
    return proposal, {
        "status": "GRAMMAR_CANDIDATE_UNVERIFIED",
        "before": segment[:700],
        "after": revised[:700],
        "scope": "ONE_LITERAL_TOKEN_IN_ONE_CHAPTER",
        "scientifically_verified": False,
    }


def run_whole_chapter_review(
    scan: CourseScan, complete_file: Path, study_file: Path,
    output_dir: Path,
) -> Path:
    """Read all matching chapters; write only to a fresh, separate preview."""
    complete_file = complete_file.expanduser().resolve()
    study_file = study_file.expanduser().resolve()
    output = output_dir.expanduser().resolve()
    source_root = scan.path.resolve()
    if (
        complete_file == study_file
        or not complete_file.is_file() or not study_file.is_file()
        or output.exists()
        or output == source_root or source_root in output.parents
        or output in {complete_file.parent, study_file.parent}
        or complete_file.parent in output.parents
        or study_file.parent in output.parents
    ):
        raise ValueError("Percorsi input/anteprima non validi, interni o già esistenti.")
    original_complete = complete_file.read_bytes()
    original_study = study_file.read_bytes()
    complete = original_complete.decode("utf-8-sig").replace("\r\n", "\n")
    study = original_study.decode("utf-8-sig").replace("\r\n", "\n")
    manifest, mapping = _validated_manifest(scan, complete)
    complete_sections = _extract_summary_chapters(complete)
    study_sections = _extract_summary_chapters(study)
    if not _ordered_title_match(
        [title for title, _ in complete_sections],
        [title for title, _ in study_sections],
    )[0]:
        raise ValueError("Capitoli della Completa e dell'Essenziale incompatibili.")

    chapters = []
    for (title, _full), (_target_title, chapter) in zip(
        complete_sections, study_sections, strict=True,
    ):
        duplicate = _exact_duplicates(chapter)
        grammar = grammar_findings(chapter, limit=10)
        chapters.append({
            "title": title,
            "exact_duplicates": duplicate,
            "language_heuristic": [item.to_dict() for item in grammar],
            "language_findings_sampled": len(grammar),
        })

    by_title = {
        _title_spelling_key(title): chapter for title, chapter in study_sections
    }
    targeted = []
    for case, kind in zip(CASES, ("krebs", "fosfolipidi"), strict=True):
        chapter = by_title.get(_title_spelling_key(case.title), "")
        matches = _coverage_in_other_statements(
            chapter, kind=kind, target=case.old,
        )
        evidence = _source_fact_evidence(scan, mapping, case)
        targeted.append({
            "title": case.title,
            "concept": kind,
            "status": (
                "POSSIBLY_COVERED_ELSEWHERE_REVIEW"
                if matches else "INSUFFICIENT_CONTEXT_FOR_AUTOMATIC_ADDITION"
            ),
            "supporting_study_statements": matches,
            "mapped_original_context": evidence,
            "scientifically_verified": False,
            "scientific_addition": "NOT_PROPOSED",
        })

    candidate, grammar_proposal = _grammar_preview(
        study, complete, by_title.get(_title_spelling_key(CASES[0].title), ""),
    )
    # Never weaken pre-existing audit failures; no new quantities, chapter edits
    # outside the grammatical token, or scientific additions are possible.
    before = audit_fast_essential(complete, study)
    after = audit_fast_essential(complete, candidate)
    if after.status == "FAIL" and before.status != "FAIL":
        raise ValueError("Anteprima introduce un nuovo FAIL.")
    if (
        original_complete != complete_file.read_bytes()
        or original_study != study_file.read_bytes()
        or _fingerprint(_snapshot(scan)) != manifest["fingerprint"]
    ):
        raise ValueError("Input o originali cambiati durante la revisione.")

    report = {
        "version": VERSION,
        "status": "PREVIEW_UNPUBLISHED",
        "scientifically_verified": False,
        "model_used": False,
        "chapters_checked": len(chapters),
        "source_fingerprint": manifest["fingerprint"],
        "complete_sha256": hashlib.sha256(original_complete).hexdigest(),
        "essential_sha256": hashlib.sha256(original_study).hexdigest(),
        "exact_duplicate_groups_total": sum(
            item["exact_duplicates"]["duplicate_groups_total"] for item in chapters
        ),
        "excess_exact_occurrences_total": sum(
            item["exact_duplicates"]["excess_exact_occurrences_total"]
            for item in chapters
        ),
        "before_audit": before.status,
        "after_audit": after.status,
        "before_compression_percent": before.compression_percent,
        "after_compression_percent": after.compression_percent,
        "chapters": chapters,
        "targeted_concepts": targeted,
        "grammar_proposal": grammar_proposal,
        "scientific_additions": 0,
        "automatic_deletions": 0,
    }
    dossier = [
        "# BC Science · Revisione del contesto capitolo — V9",
        "",
        "**NON PUBBLICATO. Nessuna prova automatica di correttezza scientifica.**",
        "",
        ("Esamina le ripetizioni testuali esatte e le possibili equivalenze "
         "nei capitoli. Le parafrasi richiedono sempre verifica umana."),
        "",
        f"Capitoli analizzati: {len(chapters)}",
        ("Ripetizioni identiche — gruppi: "
         f"{report['exact_duplicate_groups_total']}"),
        ("Occorrenze identiche in eccesso: "
         f"{report['excess_exact_occurrences_total']}"),
        f"Audit: {before.status} → {after.status}",
        "",
        "## Verifica mirata dei concetti",
        "",
    ]
    for target in targeted:
        dossier += [
            f"### {target['title']}",
            f"Stato: **{target['status']}**",
            "Aggiunta scientifica: nessuna.",
            "",
        ]
        if target["supporting_study_statements"]:
            dossier.append("Passaggi già presenti nell'Essenziale:")
            for sentence in target["supporting_study_statements"]:
                dossier.append(f"- {sentence}")
        else:
            dossier.append(
                "Nessuna copertura sufficientemente specifica rilevata "
                "dalla regola lessicale; valutare il capitolo manualmente."
            )
        source = target["mapped_original_context"]
        if source:
            dossier += [
                "",
                f"Dispensa: {source['source_path']}",
                f"Pagina: {source['page']} · Blocco: {source['block']}",
                f"Estratto originale (contesto, non prova): {source['excerpt']}",
            ]
        dossier.append("")
    dossier += [
        "## Correzione grammaticale proposta",
        "",
        f"Stato: **{grammar_proposal['status']}**",
        "",
    ]
    if grammar_proposal["status"] == "GRAMMAR_CANDIDATE_UNVERIFIED":
        dossier += [
            f"Prima: {grammar_proposal['before']}",
            f"Dopo: {grammar_proposal['after']}",
            "",
        ]
    dossier += [
        "## Ripetizioni testuali esatte",
        "",
        ("Solo segnalazioni; nessun periodo duplicato è stato cancellato "
         "automaticamente."),
        "",
    ]
    for chapter in chapters:
        duplication = chapter["exact_duplicates"]
        if not duplication["duplicate_groups_total"]:
            continue
        dossier += [
            f"### {chapter['title']}",
            ("Gruppi: " + str(duplication["duplicate_groups_total"])
             + " · Occorrenze extra: "
             + str(duplication["excess_exact_occurrences_total"])),
            "",
        ]
        for item in duplication["examples"]:
            dossier.append(
                f"- {item['occurrences']}× {item['text']}"
            )
        dossier.append("")

    # Only a new directory outside all input locations may be written.
    output.mkdir(parents=True, exist_ok=False)
    (output / "v9-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    (output / "v9-dossier.md").write_text(
        "\n".join(dossier), encoding="utf-8",
    )
    (output / "essenziale-v9-GRAMMATICA-CANDIDATA-NON-PUBBLICATA.md").write_text(
        candidate, encoding="utf-8",
    )
    return output
