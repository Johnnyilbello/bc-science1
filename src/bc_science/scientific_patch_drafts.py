"""Prepare isolated editorial correction drafts from an existing scientific-flags audit.

NO live modifications: uses verified paths and SHA256 recorded in the audit.
Human approval and independent source verification remain mandatory.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import app_home
from .courses import (
    course_output_path,
    course_study_output_path,
    scan_courses,
)
from .scientific_claim_flags import inspect_scientific_claims

_CORRECTIONS = {
    "ESOPHAGUS_DIRECTION": (
        re.compile(r"\bdallo\s+stomaco\s+allo\s+stomaco\b", re.IGNORECASE),
        "dalla faringe allo stomaco",
    ),
    "BILIARY_DIRECTION": (
        re.compile(r"\bverso\s+l['’]esterno\s+del\s+corpo\b", re.IGNORECASE),
        "dal fegato verso il dotto epatico comune e, attraverso il coledoco, al duodeno",
    ),
    "PALMITATE_STOICHIOMETRY": (
        re.compile(
            r"La sintesi del palmitato richiede 7 molecole di acetil-CoA,\s*"
            r"14 di malonil-CoA,\s*14 di NADPH\s*\+\s*H\+\s*e\s*14 di ATP\.",
            re.IGNORECASE,
        ),
        (
            "Per sintetizzare una molecola di palmitato, l'acido grasso sintasi "
            "utilizza 1 acetil-CoA iniziale, 7 malonil-CoA e 14 NADPH. "
            "La formazione dei 7 malonil-CoA consuma 7 ATP e richiede "
            "7 ulteriori acetil-CoA: 8 acetil-CoA in totale."
        ),
    ),
    "TIDAL_VOLUME_SPIROMETRY": (
        re.compile(
            r"Questo volume non pu[oò] essere misurato dallo spirometro\s+"
            r"perch[eé]\s+lo strumento registra (?:solo|soltanto)\s+"
            r"l['’]aria in movimento",
            re.IGNORECASE,
        ),
        (
            "Il volume corrente (VT) è misurabile mediante spirometria; "
            "è il volume residuo (VR) che non può essere misurato "
            "direttamente con la spirometria"
        ),
    ),
}


@dataclass(frozen=True, slots=True)
class DraftReview:
    subject: str
    edition: str
    original_path: str
    original_sha256: str
    state: str
    reason: str
    rules: tuple[str, ...]
    draft_path: str | None
    diff_path: str | None
    revised_sha256: str | None


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _allowed_documents(root: Path, preview_dir: Path | None) -> dict[tuple[str, str], Path]:
    allowed: dict[tuple[str, str], Path] = {}
    scans = scan_courses(root)
    for scan in scans:
        allowed[(scan.name, "Completa")] = course_output_path(scan).resolve()
        allowed[(scan.name, "Essenziale")] = course_study_output_path(scan).resolve()

    if preview_dir is None:
        return allowed
    base = preview_dir.expanduser().resolve()
    report = json.loads((base / "preview-report.json").read_text(encoding="utf-8"))
    if (
        not isinstance(report, dict)
        or report.get("schema") != "bc-science-essential-preview-v1"
        or not isinstance(report.get("root"), str)
        or Path(report["root"]).resolve() != root.expanduser().resolve()
        or not isinstance(report.get("report_dir"), str)
        or Path(report["report_dir"]).resolve() != base
        or not isinstance(report.get("subjects"), list)
    ):
        raise ValueError("Report anteprime non valido o relativo ad altra cartella")
    for item in report["subjects"]:
        if not isinstance(item, dict):
            raise TypeError("Riga anteprima non valida")
        name, filename = item.get("subject"), item.get("preview_path")
        if filename is None:
            continue
        if not isinstance(name, str) or not isinstance(filename, str):
            raise TypeError("Materia o percorso anteprima non valido")
        resolved = Path(filename).resolve()
        if (
            (name, "Completa") not in allowed
            or not resolved.is_relative_to(base)
            or resolved.suffix.casefold() != ".md"
        ):
            raise ValueError("Anteprima fuori dalla cartella report o materia sconosciuta")
        if (name, "Anteprima") in allowed:
            raise ValueError("Anteprima duplicata")
        allowed[(name, "Anteprima")] = resolved
    return allowed


def prepare_scientific_correction_drafts(
    root: Path,
    audit_json: Path,
    output_dir: Path,
    *,
    preview_dir: Path | None = None,
) -> dict:
    """Create diff and markdown proposals, never edit live sources or manifests."""
    root = root.expanduser().resolve()
    target = output_dir.expanduser().resolve()
    published_root = (app_home() / "outputs").resolve()
    preview = preview_dir.expanduser().resolve() if preview_dir is not None else None
    if (
        target == root or target.is_relative_to(root) or root.is_relative_to(target)
        or target == published_root or target.is_relative_to(published_root)
        or published_root.is_relative_to(target)
        or (preview is not None and (
            target == preview or target.is_relative_to(preview)
            or preview.is_relative_to(target)
        ))
    ):
        raise ValueError("Directory di revisione non isolata dai documenti originali")
    if target.exists():
        raise ValueError("Directory di revisione già esistente: nessuna sovrascrittura")

    audit = json.loads(audit_json.read_text(encoding="utf-8"))
    if (
        not isinstance(audit, dict)
        or audit.get("schema") != "bc-science-known-scientific-flags-v1"
        or not isinstance(audit.get("flags"), list)
    ):
        raise ValueError("Report scientifico non valido")

    allowed = _allowed_documents(root, preview)
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for flag in audit["flags"]:
        if not isinstance(flag, dict):
            raise TypeError("Segnalazione non valida")
        subject = flag.get("subject")
        edition = flag.get("edition")
        if not isinstance(subject, str) or not isinstance(edition, str):
            raise TypeError("Materia o edizione non valide")
        grouped[(subject, edition)].append(flag)

    plan: list[DraftReview] = []
    drafts: list[tuple[Path, str, Path, str]] = []
    file_index = 0
    for (subject, edition), flags in grouped.items():
        rules = tuple(sorted({str(f.get("rule") or "") for f in flags}))
        first = flags[0]
        orig = str(first.get("path") or "")
        expected_sha = str(first.get("document_sha256") or "")
        source = allowed.get((subject, edition))
        state = "BLOCKED"
        reason = ""
        draft_path: str | None = None
        diff_path: str | None = None
        new_sha: str | None = None

        # Do not follow arbitrary paths from the JSON; only allow the paths
        # derived from the current course scan or verified preview manifest.
        if source is None or not orig or Path(orig).resolve() != source:
            reason = "Percorso non ammesso per materia/edizione"
        elif any(
            f.get("path") != orig or f.get("document_sha256") != expected_sha
            for f in flags
        ):
            reason = "Segnalazioni non coerenti sullo stesso documento"
        elif not source.is_file():
            reason = "Documento originale non trovato"
        else:
            before = source.read_text(encoding="utf-8")
            if not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
                reason = "SHA256 assente o non valido"
            elif _digest(before) != expected_sha:
                reason = "Documento modificato dopo l'audit: nessuna proposta"
            else:
                revised = before
                for rule in rules:
                    if rule not in _CORRECTIONS:
                        reason = f"Regola senza proposta: {rule}"
                        break
                    matcher, replacement = _CORRECTIONS[rule]
                    revised, count = matcher.subn(replacement, revised)
                    if count != 1:
                        reason = f"Occorrenze esatte non univoche per {rule}: {count}"
                        break
                if not reason:
                    old_rules = {flag.rule for flag in inspect_scientific_claims(before)}
                    new_rules = {flag.rule for flag in inspect_scientific_claims(revised)}
                    if (set(rules) & new_rules) or (new_rules - old_rules):
                        reason = "La proposta non elimina l'errore o genera nuovi allarmi"
                    elif revised == before:
                        reason = "Nessun cambiamento"
                    else:
                        # An individually reviewed chapter can later be
                        # approved; never change originals here.
                        state = "REVIEW"
                        reason = "Proposta isolata da validare su fonti e bibliografia"
                        file_index += 1
                        stem = f"proposta-{file_index:02d}"
                        draft = target / f"{stem}.md"
                        diff = target / f"{stem}.diff"
                        draft_path = str(draft)
                        diff_path = str(diff)
                        new_sha = _digest(revised)
                        patch = "".join(difflib.unified_diff(
                            before.splitlines(keepends=True),
                            revised.splitlines(keepends=True),
                            fromfile=f"{subject}/{edition}/originale",
                            tofile=f"{subject}/{edition}/proposta",
                        ))
                        drafts.append((draft, revised, diff, patch))
        plan.append(DraftReview(
            subject, edition, orig, expected_sha, state, reason, rules,
            draft_path, diff_path, new_sha,
        ))

    target.mkdir(parents=True, exist_ok=False)
    for draft, revised, diff, patch in drafts:
        draft.write_text(revised, encoding="utf-8")
        diff.write_text(patch, encoding="utf-8")
    result = {
        "schema": "bc-science-scientific-correction-proposals-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_report": str(audit_json.resolve()),
        "source_root": str(root),
        "report_dir": str(target),
        "published_modified": False,
        "scientifically_verified": False,
        "proposals": [asdict(item) for item in plan],
    }
    (target / "review-report.json").write_text(
        json.dumps(result, ensure_ascii=True, indent=2) + "\n", encoding="utf-8",
    )
    lines = [
        "# Proposte scientifiche — NON PUBBLICATE", "",
        "Questi file sono bozze. Controllare sui PDF originali e su fonti scientifiche,",
        "inclusi i conflitti nel materiale didattico. Non è una certificazione.", "",
    ]
    for item in plan:
        lines.extend([
            f"## {item.subject} · {item.edition} — {item.state}", "",
            f"Regole: {', '.join(item.rules)}", f"Motivo: {item.reason}",
            f"Bozza: {item.draft_path or '—'}",
            f"Diff: {item.diff_path or '—'}", "",
        ])
    (target / "review-report.md").write_text("\n".join(lines), encoding="utf-8")
    return result
