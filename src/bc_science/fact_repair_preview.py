"""Two explicitly scoped, source-context-backed Essential edits (preview only).

This is an intentionally narrow pilot for the two documented Biologia/Chimica
cases, NOT a general-purpose automated scientific fact-checker. Neither matching
keywords on one PDF page nor the preceding summary guarantees factual truth.
No input, manifest, canonical output, or original PDF can be written.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .autopilot_runner import _validated_manifest
from .courses import CourseScan, _fingerprint, _snapshot
from .fast_audit import _ordered_title_match, audit_fast_essential
from .quality_signals import _lines
from .summarizer import _extract_summary_chapters

PILOT_VERSION = "two-fact-preview-v8-markdown-source-matching"


@dataclass(frozen=True)
class FactCase:
    title: str
    old: str
    revised: str
    complete_anchor: str
    source_terms: tuple[str, ...]
    source_focus: str
    already_present: tuple[str, ...]


CASES = (
    FactCase(
        title="Il ciclo di Krebs",
        old=(
            "Ciclo aperto: Definizione del ciclo di Krebs basata sulla possibilità "
            "che gli intermedi escano per la sintesi di altre molecole o "
            "rientrano per essere rigenerati."
        ),
        revised=(
            "Ciclo aperto: gli intermedi del ciclo di Krebs possono uscire per "
            "la sintesi di altre molecole; le loro concentrazioni vengono "
            "ripristinate mediante reazioni anaplerotiche."
        ),
        complete_anchor=(
            "Ciclo aperto: Definizione del ciclo di Krebs perché i suoi "
            "intermedi possono uscire per essere usati nella sintesi di "
            "altre molecole o rientrare per essere ripristinati."
        ),
        source_terms=(
            "ciclo di krebs", "aperto", "intermedi", "biosintetiche",
            "anaplerotiche",
        ),
        source_focus="anaplerotiche",
        already_present=("anaplerotiche",),
    ),
    FactCase(
        title="La struttura terziaria e quaternaria delle proteine ed i lipidi",
        old=(
            "Fosfolipidi e Sfingolipidi: Lipidi anfipatici fondamentali per "
            "la costruzione delle membrane cellulari in doppio strato."
        ),
        revised=(
            "Fosfolipidi e Sfingolipidi: Lipidi anfipatici fondamentali per "
            "la costruzione delle membrane cellulari in doppio strato. "
            "Nei fosfolipidi, le teste polari idrofile si orientano verso "
            "l'ambiente acquoso, mentre le code apolari idrofobiche sono "
            "rivolte verso l'interno del doppio strato."
        ),
        complete_anchor=(
            "Fosfolipidi: Lipidi anfipatici (testa polare, code apolari) "
            "che formano il doppio strato delle membrane cellulari."
        ),
        source_terms=(
            "fosfolipidi", "doppio strato", "teste idrofile",
            "code idrofobiche", "verso l'esterno", "verso l'interno",
        ),
        source_focus="teste idrofile",
        already_present=("teste polari", "code apolari"),
    ),
)


def _normalize(text: str) -> str:
    """Normalize whitespace and typographic apostrophes, not word meanings."""
    return " ".join(text.casefold().replace("’", "'").split())


def _canonical_with_offsets(text: str) -> tuple[str, list[int]]:
    """A view removing Markdown decorations while retaining original offsets.

    Only formatting whitespace, list prefixes, ** emphasis and code ticks
    are removed. Never drop punctuation, numbers, negation or content words.
    This is not fuzzy or semantic matching.
    """
    bullet_spans = [
        (match.start(), match.end())
        for match in re.finditer(r"(?m)^[ \t]*(?:[-*]|\d+[.)])[ \t]+", text)
    ]
    excluded = {
        position for start, stop in bullet_spans
        for position in range(start, stop)
    }
    chars: list[str] = []
    offsets: list[int] = []
    for index, char in enumerate(text):
        if index in excluded or char in "*\x60_":
            continue
        if char.isspace():
            if not chars or chars[-1] == " ":
                continue
            chars.append(" ")
            offsets.append(index)
            continue
        for normalized in char.casefold().replace("’", "'"):
            chars.append(normalized)
            offsets.append(index)
    return "".join(chars), offsets


def _exact_statement_exists(chapter: str, statement: str) -> bool:
    """Require exactly one full statement, as parsed by the V7 diagnostics."""
    expected = _canonical_with_offsets(statement)[0].strip()
    if not expected:
        return False
    return sum(
        _canonical_with_offsets(line)[0].strip() == expected
        for line in _lines(chapter)
    ) == 1


def _unique_study_span(
    study: str, chapter: str, statement: str,
) -> tuple[int, int] | None:
    """Locate the original byte range of one Markdown-decorated sentence.

    Both the chapter statement and entire document occurrence must be unique.
    A different word, qualifier, number, or punctuation blocks the edit.
    """
    if not _exact_statement_exists(chapter, statement):
        return None
    normalized, offsets = _canonical_with_offsets(study)
    needle = _canonical_with_offsets(statement)[0].strip()
    location = normalized.find(needle)
    if location < 0 or normalized.find(needle, location + 1) >= 0:
        return None
    start, stop = offsets[location], offsets[location + len(needle) - 1] + 1
    # Include the opening Markdown emphasis in the replaceable span.
    # Otherwise replacing a bold-labelled statement would leave a stray **.
    if start >= 2 and study[start - 2:start] == "**":
        start -= 2
    if not study[start:stop].strip():
        return None
    return start, stop


def _preserve_inline_label(original: str, case: FactCase) -> str:
    """Keep the label's bold styling where it existed in the original line."""
    label = case.old.split(":", 1)[0] + ":"
    if original.startswith("**" + label + "**"):
        return "**" + label + "**" + case.revised[len(label):]
    if original.startswith("**" + label[:-1] + "**:"):
        return "**" + label[:-1] + "**:" + case.revised[len(label):]
    return case.revised


