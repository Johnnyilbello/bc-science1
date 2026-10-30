"""Audit for the extractive Essential, distinct from the 97% Complete-fact audit.

A short summary intentionally omits secondary facts. PASS here means the
automated provenance, exam-number and structural checks passed. It does NOT
certify independent scientific accuracy of the original course documents.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .course_audit import FAST_ESSENTIAL_AUDIT_VERSION
from .italian_proofreading import grammar_findings
from .quality_signals import assess_chapter
from .structural_checks import find_structural_issues
from .summarizer import _extract_summary_chapters, _preserves_exam_numbers


@dataclass(frozen=True, slots=True)
class FastEssentialAudit:
    audit_version: str
    status: str
    complete_sha256: str
    study_sha256: str
    source_documents: int
    source_coverage_complete: bool
    chapters_total: int
    chapters_present: int
    complete_words: int
    study_words: int
    compression_percent: float
    missing_exam_number_chapters: tuple[str, ...]
    overly_long_paragraphs: int
    warnings: tuple[str, ...]
    content_review_status: str = "NON_VERIFICATO_SCIENTIFICAMENTE"
    chapters_needing_review: int = 0
    chapter_signals: tuple[dict, ...] = ()
    linguistic_status: str = "NON_VALUTATO"
    chapters_with_language_issues: int = 0
    chapters_with_structural_issues: int = 0


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def audit_fast_essential(
    complete_text: str,
    study_text: str,
    *,
    source_documents: int = 0,
    source_coverage_complete: bool = True,
) -> FastEssentialAudit:
    originals = _extract_summary_chapters(complete_text)
    essential = _extract_summary_chapters(study_text)
    source_titles = [name for name, _ in originals]
    target_titles = [name for name, _ in essential]
    missing_numeric: list[str] = []
    chapter_signals: list[dict] = []
    for (name, original), (target, simplified) in zip(originals, essential):
        if name != target:
            continue
        if not _preserves_exam_numbers(original, simplified):
            missing_numeric.append(name)
        report = assess_chapter(name, original, simplified)
        # Store only short source quotes for actionable manual review;
        # never misrepresent lexical overlap as semantic entailment.
        info = report.to_dict()
        info["possible_missing"] = info["possible_missing"][:8]
        language = grammar_findings(simplified, limit=15)
        info["language_findings"] = [finding.to_dict() for finding in language]
        info["structural_findings"] = [
            finding.to_dict() for finding in find_structural_issues(simplified)
        ]
        info["language_review_status"] = (
            "DA_RIVEDERE" if language else "NESSUNA_ANOMALIA_RILEVATA"
        )
        chapter_signals.append(info)

    word = r"\b[\wÀ-ÿ'+-]+\b"
    cw = len(re.findall(word, complete_text))
    sw = len(re.findall(word, study_text))
    compression = max(0.0, 1.0 - (sw / cw)) * 100 if cw else 0.0
    long_paragraphs = sum(
        1 for paragraph in re.split(r"\n\s*\n", study_text)
        if (
            not paragraph.strip().startswith("#")
            and not paragraph.strip().startswith(("-", "*"))
            and len(re.findall(word, paragraph)) > 135
        )
    )
    warnings = [
        ("Controllo automatico source-only: non certifica la verità scientifica "
         "delle dispense originali; i contenuti vanno studiati e verificati.")
    ]
    hard_failure = (
        not source_coverage_complete
        or not originals
        or source_titles != target_titles
        or bool(missing_numeric)
        or sw == 0
    )
    if source_titles != target_titles:
        warnings.append("I capitoli non corrispondono alla versione Completa.")
    if missing_numeric:
        warnings.append(
            "Punti d'esame numerici mancanti nei capitoli: "
            + ", ".join(missing_numeric[:10])
        )
    if not source_coverage_complete:
        warnings.append("Provenienza della Completa non verificata.")
    if compression < 20 and cw >= 250:
        warnings.append(
            f"Compressione {compression:.1f}%: troppo vicina alla Completa."
        )
    if long_paragraphs:
        warnings.append(
            f"{long_paragraphs} paragrafi troppo lunghi per una lettura agevole."
        )
    review_needed = sum(
        info["review_priority"] != "nessuna" for info in chapter_signals
    )
    if review_needed:
        warnings.append(
            f"{review_needed} capitoli mostrano possibili omissioni o problemi "
            "di leggibilità: richiedono controllo umano prima di dichiarare "
            "verificato il contenuto."
        )
    language_chapters = sum(
        bool(info["language_findings"]) for info in chapter_signals
    )
    if language_chapters:
        warnings.append(
            f"{language_chapters} capitoli con potenziali problemi linguistici "
            "o incoerenze di significato. Non considerare la grammatica verificata."
        )
    structural_chapters = sum(
        bool(info["structural_findings"]) for info in chapter_signals
    )
    if structural_chapters:
        warnings.append(
            f"{structural_chapters} capitoli con possibili ripetizioni, "
            "frammenti o problemi di significato da confrontare sulle dispense."
        )
    # The old 97% lexical fact metric is NOT an Essenziale pass/fail gate:
    # omitting secondary facts is an intentional requirement.
    # A widespread review queue or flagged Italian must not silently appear
    # as PASS. WARN does not claim that the flagged omissions are confirmed.
    widespread_review = (
        len(chapter_signals) >= 3
        and review_needed > len(chapter_signals) / 2
    )
    needs_attention = (
        (compression < 20 and cw >= 250)
        or long_paragraphs > 0
        or widespread_review
        or language_chapters > 0
        or structural_chapters > 0
    )
    status = "FAIL" if hard_failure else ("WARN" if needs_attention else "PASS")
    return FastEssentialAudit(
        audit_version=FAST_ESSENTIAL_AUDIT_VERSION,
        status=status,
        complete_sha256=_sha(complete_text),
        study_sha256=_sha(study_text),
        source_documents=source_documents,
        source_coverage_complete=source_coverage_complete,
        chapters_total=len(originals),
        chapters_present=len(essential),
        complete_words=cw,
        study_words=sw,
        compression_percent=round(compression, 1),
        missing_exam_number_chapters=tuple(missing_numeric),
        overly_long_paragraphs=long_paragraphs,
        warnings=tuple(warnings),
        content_review_status="NON_VERIFICATO_SCIENTIFICAMENTE",
        chapters_needing_review=review_needed,
        chapter_signals=tuple(chapter_signals),
        linguistic_status=(
            "DA_RIVEDERE" if language_chapters else "NESSUNA_ANOMALIA_RILEVATA"
        ),
        chapters_with_language_issues=language_chapters,
        chapters_with_structural_issues=structural_chapters,
    )


def write_fast_audit(report: FastEssentialAudit, directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    js = directory / "audit.json"
    md = directory / "audit.md"
    js.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# BC Science — Audit Essenziale estrattivo",
        "",
        f"**Esito tecnico:** {report.status}",
        f"**Capitoli:** {report.chapters_present}/{report.chapters_total}",
        f"**Compressione:** {report.compression_percent:.1f}%",
        f"**Numeri d'esame assenti:** {len(report.missing_exam_number_chapters)}",
        f"**Verifica contenutistica:** {report.content_review_status}",
        f"**Capitoli da controllare:** {report.chapters_needing_review}",
        f"**Lingua italiana (euristiche):** {report.linguistic_status}",
        (f"**Capitoli con anomalie linguistiche:** "
         f"{report.chapters_with_language_issues}"),
        (f"**Capitoli con anomalie strutturali:** "
         f"{report.chapters_with_structural_issues}"),
        "",
        "Questo esito tecnico non equivale a una validazione scientifica esterna.",
        "",
        "## Avvertenze",
        *[f"- {warning}" for warning in report.warnings],
        "",
        "## Capitoli con possibili problemi",
        *[
            f"- **{item['title']}**: priorità {item['review_priority']}; "
            f"possibili omissioni {len(item['possible_missing'])}; "
            f"frasi difficili {item['difficult_sentences']}; "
            f"frasi non ben allineate {item['unsupported_phrases']}"
            for item in report.chapter_signals
            if (
                item["review_priority"] != "nessuna"
                or item["language_findings"]
                or item["structural_findings"]
            )
        ],
        "",
        "## Forme grammaticali e problemi di significato da verificare",
        *[
            f"- **{item['title']}**: «{finding['quote']}» — "
            f"{finding['message']}"
            for item in report.chapter_signals
            for finding in item["language_findings"]
        ],
        "",
        "## Ripetizioni, frasi incomplete e contraddizioni sospette",
        *[
            f"- **{item['title']}**: «{finding['quote']}» — "
            f"{finding['reason']}"
            for item in report.chapter_signals
            for finding in item["structural_findings"]
        ],
    ]
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md, js
