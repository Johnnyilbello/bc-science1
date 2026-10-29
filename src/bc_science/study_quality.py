"""Non-destructive quality signals for the post-study Essential edition.

The atomic-fact matcher is heuristic: a WARNING requests review, never removal
of source material or automatic expansion solely to reach a numerical target.
"""
from __future__ import annotations

from dataclasses import dataclass

from .course_audit import StudyAuditResult, audit_study_pair, extract_atomic_facts

CRITICAL_CATEGORIES = frozenset({
    "numero", "esame", "definizione", "classificazione", "sequenza", "eccezione",
})
MIN_COMPLETE_WORDS = 250
TARGET_COMPRESSION_PERCENT = 25.0


@dataclass(frozen=True, slots=True)
class EssentialQuality:
    complete_words: int
    essential_words: int
    compression_percent: float
    target_applies: bool
    compression_target_met: bool
    critical_facts: int
    missing_critical_facts: int
    critical_recall_percent: float
    missing_numeric_facts: int
    missing_exam_facts: int
    warnings: tuple[str, ...]

    @property
    def needs_review(self) -> bool:
        return bool(self.warnings)


def assess_essential(
    complete_text: str,
    essential_text: str,
    *,
    audit: StudyAuditResult | None = None,
) -> EssentialQuality:
    """Measure concision and important-fact preservation without modifying output."""
    result = audit or audit_study_pair(complete_text, essential_text)
    source_facts = extract_atomic_facts(complete_text)
    critical = [fact for fact in source_facts if fact.category in CRITICAL_CATEGORIES]
    missing = [
        fact for fact in result.missing_facts
        if fact.category in CRITICAL_CATEGORIES
    ]
    critical_recall = (
        100.0 * (len(critical) - len(missing)) / len(critical)
        if critical else 100.0
    )
    applies = result.complete_words >= MIN_COMPLETE_WORDS
    compact = (
        result.compression_percent >= TARGET_COMPRESSION_PERCENT
        if applies else True
    )

    warnings: list[str] = []
    if applies and not compact:
        warnings.append(
            "Essenziale troppo simile a Completa: compressione sotto il "
            f"{TARGET_COMPRESSION_PERCENT:.0f}% (obiettivo indicativo)."
        )
    if result.missing_numeric_facts or result.missing_exam_facts:
        warnings.append(
            "Verificare formule, numeri e punti d'esame segnalati dall'audit."
        )
    if missing:
        warnings.append(
            f"{len(missing)} concetti potenzialmente critici richiedono revisione: "
            "il confronto lessicale puo non riconoscere tutte le parafrasi."
        )
    if not source_facts:
        warnings.append("Nessun fatto estraibile: verifica manuale necessaria.")

    return EssentialQuality(
        complete_words=result.complete_words,
        essential_words=result.study_words,
        compression_percent=result.compression_percent,
        target_applies=applies,
        compression_target_met=compact,
        critical_facts=len(critical),
        missing_critical_facts=len(missing),
        critical_recall_percent=round(max(0.0, critical_recall), 1),
        missing_numeric_facts=result.missing_numeric_facts,
        missing_exam_facts=result.missing_exam_facts,
        warnings=tuple(warnings),
    )