def _source_fact_evidence(
    scan: CourseScan, mapping: dict[str, str], case: FactCase,
) -> dict | None:
    """Accept only real mapped PDF pages/DOCX paragraphs containing all anchors."""
    import pymupdf

    from .fast_audit import _title_spelling_key

    target = _title_spelling_key(case.title)
    files = sorted(
        scan.path / name for name, heading in mapping.items()
        if _title_spelling_key(heading) == target
    )
    allowed = set(scan.supported_files)
    matched: list[dict] = []

    def consider(text: str, source: Path, page: int | None, block: int | None) -> None:
        normalized = _normalize(text)
        if not all(_normalize(term) in normalized for term in case.source_terms):
            return
        # Keep original wording and page/block provenance for independent review.
        focus_at = normalized.find(_normalize(case.source_focus))
        # Normalization changes offsets; extract around a raw focus instead.
        raw = " ".join(text.split())
        marker = raw.casefold().find(case.source_focus)
        center = marker if marker >= 0 else max(0, min(focus_at, len(raw)))
        start = max(0, center - 460)
        excerpt = raw[start:start + 1050]
        matched.append({
            "source_path": str(source.resolve()),
            "page": page,
            "block": block,
            "excerpt": excerpt,
            "provenance": "mapped_original_pdf_or_docx",
            "interpretation": "SOURCE_CONTEXT_ONLY_NOT_SCIENTIFICALLY_CERTIFIED",
        })

    for source in files:
        if source.resolve() not in allowed:
            raise ValueError("Documento non autorizzato nel manifest.")
        if source.suffix.casefold() == ".pdf":
            with pymupdf.open(source) as document:
                for page_number, page in enumerate(document, 1):
                    consider(page.get_text("text", sort=True), source, page_number, None)
        elif source.suffix.casefold() == ".docx":
            from docx import Document

            document = Document(source)
            for block_number, paragraph in enumerate(document.paragraphs, 1):
                consider(paragraph.text, source, None, block_number)

    return matched[0] if matched else None


def _validated_scoped_edit(
    study: str,
    complete: str,
    case: FactCase,
    evidence: dict | None,
) -> tuple[str, str]:
    """Return a candidate only if one exact, scoped replacement is possible."""
    original = _extract_summary_chapters(study)
    full = dict(_extract_summary_chapters(complete))
    old = dict(original)
    if case.title not in old or case.title not in full:
        return study, "BLOCKED_CHAPTER_NOT_FOUND"
    source_body = full[case.title]
    study_body = old[case.title]
    if not _exact_statement_exists(source_body, case.complete_anchor):
        return study, "BLOCKED_COMPLETE_ANCHOR_MISSING"
    if all(term in _normalize(study_body) for term in case.already_present):
        return study, "SKIPPED_DETAIL_ALREADY_PRESENT"
    span = _unique_study_span(study, study_body, case.old)
    if span is None:
        return study, "BLOCKED_ESSENTIAL_ANCHOR_NOT_UNIQUE"
    if evidence is None:
        return study, "BLOCKED_ORIGINAL_SOURCE_CONTEXT_MISSING"
    start, stop = span
    old_segment = study[start:stop]
    replacement = _preserve_inline_label(old_segment, case)
    proposed = study[:start] + replacement + study[stop:]
    if proposed[:start] != study[:start] or proposed[start + len(replacement):] != study[stop:]:
        raise ValueError("Modifica fuori dalla frase bersaglio.")
    updated_chapters = _extract_summary_chapters(proposed)
    if (
        [name for name, _ in original] != [name for name, _ in updated_chapters]
        or any(
            before != after for (name, before), (_, after) in
            zip(original, updated_chapters, strict=True)
            if name != case.title
        )
    ):
        raise ValueError("Modifica fuori dal capitolo autorizzato.")
    number = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?")
    if number.findall(study) != number.findall(proposed):
        raise ValueError("Quantità o valori di esame alterati.")
    if len(case.revised.split()) > len(case.old.split()) + 45:
        raise ValueError("Ampliamento eccessivo della correzione.")
    return proposed, "CANDIDATE_UNVERIFIED"


