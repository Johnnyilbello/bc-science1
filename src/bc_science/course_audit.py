from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from functools import lru_cache
from difflib import SequenceMatcher
from pathlib import Path

from .clarity import audit_novice_document
from .summarizer import _extract_summary_chapters, topic_key

_STOPWORDS = {
    "anche", "alla", "alle", "agli", "allo", "attraverso", "come", "con", "dalla",
    "dalle", "dagli", "dallo", "della", "delle", "degli", "dello", "dell", "dopo",
    "durante", "essere", "gli", "nella", "nelle", "negli", "nello", "ogni", "per",
    "perche", "perché", "prima", "questa", "queste", "questi", "questo", "sono",
    "tale", "tra", "tutte", "tutti", "una", "uno", "viene",
}

_DEFINITION_HINTS = (
    " è ", " indica ", " significa ", " si definisce ", " consiste ", " rappresenta ",
)
_CLASSIFICATION_HINTS = (
    " si divide ", " si distingu", " classific", " tipi ", " classi ", " categorie ",
)
_SEQUENCE_HINTS = (
    " prima ", " poi ", " successivamente ", " infine ", " fase ", " stadio ", "→", "->",
)
_EXCEPTION_HINTS = (
    " eccetto ", " tranne ", " tuttavia ", " invece ", " mentre ", " non ", " ma ",
)

AUDIT_VERSION = "atomic-fact-v1"


@dataclass(frozen=True, slots=True)
class AtomicFact:
    chapter: str
    text: str
    category: str
    weight: int
    numbers: tuple[str, ...]
    exam_recap: bool


@dataclass(frozen=True, slots=True)
class MissingFact:
    chapter: str
    text: str
    category: str
    weight: int
    best_similarity: float


@dataclass(frozen=True, slots=True)
class StudyAuditResult:
    audit_version: str
    status: str
    complete_sha256: str
    study_sha256: str
    source_documents: int
    source_coverage_complete: bool
    complete_words: int
    study_words: int
    compression_percent: float
    source_facts: int
    covered_facts: int
    weighted_fact_coverage: float
    missing_exam_facts: int
    missing_numeric_facts: int
    study_facts: int
    duplicate_pairs: int
    duplicate_rate: float
    clarity_score: float
    clarity_passed: bool
    warnings: tuple[str, ...]
    missing_facts: tuple[MissingFact, ...]


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _strip_citations(text: str) -> str:
    value = re.sub(
        r"(?i)\s*\((?:fonte|fonti)\s*:[^)\n]{1,320}\)",
        "",
        text,
    )
    value = re.sub(
        r"(?i)\s*\[(?:fonte|fonti)\s*:[^\]\n]{1,320}\]",
        "",
        value,
    )
    return value


def _plain(text: str) -> str:
    value = _strip_citations(text)
    value = re.sub(r"(?m)^#{1,6}\s+", "", value)
    value = re.sub(r"(?m)^\s*>\s?", "", value)
    value = re.sub(r"(?m)^\s*(?:[-*]|\d+[.)])\s+", "", value)
    value = re.sub(r"\*\*(.+?)\*\*", r"\1", value)
    value = re.sub(r"(?<!\*)\*(.+?)\*(?!\*)", r"\1", value)
    value = re.sub(r"\x60(.+?)\x60", r"\1", value)
    return re.sub(r"\s+", " ", value).strip()


@lru_cache(maxsize=8192)
def _normalized(text: str) -> str:
    value = _plain(text).casefold()
    value = value.replace("–", "-").replace("—", "-")
    value = re.sub(r"[^a-z0-9à-ÿ%µ°+\-/ ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


@lru_cache(maxsize=8192)
def _tokens(text: str) -> frozenset[str]:
    return frozenset({
        token
        for token in re.findall(r"[a-z0-9à-ÿµ°]+", _normalized(text))
        if len(token) >= 3 and token not in _STOPWORDS
    })


@lru_cache(maxsize=8192)
def _numbers(text: str) -> tuple[str, ...]:
    values = re.findall(
        r"(?<!\w)\d+(?:[.,]\d+)?(?:\s*(?:%|[a-zA-ZÀ-ÿµ°]+))?",
        _strip_citations(text),
    )
    normalized = {
        re.sub(r"\s+", " ", value).strip().casefold().replace(",", ".")
        for value in values
        if value.strip()
    }
    return tuple(sorted(normalized))


def _sentence_units(text: str) -> list[str]:
    units: list[str] = []
    for raw in text.splitlines():
        line = _plain(raw)
        if not line:
            continue
        parts = re.split(r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Ý0-9])", line)
        units.extend(part.strip() for part in parts if len(part.strip()) >= 18)
    return units


