"""V10.5: non-published biosynthesis source correction preview.

Four reviewed edits to ONE chapter in the locked Complete and V10.4
candidates. Does not modify originals or infer correctness from lexical coverage.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import zipfile
from pathlib import Path

from .fast_audit import audit_fast_essential
from .scientific_clarifications_v101 import _decode
from .source_grounded_v103 import FACTS, validate_fact_coverage
from .structural_compression import TITLES, _spans
from .study_polish_v104 import ADDITIONAL_SOURCE_CHECKS

VERSION = "spark-v10.5-biosynthesis-source-corrections-review-only"
TITLE = "BIOSINTESI DEGLI ACIDI GRASSI"
PLAN_PATH = (
    Path(__file__).resolve().parent / "data" / "biosynthesis_v105_source_fixes.json"
)
CHAPTER_RE = re.compile(
    r"(?ms)^##[ \t]+BIOSINTESI DEGLI ACIDI GRASSI[ \t]*\r?\n"
    r".*?(?=^##[ \t]+[^#]|\Z)"
)
EXPECTED_FIX_IDS = ("B01", "B02", "B03", "B04")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _plan() -> dict:
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    ids = tuple(item["id"] for item in plan["fixes"])
    if ids != EXPECTED_FIX_IDS or len(plan["pdf_sources"]) != 8:
        raise ValueError("Piano V10.5 non conforme: 4 correzioni e 8 PDF richiesti.")
    if any(
        not item["old"] or not item["replacement"]
        or item["source_pdf"] not in plan["pdf_sources"]
        or item["source_page"] < 1
        for item in plan["fixes"]
    ):
        raise ValueError("Piano V10.5 incompleto.")
    return plan


def _verify_archive(path: Path, plan: dict) -> None:
    data = path.read_bytes()
    if _sha(data) != plan["source_archive_sha256"]:
        raise ValueError("ZIP delle 8 fonti diverso da quello verificato.")
    with zipfile.ZipFile(path) as z:
        if len(z.namelist()) != len(set(z.namelist())):
            raise ValueError("Nomi duplicati nell'archivio delle fonti.")
        manifest = json.loads(z.read("manifest.json").decode("utf-8-sig"))
        mapped = {row["ArchiveName"]: row for row in manifest}
        if len(manifest) != 8 or set(mapped) != set(plan["pdf_sources"]):
            raise ValueError("Manifest PDF incompleto o duplicato.")
        for filename, expected in plan["pdf_sources"].items():
            row = mapped[filename]
            if (
                row["OriginalName"] != expected["name"]
                or row["SHA256"].casefold() != expected["sha256"]
                or _sha(z.read(filename)) != expected["sha256"]
            ):
                raise ValueError(f"Integrità PDF fallita: {filename}")
        for label, name in (
            ("complete", "01-capitolo-completa.md"),
            ("essential", "02-capitolo-essenziale-v104.md"),
        ):
            if _sha(z.read(name).strip()) != plan["chapter_original_sha256"][label]:
                raise ValueError(f"Estratto originale ZIP incoerente: {name}")


def _replace_single_chapter(
    original: bytes, plan: dict, label: str
) -> tuple[bytes, list[str]]:
    # Keep CRLF exactly: the audited extracts and their SHA-256 use CRLF.
    _normalized, _newline, bom = _decode(original)
    text = original[len(bom):].decode("utf-8")
    found = list(CHAPTER_RE.finditer(text))
    if len(found) != 1:
        raise ValueError("Capitolo Biosintesi assente o duplicato.")
    match = found[0]
    old_segment = match.group(0)
    old_chapter = old_segment.strip()
    if _sha(old_chapter.encode("utf-8")) != plan["chapter_original_sha256"][label]:
        raise ValueError(f"Biosintesi {label}: capitolo diverso dalla fonte bloccata.")
    updated = old_chapter
    applied: list[str] = []
    for fix in plan["fixes"]:
        # Allow line wraps between words, not arbitrary fuzzy replacements.
        expression = re.escape(fix["old"]).replace(r"\ ", r"\s+")
        updated, count = re.subn(
            expression, lambda _match, item=fix: item["replacement"], updated
        )
        if count != 1:
            raise ValueError(
                f"V10.5 {label}: correzione {fix['id']} non univoca ({count})."
            )
        applied.append(fix["id"])
    if _sha(updated.encode("utf-8")) != (
        plan["chapter_expected_corrected_sha256"][label]
    ):
        raise ValueError(f"V10.5 {label}: capitolo diverso dalla proposta revisionata.")
    # The original chapter owns the final divider and blank space.
    whitespace = old_segment[len(old_segment.rstrip()):]
    revised = text[:match.start()] + updated + whitespace + text[match.end():]
    if (
        revised[:match.start()] != text[:match.start()]
        or revised[match.start() + len(updated + whitespace):] != text[match.end():]
    ):
        raise ValueError("Sezioni esterne a Biosintesi modificate.")
    payload = bom + revised.encode("utf-8")
    if payload == original:
        raise ValueError("Correzioni non applicate.")
    before_text = _decode(original)[0]
    after_text = _decode(payload)[0]
    before_spans = _spans(before_text)
    after_spans = _spans(after_text)
    if len(before_spans) != 65 or list(before_spans) != list(after_spans):
        raise ValueError("Indice o numero dei 65 capitoli modificato.")
    for title in before_spans:
        if title != TITLE:
            if (
                before_text[slice(*before_spans[title])]
                != after_text[slice(*after_spans[title])]
            ):
                raise ValueError(f"Capitolo esterno modificato: {title}")
    return payload, applied


def _validate_essential_witnesses(essential: str) -> None:
    validate_fact_coverage(essential)
    spans = _spans(essential)
    for fact in ADDITIONAL_SOURCE_CHECKS:
        chapter = essential[slice(*spans[fact.chapter])]
        if chapter.count(fact.witness) != 1:
            raise ValueError(f"V10.4 fatto aggiuntivo mancante: {fact.ident}")


def run_v105(
    complete_file: Path,
    essential_file: Path,
    v104_report_file: Path,
    source_archive: Path,
    output_dir: Path,
) -> Path:
    """Generate reviewed copies only; never overwrite the V10.4 source."""
    inputs = tuple(
        path.expanduser().resolve()
        for path in (complete_file, essential_file, v104_report_file, source_archive)
    )
    output = output_dir.expanduser().resolve()
    if (
        len(set(inputs)) != 4
        or any(not path.is_file() for path in inputs)
        or output.exists()
        or any(output in path.parents or output == path for path in inputs)
    ):
        raise ValueError("Input o cartella di output V10.5 non validi.")
    originals = tuple(path.read_bytes() for path in inputs)
    plan_data = PLAN_PATH.read_bytes()
    plan = _plan()
    for data, key in zip(
        originals[:3],
        ("complete_sha256", "v104_candidate_sha256", "v104_report_sha256"),
        strict=True,
    ):
        if _sha(data) != plan[key]:
            raise ValueError(f"Hash input non valido: {key}")
    report104 = json.loads(originals[2].decode("utf-8-sig"))
    if (
        report104.get("publication_allowed") is not False
        or report104.get("scientifically_certified") is not False
        or report104.get("total_guarded_fact_count") != 35
        or report104.get("unchanged_other_chapters") != 62
        or report104.get("candidate_sha256") != plan["v104_candidate_sha256"]
        or report104.get("after_audit") != "WARN"
    ):
        raise ValueError("Report V10.4 non autorizzato come input.")
    _verify_archive(inputs[3], plan)
    new_complete, complete_fixes = _replace_single_chapter(
        originals[0], plan, "complete"
    )
    new_essential, essential_fixes = _replace_single_chapter(
        originals[1], plan, "essential"
    )
    if complete_fixes != list(EXPECTED_FIX_IDS) or essential_fixes != complete_fixes:
        raise ValueError("Le quattro correzioni non risultano simmetriche.")
    original_complete = _decode(originals[0])[0]
    original_essential = _decode(originals[1])[0]
    corrected_complete = _decode(new_complete)[0]
    corrected_essential = _decode(new_essential)[0]
    _validate_essential_witnesses(corrected_essential)
    before = audit_fast_essential(original_complete, original_essential)
    after = audit_fast_essential(corrected_complete, corrected_essential)
    if before.status == "FAIL" or after.status == "FAIL":
        raise ValueError(
            f"Audit V10.5 bloccato: {before.status} -> {after.status}."
        )
    # Fail closed if a source changes while the two drafts are being generated.
    if (
        any(path.read_bytes() != old for path, old in zip(inputs, originals, strict=True))
        or PLAN_PATH.read_bytes() != plan_data
    ):
        raise ValueError("Input o piano cambiato durante la generazione V10.5.")
    report = {
        "version": VERSION,
        "publication_allowed": False,
        "scientifically_certified": False,
        "human_scientific_review_required": True,
        "source_pdf_count": 8,
        "source_archive_sha256": _sha(originals[3]),
        "complete_original_sha256": _sha(originals[0]),
        "v104_original_sha256": _sha(originals[1]),
        "v104_report_sha256": _sha(originals[2]),
        "plan_sha256": _sha(plan_data),
        "complete_candidate_sha256": _sha(new_complete),
        "essential_candidate_sha256": _sha(new_essential),
        "corrected_chapters_per_document": 1,
        "unchanged_chapters_per_document": 64,
        "v104_three_pilot_chapters_unchanged": True,
        "corrections": [
            {
                "id": item["id"], "reason": item["reason"],
                "source_pdf": plan["pdf_sources"][item["source_pdf"]]["name"],
                "page": item["source_page"], "complete_applied": True,
                "essential_applied": True,
            }
            for item in plan["fixes"]
        ],
        "before_audit": before.status,
        "after_audit": after.status,
        "source_discrepancy": (
            "NADH/NADPH discordano fra le figure e il testo narrativo di "
            "BIOSINTESI DEGLI ACIDI GRASSI 2.pdf. "
            "Figure e bilancio sono distinguibili dalle note di corso."
        ),
    }
    outputs = {
        "completa-v10.5-BIOSINTESI-CANDIDATA-NON-PUBBLICATA.md": new_complete,
        "essenziale-v10.5-BIOSINTESI-CANDIDATA-NON-PUBBLICATA.md": new_essential,
    }
    for name, old_text, revised in (
        ("completa", original_complete, corrected_complete),
        ("essenziale", original_essential, corrected_essential),
    ):
        diff = "".join(difflib.unified_diff(
            old_text.splitlines(keepends=True),
            revised.splitlines(keepends=True),
            fromfile=f"{name}-originale",
            tofile=f"{name}-v10.5-candidata",
        ))
        outputs[f"v10.5-{name}-diff.patch"] = diff.encode("utf-8")
    outputs["v10.5-report.json"] = json.dumps(
        report, ensure_ascii=False, indent=2
    ).encode("utf-8")
    dossier = [
        "# BC Science V10.5 — Correzione scientifica biosintesi", "",
        "**NON PUBBLICATA, NON CERTIFICATA SCIENTIFICAMENTE.**", "",
        f"Fonti: 8 PDF pinned SHA256; audit {before.status} → {after.status}.", "",
        "Solo Biosintesi degli acidi grassi cambia, sia nella Completa che "
        "nell'Essenziale V10.4. Gli altri 64 capitoli sono identici.", "",
        "## Correzioni e pagine", "",
    ]
    for item in report["corrections"]:
        dossier.append(
            f"- {item['id']}: {item['reason']} "
            f"({item['source_pdf']}, p. {item['page']})"
        )
    dossier += [
        "", "## Caveat per l'esame", "",
        "La diapositiva eCampus sul palmitato mostra 8 acetil-CoA totali, "
        "7 ATP e 14 NADPH; 7 acetil-CoA diventano 7 malonil-CoA. "
        "Alcune frasi del testo narrativo delle dispense confondono NADH "
        "e NADPH; la proposta segue il meccanismo illustrato e non "
        "nasconde questa incongruenza.", "",
        "Queste sono quattro correzioni motivate, non la certificazione "
        "scientifica integrale dei 65 capitoli.", "",
    ]
    outputs["v10.5-dossier.md"] = "\n".join(dossier).encode("utf-8")
    output.mkdir(parents=True, exist_ok=False)
    for name, data in outputs.items():
        (output / name).write_bytes(data)
    (output / "SHA256SUMS.json").write_text(
        json.dumps(
            {name: _sha(data) for name, data in sorted(outputs.items())},
            indent=2,
        ),
        encoding="utf-8",
    )
    return output