def stage_two_fact_preview(
    scan: CourseScan,
    complete_file: Path,
    study_file: Path,
    output_dir: Path,
) -> Path:
    """Stage only independent, evidence-linked proposals; never publish."""
    complete_file = complete_file.expanduser().resolve()
    study_file = study_file.expanduser().resolve()
    root = output_dir.expanduser().resolve()
    source_root = scan.path.resolve()
    if (
        complete_file == study_file or not complete_file.is_file()
        or not study_file.is_file()
        or root.exists()
        or root == source_root or source_root in root.parents
        or root in {complete_file.parent, study_file.parent}
        or complete_file.parent in root.parents
        or study_file.parent in root.parents
    ):
        raise ValueError("Percorsi di input/anteprima non validi o già esistenti.")
    original_complete = complete_file.read_bytes()
    original_study = study_file.read_bytes()
    complete = original_complete.decode("utf-8-sig").replace("\r\n", "\n")
    study = original_study.decode("utf-8-sig").replace("\r\n", "\n")
    manifest, mapping = _validated_manifest(scan, complete)
    chapters_complete = _extract_summary_chapters(complete)
    chapters_study = _extract_summary_chapters(study)
    if not _ordered_title_match(
        [title for title, _ in chapters_complete],
        [title for title, _ in chapters_study],
    )[0]:
        raise ValueError("Titoli/ordine delle edizioni incompatibili.")

    candidate = study
    findings = []
    for case in CASES:
        evidence = _source_fact_evidence(scan, mapping, case)
        candidate_next, status = _validated_scoped_edit(
            candidate, complete, case, evidence,
        )
        findings.append({
            "title": case.title,
            "status": status,
            "before": case.old,
            "after": case.revised if status == "CANDIDATE_UNVERIFIED" else None,
            "evidence": evidence,
            "requires_human_review": True,
        })
        candidate = candidate_next

    before_audit = audit_fast_essential(complete, study)
    after_audit = audit_fast_essential(complete, candidate)
    if after_audit.status == "FAIL" and before_audit.status != "FAIL":
        raise ValueError("La revisione introduce FAIL nell'audit.")
    if (
        complete_file.read_bytes() != original_complete
        or study_file.read_bytes() != original_study
        or _fingerprint(_snapshot(scan)) != manifest["fingerprint"]
    ):
        raise ValueError("Input o dispense cambiati durante l'analisi.")

    # Only review-only output goes into a fresh standalone preview directory.
    root.mkdir(parents=True, exist_ok=False)
    proposed = root / "essenziale-v8-CANDIDATA-NON-PUBBLICATA.md"
    proposed.write_text(candidate, encoding="utf-8")
    report = {
        "version": PILOT_VERSION,
        "publication": "NOT_PERFORMED",
        "scientifically_verified": False,
        "model_used": False,
        "complete_sha256": hashlib.sha256(original_complete).hexdigest(),
        "study_sha256": hashlib.sha256(original_study).hexdigest(),
        "source_fingerprint": manifest["fingerprint"],
        "before_audit": before_audit.status,
        "after_audit": after_audit.status,
        "before_compression_percent": before_audit.compression_percent,
        "after_compression_percent": after_audit.compression_percent,
        "proposals": findings,
        "candidate_count": sum(
            record["status"] == "CANDIDATE_UNVERIFIED" for record in findings
        ),
    }
    (root / "v8-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    dossier = [
        "# BC Science V8 — Dossier di due integrazioni mirate",
        "",
        "**NON PUBBLICATO. Nessuna verifica scientifica automatica.**",
        "",
        ("Le fonti sono pagine/paragrafi originali mappati. La presenza di "
         "termini sulla stessa pagina non garantisce da sola la correttezza "
         "del nuovo testo. Verificare contesto, significato e omissioni."),
        "",
        f"Audit: {before_audit.status} → {after_audit.status}",
        (f"Compressione: {before_audit.compression_percent:.1f}% → "
         f"{after_audit.compression_percent:.1f}%"),
        "",
    ]
    for entry in findings:
        dossier += [
            f"## {entry['title']}",
            "",
            f"Stato: **{entry['status']}**",
            "",
            f"Prima: {entry['before']}",
            "",
            f"Dopo: {entry['after'] or '(nessuna modifica)'}",
            "",
        ]
        source = entry["evidence"]
        if source:
            dossier += [
                f"Fonte: {source['source_path']}",
                f"Pagina: {source['page']} · Blocco: {source['block']}",
                "",
                f"Estratto: {source['excerpt']}",
            ]
        else:
            dossier.append("Nessun contesto originale con tutte le ancore richieste.")
        dossier.append("")
    (root / "v8-dossier.md").write_text(
        "\n".join(dossier), encoding="utf-8",
    )
    return root