def _fact_category(text: str, *, exam: bool) -> tuple[str, int]:
    lowered = f" {_normalized(text)} "
    has_number = bool(_numbers(text))
    if has_number:
        return "numero", 3
    if exam:
        return "esame", 3
    if any(hint in lowered for hint in _CLASSIFICATION_HINTS):
        return "classificazione", 2
    if any(hint in lowered for hint in _SEQUENCE_HINTS):
        return "sequenza", 2
    if any(hint in lowered for hint in _EXCEPTION_HINTS):
        return "eccezione", 2
    if any(hint in lowered for hint in _DEFINITION_HINTS):
        return "definizione", 2
    return "fatto", 1


def _section_name(line: str) -> str | None:
    match = re.match(r"^###\s+(.+?)\s*$", line.strip())
    return match.group(1).strip() if match else None


def _candidate_fact(text: str, section: str, *, is_bullet: bool) -> bool:
    has_numbers = bool(_numbers(text))
    if len(_tokens(text)) < 3 and not has_numbers:
        return False
    if section.casefold() == "in parole semplici" and not has_numbers:
        return False
    if is_bullet:
        return True

    lowered = f" {_normalized(text)} "
    return (
        bool(_numbers(text))
        or any(hint in lowered for hint in _DEFINITION_HINTS)
        or any(hint in lowered for hint in _CLASSIFICATION_HINTS)
        or any(hint in lowered for hint in _SEQUENCE_HINTS)
        or any(hint in lowered for hint in _EXCEPTION_HINTS)
        or (
            bool(section)
            and section.casefold() not in {"in parole semplici", "parole chiave"}
        )
    )


def extract_atomic_facts(text: str) -> list[AtomicFact]:
    chapters = _extract_summary_chapters(text)
    facts: list[AtomicFact] = []
    seen: set[tuple[str, str]] = set()

    for chapter_title, chapter in chapters:
        section = ""
        for raw_line in chapter.splitlines():
            heading = _section_name(raw_line)
            if heading is not None:
                section = heading
                continue
            if raw_line.startswith("## "):
                continue

            bullet = re.match(r"^\s*(?:[-*]|\d+[.)])\s+(.+?)\s*$", raw_line)
            if bullet:
                candidates = [bullet.group(1).strip()]
                is_bullet = True
            else:
                candidates = _sentence_units(raw_line)
                is_bullet = False

            for candidate in candidates:
                exam = section.casefold() == "da ricordare per l'esame"
                if not _candidate_fact(candidate, section, is_bullet=is_bullet):
                    continue
                normalized = _normalized(candidate)
                key = (chapter_title.casefold(), normalized)
                if not normalized or key in seen:
                    continue
                seen.add(key)
                category, weight = _fact_category(candidate, exam=exam)
                facts.append(
                    AtomicFact(
                        chapter=chapter_title,
                        text=_plain(candidate),
                        category=category,
                        weight=weight,
                        numbers=_numbers(candidate),
                        exam_recap=exam,
                    )
                )
    return facts


@lru_cache(maxsize=8192)
def _grams(value: str) -> frozenset[str]:
    compact = re.sub(r"\s+", " ", value)
    return frozenset(
        compact[index:index + 4]
        for index in range(max(0, len(compact) - 3))
    )


