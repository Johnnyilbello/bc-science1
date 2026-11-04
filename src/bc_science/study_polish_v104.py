"""V10.4 editorial pilot: progressive study text, source facts unchanged.

This is a deterministic, review-only transformation of the pinned V10.3
candidate. It does not certify semantic equivalence or publish study material.
The three authorized chapters are replaced; all other bytes are preserved.
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import pymupdf as fitz

from .autopilot_runner import _validated_manifest
from .courses import CourseScan, _fingerprint, _snapshot
from .fast_audit import _ordered_title_match, audit_fast_essential
from .scientific_clarifications_v101 import _decode, _encode
from .source_grounded_v103 import (
    FACTS,
    KREBS_PDF,
    PROTEIN_PDF,
    CHEM_PDF,
    SOURCE_ARCHIVE_SHA256,
    _archive_sources,
    _clean,
    validate_fact_coverage,
    validate_original_pdfs,
)
from .structural_compression import TITLES, _replace, _spans, sha

VERSION = "spark-v10.4-didactic-review-only"
V103_CANDIDATE_SHA256 = (
    "d3bdc446fa25c77e31aef34ee90e94a5f55c48223d124a8e7bdf8735c323fec1"
)
V103_REPORT_SHA256 = (
    "3b1dce7a0373d3fa4b95f189618bc12a0cee32afb4bafad964e2ff9bb8664980"
)
COMPLETE_SHA256 = (
    "72c1dfc066489d5936ebccbef7aaa06df3a3ca5d171cc329279e8bd71c18c40b"
)
TEMPLATE_PATH = (
    Path(__file__).resolve().parent / "data" / "spark_v104_didactic_chapters.md"
)


@dataclass(frozen=True)
class DidacticSourceCheck:
    ident: str
    chapter: str
    pdf: str
    page: int
    terms: tuple[str, ...]
    witness: str


# Additional evidence surfaced during the manual seven-PDF review.
# The 30 original source facts remain unchanged and independently guarded.
ADDITIONAL_SOURCE_CHECKS = (
    DidacticSourceCheck(
        "D01_KREBS_KETONES_AMINOACIDS", TITLES[0], KREBS_PDF, 7,
        ("corpi chetonici", "Leucina e isoleucina"),
        "**corpi chetonici**",
    ),
    DidacticSourceCheck(
        "D02_GLYCOSPHINGOLIPID_RECOGNITION", TITLES[1], PROTEIN_PDF, 5,
        ("sfingolipidi", "gruppi sanguigni"),
        "**gruppi sanguigni**",
    ),
    DidacticSourceCheck(
        "D03_GLUCOSE_ANOMER_DIRECTION", TITLES[2], CHEM_PDF, 8,
        ("ossidrile", "Beta", "alfa"),
        "**opposto rispetto a CH2OH**",
    ),
    DidacticSourceCheck(
        "D04_FRUCTOSE_FURANOSE", TITLES[2], CHEM_PDF, 8,
        ("fruttosio", "carbonio in posizione 2", "C5"),
        "**fruttosio** può formare un anello a cinque membri",
    ),
    DidacticSourceCheck(
        "D05_HEXOSE_FORMULA", TITLES[2], CHEM_PDF, 7,
        ("C6H12O6", "glucosio", "fruttosio"),
        "**C6H12O6**",
    ),
)


def _check_additional_pdf_evidence(source_archive: Path) -> list[dict]:
    """Require each new concept to exist on the stated physical PDF page."""
    sources = _archive_sources(source_archive)
    rows = []
    for fact in ADDITIONAL_SOURCE_CHECKS:
        with fitz.open(stream=sources[fact.pdf], filetype="pdf") as pdf:
            if len(pdf) < fact.page:
                raise ValueError(f"Pagina mancante per {fact.ident}.")
            source_text = _clean(pdf[fact.page - 1].get_text("text"))
        missing = [
            term for term in fact.terms
            if _clean(term) not in source_text
        ]
        if missing:
            raise ValueError(
                f"Termini sorgente mancanti per {fact.ident}: "
                + ", ".join(missing)
            )
        rows.append({
            **asdict(fact),
            "source_page_terms_verified": True,
            "scientific_interpretation_requires_human_review": True,
        })
    return rows


def _chapters_from_template(text: str) -> dict[str, str]:
    """Reject unauthorized headings and missing/duplicated chapter definitions."""
    normalized = text.replace("\r\n", "\n")
    headings = list(re.finditer(r"(?m)^## ([^\r\n]+)$", normalized))
    titles = [m.group(1) for m in headings]
    if len(titles) != 3 or set(titles) != set(TITLES):
        raise ValueError("Template V10.4: titoli mancanti, duplicati o non autorizzati.")
    sections = {}
    for index, found in enumerate(headings):
        stop = (
            headings[index + 1].start() if index + 1 < len(headings)
            else len(normalized)
        )
        block = normalized[found.start():stop].strip()
        if not block.endswith("\n---"):
            raise ValueError(f"Template V10.4 privo di separatore: {found.group(1)}")
        # _spans covers chapter content only: original document owns dividers.
        body = block[:-len("\n---")].rstrip()
        if "### Ripasso attivo" not in body:
            raise ValueError(f"Ripasso attivo mancante: {found.group(1)}")
        sections[found.group(1)] = body
    return sections


def apply_v104(v103_text: str, template_text: str) -> tuple[str, list[dict]]:
    """Check coverage and reversible section replacement; no fuzzy search."""
    spans = _spans(v103_text)
    if len(spans) != 65 or not set(TITLES).issubset(spans):
        raise ValueError("Documento V10.3 con capitoli inattesi.")
    replacements = _chapters_from_template(template_text)
    for fact in (*FACTS, *ADDITIONAL_SOURCE_CHECKS):
        section = replacements[fact.chapter]
        if section.count(fact.witness) != 1:
            raise ValueError(f"Fatto atomico perso o duplicato: {fact.ident}")
    updated = _replace(v103_text, spans, replacements)
    validate_fact_coverage(updated)
    updated_spans = _spans(updated)
    reverted = _replace(updated, updated_spans, {
        name: v103_text[slice(*spans[name])] for name in TITLES
    })
    if reverted != v103_text:
        raise ValueError("V10.4 ha modificato sezioni esterne al pilot.")
    changes = []
    for name in TITLES:
        original = v103_text[slice(*spans[name])]
        revised = updated[slice(*updated_spans[name])]
        if original == revised:
            raise ValueError(f"Nessuna revisione didattica rilevata: {name}")
        changes.append({
            "title": name,
            "v103_words": len(original.split()),
            "v104_words": len(revised.split()),
            "ripasso_attivo": True,
            "facts_preserved": [
                f.ident for f in FACTS if f.chapter == name
            ],
        })
    return updated, changes


def run_v104(
    scan: CourseScan,
    complete_file: Path,
    v103_candidate: Path,
    v103_report: Path,
    source_archive: Path,
    output_dir: Path,
) -> Path:
    """Create a fresh, non-published pilot only from locked course sources."""
    paths = [
        p.expanduser().resolve()
        for p in (complete_file, v103_candidate, v103_report, source_archive)
    ]
    output = output_dir.expanduser().resolve()
    root = scan.path.resolve()
    if (
        len(set(paths)) != 4 or any(not p.is_file() for p in paths)
        or output.exists() or output == root or root in output.parents
        or any(output == p.parent or p.parent in output.parents for p in paths)
    ):
        raise ValueError("Input o cartella di output non validi per V10.4.")
    original_bytes = [p.read_bytes() for p in paths]
    expected_hashes = [
        COMPLETE_SHA256, V103_CANDIDATE_SHA256,
        V103_REPORT_SHA256, SOURCE_ARCHIVE_SHA256,
    ]
    if [sha(data) for data in original_bytes] != expected_hashes:
        raise ValueError("Hash V10.3, Completa, report o ZIP inattesi.")
    complete, _, _ = _decode(original_bytes[0])
    previous_text, newline, bom = _decode(original_bytes[1])
    previous_report = json.loads(original_bytes[2].decode("utf-8-sig"))
    if (
        previous_report.get("version")
        != "spark-v10.3-source-grounded-review-only"
        or previous_report.get("publication_allowed") is not False
        or previous_report.get("scientifically_certified") is not False
        or previous_report.get("mandatory_fact_count") != len(FACTS)
        or previous_report.get("unchanged_other_chapters") != 62
        or previous_report.get("candidate_sha256") != V103_CANDIDATE_SHA256
        or previous_report.get("complete_sha256") != COMPLETE_SHA256
        or previous_report.get("source_archive_sha256") != SOURCE_ARCHIVE_SHA256
        or previous_report.get("after_audit") == "FAIL"
    ):
        raise ValueError("Report V10.3 non valido per il pilot V10.4.")
    manifest, _ = _validated_manifest(scan, complete)
    if manifest["fingerprint"] != previous_report.get("source_fingerprint"):
        raise ValueError("Fingerprint dispense differente dalla V10.3.")
    evidence = validate_original_pdfs(root, paths[3])
    additional_evidence = _check_additional_pdf_evidence(paths[3])
    if len(evidence) != len(FACTS) or len(additional_evidence) != len(ADDITIONAL_SOURCE_CHECKS):
        raise ValueError("Matrice PDF incompleta.")
    template_bytes = TEMPLATE_PATH.read_bytes()
    template = template_bytes.decode("utf-8-sig")
    original_spans, complete_spans = _spans(previous_text), _spans(complete)
    if not _ordered_title_match(
        list(complete_spans), list(original_spans)
    )[0]:
        raise ValueError("Indice V10.3 diverso da quello della Completa.")
    updated, changes = apply_v104(previous_text, template)
    new_spans = _spans(updated)
    reverted = _replace(updated, new_spans, {
        name: previous_text[slice(*original_spans[name])] for name in TITLES
    })
    if _encode(reverted, newline, bom) != original_bytes[1]:
        raise ValueError("Altri 62 capitoli non conservati byte-per-byte.")
    before = audit_fast_essential(complete, previous_text)
    after = audit_fast_essential(complete, updated)
    if after.status == "FAIL" and before.status != "FAIL":
        raise ValueError("Nuovo FAIL dell'audit V10.4: modifica bloccata.")
    if (
        _fingerprint(_snapshot(scan)) != manifest["fingerprint"]
        or any(p.read_bytes() != original for p, original in zip(
            paths, original_bytes, strict=True
        ))
        or TEMPLATE_PATH.read_bytes() != template_bytes
    ):
        raise ValueError("Input, template o dispense modificati durante la generazione.")
    validate_original_pdfs(root, paths[3])
    _check_additional_pdf_evidence(paths[3])

    per_chapter = []
    for item in changes:
        name = item["title"]
        old_full_words = len(complete[slice(*complete_spans[name])].split())
        now_words = len(updated[slice(*new_spans[name])].split())
        per_chapter.append({
            **item,
            "complete_words": old_full_words,
            "reduction_percent": round(100 * (1 - now_words / old_full_words), 2),
            "compression_floor_or_cap_enforced": False,
        })
    payload = _encode(updated, newline, bom)
    report = {
        "version": VERSION,
        "status": "DIDACTIC_REVIEW_ONLY_NON_PUBLISHED",
        "publication_allowed": False,
        "scientifically_certified": False,
        "full_semantic_equivalence_verified": False,
        "requires_human_scientific_and_didactic_review": True,
        "complete_sha256": sha(original_bytes[0]),
        "v103_candidate_sha256": sha(original_bytes[1]),
        "v103_report_sha256": sha(original_bytes[2]),
        "source_archive_sha256": sha(original_bytes[3]),
        "template_sha256": sha(template_bytes),
        "candidate_sha256": sha(payload),
        "source_fingerprint": manifest["fingerprint"],
        "source_pdf_hashes": previous_report["source_pdf_hashes"],
        "source_discrepancies": previous_report["source_discrepancies"],
        "source_validation_mode": "sha256_pinned_zip",
        "pdf_page_anchors_verified": True,
        "unchanged_other_chapters": len(original_spans) - len(TITLES),
        "before_audit": before.status,
        "after_audit": after.status,
        "chapters": per_chapter,
        "mandatory_fact_count": len(FACTS),
        "additional_source_fact_count": len(ADDITIONAL_SOURCE_CHECKS),
        "total_guarded_fact_count": len(FACTS) + len(ADDITIONAL_SOURCE_CHECKS),
        "additional_source_matrix": additional_evidence,
        "atomic_fact_matrix": [
            {
                **fact,
                "candidate_witness_occurrences": updated[
                    slice(*new_spans[fact["chapter"]])
                ].count(fact["witness"]),
            }
            for fact in evidence
        ],
        "manual_review_gate": "BLOCKED_UNTIL_PDF_AND_DIDACTIC_HUMAN_REVIEW",
    }
    dossier = [
        "# BC Science V10.4 — Pilot didattico source-grounded", "",
        "**Non pubblicato. Non certificato scientificamente.**", "",
        ("Tre capitoli riscritti in modo progressivo e con domande di ripasso. "
         "Trenta fatti obbligatori conservati come testimoni letterali, "
         "ma il controllo testuale non certifica l'equivalenza semantica."), "",
        (f"Audit: {before.status} → {after.status}. "
         f"Capitoli esterni identici: {report['unchanged_other_chapters']}."), "",
        "## Riduzioni senza soglia rigida", "",
    ]
    for item in per_chapter:
        dossier.append(
            f"- {item['title']}: {item['v103_words']} → {item['v104_words']} "
            f"parole; {item['reduction_percent']}% in meno della Completa."
        )
    dossier += [
        "", "## Copertura delle 30 unità fonte-pagina", "",
    ]
    for item in report["atomic_fact_matrix"]:
        dossier.append(
            f"- {item['ident']} | {item['pdf']} p.{item['page']} | "
            f"presenza nel capitolo: {item['candidate_witness_occurrences']}"
        )
    dossier += [
        "", "## Altri cinque fatti recuperati dalla revisione PDF", "",
        *[
            f"- {item['ident']} | {item['pdf']} p.{item['page']} | "
            f"{item['witness']}"
            for item in additional_evidence
        ],
        "", "## Limitazioni", "",
        "- Riordino didattico da controllare manualmente sui PDF originali.",
        ("- Riferimenti esterni e discrepanze presenti nelle dispense "
         "restano distinti dalle affermazioni del corso."),
        "- Nessuna generalizzazione automatica ai 62 capitoli non coinvolti.",
        "- Nessuna modifica ai materiali sorgenti né merge/pubblicazione.",
        "",
    ]
    output.mkdir(parents=True, exist_ok=False)
    (output / "essenziale-v10.4-CANDIDATA-NON-PUBBLICATA.md").write_bytes(payload)
    (output / "v10.4-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output / "v10.4-dossier.md").write_text(
        "\n".join(dossier), encoding="utf-8",
    )
    diff = "".join(difflib.unified_diff(
        previous_text.splitlines(keepends=True),
        updated.splitlines(keepends=True),
        fromfile="essenziale-v10.3", tofile="essenziale-v10.4",
    ))
    (output / "v10.4-vs-v10.3.diff").write_text(diff, encoding="utf-8")
    (output / "SHA256SUMS.json").write_text(
        json.dumps({
            p.name: sha(p.read_bytes())
            for p in sorted(output.iterdir())
        }, indent=2),
        encoding="utf-8",
    )
    return output
