"""V10 closed-corpus authored compression pilot; no model or publication.

The reduction range is a soft target, never a reason to discard a statement.
An inventory unit is a source sentence, not a certified atomic scientific fact.
Authored plans are bound to exact source chapter hashes, with exhaustive
source-to-destination mappings. Those mappings are review evidence, never an
automatic proof of scientific equivalence. Unknown chapters fail closed.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .autopilot_runner import _original_evidence, _validated_manifest
from .courses import CourseScan, _fingerprint, _snapshot
from .fast_audit import _ordered_title_match, audit_fast_essential
from .summarizer import _extract_summary_chapters

VERSION = "structural-compression-v10"
TITLES = (
    "Il ciclo di Krebs",
    "La struttura terziaria e quaternaria delle proteine ed i lipidi",
    "CHIMICA ORGANICA e carboidrati",
)
V9_VERSION = "whole-chapter-review-v9-20261103"
PLAN_SHA256 = "829fdf579894b03ba5558c3b5fd701399fdeaa3575190ef15b851b4d2121d4d5"
PROTECTED = re.compile(
    r"\b(?:\d+(?:[.,]\d+)?|non|solo|esclusivamente|sempre|mai|circa|"
    r"generalmente|possono|può|fino|alcuni|senza|se|uno|due|tre|quattro|otto)\b"
    r"|\$[^$]+\$", re.IGNORECASE,
)


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def plain(text: str) -> str:
    # Do not remove underscores: CH_2O and CH2O are different representations.
    return " ".join(text.replace("**", "").split())


@dataclass(frozen=True)
class Unit:
    id: str
    text: str
    line: int
    section: str


def inventory(chapter: str, *, first_line: int = 1) -> list[Unit]:
    """Conservative sentence units; no splitting on decimals or p.1 citations.

    Keep a whole source line if it contains math or a parenthetical expression:
    cutting those could lose the scope of a qualifier or a formula.
    """
    result = []
    section = ""
    for line_no, raw in enumerate(chapter.splitlines(), first_line):
        if not raw.strip():
            continue
        if raw.startswith("#"):
            section = raw.lstrip("# ").strip()
            continue
        line = re.sub(r"^\s*(?:[-*]|\d+[.)])\s+", "", raw).strip()
        text = plain(line)
        pieces = [text] if any(c in text for c in "($") else re.split(
            r"(?<=[.!?])\s+(?=[A-ZÀ-Ý])", text,
        )
        for piece in pieces:
            if piece:
                result.append(Unit(f"F{len(result) + 1:03}", piece, line_no, section))
    if not result:
        raise ValueError("Inventario vuoto: nessuna compressione consentita.")
    return result


def synthesize(title: str, units: list[Unit]) -> tuple[str, list[dict]]:
    """Coalesce exact full sentences only; preserve order and all content words.

    Do not coalesce sentences with contextual references, even identical ones:
    'questo' after two different statements need not refer to the same fact.
    """
    destinations: dict[str, str] = {}
    records = []
    paragraphs = []
    contextual = re.compile(
        r"\b(quest[oaie]|questi|loro|essi|esse|ciò|tale|tali|esso|essa)\b",
        re.IGNORECASE,
    )
    for unit in units:
        duplicate = destinations.get(unit.text)
        if contextual.search(unit.text):
            duplicate = None
        if duplicate:
            action = "EXACT_SENTENCE_COALESCED"
            target = duplicate
        else:
            target = unit.id
            action = "RETAINED_LITERAL"
            paragraphs.append(unit.text)
            destinations[unit.text] = target
        records.append({
            **asdict(unit), "action": action, "destination_id": target,
            "destination_text": unit.text,
            "protected_tokens": PROTECTED.findall(unit.text),
            "scientifically_verified": False,
        })
    # One progressive explanation; do not recreate the source's repeated recap.
    body = f"## {title}\n\n### Sintesi progressiva\n\n" + "\n\n".join(paragraphs)
    validate_synthesis(title, units, body, records)
    return body, records


def validate_synthesis(title: str, units: list[Unit], body: str, records: list[dict]) -> None:
    """Bidirectional sentence equality, not bag-of-words/number-set approval."""
    if not body.startswith(f"## {title}\n") or len(records) != len(units):
        raise ValueError("Struttura o inventario alterati.")
    expected = []
    by_id = {unit.id: unit for unit in units}
    if len(by_id) != len(units):
        raise ValueError("ID inventario ambigui.")
    retained: dict[str, str] = {}
    for unit, record in zip(units, records, strict=True):
        target = record["destination_id"]
        if record["id"] != unit.id or record["destination_text"] != unit.text:
            raise ValueError("Fatto, numero o qualificatore alterato.")
        if record["action"] == "RETAINED_LITERAL" and target == unit.id:
            expected.append(unit.text)
            retained[target] = unit.text
        elif record["action"] != "EXACT_SENTENCE_COALESCED" or (
            retained.get(target) != unit.text
        ):
            raise ValueError("Omissione o equivalenza non verificata.")
    actual = [unit.text for unit in inventory(body)]
    if actual != expected:
        raise ValueError("Aggiunta, omissione, ordine o testo non verificati.")


def authored_synthesis(title: str, source: str, units: list[Unit]) -> tuple[str, list[dict]]:
    """Use only the three authored, hash-pinned plans, never arbitrary model text."""
    data = Path(__file__).with_name("structural_compression_pilot.json").read_bytes()
    if sha(data) != PLAN_SHA256:
        raise ValueError("Piano V10 alterato: richiede revisione del codice e delle fonti.")
    plans = json.loads(data)
    plan = next(p for p in plans if p["title"] == title)
    if sha(source.encode("utf-8")) != plan["source_sha256"]:
        raise ValueError("Capitolo Completa diverso dal pilot verificato: nessuna sintesi.")
    known = {u.id for u in units}
    covered = {sid for g in plan["groups"] for sid in g["source_ids"]}
    editorial = set(plan.get("editorial_ids", []))
    if covered | editorial != known or covered & editorial:
        raise ValueError("Copertura inventario incompleta o ambigua.")
    records = []
    for unit in units:
        destinations = [
            {"id": f"P{i}", "text": group["text"],
             "rationale": group["coverage_rationale"]}
            for i, group in enumerate(plan["groups"], 1) if unit.id in group["source_ids"]
        ]
        records.append({
            **asdict(unit), "action": (
                "EDITORIAL_MOVED_TO_DOSSIER" if unit.id in editorial
                else "AUTHORED_MAPPING_MANUAL_REVIEW_REQUIRED"
            ),
            "destinations": destinations,
            "editorial_rationale": (
                "Commento sulla corrispondenza titolo/contenuto; tutti i temi elencati "
                "sono conservati nei paragrafi P1–P3. Nessuna informazione d'esame unica."
                if unit.id in editorial else None
            ),
            "protected_tokens": PROTECTED.findall(unit.text),
            "scientifically_verified": False,
        })
    body = f"## {title}\n\n### Sintesi progressiva\n\n" + "\n\n".join(
        g["text"] for g in plan["groups"]
    )
    return body, records


def validate_authored_candidate(title: str, source: str, units: list[Unit],
                                body: str, records: list[dict]) -> None:
    expected_body, expected_records = authored_synthesis(title, source, units)
    if body != expected_body or records != expected_records:
        raise ValueError("Testo o mappa diversi dal piano: numeri/qualificatori non garantiti.")


def _spans(text: str) -> dict[str, tuple[int, int]]:
    """Require the deterministic index and exactly one heading per chapter."""
    parsed = _extract_summary_chapters(text)
    indexed = re.search(
        r"(?ms)^## Indice degli argomenti\s*$\n(.*?)(?:\n---\n|\Z)", text,
    )
    if not indexed:
        raise ValueError("Indice mancante.")
    names = re.findall(r"(?m)^-\s+(.+?)\s*$", indexed.group(1))
    headings = list(re.finditer(r"(?m)^## ([^\r\n]+)[ \t]*$", text[indexed.end():]))
    titles = [m.group(1).strip() for m in headings]
    if names != titles or names != [name for name, _ in parsed] or len(set(names)) != len(names):
        raise ValueError("Indice, titoli o capitoli mancanti/duplicati/ambigui.")
    spans = {}
    for (name, body), heading in zip(parsed, headings, strict=True):
        start = indexed.end() + heading.start()
        if text[start:start + len(body)] != body:
            raise ValueError("Confini capitolo non univoci.")
        spans[name] = (start, start + len(body))
    return spans


def _replace(text: str, spans: dict, replacements: dict[str, str]) -> str:
    for title in sorted(replacements, key=lambda name: spans[name][0], reverse=True):
        start, stop = spans[title]
        text = text[:start] + replacements[title] + text[stop:]
    return text


def run_structural_compression(
    scan: CourseScan, complete_file: Path, study_file: Path,
    v9_file: Path, output_dir: Path,
) -> Path:
    """Fresh output only; hash-bound V9 inputs and original mapped course files."""
    paths = [p.expanduser().resolve() for p in (complete_file, study_file, v9_file)]
    output = output_dir.expanduser().resolve()
    root = scan.path.resolve()
    if len(set(paths)) != 3 or output.exists() or any(not p.is_file() for p in paths):
        raise ValueError("Percorsi input/output non validi o già esistenti.")
    if output == root or root in output.parents or any(
        output == p.parent or p.parent in output.parents for p in paths
    ):
        raise ValueError("Anteprima interna agli input o al corso.")
    originals = [p.read_bytes() for p in paths]
    complete, study = [b.decode("utf-8-sig") for b in originals[:2]]
    # Work in the input's original newline convention; untouched bytes survive.
    newline = "\r\n" if "\r\n" in study else "\n"
    full = complete.replace("\r\n", "\n")
    old = study.replace("\r\n", "\n")
    v9 = json.loads(originals[2].decode("utf-8-sig"))
    if v9.get("version") != V9_VERSION or any(
        v9.get(key) != sha(payload) for key, payload in zip(
            ("complete_sha256", "essential_sha256"), originals[:2], strict=True,
        )
    ):
        raise ValueError("Report V9 o SHA256 degli allegati incompatibili.")
    manifest, mapping = _validated_manifest(scan, full)
    if v9.get("source_fingerprint") != manifest["fingerprint"]:
        raise ValueError("Fonti diverse dal test V9.")
    full_spans, old_spans = _spans(full), _spans(old)
    if not _ordered_title_match(list(full_spans), list(old_spans))[0] or not (
        set(TITLES) <= full_spans.keys() and set(TITLES) <= old_spans.keys()
    ):
        raise ValueError("Capitoli Completa/Essenziale incompatibili con il pilot.")

    chapters = []
    replacements = {}
    for title in TITLES:
        start, stop = full_spans[title]
        source = full[start:stop]
        units = inventory(source, first_line=full.count("\n", 0, start) + 1)
        proposed, records = authored_synthesis(title, source, units)
        validate_authored_candidate(title, source, units, proposed, records)
        reduction = round(100 * (1 - len(proposed.split()) / len(source.split())), 2)
        if not 30 <= reduction <= 45:
            raise ValueError("Piano fuori obiettivo: rivalutare senza forzare omissioni.")
        replacements[title] = proposed
        contexts = [asdict(e) for e in _original_evidence(scan, mapping, title, source)]
        chapters.append({
            "title": title, "complete_words": len(source.split()),
            "old_essential_words": len(old[slice(*old_spans[title])].split()),
            "candidate_words": len(proposed.split()), "reduction_percent": reduction,
            "target_met": 30 <= reduction <= 45,
            "status": "AUTHORED_CANDIDATE_MANUAL_REVIEW_REQUIRED",
            "inventory": records,
            "source_context": contexts,
            "source_context_is_entailment": False,
            "unmapped_source_units": 0,
            "coverage_is_automated_semantic_proof": False,
            "author_mapping_reviewed": True,
            "manual_scientific_review_required": True,
        })
    candidate = _replace(old, old_spans, replacements)
    # Reconstruct the exact starting document by reverting only the three spans.
    candidate_spans = _spans(candidate)
    restored = _replace(candidate, candidate_spans, {
        title: old[slice(*old_spans[title])] for title in TITLES
    })
    if restored != old:
        raise ValueError("Modifica fuori dai tre capitoli autorizzati.")
    restored_bytes = restored.replace("\n", newline).encode("utf-8")
    if originals[1].startswith(b"\xef\xbb\xbf"):
        restored_bytes = b"\xef\xbb\xbf" + restored_bytes
    if restored_bytes != originals[1]:
        raise ValueError("Convenzioni newline miste: impossibile garantire i byte esterni.")
    before = audit_fast_essential(full, old)
    after = audit_fast_essential(full, candidate)
    if after.status == "FAIL" and before.status != "FAIL":
        raise ValueError("Nuovo FAIL nell'audit: anteprima bloccata.")
    sources_unchanged = _fingerprint(_snapshot(scan)) == manifest["fingerprint"]
    if not sources_unchanged or any(
        p.read_bytes() != b for p, b in zip(paths, originals, strict=True)
    ):
        raise ValueError("Input o fonti cambiati durante il pilot.")

    payload = candidate.replace("\n", newline).encode("utf-8")
    if originals[1].startswith(b"\xef\xbb\xbf"):
        payload = b"\xef\xbb\xbf" + payload
    report = {
        "version": VERSION, "status": "PREVIEW_UNPUBLISHED_MANUAL_REVIEW_REQUIRED",
        "scientifically_verified": False, "publication_allowed": False,
        "model_used": False, "target_reduction_percent": [30, 45],
        "word_count_method": "whitespace_split_including_markdown_headings",
        "protection_method": "EXACT_SOURCE_AND_PLAN_HASHES_PLUS_PINNED_OUTPUT_EQUALITY",
        "plan_sha256": PLAN_SHA256,
        "target_policy": "SOFT_TARGET_NEVER_DROP_UNVERIFIED_CONTENT",
        "inventory_policy": "SENTENCE_UNITS_NOT_CERTIFIED_ATOMIC_FACTS",
        "complete_sha256": sha(originals[0]), "essential_sha256": sha(originals[1]),
        "v9_report_sha256": sha(originals[2]), "candidate_sha256": sha(payload),
        "source_fingerprint": manifest["fingerprint"],
        "source_files": manifest["files"], "chapters": chapters,
        "unchanged_chapters": len(old_spans) - 3,
        "outside_pilot_byte_content_preserved": True,
        "before_audit": before.status, "after_audit": after.status,
        "risks": [
            "La Completa non è una fonte scientifica certificata; le sue inesattezze restano.",
            "Krebs: circa 12 ATP, ripartizione e localizzazione richiedono verifica sulle dispense.",
            "La mappa redatta per il pilot richiede revisione umana di ogni equivalenza.",
            "Hash e copertura degli ID non provano equivalenza scientifica delle parafrasi.",
            "Il solo commento editoriale su titolo/contenuto è trasferito nel dossier.",
        ],
    }
    dossier = _dossier(report, paths)
    output.mkdir(parents=True, exist_ok=False)
    (output / "v10-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    (output / "v10-dossier.md").write_text(dossier, encoding="utf-8")
    (output / "essenziale-v10-CANDIDATA-NON-PUBBLICATA.md").write_bytes(payload)
    for label, baseline in (("essenziale", old), ("completa", full)):
        diff = "".join(difflib.unified_diff(
            baseline.splitlines(keepends=True), candidate.splitlines(keepends=True),
            fromfile=f"{label}-originale.md", tofile="essenziale-v10-candidata.md",
        ))
        (output / f"v10-vs-{label}.diff").write_text(diff, encoding="utf-8")
    files = sorted(output.iterdir())
    (output / "SHA256SUMS.json").write_text(
        json.dumps({p.name: sha(p.read_bytes()) for p in files}, indent=2), encoding="utf-8",
    )
    return output


def _dossier(report: dict, paths: list[Path]) -> str:
    lines = [
        "# BC Science Spark V10 — Structural Compression", "",
        "**CANDIDATA NON PUBBLICATA. Revisione manuale obbligatoria prima del merge.**", "",
        ("Il 30–45% è un obiettivo condizionato alla conservazione dei contenuti. "
         "Sintesi redatte e mappate sui tre capitoli esatti; nessuna generazione libera. "
         "Le unità inventariate sono frasi, non fatti atomici certificati. "
         "La mappa richiede revisione manuale; un hash non prova equivalenza scientifica."), "",
        "## Input e integrità", "",
    ]
    for path, key in zip(paths, ("complete_sha256", "essential_sha256", "v9_report_sha256"),
                         strict=True):
        lines += [f"- {path}: `{report[key]}`"]
    lines += ["", f"Audit: {report['before_audit']} → {report['after_audit']}",
              f"Capitoli esterni preservati: {report['unchanged_chapters']}", ""]
    for chapter in report["chapters"]:
        lines += [f"## {chapter['title']}", "",
                  (f"Parole: Completa {chapter['complete_words']}; "
                  f"Essenziale precedente {chapter['old_essential_words']}; "
                  f"candidata {chapter['candidate_words']}."),
                  (f"Riduzione sulla Completa: {chapter['reduction_percent']}%. "
                  f"Obiettivo raggiunto: {'sì' if chapter['target_met'] else 'no'}."), "",
                  "### Contesto dalle dispense (nessuna equivalenza certificata)", ""]
        for evidence in chapter["source_context"]:
            lines += [(f"- {evidence['source_path']} — pagina {evidence['page']}, "
                      f"blocco {evidence['block']}: {evidence['excerpt']}")]
        if not chapter["source_context"]:
            lines += ["Contesto non disponibile: confronto con le dispense da completare."]
        lines += ["", "### Inventario e destinazioni", ""]
        for item in chapter["inventory"]:
            lines += [(f"- {item['id']} · Completa riga {item['line']} · "
                       f"{item['section']} · {item['action']}"),
                      f"  Fonte: {item['text']}"]
            for target in item["destinations"]:
                lines += [f"  Destinazione {target['id']}: {target['text']}",
                          f"  Motivazione: {target['rationale']}"]
            if item["editorial_rationale"]:
                lines += [f"  Motivazione editoriale: {item['editorial_rationale']}"]
        lines.append("")
    lines += ["## Limiti e revisione prima del merge", ""]
    lines += [f"- {risk}" for risk in report["risks"]]
    lines += [
        "- Esaminare entrambi i diff e tutte le destinazioni dell'inventario sulle dispense.",
        "- Verificare numeri, formule, soggetto delle negazioni e condizioni nel contesto.",
        "- Valutare leggibilità e copertura delle informazioni d'esame nei tre capitoli.",
        "- Il superamento dei test non autorizza pubblicazione, merge o estensione agli altri capitoli.",
        "- SHA256SUMS.json verifica tutti gli artefatti generati (escluso se stesso).", "",
    ]
    return "\n".join(lines)