def _fact_similarity(source: AtomicFact, candidate: AtomicFact) -> float:
    source_norm = _normalized(source.text)
    candidate_norm = _normalized(candidate.text)
    if not source_norm or not candidate_norm:
        return 0.0

    if source.numbers and not set(source.numbers) <= set(candidate.numbers):
        return 0.0

    if len(source_norm) >= 20 and (
        source_norm in candidate_norm or candidate_norm in source_norm
    ):
        return 1.0

    source_tokens = _tokens(source.text)
    candidate_tokens = _tokens(candidate.text)
    if not source_tokens or not candidate_tokens:
        return 0.0

    overlap = len(source_tokens & candidate_tokens)
    recall = overlap / len(source_tokens)
    precision = overlap / len(candidate_tokens)
    token_score = 0.75 * recall + 0.25 * precision

    # Character n-gram overlap helps when singular/plural or light rephrasing changes tokens.
    left_grams = _grams(source_norm)
    right_grams = _grams(candidate_norm)
    gram_score = (
        len(left_grams & right_grams) / len(left_grams)
        if left_grams
        else 0.0
    )
    return min(1.0, 0.8 * token_score + 0.2 * gram_score)


def _coverage_threshold(fact: AtomicFact) -> float:
    if fact.numbers:
        return 0.56
    if fact.exam_recap:
        return 0.58
    if fact.weight >= 2:
        return 0.54
    return 0.50


def _global_duplicate_pairs(facts: list[AtomicFact]) -> int:
    pairs = 0
    for index, left in enumerate(facts):
        if left.exam_recap:
            continue
        left_tokens = _tokens(left.text)
        if not left_tokens:
            continue
        for right in facts[index + 1:]:
            if right.exam_recap or right.chapter.casefold() == left.chapter.casefold():
                continue
            right_tokens = _tokens(right.text)
            if not right_tokens:
                continue
            intersection = len(left_tokens & right_tokens)
            union = len(left_tokens | right_tokens)
            jaccard = intersection / union if union else 0.0
            if jaccard < 0.72:
                continue
            left_norm = _normalized(left.text)
            right_norm = _normalized(right.text)
            shorter = min(len(left_norm), len(right_norm))
            if shorter < 18:
                continue
            common = intersection / min(len(left_tokens), len(right_tokens))
            if common >= 0.86:
                pairs += 1
    return pairs


