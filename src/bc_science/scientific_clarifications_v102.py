"""V10.2 bounded corrections to V10.1; no automatic science certification.

A pinned, deterministic candidate with five exact edits in three authorized
chapters. External scientific clarifications are visibly documented, never
presented as recovered facts from the eCampus PDFs. All original inputs and
other 62 chapters remain byte-identical. Any new FAIL blocks publication.
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .autopilot_runner import _validated_manifest
from .courses import CourseScan, _fingerprint, _snapshot
from .fast_audit import _ordered_title_match, audit_fast_essential
from .scientific_clarifications_v101 import _decode, _encode
from .structural_compression import TITLES, _replace, _spans, sha

VERSION = "scientific-clarifications-v10.2-review-only"
EXPECTED_COMPLETE_SHA256 = "72c1dfc066489d5936ebccbef7aaa06df3a3ca5d171cc329279e8bd71c18c40b"
EXPECTED_V101_CANDIDATE_SHA256 = "44982ffe5739d12aeb9121dd034741e69be3f0a67353d6d1b340a264f203bac8"
EXPECTED_V101_REPORT_SHA256 = "8133e4947b4f6c4cedb3f15b9b9ab6f592b6d1090e978c5caeddd881f01a8841"


@dataclass(frozen=True)
class Revision:
    ident: str
    title: str
    before: str
    after: str
    purpose: str
    provenance: str
    reference: str | None = None
    status: str = "ORIGINAL_PDF_AND_HUMAN_SCIENCE_REVIEW_REQUIRED"


REVISIONS = (
    Revision(
        "KREBS_ENZYME_LOCATION",
        TITLES[0],
        "Il ciclo di Krebs è un processo chimico che avviene nella matrice "
        "mitocondriale delle cellule eucariotiche.",
        "Nelle cellule eucariotiche, il ciclo di Krebs avviene prevalentemente "
        "nella matrice mitocondriale; la succinato deidrogenasi, che catalizza "
        "una delle otto tappe, è associata alla membrana mitocondriale interna.",
        "La succinato deidrogenasi è l'eccezione alla localizzazione matriciale "
        "degli enzimi del ciclo; verificare la localizzazione nella dispensa.",
        "SOURCE_LESSON_NAMES_ENZYME_PLUS_EXTERNAL_MEMBRANE_LOCATION",
        "https://www.ncbi.nlm.nih.gov/books/NBK556032/",
    ),
    Revision(
        "KREBS_CYCLE_VS_RESPIRATION",
        TITLES[0],
        ("La funzione principale del ciclo è ossidare completamente i nutrienti, "
         "come glucosio, grassi e amminoacidi, fino a CO2 e H2O. L'energia viene "
         "rilasciata sotto forma di trasportatori di elettroni NADH e FADH2 e di "
         "ATP/GTP."),
        ("Il ciclo ossida il gruppo acetilico dell'acetil-CoA, derivato dal "
         "catabolismo di glucosio, grassi e alcuni amminoacidi, liberando CO2 "
         "e conservando energia in NADH, FADH2 e GTP (o ATP). La formazione "
         "di H2O per riduzione dell'ossigeno (O2) avviene invece nella catena "
         "respiratoria, non nel ciclo di Krebs."),
        "Distingue i prodotti del ciclo dalle reazioni di riduzione di O2 "
        "a H2O nella catena respiratoria. Il corso descrive le tre fasi "
        "della respirazione in capitolo/dispensa.",
        "COURSE_THREE_STAGES_PLUS_EXTERNAL_SCIENTIFIC_CLARIFICATION",
        "https://www.ncbi.nlm.nih.gov/books/NBK26894/",
    ),
    Revision(
        "KREBS_ATP_STUDY_VS_REVIEW_NOTE",
        TITLES[0],
        ("Per acetil-CoA: 3 NADH, 1 FADH2 e 1 GTP (o ATP). La Completa indica "
         "circa 12 ATP equivalenti secondo la convenzione storica "
         "(3 ATP/NADH e 2 ATP/FADH2). Con stime moderne (2,5 ATP/NADH "
         "e 1,5 ATP/FADH2) si ottengono circa 10 ATP equivalenti. "
         "**Nota di verifica:** la ripartizione riportata nella Completa è "
         "internamente incoerente; confrontare il valore richiesto con le "
         "dispense e il docente."),
        ("Per ogni acetil-CoA il ciclo produce 3 NADH, 1 FADH2 e 1 GTP (o ATP). "
         "La resa teorica è circa 12 ATP equivalenti secondo la convenzione "
         "storica (3 ATP/NADH e 2 ATP/FADH2); con i coefficienti moderni "
         "(2,5 ATP/NADH e 1,5 ATP/FADH2) si stimano circa 10 ATP equivalenti."),
        "Mantiene entrambe le convenzioni e i numeri d'esame nel testo di studio; "
        "sposta nel dossier l'avvertenza che la ripartizione nella Completa "
        "è internamente incoerente e che il docente può richiedere la "
        "convenzione storica. Nessuna decisione sull'esame è inferita.",
        "EDIT_STUDY_TEXT_ONLY_SOURCE_CONFLICT_PRESERVED_IN_DOSSIER",
        "https://www.ncbi.nlm.nih.gov/books/NBK526105/",
    ),
    Revision(
        "PROTEIN_VDW_VS_HYDROPHOBIC",
        TITLES[1],
        ("Esistono quattro tipi di forze: legami ionici tra cariche opposte, "
         "legami idrogeno tra aminoacidi polari, forze di van der Waals tra "
         "catene apolari che si nascondono dall'acqua e legami disolfuro."),
        ("Il ripiegamento e la stabilità dipendono da legami ionici fra "
         "gruppi di carica opposta, legami a idrogeno, forze di van der Waals "
         "fra atomi vicini e ponti disolfuro (legami covalenti). "
         "L'effetto idrofobico è distinto dalle forze di van der Waals: "
         "favorisce il ripiegamento delle catene laterali apolari "
         "verso l'interno, lontano dall'acqua."),
        "Distingue un'interazione a corto raggio fra atomi dall'effetto "
        "idrofobico (termodinamico) in ambiente acquoso, senza negare "
        "l'esistenza delle quattro interazioni citate nel testo iniziale.",
        "EXTERNAL_PROTEIN_FOLDING_SCIENTIFIC_CLARIFICATION",
        "https://www.ncbi.nlm.nih.gov/books/NBK470269/",
    ),
    Revision(
        "CHIMICA_REMOVE_HYDROLYSIS_DUPLICATION",
        TITLES[2],
        ("La condensazione unisce due monomeri liberando una molecola d'acqua; "
         "l'idrolisi rompe il legame fra monomeri consumando acqua "
         "(Fonte p.6). In breve: la condensazione unisce liberando acqua; "
         "l'idrolisi rompe consumando acqua."),
        ("La condensazione unisce due monomeri liberando una molecola "
         "d'acqua; l'idrolisi rompe il legame fra monomeri consumando "
         "acqua (Fonte p.6)."),
        "Conserva parola per parola la definizione sorgente e il suo "
        "riferimento; elimina soltanto il promemoria ripetuto.",
        "EXACT_DEDUPLICATION_NO_NEW_SCIENCE",
    ),
)


def _number_tokens(text: str) -> list[str]:
    return re.findall(r"(?<!\w)\d+(?:[.,]\d+)?", text)


def apply_v102(v101_text: str) -> tuple[str, list[dict]]:
    """Require five exact single-occurrence anchors; refuse fuzzy matches."""
    spans = _spans(v101_text)
    if not set(TITLES).issubset(spans):
        raise ValueError("V10.1 priva di uno dei tre capitoli del pilot.")
    replacements: dict[str, str] = {}
    changes = []
    for title in TITLES:
        old_chapter = v101_text[slice(*spans[title])]
        changed_chapter = old_chapter
        for patch in (item for item in REVISIONS if item.title == title):
            if changed_chapter.count(patch.before) != 1 or patch.after in changed_chapter:
                raise ValueError(f"Ancoraggio V10.2 assente o ambiguo: {patch.ident}")
            changed_chapter = changed_chapter.replace(patch.before, patch.after, 1)
            before_nums = _number_tokens(patch.before)
            after_nums = _number_tokens(patch.after)
            changes.append({
                **asdict(patch),
                "numeric_tokens_before": before_nums,
                "numeric_tokens_after": after_nums,
                "numeric_change_disclosed": before_nums != after_nums,
                "qualifier_and_semantic_recheck_required": True,
                "original_pdf_checked": False,
                "scientifically_certified": False,
            })
        replacements[title] = changed_chapter

    result = _replace(v101_text, spans, replacements)
    updated_spans = _spans(result)
    original = _replace(result, updated_spans, {
        title: v101_text[slice(*spans[title])] for title in TITLES
    })
    if original != v101_text:
        raise ValueError("V10.2: alterazioni fuori dai tre capitoli o non reversibili.")
    if len(changes) != len(REVISIONS):
        raise ValueError("Numero modifiche diverso dall'inventario V10.2.")
    return result, changes


def run_v102(
    scan: CourseScan,
    complete_file: Path,
    v101_candidate_file: Path,
    v101_report_file: Path,
    output_dir: Path,
) -> Path:
    """Emit a new non-published preview, only from pinned real local V10.1 files."""
    paths = [
        p.expanduser().resolve()
        for p in (complete_file, v101_candidate_file, v101_report_file)
    ]
    root = scan.path.resolve()
    dest = output_dir.expanduser().resolve()
    if len(set(paths)) != 3 or any(not p.is_file() for p in paths):
        raise ValueError("Input V10.2 mancanti, non-file o duplicati.")
    if (
        dest.exists() or dest == root or root in dest.parents
        or any(dest == p.parent or p.parent in dest.parents for p in paths)
    ):
        raise ValueError("Output V10.2 già esistente o interno ai materiali originali.")
    originals = [p.read_bytes() for p in paths]
    expected = (
        EXPECTED_COMPLETE_SHA256,
        EXPECTED_V101_CANDIDATE_SHA256,
        EXPECTED_V101_REPORT_SHA256,
    )
    if tuple(sha(b) for b in originals) != expected:
        raise ValueError("Hash input diversi dalla V10.1 locale verificata.")
    complete_text, _, _ = _decode(originals[0])
    old, newline, bom = _decode(originals[1])
    v101_report = json.loads(originals[2].decode("utf-8-sig"))
    if (
        v101_report.get("version") != "scientific-clarifications-v10.1-review-only"
        or v101_report.get("publication_allowed") is not False
        or v101_report.get("scientifically_verified") is not False
        or v101_report.get("complete_sha256") != sha(originals[0])
        or v101_report.get("candidate_sha256") != sha(originals[1])
        or v101_report.get("unchanged_other_chapters") != 62
        or v101_report.get("after_audit") == "FAIL"
    ):
        raise ValueError("Provenienza o stato V10.1 non corrispondenti.")
    manifest, _mapping = _validated_manifest(scan, complete_text)
    if manifest["fingerprint"] != v101_report.get("source_fingerprint"):
        raise ValueError("Dispense originali differenti dalla V10.1.")
    complete_spans = _spans(complete_text)
    old_spans = _spans(old)
    if not _ordered_title_match(list(complete_spans), list(old_spans))[0]:
        raise ValueError("Indice V10.1 non allineato con la Completa.")
    candidate, changes = apply_v102(old)
    candidate_spans = _spans(candidate)
    reverted = _replace(candidate, candidate_spans, {
        title: old[slice(*old_spans[title])] for title in TITLES
    })
    if _encode(reverted, newline, bom) != originals[1]:
        raise ValueError("Il confronto byte-a-byte esterno al pilot è fallito.")
    pre = audit_fast_essential(complete_text, old)
    post = audit_fast_essential(complete_text, candidate)
    if post.status == "FAIL" and pre.status != "FAIL":
        missing = sorted(
            set(post.missing_exam_number_chapters)
            - set(pre.missing_exam_number_chapters)
        )
        detail = ", ".join(missing) if missing else "rischio strutturale o numerico"
        raise ValueError(f"Nuovo FAIL dell'audit V10.2: {detail}")
    if _fingerprint(_snapshot(scan)) != manifest["fingerprint"] or any(
        p.read_bytes() != original for p, original in zip(paths, originals, strict=True)
    ):
        raise ValueError("Input o dispense modificati durante il test V10.2.")
    metrics = []
    for title in TITLES:
        source = complete_text[slice(*complete_spans[title])]
        proposed = candidate[slice(*candidate_spans[title])]
        words, source_words = len(proposed.split()), len(source.split())
        reduction = round(100 * (1 - words / source_words), 2)
        metrics.append({
            "title": title,
            "complete_words": source_words,
            "candidate_words": words,
            "reduction_percent": reduction,
            "target_met": 30 <= reduction <= 45,
            "human_review_required": True,
        })
    payload = _encode(candidate, newline, bom)
    report = {
        "version": VERSION,
        "status": "PREVIEW_UNPUBLISHED_MANUAL_SCIENCE_REVIEW_REQUIRED",
        "publication_allowed": False,
        "scientifically_verified": False,
        "model_used": False,
        "original_pdfs_individually_verified": False,
        "required_human_review": True,
        "complete_sha256": sha(originals[0]),
        "v101_candidate_sha256": sha(originals[1]),
        "v101_report_sha256": sha(originals[2]),
        "candidate_sha256": sha(payload),
        "source_fingerprint": manifest["fingerprint"],
        "unchanged_other_chapters": len(old_spans) - len(TITLES),
        "outside_pilot_byte_content_preserved": True,
        "before_audit": pre.status,
        "after_audit": post.status,
        "chapters": metrics,
        "changes": changes,
        "explicit_scientific_conflicts": [
            {
                "topic": "KREBS_ATP_CONVENTIONS",
                "status": "SOURCE_VS_MODERN_CONVENTION_UNRESOLVED",
                "text": (
                    "La Completa riporta circa 12 ATP equivalenti ma una "
                    "ripartizione interna incoerente. Nella V10.2 il testo "
                    "per lo studio indica 12 (storico) e 10 (moderno); "
                    "prima di usarlo per l'esame verificare le dispense "
                    "e quale convenzione è richiesta dal docente."
                ),
                "source_verification_required": True,
            },
        ],
        "manual_review_gate": "BLOCKED_UNTIL_SOURCE_PDF_AND_SCIENCE_REVIEW",
    }
    dossier = [
        "# BC Science Spark V10.2 — rifinitura scientifica, solo anteprima", "",
        "**NON PUBBLICATA — NON APPROVATA SCIENTIFICAMENTE.**",
        "",
        ("Cinque modifiche mirate ai tre capitoli del pilot. Le precisazioni "
         "scientifiche esterne non sostituiscono silenziosamente le dispense "
         "eCampus. Nessun PDF originale è stato certificato riga per riga."),
        "",
        f"Audit: {pre.status} → {post.status}",
        f"Capitoli esterni identici byte-per-byte: {len(old_spans) - len(TITLES)}",
        "",
    ]
    for metric in metrics:
        dossier += [
            f"## {metric['title']}", "",
            (f"Completa: {metric['complete_words']} parole; V10.2: "
             f"{metric['candidate_words']}; riduzione "
             f"{metric['reduction_percent']}%."),
            "",
        ]
        for change in changes:
            if change["title"] != metric["title"]:
                continue
            dossier += [
                f"### {change['ident']}", "",
                f"Provenienza: {change['provenance']}",
                f"**Prima:** {change['before']}",
                f"**Dopo:** {change['after']}",
                f"**Motivo:** {change['purpose']}",
                (f"Valori numerici prima: {change['numeric_tokens_before']} — "
                 f"dopo: {change['numeric_tokens_after']}."),
                ("Variazioni numeriche esplicite: "
                 f"{change['numeric_change_disclosed']}."),
                "**Controllo dei PDF originali e revisione umana: obbligatori.**",
            ]
            if change["reference"]:
                dossier += [
                    f"Riferimento esterno: {change['reference']}",
                ]
            dossier += [""]
    dossier += [
        "## Divergenze ancora da esaminare", "",
        *[f"- {entry['text']}" for entry in report["explicit_scientific_conflicts"]],
        "",
        "## Blocchi di approvazione", "",
        "- Confrontare ogni modifica con i PDF del corso: prima di un merge.",
        "- Controllare il diff e le condizioni/negazioni delle frasi adiacenti.",
        "- Non convertire WARN in PASS per eludere i controlli.",
        "- Non generalizzare agli altri 62 capitoli prima della revisione del pilot.",
        "- Lo stato dei test non equivale a certificazione scientifica.",
        "",
    ]
    dest.mkdir(parents=True, exist_ok=False)
    (dest / "essenziale-v10.2-CANDIDATA-NON-PUBBLICATA.md").write_bytes(payload)
    (dest / "v10.2-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    (dest / "v10.2-dossier.md").write_text(
        "\n".join(dossier), encoding="utf-8",
    )
    diff = "".join(difflib.unified_diff(
        old.splitlines(keepends=True), candidate.splitlines(keepends=True),
        fromfile="essenziale-v10.1-CANDIDATA.md",
        tofile="essenziale-v10.2-CANDIDATA.md",
    ))
    (dest / "v10.2-vs-v10.1.diff").write_text(diff, encoding="utf-8")
    files = sorted(dest.iterdir())
    (dest / "SHA256SUMS.json").write_text(
        json.dumps({p.name: sha(p.read_bytes()) for p in files}, indent=2),
        encoding="utf-8",
    )
    return dest
