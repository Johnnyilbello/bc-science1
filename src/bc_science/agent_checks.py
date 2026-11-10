"""Read-only offline evidence checks for BC Science's agent mesh.

Every group has ten different predicates. A missing prerequisite is SKIP, never
an invented PASS. These checks do not certify the correctness of source science.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .config import AppConfig, app_home
from .course_audit import AUDIT_VERSION, FAST_ESSENTIAL_AUDIT_VERSION
from .courses import (
    CourseScan,
    _coverage_map_from_manifest,
    _fingerprint,
    _load_manifest,
    _manifest_path,
    _snapshot,
    audit_course_summary,
    course_audit_json_path,
    course_output_path,
    course_pdf_path,
    course_study_output_path,
    course_study_pdf_path,
)
from .documents import SUPPORTED
from .summarizer import _extract_summary_chapters, group_course_files, topic_key

Check = tuple[str, str]
VALID_STATUSES = {"PASS", "WARN", "FAIL", "SKIP", "N/A"}
_PLACEHOLDER = re.compile(r"\bTODO\b|lorem ipsum|\bTBD\b|\[INSERIRE\]", re.IGNORECASE)
_SCRIPT = re.compile(r"<script\b|javascript\s*:|<iframe\b", re.IGNORECASE)
_NUMBER = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?")
_SECRET_KEYS = {"api_key", "password", "secret", "private_key", "access_token"}


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8") if path.is_file() else None
    except (OSError, UnicodeError):
        return None


def _read_json(path: Path) -> dict | None:
    content = _read_text(path)
    if content is None:
        return None
    try:
        parsed = json.loads(content)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _pass(ok: bool, description: str, *, warning: bool = False) -> Check:
    return ("PASS" if ok else ("WARN" if warning else "FAIL"), description)


def _skip(reason: str) -> Check:
    return "SKIP", reason


def _not_applicable(reason: str) -> Check:
    """Explicitly disabled optional workflow; never counted as verified PASS."""
    return "N/A", reason


def _numeric_evidence(audit: dict) -> str:
    """Keep numeric omissions separate from exam assertions in the evidence."""
    numeric = audit.get("missing_numeric_facts", 0)
    exam = audit.get("missing_exam_facts", 0)
    chapters = audit.get("missing_exam_number_chapters") or ()
    facts = audit.get("missing_facts")
    samples: dict[str, list[str]] = {"numero": [], "esame": []}
    if isinstance(facts, list):
        for row in facts:
            if not isinstance(row, dict):
                continue
            category = str(row.get("category") or "")
            if category not in samples or len(samples[category]) >= 3:
                continue
            chapter = str(row.get("chapter") or "capitolo sconosciuto")
            statement = str(row.get("text") or "").strip()
            if statement:
                samples[category].append(f"{chapter}: {statement[:140]}")

    parts = [
        f"Fatti numerici non coperti: {numeric}",
        f"punti d'esame: {exam}",
        f"capitoli numerici segnalati: {len(chapters)}",
    ]
    if samples["numero"]:
        parts.append("Esempi NUMERICI: " + "; ".join(samples["numero"]))
    if samples["esame"]:
        parts.append("Esempi D'ESAME: " + "; ".join(samples["esame"]))
    if not samples["numero"] and not samples["esame"]:
        parts.append("Aprire audit.json per le evidenze originali")
    return ". ".join(parts)


def _inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def _pdf_header(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            return stream.read(5) == b"%PDF-"
    except OSError:
        return False


def _has_secret_key(obj: object) -> bool:
    if isinstance(obj, dict):
        return any(
            any(s in str(key).lower() for s in _SECRET_KEYS) or _has_secret_key(value)
            for key, value in obj.items()
        )
    if isinstance(obj, list):
        return any(_has_secret_key(value) for value in obj)
    return False


@dataclass(slots=True)
class AgentContext:
    scan: CourseScan
    snapshot: list[dict]
    manifest: dict | None
    raw_manifest: dict | None
    manifest_path: Path
    mapping: dict[str, str]
    complete_path: Path
    complete: str | None
    complete_chapters: list[tuple[str, str]]
    complete_pdf: Path
    study_path: Path
    study: str | None
    study_chapters: list[tuple[str, str]]
    study_pdf: Path
    audit_path: Path
    audit: dict | None
    research_path: Path
    research: str | None
    research_enabled: bool
    ollama_url: str
    coverage_complete: bool
    covered_topics: tuple[str, ...]
    links: tuple[Path, ...]


def make_context(scan: CourseScan) -> AgentContext:
    snapshot = _snapshot(scan)
    manifest_path = _manifest_path(scan)
    raw_manifest = _read_json(manifest_path)
    manifest = _load_manifest(scan)
    complete_path = course_output_path(scan)
    complete = _read_text(complete_path)
    study_path = course_study_output_path(scan)
    study = _read_text(study_path)
    audit_path = course_audit_json_path(scan)
    coverage = (
        audit_course_summary(scan, complete, manifest=manifest, snapshot=snapshot)
        if complete is not None else None
    )
    links: list[Path] = []
    for current, dirs, files in os.walk(scan.path, followlinks=False):
        for name in dirs + files:
            path = Path(current) / name
            if path.is_symlink():
                links.append(path)
        dirs[:] = [
            name for name in dirs
            if not name.startswith(".") and name.casefold() not in {
                ".venv", "venv", "outputs", "output", "final", "node_modules",
                "__pycache__", ".git",
            }
        ]
    try:
        config = AppConfig.load()
    except (OSError, TypeError, ValueError):
        config = AppConfig()

    research_path = app_home() / "outputs" / "research" / scan.name / (
        "approfondimento-scientifico.md"
    )
    return AgentContext(
        scan=scan, snapshot=snapshot,
        manifest=manifest, raw_manifest=raw_manifest,
        manifest_path=manifest_path,
        mapping=_coverage_map_from_manifest(manifest),
        complete_path=complete_path, complete=complete,
        complete_chapters=_extract_summary_chapters(complete) if complete else [],
        complete_pdf=course_pdf_path(scan),
        study_path=study_path, study=study,
        study_chapters=_extract_summary_chapters(study) if study else [],
        study_pdf=course_study_pdf_path(scan),
        audit_path=audit_path, audit=_read_json(audit_path),
        research_path=research_path, research=_read_text(research_path),
        research_enabled=config.scientific_research_enabled,
        ollama_url=config.ollama_url,
        coverage_complete=bool(coverage and coverage.complete),
        covered_topics=coverage.covered_topics if coverage else (),
        links=tuple(links),
    )


def _sources(c: AgentContext, k: int) -> Check:
    rows = c.snapshot
    paths = [Path(c.scan.path) / str(r["path"]) for r in rows]
    if k == 0:
        return _pass(bool(rows), "Almeno una fonte supportata")
    if not rows:
        return _skip("Nessuna fonte da controllare")
    if k == 1:
        return _pass(all(p.exists() for p in paths), "Tutti i percorsi sorgente esistono")
    if k == 2:
        return _pass(all(p.is_file() for p in paths), "Le fonti sono file regolari")
    if k == 3:
        return _pass(all(int(r["size"]) > 0 for r in rows), "File non vuoti")
    if k == 4:
        return _pass(all(p.suffix.casefold() in SUPPORTED for p in paths), "Formati validi")
    if k == 5:
        normalized = [str(r["path"]).casefold() for r in rows]
        return _pass(len(set(normalized)) == len(rows), "Percorsi univoci")
    if k == 6:
        groups: dict[str, list[str]] = {}
        for row in rows:
            groups.setdefault(str(row["sha256"]), []).append(str(row["path"]))
        duplicates = [names for names in groups.values() if len(names) > 1]
        if not duplicates:
            return "PASS", "Nessun SHA256 duplicato"
        samples = "; ".join(" = ".join(names[:3]) for names in duplicates[:3])
        return "WARN", (
            f"{len(duplicates)} gruppi di file con SHA256 identico "
            f"(non modificati): {samples}"
        )
    if k == 7:
        return _pass(not c.links, "Nessun collegamento simbolico nelle fonti")
    if k == 8:
        return _pass(all(_inside(p, c.scan.path) for p in paths), "Fonti nel workspace")
    return _pass(
        all(int(r["size"]) <= 50 * 1024 * 1024 for r in rows),
        "Nessun file supera 50 MiB",
        warning=True,
    )


def _manifest(c: AgentContext, k: int) -> Check:
    m = c.manifest
    if k == 0:
        return _pass(c.manifest_path.is_file(), "Manifest presente")
    if k == 1:
        return _pass(c.raw_manifest is not None, "Manifest JSON leggibile")
    if m is None:
        return _skip("Manifest assente, invalido o in schema non supportato")
    if k == 2:
        return _pass(m.get("schema") in {2, 3}, "Schema con tracciabilità")
    if k == 3:
        return _pass(m.get("course") == c.scan.name, "Identità della materia")
    if k == 4:
        return _pass(m.get("source") == str(c.scan.path), "Radice sorgente invariata")
    if k == 5:
        return _pass(isinstance(m.get("files"), list), "Elenco fonti strutturato")
    if k == 6:
        return _pass(m.get("fingerprint") == _fingerprint(c.snapshot), "Fingerprint")
    old = {
        r.get("path"): r for r in m.get("files", [])
        if isinstance(r, dict) and isinstance(r.get("path"), str)
    } if isinstance(m.get("files"), list) else {}
    if k == 7:
        return _pass(set(old) == {r["path"] for r in c.snapshot}, "Insieme fonti")
    if k == 8:
        return _pass(
            all(old.get(r["path"], {}).get("sha256") == r["sha256"] for r in c.snapshot),
            "Hash dei documenti",
        )
    return _pass(
        all(old.get(r["path"], {}).get("size") == r["size"] for r in c.snapshot),
        "Dimensioni dei documenti",
    )


def _mapping(c: AgentContext, k: int) -> Check:
    m = c.manifest
    if k == 0:
        return _pass(bool(c.mapping), "Mappa documenti → capitoli presente")
    if m is None:
        return _skip("Manifest mancante")
    raw = m.get("coverage")
    if k == 1:
        return _pass(
            isinstance(raw, list) and all(
                isinstance(x, dict) and
                isinstance(x.get("source"), str) and
                isinstance(x.get("chapter"), str)
                for x in raw
            ), "Righe di copertura ben formate",
        )
    if not isinstance(raw, list):
        return _skip("Mappa di copertura non disponibile")
    keys = [r.get("source") for r in raw if isinstance(r, dict)]
    current = {str(r["path"]) for r in c.snapshot}
    if k == 2:
        return _pass(len(keys) == len(set(keys)), "Nessuna fonte duplicata in mappa")
    if k == 3:
        return _pass(set(keys).issubset(current), "Nessuna fonte orfana")
    if k == 4:
        return _pass(current.issubset(c.mapping), "Ogni fonte collegata")
    if k == 5:
        return _pass(all(bool(v.strip()) for v in c.mapping.values()), "Capitoli nominati")
    if c.complete is None:
        return _skip("Riassunto completo non disponibile")
    keys_chapters = [topic_key(title) for title, _ in c.complete_chapters]
    if k == 6:
        return _pass(
            all(topic_key(ch) in keys_chapters for ch in c.mapping.values()),
            "Capitoli referenziati esistono",
        )
    if k == 7:
        return _pass(
            len(keys_chapters) == len(set(keys_chapters)),
            "Nessuna duplicazione di capitolo",
        )
    if k == 8:
        groups = group_course_files(list(c.scan.supported_files))
        return _pass(
            all(
                all(f.relative_to(c.scan.path).as_posix() in c.mapping for f in group.files)
                for group in groups
            ),
            "Ogni argomento rappresentato",
        )
    return _pass(c.coverage_complete, "Copertura verificata con hash e capitoli")


def _complete(c: AgentContext, k: int) -> Check:
    text = c.complete
    if k == 0:
        return _pass(c.complete_path.is_file(), "Riassunto completo presente")
    if k == 1:
        return _pass(text is not None, "Testo UTF-8 leggibile")
    if text is None:
        return _skip("Completa non disponibile")
    if k == 2:
        return _pass(bool(text.strip()), "Riassunto non vuoto")
    if k == 3:
        return _pass(bool(re.search(r"(?m)^#\s+\S", text)), "Titolo H1")
    if k == 4:
        return _pass(bool(c.complete_chapters), "Capitoli presenti")
    if k == 5:
        keys = [topic_key(title) for title, _ in c.complete_chapters]
        return _pass(len(keys) == len(set(keys)), "Capitoli univoci")
    if k == 6:
        return _pass(all(len(body.strip()) > 10 for _, body in c.complete_chapters),
                     "Capitoli con contenuto")
    if k == 7:
        return _pass("indice degli argomenti" in text.casefold(), "Indice presente")
    if k == 8:
        return _pass(not _PLACEHOLDER.search(text), "Nessun segnaposto editoriale")
    if c.manifest is None:
        return _skip("SHA manifest non disponibile")
    return _pass(c.manifest.get("summary_sha256") == _sha(text), "SHA256 Completa")


def _complete_pdf(c: AgentContext, k: int) -> Check:
    p = c.complete_pdf
    if k == 0:
        return _pass(p.is_file(), "PDF completo presente")
    if not p.is_file():
        return _skip("PDF completo mancante")
    if k == 1:
        return _pass(_pdf_header(p), "Header PDF valido")
    if k == 2:
        return _pass(p.stat().st_size > 0, "PDF non vuoto")
    pdf = (c.manifest or {}).get("pdf")
    if k == 3:
        return _pass(isinstance(pdf, dict), "Metadati PDF")
    if k in {4, 5, 6} and not isinstance(pdf, dict):
        return _skip("Metadati PDF mancanti")
    if k == 4:
        return _pass(pdf.get("path") == str(p), "Percorso PDF verificato")
    if k == 5:
        return _pass(isinstance(pdf.get("pages"), int) and pdf["pages"] > 0,
                     "Numero pagine verificato")
    if k == 6:
        return _pass(
            c.complete is not None and pdf.get("summary_sha256") == _sha(c.complete),
            "PDF collegato al riassunto",
        )
    if k == 7:
        return _pass(not p.with_name(p.name + ".tmp").exists(),
                     "Nessun residuo temporaneo", warning=True)
    if k == 8:
        return _pass(p.parent == c.complete_path.parent, "Cartella PDF isolata")
    return _pass(p.stat().st_size > 100, "PDF non sospettosamente piccolo", warning=True)


def _study(c: AgentContext, k: int) -> Check:
    text = c.study
    if k == 0:
        return _pass(c.study_path.is_file(), "Essenziale presente")
    if k == 1:
        return _pass(text is not None, "Essenziale UTF-8")
    if text is None:
        return _skip("Essenziale non disponibile")
    if k == 2:
        return _pass(bool(text.strip()), "Essenziale non vuoto")
    if k == 3:
        return _pass(bool(re.search(r"(?m)^#\s+\S", text)), "Titolo Essenziale")
    if k == 4:
        return _pass(bool(c.study_chapters), "Capitoli Essenziale presenti")
    if k == 5:
        return _pass(
            all(len(body.strip()) > 10 for _, body in c.study_chapters),
            "Capitoli Essenziale con testo",
        )
    if k == 6:
        return _pass(not _PLACEHOLDER.search(text), "Nessun segnaposto Essenziale")
    info = (c.manifest or {}).get("study")
    if k in {7, 8} and not isinstance(info, dict):
        return _skip("Metadati Essenziale mancanti")
    if k == 7:
        return _pass(
            c.complete is not None
            and info.get("source_summary_sha256") == _sha(c.complete),
            "Provenienza dal completo corrente",
        )
    if k == 8:
        return _pass(info.get("summary_sha256") == _sha(text), "SHA256 Essenziale")
    if c.audit is None:
        return _skip("Audit numerico mancante: non verificato")
    if not (
        "missing_numeric_facts" in c.audit
        or "missing_exam_number_chapters" in c.audit
    ):
        return _skip("Audit senza campo esplicito sulle omissioni numeriche")
    misses = c.audit.get("missing_numeric_facts", 0)
    chapters = c.audit.get("missing_exam_number_chapters", ())
    return _pass(
        misses == 0 and not chapters,
        "Nessuna omissione numerica rilevata" if misses == 0 and not chapters
        else _numeric_evidence(c.audit),
    )


def _study_pdf(c: AgentContext, k: int) -> Check:
    p = c.study_pdf
    if k == 0:
        return _pass(p.is_file(), "PDF Essenziale presente")
    if not p.is_file():
        return _skip("PDF Essenziale mancante")
    if k == 1:
        return _pass(_pdf_header(p), "Header PDF Essenziale")
    if k == 2:
        return _pass(p.stat().st_size > 0, "PDF Essenziale non vuoto")
    info = (c.manifest or {}).get("study") or {}
    pdf = info.get("pdf") if isinstance(info, dict) else None
    if k == 3:
        return _pass(isinstance(pdf, dict), "Metadati PDF Essenziale")
    if k in {4, 5, 6} and not isinstance(pdf, dict):
        return _skip("Metadati PDF Essenziale mancanti")
    if k == 4:
        return _pass(pdf.get("path") == str(p), "Percorso PDF Essenziale")
    if k == 5:
        return _pass(isinstance(pdf.get("pages"), int) and pdf["pages"] > 0,
                     "Numero pagine Essenziale")
    if k == 6:
        return _pass(
            c.study is not None and pdf.get("summary_sha256") == _sha(c.study),
            "PDF Essenziale legato al Markdown",
        )
    if k == 7:
        return _pass(p.parent == c.study_path.parent, "PDF e MD vicini")
    if k == 8:
        return _pass(p.stem == c.study_path.stem, "Nomina PDF coerente")
    return _pass(p.stat().st_size <= 500 * 1024 * 1024,
                 "Dimensione PDF ragionevole", warning=True)


def _audit(c: AgentContext, k: int) -> Check:
    a = c.audit
    if k == 0:
        return _pass(c.audit_path.is_file(), "Audit persistito")
    if k == 1:
        return _pass(a is not None, "Audit JSON valido")
    if a is None:
        return _skip("Report audit mancante o non valido")
    if k == 2:
        version = a.get("audit_version")
        if not isinstance(version, str) or not version:
            return "FAIL", "Versione audit mancante"
        return _pass(
            version in {AUDIT_VERSION, FAST_ESSENTIAL_AUDIT_VERSION},
            f"Versione audit: {version}"
            + (" (obsoleta: ricalcolare l'audit, non il riassunto)"
               if version not in {AUDIT_VERSION, FAST_ESSENTIAL_AUDIT_VERSION}
               else " (corrente)"),
            warning=True,
        )
    if k == 3:
        return _pass(a.get("status") in {"PASS", "WARN", "FAIL"}, "Stato riconosciuto")
    if k == 4:
        return _pass(
            c.complete is not None and a.get("complete_sha256") == _sha(c.complete),
            "Audit della Completa corrente",
        )
    if k == 5:
        return _pass(
            c.study is not None and a.get("study_sha256") == _sha(c.study),
            "Audit della Essenziale corrente",
        )
    if k == 6:
        return _pass(a.get("source_documents") == len(c.snapshot), "Numero sorgenti")
    if k == 7:
        return _pass(a.get("source_coverage_complete") is True, "Copertura sorgenti")
    if k == 8:
        if not (
            "missing_numeric_facts" in a
            or "missing_exam_number_chapters" in a
        ):
            return _skip("Audit privo di metrica sulle omissioni")
        ok = (
            a.get("missing_numeric_facts", 0) == 0
            and a.get("missing_exam_facts", 0) == 0
            and not a.get("missing_exam_number_chapters", ())
        )
        return _pass(
            ok,
            "Nessuna omissione numerica o d'esame rilevata"
            if ok else _numeric_evidence(a),
        )
    state = a.get("status")
    if state == "PASS":
        return "PASS", "Audit finale PASS"
    raw_warnings = a.get("warnings")
    details = (
        "; ".join(str(item) for item in raw_warnings[:3])
        if isinstance(raw_warnings, list) else ""
    )
    message = f"Audit finale {state or 'sconosciuto'}"
    if details:
        message += f": {details}"
    if state == "WARN":
        return "WARN", message
    return "FAIL", message


def _research(c: AgentContext, k: int) -> Check:
    if not c.research_enabled:
        return _not_applicable("Ricerca PubMed disattivata: funzione facoltativa")
    r = c.research
    if k == 0:
        return _pass(c.research_path.is_file(), "Approfondimento separato presente")
    if r is None:
        return _skip("Approfondimento separato mancante")
    if k == 1:
        return _pass(bool(r.strip()), "Documento di ricerca non vuoto")
    if k == 2:
        return _pass("pubmed" in r.casefold(), "Indicazione PubMed")
    if k == 3:
        return _pass(bool(re.search(r"\bPMID\b", r, re.IGNORECASE)), "PMID presenti")
    if k == 4:
        return _pass(bool(re.search(r"\[\d+\]", r)), "Citazioni numerate presenti")
    if k == 5:
        return _pass(not _PLACEHOLDER.search(r), "Nessun segnaposto ricerca")
    if k == 6:
        return _pass(r != c.complete, "Ricerca separata dalla Completa")
    if k == 7:
        return _pass(r != c.study, "Ricerca separata dall'Essenziale")
    if k == 8:
        cited = set(re.findall(r"\[(\d+)\]", r))
        biblio = set(re.findall(r"(?m)^\s*\[(\d+)\]", r))
        return _pass(bool(cited) and cited.issubset(biblio),
                     "Rimandi di ricerca con voci numerate")
    return _pass(
        _inside(c.research_path, app_home() / "outputs" / "research"),
        "Materiale esterno nella propria cartella",
    )


def _security(c: AgentContext, k: int) -> Check:
    paths = [Path(c.scan.path) / str(r["path"]) for r in c.snapshot]
    if k == 0:
        return _pass(
            not _inside(c.complete_path, c.scan.path)
            and not _inside(c.study_path, c.scan.path),
            "Gli output non contaminano la cartella fonti",
        )
    if k == 1:
        return _pass(
            all(
                not any(mark in p.name.casefold() for mark in (
                    "-riassunto-unico", "-riassunto-studio", "-dispensa-finale",
                    "-riassunto-rifinito",
                ))
                for p in paths
            ), "Nessun output generato tra le sorgenti",
        )
    if k == 2:
        return _pass(not c.links, "Non sono ammessi symlink nel corpus")
    if k == 3:
        return _pass(all(_inside(p, c.scan.path) for p in paths),
                     "Ogni sorgente resta nella materia")
    if k == 4:
        return _pass(
            c.manifest is None or c.manifest.get("source") == str(c.scan.path),
            "Manifest vincolato alla materia",
        )
    if k == 5:
        return _pass(
            c.raw_manifest is None or not _has_secret_key(c.raw_manifest),
            "Manifest senza chiavi segrete",
        )
    if k in {6, 7, 8}:
        if k == 8 and not c.research_enabled:
            return _not_applicable("Documento PubMed facoltativo non abilitato")
        texts = {6: c.complete, 7: c.study, 8: c.research}
        if texts[k] is None:
            return _skip("Documento non disponibile")
        return _pass(not _SCRIPT.search(texts[k]), "Assenza di script HTML attivi")
    return _pass(
        c.ollama_url.startswith(("http://127.0.0.1:", "http://localhost:")),
        "Endpoint Ollama locale, senza invio a server remoto",
        warning=True,
    )


_CHECKERS = {
    "SRC": _sources,
    "MAN": _manifest,
    "MAP": _mapping,
    "CMP": _complete,
    "PDF": _complete_pdf,
    "STD": _study,
    "SPF": _study_pdf,
    "AUD": _audit,
    "RES": _research,
    "SEC": _security,
}


def execute_check(ctx: AgentContext, group: str, rule: int) -> Check:
    if group not in _CHECKERS or not 0 <= rule <= 9:
        raise ValueError(f"Regola non disponibile: {group}/{rule}")
    return _CHECKERS[group](ctx, rule)