def audit_study_pair(
    complete_text: str,
    study_text: str,
    *,
    source_documents: int = 0,
    source_coverage_complete: bool = True,
) -> StudyAuditResult:
    source_facts = extract_atomic_facts(complete_text)
    study_facts = extract_atomic_facts(study_text)

    missing: list[MissingFact] = []
    covered = 0
    covered_weight = 0
    total_weight = sum(fact.weight for fact in source_facts)

    token_index: dict[str, set[int]] = {}
    number_index: dict[str, set[int]] = {}
    for candidate_index, candidate in enumerate(study_facts):
        for token in _tokens(candidate.text):
            token_index.setdefault(token, set()).add(candidate_index)
        for number in candidate.numbers:
            number_index.setdefault(number, set()).add(candidate_index)

    for fact in source_facts:
        candidate_indexes: set[int] = set()
        for token in _tokens(fact.text):
            candidate_indexes.update(token_index.get(token, ()))

        if fact.numbers:
            numeric_indexes: set[int] | None = None
            for number in fact.numbers:
                matches = set(number_index.get(number, ()))
                numeric_indexes = (
                    matches
                    if numeric_indexes is None
                    else numeric_indexes & matches
                )
            numeric_indexes = numeric_indexes or set()
            candidate_indexes = (
                candidate_indexes & numeric_indexes
                if candidate_indexes
                else numeric_indexes
            )

        best = 0.0
        for candidate_index in candidate_indexes:
            similarity = _fact_similarity(
                fact,
                study_facts[candidate_index],
            )
            if similarity > best:
                best = similarity
                if best >= 1.0:
                    break
        if best >= _coverage_threshold(fact):
            covered += 1
            covered_weight += fact.weight
        else:
            missing.append(
                MissingFact(
                    chapter=fact.chapter,
                    text=fact.text,
                    category=fact.category,
                    weight=fact.weight,
                    best_similarity=round(best, 3),
                )
            )

    weighted_coverage = (
        covered_weight / total_weight if total_weight else 1.0
    )
    missing_exam = sum(1 for item in missing if item.category == "esame")
    missing_numeric = sum(1 for item in missing if item.category == "numero")

    complete_words = len(re.findall(r"\b[\wÀ-ÿ'+-]+\b", _plain(complete_text)))
    study_words = len(re.findall(r"\b[\wÀ-ÿ'+-]+\b", _plain(study_text)))
    compression = (
        max(0.0, 1.0 - (study_words / complete_words))
        if complete_words
        else 0.0
    )

    duplicate_pairs = _global_duplicate_pairs(study_facts)
    duplicate_denominator = sum(1 for fact in study_facts if not fact.exam_recap)
    duplicate_rate = (
        min(1.0, duplicate_pairs / max(1, duplicate_denominator))
        if duplicate_denominator
        else 0.0
    )

    clarity_passed, clarity_rows = audit_novice_document(study_text)
    chapter_scores = [
        audit.metrics.score
        for title, audit in clarity_rows
        if title != "Front matter"
    ]
    clarity_score = (
        sum(chapter_scores) / len(chapter_scores) if chapter_scores else 0.0
    )

    warnings: list[str] = []
    if not source_facts:
        warnings.append(
            "Nessun fatto atomico estraibile: il documento non è auditabile in modo affidabile."
        )
    if not source_coverage_complete:
        warnings.append("La copertura documentale del riassunto completo non è verificata.")
    if weighted_coverage < 0.97:
        warnings.append(
            f"Copertura fatti {weighted_coverage:.1%}: sotto il target del 97%."
        )
    if missing_exam:
        warnings.append(f"{missing_exam} punti d'esame non risultano coperti con sufficiente confidenza.")
    if missing_numeric:
        warnings.append(f"{missing_numeric} fatti numerici non risultano coperti.")
    if duplicate_rate > 0.08:
        warnings.append(
            f"Ridondanza globale stimata {duplicate_rate:.1%}: sopra il target dell'8%."
        )
    if clarity_score < 80 or not clarity_passed:
        warnings.append(
            f"Chiarezza media {clarity_score:.0f}/100: almeno un capitolo non supera il gate."
        )

    if (
        not source_facts
        or not source_coverage_complete
        or weighted_coverage < 0.90
        or missing_numeric > 0
    ):
        status = "FAIL"
    elif (
        weighted_coverage < 0.97
        or missing_exam > 0
        or duplicate_rate > 0.08
        or clarity_score < 80
        or not clarity_passed
    ):
        status = "WARN"
    else:
        status = "PASS"

    return StudyAuditResult(
        audit_version=AUDIT_VERSION,
        status=status,
        complete_sha256=_sha256(complete_text),
        study_sha256=_sha256(study_text),
        source_documents=source_documents,
        source_coverage_complete=source_coverage_complete,
        complete_words=complete_words,
        study_words=study_words,
        compression_percent=round(compression * 100, 1),
        source_facts=len(source_facts),
        covered_facts=covered,
        weighted_fact_coverage=round(weighted_coverage, 4),
        missing_exam_facts=missing_exam,
        missing_numeric_facts=missing_numeric,
        study_facts=len(study_facts),
        duplicate_pairs=duplicate_pairs,
        duplicate_rate=round(duplicate_rate, 4),
        clarity_score=round(clarity_score, 1),
        clarity_passed=clarity_passed,
        warnings=tuple(warnings),
        missing_facts=tuple(missing),
    )


