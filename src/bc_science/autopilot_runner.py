"""Run a bounded three-chapter Spark pilot with restartable, review-only outputs.

The original course files, published summaries, PDFs and manifest are *read*,
never rewritten. Raw source passages are extracted from hash-verified mapped
course documents. No network, OCR, or semantic assertion is needed for retrieval.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path

from .config import AppConfig
from .courses import (
    CourseScan,
    _coverage_map_from_manifest,
    _fingerprint,
    _load_manifest,
    _snapshot,
)
from .evidence_engine import OriginalEvidence
from .fast_audit import _ordered_title_match, _title_spelling_key, audit_fast_essential
from .scientific_autopilot import (
    AGENT_VERSION,
    PRIORITY_CHAPTERS,
    diagnose_content_gaps,
    review_with_agents,
)
from .scientific_repair import _replace_single_study_chapter
from .summarizer import Summarizer, _extract_summary_chapters, _key

RUNNER_SCHEMA = 1
_SKIP = frozenset({
    "della", "delle", "degli", "nelle", "sono", "come", "questo",
    "questa", "anche", "dello", "sulla", "dalla", "del", "alla",
})


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _validated_manifest(scan: CourseScan, complete: str) -> tuple[dict, dict[str, str]]:
    """Require exact source snapshots and a one-to-one document->chapter map."""
    manifest = _load_manifest(scan)
    if not manifest or manifest.get("schema") != 3:
        raise ValueError("Manifest originale v3 non disponibile: esecuzione bloccata.")
    current = _snapshot(scan)
    mapping = _coverage_map_from_manifest(manifest)
    if (
        manifest.get("source") != str(scan.path)
        or manifest.get("course") != scan.name
        or manifest.get("files") != current
        or manifest.get("fingerprint") != _fingerprint(current)
        or set(mapping) != {str(item["path"]) for item in current}
    ):
        raise ValueError("Dispense originali o mappatura cambiate: nessuna revisione.")
    chapters = _extract_summary_chapters(complete)
    indexed = [_title_spelling_key(title) for title, _ in chapters]
    if not chapters or len(indexed) != len(set(indexed)):
        raise ValueError("Capitoli della Completa ambigui.")
    if any(_title_spelling_key(title) not in indexed for title in mapping.values()):
        raise ValueError("La mappatura del manifest non corrisponde alla Completa.")
    return manifest, mapping


def _terms(text: str) -> set[str]:
    return {
        word for word in re.findall(r"[a-zà-ÿ]{4,}", text.casefold())
        if word not in _SKIP
    }


def _original_evidence(
    scan: CourseScan,
    mapping: dict[str, str],
    title: str,
    complete_chapter: str,
    *,
    limit: int = 4,
) -> list[OriginalEvidence]:
    """Search ONLY originally mapped source documents; cite real pages/blocks."""
    import pymupdf

    target = _title_spelling_key(title)
    relevant = [scan.path / name for name, chapter in mapping.items()
                if _title_spelling_key(chapter) == target]
    query = _terms(title) | _terms(complete_chapter[:550])
    if not relevant:
        return []
    allowed = set(scan.supported_files)
    candidates: list[tuple[int, str, int | None, int | None, str, str]] = []
    for source in sorted(relevant):
        if source.resolve() not in allowed:
            raise ValueError("Documento non autorizzato nella mappatura.")
        if source.suffix.casefold() == ".pdf":
            with pymupdf.open(source) as pdf:
                for page_no, page in enumerate(pdf, 1):
                    body = " ".join(page.get_text("text", sort=True).split())
                    if len(body) < 50:
                        continue
                    terms = _terms(body)
                    score = len(terms & query)
                    if score:
                        excerpt = body[:1700]
                        candidates.append(
                            (score, str(source.resolve()), page_no, None,
                             excerpt, "original_pdf_page")
                        )
        elif source.suffix.casefold() == ".docx":
            from docx import Document

            document = Document(source)
            for index, paragraph in enumerate(document.paragraphs, 1):
                body = " ".join(paragraph.text.split())
                if len(body) < 50:
                    continue
                score = len(_terms(body) & query)
                if score:
                    candidates.append(
                        (score, str(source.resolve()), None, index,
                         body[:1700], "original_docx_paragraph")
                    )
        # Other formats are intentionally unsupported for this pilot:
        # no OCR, conversion or inferred page numbers.
    candidates.sort(key=lambda row: (-row[0], row[1], row[2] or 0, row[3] or 0))
    return [
        OriginalEvidence(
            source_path=path, page=page, block=block, excerpt=excerpt,
            rank=n, provenance=provenance,
        )
        for n, (_score, path, page, block, excerpt, provenance)
        in enumerate(candidates[:limit], 1)
    ]


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    temporary.replace(path)


def run_scientific_pilot(
    scan: CourseScan,
    complete_file: Path,
    study_file: Path,
    output_dir: Path,
    config: AppConfig,
    *,
    max_attempts: int = 3,
    resume: bool = False,
    progress=None,
) -> Path:
    """Stage source-grounded attempts; checkpoints are local, outputs unapproved."""
    if not 1 <= max_attempts <= 5:
        raise ValueError("Tentativi ammessi: da 1 a 5.")
    complete_file = complete_file.expanduser().resolve()
    study_file = study_file.expanduser().resolve()
    root = output_dir.expanduser().resolve()
    if complete_file == study_file:
        raise ValueError("Completa ed Essenziale devono essere file diversi.")
    if root == scan.path or scan.path in root.parents:
        raise ValueError("L'anteprima non può essere salvata nelle dispense.")
    if root in {complete_file.parent, study_file.parent}:
        raise ValueError("L'anteprima deve avere una cartella dedicata.")
    if root == complete_file or root == study_file:
        raise ValueError("Impossibile sostituire i documenti originali.")
    if root.exists() != resume:
        raise ValueError(
            "Usa una nuova cartella per partire; --resume solo su quella esistente."
        )
    original_complete = complete_file.read_bytes()
    original_study = study_file.read_bytes()
    complete = original_complete.decode("utf-8-sig").replace("\r\n", "\n")
    study = original_study.decode("utf-8-sig").replace("\r\n", "\n")
    manifest, mapping = _validated_manifest(scan, complete)
    full_chapters = _extract_summary_chapters(complete)
    short_chapters = _extract_summary_chapters(study)
    titles_ok, _aliases = _ordered_title_match(
        [title for title, _ in full_chapters],
        [title for title, _ in short_chapters],
    )
    if not titles_ok:
        raise ValueError("Indice della Completa e dell'Essenziale incompatibile.")
    source_by_key = {_title_spelling_key(title): body for title, body in full_chapters}
    study_by_key = {_title_spelling_key(title): (title, body)
                    for title, body in short_chapters}
    wanted = []
    for priority in PRIORITY_CHAPTERS:
        key = _title_spelling_key(priority)
        if key not in source_by_key or key not in study_by_key:
            raise ValueError(f"Capitolo prioritario assente: {priority}")
        wanted.append(study_by_key[key][0])
    if len(set(wanted)) != len(PRIORITY_CHAPTERS):
        raise ValueError("Capitoli prioritari non univoci.")
    signature = _key(
        AGENT_VERSION, str(RUNNER_SCHEMA), config.generation_model,
        _sha(original_complete), _sha(original_study),
        str(manifest["fingerprint"]), str(max_attempts),
    )
    checkpoint_file = root / "autopilot-checkpoint.json"
    completed = []
    if resume:
        record = json.loads(checkpoint_file.read_text(encoding="utf-8"))
        if (
            record.get("schema") != RUNNER_SCHEMA
            or record.get("signature") != signature
            or not isinstance(record.get("completed"), list)
        ):
            raise ValueError("Checkpoint estraneo o sorgenti cambiate.")
        completed = record["completed"]
        if [item.get("title") for item in completed] != wanted[:len(completed)]:
            raise ValueError("Checkpoint con ordine o capitoli differenti.")
    else:
        root.mkdir(parents=True, exist_ok=False)
        _atomic_json(checkpoint_file, {
            "schema": RUNNER_SCHEMA, "signature": signature, "completed": [],
        })

    summarizer = None
    try:
        for title in wanted[len(completed):]:
            key = _title_spelling_key(title)
            source = source_by_key[key]
            original = study_by_key[key][1]
            hits = _original_evidence(scan, mapping, title, source)
            if progress:
                progress(f"Capitolo {len(completed)+1}/3: {title}; fonti: {len(hits)}")
            # Citation-less chapters get a BLOCKED entry, never an AI guess.
            if hits and summarizer is None:
                summarizer = Summarizer(
                    config, "standard", db_path=root / "agent-cache.db",
                )
            if summarizer is not None and hits:
                outcome = review_with_agents(
                    title, source, original, config,
                    summarizer=summarizer, original_evidence=hits,
                    max_attempts=max_attempts, progress=progress,
                )
            else:
                from .scientific_autopilot import AgentResult

                outcome = AgentResult(
                    title, "BLOCKED_NO_ORIGINAL_EVIDENCE", original, (), 0,
                )
            diagnostic = diagnose_content_gaps(
                title, source, original, hits,
            )
            completed.append({
                "title": title,
                "status": outcome.status,
                "diagnostics": diagnostic,
                "candidate": outcome.candidate,
                "attempts": [asdict(item) for item in outcome.attempts],
                "requires_human_review": True,
                "evidence": [e.to_dict() for e in hits],
            })
            _atomic_json(checkpoint_file, {
                "schema": RUNNER_SCHEMA, "signature": signature,
                "completed": completed,
            })
        candidate = study
        authorized = []
        for row in completed:
            if row["status"] == "CANDIDATE_UNVERIFIED":
                candidate = _replace_single_study_chapter(
                    candidate, row["candidate"], chapter=row["title"],
                )
                authorized.append(row["title"])
        result_chapters = _extract_summary_chapters(candidate)
        if (
            [name for name, _ in result_chapters]
            != [name for name, _ in short_chapters]
            or any(old != new for (name, old), (_, new) in zip(
                short_chapters, result_chapters, strict=True,
            ) if name not in authorized)
        ):
            raise ValueError("Modifica fuori dai capitoli autorizzati.")
        before = audit_fast_essential(complete, study)
        after = audit_fast_essential(complete, candidate)
        if after.status == "FAIL" and before.status != "FAIL":
            raise ValueError("Nuovo FAIL introdotto, proposta bloccata.")
        # Recheck original content and course hashes before writing the proposal.
        if (
            complete_file.read_bytes() != original_complete
            or study_file.read_bytes() != original_study
            or _fingerprint(_snapshot(scan)) != manifest["fingerprint"]
        ):
            raise ValueError("Fonti cambiate durante l'esecuzione.")
        report = {
            "schema": RUNNER_SCHEMA,
            "status": (
                "CANDIDATE_REQUIRES_HUMAN_REVIEW" if authorized
                else "NO_CANDIDATE_REQUIRES_REVIEW"
            ),
            "publication": "NOT_PERFORMED",
            "scientifically_verified": False,
            "model": config.generation_model,
            "complete_sha256": _sha(original_complete),
            "study_sha256": _sha(original_study),
            "source_fingerprint": manifest["fingerprint"],
            "before_status": before.status,
            "after_status": after.status,
            "before_compression": before.compression_percent,
            "after_compression": after.compression_percent,
            "chapters": [
                {k: v for k, v in item.items() if k != "candidate"}
                for item in completed
            ],
        }
        destination = root / "essenziale-autopilot-CANDIDATA.md"
        destination.write_text(candidate, encoding="utf-8")
        _atomic_json(root / "autopilot-report.json", report)
        lines = [
            "# BC Science · Autopilot scientifico · anteprima",
            "",
            "**NON PUBBLICATO — NON SCIENTIFICAMENTE CERTIFICATO**",
            "",
            f"- Modello: {config.generation_model}",
            f"- Audit: {before.status} → {after.status}",
            (f"- Compressione: {before.compression_percent:.1f}% → "
             f"{after.compression_percent:.1f}%"),
            "",
            "## Capitoli prioritari",
        ]
        for item in completed:
            lines += [
                f"### {item['title']}",
                f"- Stato: {item['status']}",
                f"- Estratti originali: {len(item['evidence'])}",
                f"- Tentativi: {len(item['attempts'])}",
                ("- Possibili lacune (euristica): "
                 f"{item['diagnostics']['possible_missing_total']}"),
                ("- Con riscontro lessicale negli estratti campionati: "
                 f"{item['diagnostics']['source_matched_in_report']}"),
            ]
        (root / "autopilot-report.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8",
        )
        diagnostic_lines = [
            "# BC Science · Dossier di possibili lacune",
            "",
            "**NON PUBBLICATO — SEGNALAZIONI EURISTICHE, NON VERIFICATE**",
            "",
            ("Il campione di estratti originali non copre necessariamente "
             "tutta la dispensa. Un riscontro lessicale non certifica "
             "il significato scientifico; un riscontro mancante non "
             "dimostra che la fonte non contenga l'informazione."),
            "",
        ]
        for item in completed:
            diagnosis = item["diagnostics"]
            diagnostic_lines += [
                f"## {item['title']}",
                "",
                f"- Stato revisione: {item['status']}",
                f"- Possibili lacune totali: {diagnosis['possible_missing_total']}",
                ("- Estratti originali con riscontro per i candidati mostrati: "
                 f"{diagnosis['source_matched_in_report']}"),
                "- Nessuna prova scientifica automatica.",
                "",
            ]
            for index, entry in enumerate(diagnosis["candidates"], 1):
                diagnostic_lines += [
                    f"### Segnalazione {index}: {entry['category']}",
                    "",
                    f"- Completa: {entry['complete_quote']}",
                    f"- Essenziale più vicina: {entry['nearest_essential_quote']}",
                    ("- Somiglianza lessicale: "
                     f"{entry['nearest_essential_recall']:.3f}"),
                ]
                original = entry["original_excerpt_match"]
                if original is None:
                    diagnostic_lines.append(
                        "- Dispense: nessuna corrispondenza negli estratti disponibili; "
                        "verifica sul PDF originale necessaria."
                    )
                else:
                    diagnostic_lines += [
                        ("- Estratto di dispensa: "
                         f"{original['evidence_id']} — "
                         f"{original['source_path']}, "
                         f"pagina {original['page']}, "
                         f"blocco {original['block']}"),
                        f"- Testo estratto: {original['original_quote']}",
                    ]
                diagnostic_lines.append("")
        (root / "autopilot-diagnostics.md").write_text(
            "\n".join(diagnostic_lines) + "\n", encoding="utf-8",
        )
        return root
    finally:
        if summarizer is not None:
            summarizer.close()
