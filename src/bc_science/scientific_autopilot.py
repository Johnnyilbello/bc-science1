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
from .quality_signals import _lines, _overlap, assess_chapter
from .structural_checks import find_structural_issues
from .summarizer import (
    Summarizer,
    _extract_summary_chapters,
    _key,
    _preserves_exam_numbers,
)
from .targeted_review import review_chapter

AGENT_VERSION = "scientific-autopilot-v2-segmented-windows"
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
        statements = [line.strip() for line in split.split(raw) if line.strip()]
        if not statements:
            continue
        # Bounded, disjoint pieces: a huge paragraph must not become one
        # giant prompt and one giant scientific diff.
        for position in range(0, len(statements), 4):
            group = statements[position:position + 4]
            text = " ".join(group)
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
    baseline = _issues(title, complete, essential)
    attempts: list[AgentAttempt] = []
    for number, (window, source, proofs) in enumerate(windows[:max_attempts], 1):
        if progress:
            progress(
                f"Redattore: porzione {number}/{min(max_attempts, len(windows))} "
                f"per {title} ({len(window.split())} parole)"
            )
        # A local window becomes a tiny *independent* edit. Neither the
        # rest of the chapter nor exam bullets are transmitted for rewriting.
        before = f"## {title}\n\n{window}"
        try:
            written = review_chapter(
                title, source, before, config,
                summarizer=summarizer,
                original_evidence=proofs,
                autopilot_mode=True,
            )
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            attempts.append(AgentAttempt(number, "BLOCKED", str(exc)[:250]))
            break
        if not written.applied:
            attempts.append(AgentAttempt(
                number, "REJECTED",
                f"Porzione {number}: {written.reason[:220]}",
            ))
            continue
        result = written.text.strip()
        prefix = f"## {title}"
        if not result.startswith(prefix + "\n"):
            attempts.append(AgentAttempt(
                number, "REJECTED", "Titolo della porzione alterato",
            ))
            continue
        revised_window = result[len(prefix):].strip()
        if (
            not revised_window or "##" in revised_window
            or len(revised_window.split()) >= len(window.split())
        ):
            attempts.append(AgentAttempt(
                number, "REJECTED",
                "Porzione non accorciata o struttura non valida",
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
            or not _preserves_exam_numbers(complete, revised_chapter)
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
                "Numeri o copertura scientifica peggiorati",
            ))
            continue
        passed, reason = _critic_verdict(
            title, source, before, written.text,
            proofs, summarizer,
        )
        if not passed:
            attempts.append(AgentAttempt(
                number, "REJECTED",
                f"Revisore della porzione: {reason[:210]}",
            ))
            continue
        if (
            revised_chapter.replace(revised_window, window, 1) != essential
        ):
            raise ValueError("La proposta modifica testo fuori dalla porzione.")
        attempts.append(AgentAttempt(number, "CANDIDATE", reason))
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
                break
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