def restore_missing_audit_facts(
    study_text: str,
    missing_facts: tuple[MissingFact, ...] | list[MissingFact],
) -> tuple[str, int]:
    """Restore uncovered source facts into their study chapter without model rewriting."""
    chapters = _extract_summary_chapters(study_text)
    by_key = {
        topic_key(chapter_title): (chapter_title, chapter)
        for chapter_title, chapter in chapters
    }

    def resolve_target_key(chapter_title: str) -> str | None:
        source_key = topic_key(chapter_title)
        if source_key in by_key:
            return source_key

        source_base = re.sub(r"\s+\d+$", "", source_key).strip()
        for candidate_key in by_key:
            candidate_base = re.sub(r"\s+\d+$", "", candidate_key).strip()
            if source_base and source_base == candidate_base:
                return candidate_key

        best_key: str | None = None
        best_score = 0.0
        for candidate_key in by_key:
            score = SequenceMatcher(None, source_key, candidate_key).ratio()
            if score > best_score:
                best_key = candidate_key
                best_score = score
        return best_key if best_score >= 0.72 else None

    grouped: dict[str, list[str]] = {}
    seen: dict[str, set[str]] = {}
    for item in missing_facts:
        key = resolve_target_key(item.chapter)
        if key is None:
            continue

        normalized = _normalized(item.text)
        if not normalized:
            continue
        bucket_seen = seen.setdefault(key, set())
        if normalized in bucket_seen:
            continue
        bucket_seen.add(normalized)
        grouped.setdefault(key, []).append(item.text)

    repaired = study_text
    restored = 0

    for key, facts in grouped.items():
        target = by_key.get(key)
        if target is None:
            continue
        _title, original_chapter = target
        bullets = "\n".join(f"- {fact}" for fact in facts)
        recovery_heading = "### Dettagli recuperati dall'audit"

        existing_recovery = re.search(
            r"(?mis)^###\s+Dettagli recuperati dall'audit\s*$\n"
            r"(?P<body>.*?)(?=^###\s+|\Z)",
            original_chapter,
        )
        if existing_recovery:
            body = existing_recovery.group("body").rstrip()
            replacement = (
                recovery_heading
                + "\n"
                + body
                + ("\n" if body else "")
                + bullets
                + "\n"
            )
            updated_chapter = (
                original_chapter[:existing_recovery.start()]
                + replacement
                + original_chapter[existing_recovery.end():]
            )
        else:
            exam_heading = re.search(
                r"(?mi)^###\s+Da ricordare per l['’]esame\s*$",
                original_chapter,
            )
            recovery = recovery_heading + "\n" + bullets + "\n\n"
            if exam_heading:
                updated_chapter = (
                    original_chapter[:exam_heading.start()].rstrip()
                    + "\n\n"
                    + recovery
                    + original_chapter[exam_heading.start():]
                )
            else:
                updated_chapter = (
                    original_chapter.rstrip()
                    + "\n\n"
                    + recovery_heading
                    + "\n"
                    + bullets
                )

        repaired = repaired.replace(original_chapter, updated_chapter, 1)
        restored += len(facts)

    return repaired, restored


def audit_to_dict(result: StudyAuditResult) -> dict:
    return asdict(result)


def write_audit_reports(
    result: StudyAuditResult,
    output_dir: Path,
) -> tuple[Path, Path]:
    destination = output_dir.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    json_path = destination / "audit.json"
    md_path = destination / "audit.md"

    payload = audit_to_dict(result)
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    lines = [
        "# BC Science — Audit versione studio",
        "",
        f"**Stato:** {result.status}",
        "",
        "## Metriche",
        "",
        f"- Documenti sorgente: {result.source_documents}",
        f"- Copertura documentale completa: {'sì' if result.source_coverage_complete else 'no'}",
        f"- Parole riassunto completo: {result.complete_words}",
        f"- Parole versione studio: {result.study_words}",
        f"- Compressione: {result.compression_percent:.1f}%",
        f"- Fatti atomici sorgente: {result.source_facts}",
        f"- Fatti coperti: {result.covered_facts}",
        f"- Fact coverage pesata: {result.weighted_fact_coverage:.1%}",
        f"- Fatti numerici mancanti: {result.missing_numeric_facts}",
        f"- Punti d'esame mancanti: {result.missing_exam_facts}",
        f"- Ridondanza globale stimata: {result.duplicate_rate:.1%}",
        f"- Chiarezza media: {result.clarity_score:.0f}/100",
        "",
    ]

    if result.warnings:
        lines.extend(["## Warning", ""])
        lines.extend(f"- {warning}" for warning in result.warnings)
        lines.append("")

    if result.missing_facts:
        lines.extend(["## Fatti non coperti", ""])
        for item in result.missing_facts[:100]:
            lines.append(
                f"- **{item.chapter} · {item.category}** "
                f"(similarità {item.best_similarity:.2f}): {item.text}"
            )
        if len(result.missing_facts) > 100:
            lines.append(
                f"- … altri {len(result.missing_facts) - 100} fatti nel report JSON."
            )
        lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path, json_path


def audit_report_is_current(
    report_path: Path,
    complete_text: str,
    study_text: str,
) -> tuple[bool, str | None]:
    if not report_path.exists():
        return False, None
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return False, None
    if not isinstance(payload, dict):
        return False, None

    current = (
        payload.get("audit_version") == AUDIT_VERSION
        and payload.get("complete_sha256") == _sha256(complete_text)
        and payload.get("study_sha256") == _sha256(study_text)
    )
    status = payload.get("status")
    return current, str(status) if current and status else None
