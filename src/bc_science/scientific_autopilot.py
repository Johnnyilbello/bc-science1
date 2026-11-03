"""Bounded, source-grounded local multi-agent review; never publishes files.

Roles are logical and run sequentially on a single local Ollama model:
analyst -> original-evidence librarian -> writer -> independent critic ->
deterministic gatekeeper. Every proposed edit is optional, reversible,
and still scientifically unverified without human evaluation.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass

from .config import AppConfig
from .evidence_engine import OriginalEvidence
from .fast_audit import _title_spelling_key, audit_fast_essential
from .italian_proofreading import grammar_findings
from .quality_signals import _lines, _overlap, _qualifiers_compatible, assess_chapter
from .structural_checks import find_structural_issues
from .summarizer import (
    Summarizer,
    _extract_summary_chapters,
    _key,
    _missing_exam_points,
    _preserves_exam_numbers,
)
from .targeted_review import review_chapter

AGENT_VERSION = "scientific-autopilot-v4-source-gap-diagnostics"
PRIORITY_CHAPTERS = (
    "la cellula e il nucleo",
    "Il ciclo di Krebs",
    "La struttura terziaria e quaternaria delle proteine ed i lipidi",
)


@dataclass(frozen=True, slots=True)
class AgentAttempt:
    number: int
    status: str
    reason: str
    focus: str = ""


@dataclass(frozen=True, slots=True)
class AgentResult:
    title: str
    status: str
    candidate: str
    attempts: tuple[AgentAttempt, ...]
    source_excerpt_count: int
    requires_human_review: bool = True


def _issues(title: str, complete: str, text: str) -> tuple[int, int, int, int]:
    signs = assess_chapter(title, complete, text)
    scientific = {
        "scientific_direction_risk", "scientific_mechanism_risk",
        "detached_glossary_fragment",
    }
    risky = sum(
        finding.category in scientific
        for finding in find_structural_issues(text)
    )
    return (
        len(signs.possible_missing),
        signs.unsupported_phrases,
        len(grammar_findings(text)),
        risky,
    )


def diagnose_content_gaps(
    title: str,
    complete: str,
    essential: str,
    evidence: list[OriginalEvidence],
    *,
    limit: int = 12,
) -> dict:
    """Create a bounded, human-review dossier of *possible* missing facts.

    Lexical agreement with a retrieved original passage is useful for triage,
    but is not semantic entailment, proof of correctness, or a licence to edit.
    Evidence consists only of the limited excerpts already retrieved by the
    pilot; absence in this sample does not mean absence from the original PDF.
    """
    signals = assess_chapter(title, complete, essential)
    essential_statements = _lines(essential)
    original_statements = [
        (f"E{index}", item, sentence)
        for index, item in enumerate(evidence[:4], 1)
        for sentence in _lines(getattr(item, "excerpt", ""))
    ]
    priority = {
        "number_or_formula": 0, "negative_condition": 1,
        "absolute_condition": 2, "causal": 3, "mechanism": 4,
        "definition": 5, "classification": 6, "exception": 7,
    }
    candidates = sorted(
        signals.possible_missing,
        key=lambda fact: (priority.get(fact.category, 9), fact.quote),
    )
    findings = []
    for fact in candidates[:limit]:
        nearest = max(
            essential_statements,
            key=lambda item: _overlap(fact.quote, item),
            default="",
        )
        best = None
        for source_id, item, sentence in original_statements:
            score = _overlap(fact.quote, sentence)
            if score < 0.76 or not _qualifiers_compatible(fact.quote, sentence):
                continue
            if best is None or score > best["lexical_recall"]:
                best = {
                    "evidence_id": source_id,
                    "source_path": item.source_path,
                    "page": item.page,
                    "block": item.block,
                    "original_quote": sentence[:650],
                    "lexical_recall": round(score, 3),
                }
        findings.append({
            "category": fact.category,
            "complete_quote": fact.quote[:850],
            "nearest_essential_quote": nearest[:450],
            "nearest_essential_recall": round(
                _overlap(fact.quote, nearest), 3,
            ),
            "original_excerpt_match": best,
            "review_required": True,
        })
    return {
        "possible_missing_total": len(candidates),
        "reported_missing_count": len(findings),
        "source_matched_in_report": sum(
            item["original_excerpt_match"] is not None for item in findings
        ),
        "unsupported_phrases_heuristic": signals.unsupported_phrases,
        "numerical_mismatch_heuristic": signals.numerical_mismatch,
        "source_excerpt_count": len(evidence),
        "limit": limit,
        "scientifically_verified": False,
        "interpretation": (
            "Possibili lacune della Completa rispetto all'Essenziale, "
            "non omissioni confermate. Match lessicali su estratti "
            "parziali delle dispense; controllare contesto e PDF originali."
        ),
        "candidates": findings,
    }


def _critic_verdict(
    title: str,
    complete: str,
    original: str,
    candidate: str,
    evidence: list[OriginalEvidence],
    summarizer: Summarizer,
) -> tuple[bool, str]:
    """A second role challenges the writer; this is NOT scientific proof."""
    listed = [
        {"id": f"E{index}", "page": item.page,
         "block": item.block, "excerpt": item.excerpt[:1200]}
        for index, item in enumerate(evidence[:4], 1)
    ]
    system = (
        "Sei il revisore avversariale, distinto dal redattore. "
        "Se una frase, negazione, classificazione, formula, valore o "
        "relazione causa-effetto non è sostenuta dalle FONTI ORIGINALI, "
        "devi rifiutare. Non usare conoscenze esterne."
    )
    prompt = (
        f"CAPITOLO: {title}\n"
        f"DISPENSE ORIGINALI: {json.dumps(listed, ensure_ascii=False)}\n"
        f"COMPLETA: {complete[:6500]}\n"
        f"ESSENZIALE PRIMA: {original[:4200]}\n"
        f"PROPOSTA: {candidate[:4200]}\n"
        "Valuta anche omissioni e qualificatori (possibile/certo, "
        "associazione/causa). Rispondi SOLO JSON: "
        '{"approve":false,"reason":"motivo","evidence_ids":["E1"]}. '
        "approve=true SOLO se nessuna obiezione. "
        "Cita almeno un identificativo E verificato, mai un nuovo testo fonte."
    )
    key = _key(
        AGENT_VERSION, "critic", summarizer.model, title, complete,
        original, candidate,
        json.dumps(listed, ensure_ascii=False, sort_keys=True),
    )
    try:
        result = summarizer._cached_chat(
            key, system, prompt, num_predict=400,
        )
        match = re.search(r"\{.*\}", result, flags=re.DOTALL)
        payload = json.loads(match.group()) if match else None
        if not isinstance(payload, dict) or type(payload.get("approve")) is not bool:
            return False, "Revisore: risposta non verificabile"
        ids = payload.get("evidence_ids")
        valid = {item["id"] for item in listed}
        if (
            not isinstance(ids, list)
            or not ids
            or any(not isinstance(item, str) or item not in valid for item in ids)
        ):
            return False, "Revisore: prove originali non tracciabili"
        if not payload["approve"]:
            reason = payload.get("reason")
            return False, ("Revisore: " + reason[:250]) if isinstance(reason, str) else (
                "Revisore: proposta respinta"
            )
        return True, "Seconda verifica AI superata, non certificata"
    except (ValueError, TypeError, OSError, RuntimeError, KeyError):
        return False, "Revisore non disponibile oppure JSON non valido"


def _grounded_windows(
    title: str,
    complete: str,
    essential: str,
    evidence: list[OriginalEvidence],
) -> list[tuple[str, str, list[OriginalEvidence]]]:
    """Choose small, uniquely replaceable prose windows with real-source support.

    Never select study headings, exam bullet lists, or a window whose claims
    cannot be located in the Complete and at least one original page.
    """
    original_statements = _lines(complete)
    windows: list[tuple[str, str, list[OriginalEvidence]]] = []
    if not original_statements:
        return windows
    split = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Ý])")
    for paragraph in re.split(r"\n\s*\n", essential):
        raw = paragraph.strip()
        if (
            not raw or raw.startswith(("#", "-", "*", ">", "---"))
            or "\n#" in raw or "\n-" in raw
        ):
            continue
        spans: list[tuple[int, int]] = []
        start = 0
        for boundary in split.finditer(raw):
            spans.append((start, boundary.start()))
            start = boundary.end()
        spans.append((start, len(raw)))
        # Use exact spans, not whitespace-normalized reconstruction: otherwise
        # a paragraph with hard line wraps cannot be replaced byte-for-byte.
        for position in range(0, len(spans), 4):
            group = spans[position:position + 4]
            text = raw[group[0][0]:group[-1][1]].strip()
            words = len(text.split())
            if not 35 <= words <= 160 or essential.count(text) != 1:
                continue
            claims = _lines(text)
            if not claims or len(claims) > 8:
                continue
            selected: list[str] = []
            unsupported = False
            for claim in claims:
                exact = max(
                    original_statements,
                    key=lambda statement: _overlap(claim, statement),
                )
                if _overlap(claim, exact) < 0.65:
                    unsupported = True
                    break
                if exact not in selected:
                    selected.append(exact)
            if unsupported or not selected or len(selected) > 8:
                continue
            matching_evidence = [
                source for source in evidence
                if any(
                    _overlap(claim, source.excerpt) >= 0.65
                    for claim in claims
                )
            ]
            if not matching_evidence:
                continue
            fragment = f"## {title}\n\n" + "\n".join(selected)
            windows.append((text, fragment, matching_evidence[:4]))
    # Select longer windows that have relevant original-source excerpts first.
    windows.sort(key=lambda item: (-len(item[0].split()), item[0]))
    return windows


def _window_quality(title: str, source: str, window: str) -> tuple[int, int, int, int]:
    """Count deterministic, local editing needs; not scientific truth.

    A generic overlap with a source page is NOT evidence that a paragraph
    needs rewriting. Scientific meaning warnings remain for manual review.
    """
    language = sum(
        item.category in {"grammar", "spelling", "fragment"}
        for item in grammar_findings(window)
    )
    structure = sum(
        item.category in {"repetition", "fragment", "dangling_heading"}
        for item in find_structural_issues(window)
    )
    readability = assess_chapter(title, source, f"## {title}\n\n{window}")
    return (
        language, structure,
        readability.difficult_sentences, readability.long_paragraphs,
    )


def _quality_label(quality: tuple[int, int, int, int]) -> str:
    return (
        f"lingua={quality[0]}, struttura={quality[1]}, "
        f"periodi_lunghi={quality[2]}, paragrafi_lunghi={quality[3]}"
    )


def _review_long_chapter(
    title: str,
    complete: str,
    essential: str,
    config: AppConfig,
    *,
    summarizer: Summarizer,
    original_evidence: list[OriginalEvidence],
    max_attempts: int,
    progress: Callable[[str], None] | None,
) -> AgentResult:
    """One independent, small-window proposal per attempt, never full rewrite."""
    windows = _grounded_windows(title, complete, essential, original_evidence)
    if not windows:
        return AgentResult(
            title, "BLOCKED_NO_GROUNDED_WINDOWS", essential,
            (AgentAttempt(
                0, "BLOCKED",
                "Nessuna porzione breve con prove sufficienti nelle dispense.",
            ),),
            len(original_evidence),
        )
    # A source-backed paragraph alone is not a reason to send it to Spark.
    # Prioritize only windows with a reproducible, local quality concern.
    actionable = [
        (window, source, proofs, quality)
        for window, source, proofs in windows
        if any(quality := _window_quality(title, source, window))
    ]
    actionable.sort(
        key=lambda item: (-sum(item[3]), -len(item[0].split()), item[0])
    )
    if not actionable:
        unresolved = any(_issues(title, complete, essential)) or bool(
            find_structural_issues(essential)
        )
        status = (
            "BLOCKED_NO_ACTIONABLE_WINDOW" if unresolved
            else "SKIPPED_NO_ACTIONABLE_ISSUE"
        )
        return AgentResult(
            title, status, essential,
            (AgentAttempt(
                0, "BLOCKED" if unresolved else "SKIPPED",
                "Possibili criticita globali senza porzione correggibile con "
                "prove locali: necessaria revisione umana."
                if unresolved else
                "Nessuna criticita locale riproducibile: nessuna chiamata a Spark.",
            ),),
            len(original_evidence),
        )
    baseline = _issues(title, complete, essential)
    existing_missing = set(_missing_exam_points(complete, essential))
    attempts: list[AgentAttempt] = []
    for number, (window, source, proofs, quality) in enumerate(
        actionable[:max_attempts], 1,
    ):
        focus = _quality_label(quality)
        if progress:
            progress(
                f"Redattore: porzione {number}/{min(max_attempts, len(actionable))} "
                f"per {title} ({len(window.split())} parole); {focus}"
            )
        # A local window becomes a tiny *independent* edit. Neither the
        # rest of the chapter nor exam bullets are transmitted for rewriting.
        before = f"## {title}\n\n{window}"
        try:
            written = review_chapter(
                title, source, before, config,
                summarizer=summarizer,
                original_evidence=proofs,
                retry_context="Correggi solo la criticita locale: " + focus,
                autopilot_mode=True,
            )
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            attempts.append(AgentAttempt(number, "BLOCKED", str(exc)[:250], focus))
            break
        if not written.applied:
            attempts.append(AgentAttempt(
                number, "REJECTED",
                f"Porzione {number}: {written.reason[:220]}", focus,
            ))
            continue
        result = written.text.strip()
        prefix = f"## {title}"
        if not result.startswith(prefix + "\n"):
            attempts.append(AgentAttempt(
                number, "REJECTED", "Titolo della porzione alterato", focus,
            ))
            continue
        revised_window = result[len(prefix):].strip()
        if (
            not revised_window or "##" in revised_window
            or len(revised_window.split()) > len(window.split())
        ):
            attempts.append(AgentAttempt(
                number, "REJECTED",
                "Porzione allungata o struttura non valida", focus,
            ))
            continue
        if essential.count(window) != 1:
            raise ValueError("Ambiguità della porzione: anteprima bloccata")
        revised_chapter = essential.replace(window, revised_window, 1)
        # Preserve every source number in the changed window, not just
        # numbers in the "Da ricordare" bullets.
        numeric = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?")
        if (
            not set(numeric.findall(window))
            <= set(numeric.findall(revised_window))
            or not set(_missing_exam_points(complete, revised_chapter))
            <= existing_missing
            or any(
                current > previous
                for current, previous in zip(
                    _issues(title, complete, revised_chapter),
                    baseline, strict=True,
                )
            )
        ):
            attempts.append(AgentAttempt(
                number, "REJECTED",
                "Numeri o copertura scientifica peggiorati", focus,
            ))
            continue
        after_quality = _window_quality(title, source, revised_window)
        if (
            any(after > before for after, before in zip(
                after_quality, quality, strict=True,
            ))
            or not any(after < before for after, before in zip(
                after_quality, quality, strict=True,
            ))
        ):
            attempts.append(AgentAttempt(
                number, "REJECTED",
                "Criticita locale non ridotta in modo misurabile", focus,
            ))
            continue
        passed, reason = _critic_verdict(
            title, source, before, written.text,
            proofs, summarizer,
        )
        if not passed:
            attempts.append(AgentAttempt(
                number, "REJECTED",
                f"Revisore della porzione: {reason[:210]}", focus,
            ))
            continue
        if (
            revised_chapter.replace(revised_window, window, 1) != essential
        ):
            raise ValueError("La proposta modifica testo fuori dalla porzione.")
        attempts.append(AgentAttempt(number, "CANDIDATE", reason, focus))
        return AgentResult(
            title, "CANDIDATE_UNVERIFIED", revised_chapter,
            tuple(attempts), len(original_evidence),
        )
    return AgentResult(
        title, "BLOCKED_FOR_MANUAL_REVIEW", essential,
        tuple(attempts), len(original_evidence),
    )


def review_with_agents(
    title: str,
    complete: str,
    essential: str,
    config: AppConfig,
    *,
    summarizer: Summarizer,
    original_evidence: list[OriginalEvidence],
    max_attempts: int = 3,
    progress: Callable[[str], None] | None = None,
) -> AgentResult:
    """Sequential roles with hard evidence requirement, limits and no writes."""
    if not 1 <= max_attempts <= 5:
        raise ValueError("Numero di tentativi ammesso: da 1 a 5.")
    if not original_evidence:
        return AgentResult(title, "BLOCKED_NO_ORIGINAL_EVIDENCE", essential, (), 0)
    # Whole-chapter rewriting caused systematic "unsupported claim" and
    # ">12 citations" failures. Long chapters use isolated evidence-backed
    # windows; a rejected window is never retried unchanged three times.
    if len(essential.split()) >= 320:
        return _review_long_chapter(
            title, complete, essential, config,
            summarizer=summarizer, original_evidence=original_evidence,
            max_attempts=max_attempts, progress=progress,
        )

    history: list[AgentAttempt] = []
    seen: set[str] = set()
    feedback = ""
    baseline = _issues(title, complete, essential)
    if progress:
        progress(f"Analista: {title}; prove originali: {len(original_evidence)}")
    for number in range(1, max_attempts + 1):
        if progress:
            progress(f"Redattore: tentativo {number}/{max_attempts} per {title}")
        try:
            written = review_chapter(
                title, complete, essential, config,
                summarizer=summarizer,
                original_evidence=original_evidence,
                retry_context=feedback,
                autopilot_mode=True,
            )
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            history.append(AgentAttempt(number, "BLOCKED", str(exc)[:250]))
            break
        if not written.applied:
            reason = written.reason[:250]
            history.append(AgentAttempt(number, "REJECTED", reason))
            feedback = reason
            if reason == "Nessuna criticita rilevata":
                history[-1] = AgentAttempt(number, "SKIPPED", reason)
                return AgentResult(
                    title, "SKIPPED_NO_ACTIONABLE_ISSUE", essential,
                    tuple(history), len(original_evidence),
                )
            continue
        candidate = written.text
        digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
        if digest in seen:
            history.append(AgentAttempt(number, "STALLED", "Proposta già valutata"))
            break
        seen.add(digest)
        # The independent, deterministic gate is authoritative.
        after = _issues(title, complete, candidate)
        if (
            not _preserves_exam_numbers(complete, candidate)
            or any(a > b for a, b in zip(after, baseline, strict=True))
            or len(candidate.split()) >= len(essential.split())
        ):
            feedback = "La proposta non riduce il testo senza aumentare rischi o lacune."
            history.append(AgentAttempt(number, "REJECTED", feedback))
            continue
        ok, reason = _critic_verdict(
            title, complete, essential, candidate, original_evidence, summarizer,
        )
        if not ok:
            history.append(AgentAttempt(number, "REJECTED", reason))
            feedback = reason
            continue
        history.append(AgentAttempt(number, "CANDIDATE", reason))
        return AgentResult(
            title, "CANDIDATE_UNVERIFIED", candidate,
            tuple(history), len(original_evidence),
        )
    return AgentResult(
        title, "BLOCKED_FOR_MANUAL_REVIEW", essential,
        tuple(history), len(original_evidence),
    )


def review_priority_course(
    complete_text: str,
    study_text: str,
    config: AppConfig,
    *,
    summarizer: Summarizer,
    evidence_for: Callable[[str], list[OriginalEvidence]],
    max_attempts: int = 3,
    progress: Callable[[str], None] | None = None,
) -> tuple[str, tuple[AgentResult, ...]]:
    """Preview only three known chapters; preserve every other byte."""
    from .scientific_repair import _replace_single_study_chapter

    complete = _extract_summary_chapters(complete_text)
    study = _extract_summary_chapters(study_text)
    complete_keys = [_title_spelling_key(name) for name, _ in complete]
    study_keys = [_title_spelling_key(name) for name, _ in study]
    if (
        len(set(complete_keys)) != len(complete_keys)
        or complete_keys != study_keys
    ):
        raise ValueError("Indici diversi o ambigui: nessuna anteprima multiagente.")
    sources = dict(zip(complete_keys, complete, strict=True))
    studies = dict(zip(study_keys, study, strict=True))
    candidate = study_text
    outcomes: list[AgentResult] = []
    for wanted in PRIORITY_CHAPTERS:
        key = _title_spelling_key(wanted)
        if key not in sources:
            continue
        complete_title, complete_block = sources[key]
        study_title, study_block = studies[key]
        del complete_title
        evidence = evidence_for(study_title)
        outcome = review_with_agents(
            study_title, complete_block, study_block,
            config, summarizer=summarizer, original_evidence=evidence,
            max_attempts=max_attempts, progress=progress,
        )
        outcomes.append(outcome)
        if outcome.status == "CANDIDATE_UNVERIFIED":
            candidate = _replace_single_study_chapter(
                candidate, outcome.candidate, chapter=study_title,
            )
    original = _extract_summary_chapters(study_text)
    edited = _extract_summary_chapters(candidate)
    authorized = {
        item.title for item in outcomes if item.status == "CANDIDATE_UNVERIFIED"
    }
    if (
        [name for name, _ in original] != [name for name, _ in edited]
        or any(
            old != new for (name, old), (_, new) in zip(
                original, edited, strict=True,
            ) if name not in authorized
        )
    ):
        raise ValueError("Modifica non autorizzata: proposta annullata.")
    before = audit_fast_essential(complete_text, study_text)
    after = audit_fast_essential(complete_text, candidate)
    if after.status == "FAIL" and before.status != "FAIL":
        raise ValueError("Nuovo FAIL globale: nessuna proposta generata.")
    return candidate, tuple(outcomes)
