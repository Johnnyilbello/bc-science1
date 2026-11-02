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
from .summarizer import (
    _STUDY_TITLE_REPLACEMENTS,
    _extract_summary_chapters,
    _preserves_exam_numbers,
)


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
    title_spelling_variants: tuple[tuple[str, str], ...] = ()


def _title_spelling_key(title: str) -> str:
    """Resolve only enumerated editorial typos; never fuzzy-match concepts."""
    value = " ".join(title.casefold().split())
    for incorrect, corrected in _STUDY_TITLE_REPLACEMENTS.items():
        value = re.sub(
            rf"\b{re.escape(incorrect.casefold())}\b",
            corrected.casefold(),
            value,
        )
    return value


def _ordered_title_match(
    source_titles: list[str], target_titles: list[str],
) -> tuple[bool, tuple[tuple[str, str], ...]]:
    """Require a one-to-one ordered match; never collapse ambiguous aliases."""
    if not source_titles or len(source_titles) != len(target_titles):
        return False, ()
    original_keys = [_title_spelling_key(title) for title in source_titles]
    target_keys = [_title_spelling_key(title) for title in target_titles]
    if (
        len(set(original_keys)) != len(original_keys)
        or len(set(target_keys)) != len(target_keys)
        or original_keys != target_keys
    ):
        return False, ()
    return True, tuple(
        (source, target)
        for source, target in zip(source_titles, target_titles, strict=True)
        if source != target
    )


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
    titles_match, spelling_variants = _ordered_title_match(
        source_titles, target_titles,
    )
    missing_numeric: list[str] = []
    chapter_signals: list[dict] = []
    for (name, original), (target, simplified) in zip(originals, essential):
        if _title_spelling_key(name) != _title_spelling_key(target):
            continue
        if not _preserves_exam_numbers(original, simplified):
            missing_numeric.append(name)
        report = assess_chapter(name, original, simplified)
        # Store only short source quotes for actionable manual review;
        # never misrepresent lexical overlap as semantic entailment.
        info = report.to_dict()
        # Truncate quoted examples for report size, never the real counts.
        info["possible_missing_total"] = len(info["possible_missing"])
        info["possible_missing_by_category"] = {
            category: sum(
                fact.category == category for fact in report.possible_missing
            )
            for category in sorted({fact.category for fact in report.possible_missing})
        }
        info["possible_missing"] = info["possible_missing"][:8]
        language = grammar_findings(simplified, limit=15)
        info["language_findings"] = [finding.to_dict() for finding in language]
        info["structural_findings"] = [
            finding.to_dict() for finding in find_structural_issues(simplified)
        ]
        # Source text may itself be misleading. A fully extractive Essential
        # could reproduce that error verbatim and still pass a fact-recall gate.
        # Restrict source-side blockers to high-risk diagnostic patterns.
        source_risks = {
            "scientific_direction_risk", "scientific_mechanism_risk",
        }
        info["source_scientific_findings"] = [
            issue.to_dict()
            for issue in find_structural_issues(original)
            if issue.category in source_risks
        ]
        info["language_review_status"] = (
            "DA_RIVEDERE" if language else "NESSUNA_ANOMALIA_RILEVATA"
        )
        chapter_signals.append(info)

    word = r"\b[\wÀ-ÿ'+-]+\b"
    cw = len(re.findall(word, complete_text))
    sw = len(re.findall(word, study_text))
    # A negative percentage is meaningful: Essential is LONGER than Complete.
    compression = (1.0 - (sw / cw)) * 100 if cw else 0.0
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
        or not titles_match
        or bool(missing_numeric)
        or sw == 0
    )
    if not titles_match:
        warnings.append(
            "I capitoli non corrispondono univocamente, nello stesso ordine, "
            "alla versione Completa."
        )
    elif spelling_variants:
        warnings.append(
            f"{len(spelling_variants)} titoli con differenze ortografiche "
            "riconosciute: verificare la grafia originale e quella dello studio; "
            "nessun titolo è stato modificato."
        )
    if missing_numeric:
        warnings.append(
            "Punti d'esame numerici mancanti nei capitoli: "
            + ", ".join(missing_numeric[:10])
        )
    if not source_coverage_complete:
        warnings.append("Provenienza della Completa non verificata.")
    if compression < 0 and cw > 0:
        warnings.append(
            f"Compressione effettiva {compression:.1f}%: l'Essenziale "
            "contiene più parole della Completa; serve revisione, "
            "non un PASS automatico."
        )
    elif compression < 20 and cw >= 250:
        warnings.append(
            f"Compressione effettiva {compression:.1f}%: troppo vicina "
            "alla Completa."
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
    scientific_source_chapters = sum(
        bool(info["source_scientific_findings"]) for info in chapter_signals
    )
    context_chapters = sum(
        bool(info["source_context_warnings"]) for info in chapter_signals
    )
    if context_chapters:
        warnings.append(
            f"{context_chapters} capitoli contengono riferimenti ambigui già "
            "nella Completa; confrontare il soggetto con la dispensa originale."
        )
    if scientific_source_chapters:
        warnings.append(
            f"{scientific_source_chapters} capitoli contengono possibili "
            "errori scientifici già nella Completa: confrontare le fonti "
            "originali PRIMA di approvare la versione Essenziale."
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
        compression < 0
        or (compression < 20 and cw >= 250)
        or long_paragraphs > 0
        or widespread_review
        or language_chapters > 0
        or structural_chapters > 0
        or context_chapters > 0
        or scientific_source_chapters > 0
        or bool(spelling_variants)
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
        title_spelling_variants=spelling_variants,
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
        f"**Compressione effettiva (può essere negativa):** {report.compression_percent:.1f}%",
        f"**Differenze ortografiche nei titoli:** {len(report.title_spelling_variants)}",
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
        "## Differenze ortografiche nei titoli (non corrette automaticamente)",
        *[
            f"- Completa: «{source}» → Essenziale: «{target}»"
            for source, target in report.title_spelling_variants
        ],
        "",
        "## Capitoli con possibili problemi",
        *[
            f"- **{item['title']}**: priorità {item['review_priority']}; "
            f"possibili omissioni {item['possible_missing_total']}; "
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
        "## Potenziali errori scientifici nella Completa",
        *[
            f"- **{item['title']}**: «{finding['quote']}» — "
            f"{finding['reason']}"
            for item in report.chapter_signals
            for finding in item["source_scientific_findings"]
        ],
        "",
        "## Riferimenti ambigui già presenti nella Completa",
        *[
            f"- **{item['title']}**: «{phrase}» — "
            "soggetto/contesto da verificare nelle dispense originali"
            for item in report.chapter_signals
            for phrase in item["source_context_warnings"][:5]
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
