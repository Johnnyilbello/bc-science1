"""V10.1 closed-corpus, review-only scientific clarification preview.

This is a deterministic amendment to a known V10 candidate, not automated
scientific validation. Each exact literal change is documented with external
context and source-vs-modern interpretation for independent human review.
Original PDFs, summaries, published files and Git state remain untouched.
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
from .structural_compression import TITLES, _replace, _spans, sha

VERSION = "scientific-clarifications-v10.1-review-only"
EXPECTED_COMPLETE_SHA256 = "72c1dfc066489d5936ebccbef7aaa06df3a3ca5d171cc329279e8bd71c18c40b"
EXPECTED_V10_CANDIDATE_SHA256 = "089f8c6503c3c8241445900b6286c4705dba963d3ca076aaaa952dd6af3b4510"
EXPECTED_V10_REPORT_SHA256 = "c859722d717e4745bf20da1f003abc2f1617e2e5f5dd88d31e6c4e3369c3a734"


@dataclass(frozen=True)
class Amendment:
    ident: str
    title: str
    before: str
    after: str
    explanation: str
    provenance: str
    reference: str | None
    status: str = "HUMAN_SCIENCE_REVIEW_REQUIRED"


AMENDMENTS = (
    Amendment(
        "KREBS_CYCLE_OPEN",
        TITLES[0],
        ("Il ciclo è aperto, non chiuso: gli intermedi possono uscire per la biosintesi "
         "o rientrare tramite reazioni anaplerotiche, che riforniscono il ciclo "
         "senza fermarlo quando sono sottratti."),
        ("Il ciclo è una sequenza ciclica di reazioni che rigenera l'ossalacetato, "
         "ma è metabolicamente aperto: alcuni intermedi possono essere sottratti "
         "per la biosintesi e reintegrati mediante reazioni anaplerotiche."),
        ("Il PDF della lezione chiama il ciclo 'aperto' per la sottrazione e il "
         "reintegro di intermedi; questo non nega la rigenerazione dell'ossalacetato."),
        "COURSE_PDF_CONTEXT_P8_AND_CLARIFYING_REWORDING",
        "https://www.ncbi.nlm.nih.gov/books/NBK541072/",
    ),
    Amendment(
        "KREBS_ATP_CONVENTIONS",
        TITLES[0],
        ("Per acetil-CoA: 3 NADH, 1 FADH2 e 1 GTP (o ATP). La Completa riporta circa "
         "12 ATP totali, con la ripartizione «3 da NADH, 2 da FADH2, 1 diretto + 6 "
         "derivati dalla fosforilazione ossidativa»; nel ripasso indica circa 3 ATP "
         "per NADH e 2 per FADH2, e 12 ATP totali per acetil-CoA."),
        ("Per acetil-CoA: 3 NADH, 1 FADH2 e 1 GTP (o ATP). La Completa indica circa "
         "12 ATP equivalenti secondo la convenzione storica (3 ATP/NADH e 2 ATP/FADH2). "
         "Con stime moderne (2,5 ATP/NADH e 1,5 ATP/FADH2) si ottengono circa 10 ATP "
         "equivalenti. **Nota di verifica:** la ripartizione riportata nella Completa "
         "è internamente incoerente; confrontare il valore richiesto con le dispense "
         "e il docente."),
        ("La Completa menziona circa 12 ATP ma ne esplicita una somma non coerente. "
         "I coefficienti moderni NADH=2,5 e FADH2=1,5 danno 10 ATP equivalenti "
         "per acetil-CoA; la convenzione didattica del corso resta da confermare "
         "sulle dispense. Nuovi numeri sono aggiunti e dichiarati, mai nascosti."),
        "COMPLETE_CONFLICT_PLUS_EXTERNAL_SCIENTIFIC_CONVENTION",
        "https://www.ncbi.nlm.nih.gov/books/NBK526105/",
    ),
    Amendment(
        "PROTEIN_TERTIARY",
        TITLES[1],
        ("Dipende dalle interazioni tra i gruppi R, le catene laterali degli "
         "aminoacidi, non dalla struttura secondaria."),
        ("La struttura terziaria integra gli elementi di struttura secondaria "
         "e le interazioni tra i gruppi R, cioè le catene laterali degli aminoacidi."),
        ("Struttura terziaria = organizzazione tridimensionale complessiva "
         "di elementi secondari e gruppi laterali; la formulazione precedente "
         "'non dalla struttura secondaria' è fuorviante."),
        "EXTERNAL_SCIENTIFIC_CLARIFICATION",
        "https://www.ncbi.nlm.nih.gov/books/NBK470269/",
    ),
    Amendment(
        "LIPID_MELTING_TREND",
        TITLES[1],
        ("Quelli saturi non hanno doppi legami fra carboni: sono dritti e solidi "
         "a temperatura ambiente. Gli insaturi hanno uno o più doppi legami "
         "che piegano la catena, rendendoli liquidi."),
        ("Gli acidi grassi saturi non hanno doppi legami C=C e tendono ad avere punti "
         "di fusione più elevati. Gli insaturi hanno uno o più doppi legami; quelli cis "
         "introducono pieghe e tendono ad avere punti di fusione inferiori. Lo stato "
         "fisico dipende anche dalla lunghezza della catena e dalla configurazione "
         "cis/trans."),
        ("Lo stato solido/liquido non dipende soltanto dalla saturazione; "
         "contano lunghezza e geometria cis/trans. Rimuovere gli assoluti."),
        "EXTERNAL_SCIENTIFIC_CLARIFICATION",
        "https://openstax.org/books/organic-chemistry/pages/27-1-waxes-fats-and-oils",
    ),
    Amendment(
        "CHIRALITY_NO_ONLY_CARBON",
        TITLES[2],
        ("Gli enantiomeri sono coppie di immagini speculari non sovrapponibili "
         "(Fonte p.2); nella formulazione della Completa esistono solo se "
         "il carbonio è legato a 4 gruppi diversi."),
        ("Gli enantiomeri sono immagini speculari non sovrapponibili (Fonte p.2). "
         "Un carbonio legato a 4 gruppi diversi è un caso comune di centro "
         "chirale, ma la chiralità può essere anche assiale."),
        ("La presenza di un carbonio tetraedrico stereogenico è un caso comune, "
         "ma non una condizione universale di chiralità."),
        "EXTERNAL_IUPAC_EXCEPTION_TO_SOURCE_GENERALIZATION",
        "https://goldbook.iupac.org/terms/view/A00547/plain",
    ),
    Amendment(
        "POLYMER_CLASSIFICATION",
        TITLES[2],
        ("La Completa indica solo proteine, acidi nucleici e carboidrati "
         "come polimeri; i lipidi non lo sono."),
        ("Proteine, acidi nucleici e polisaccaridi sono polimeri; "
         "i lipidi generalmente non sono polimeri."),
        ("Sostituisce un commento sul documento Completa con la distinzione "
         "tra carboidrati in generale e polisaccaridi, evitando il 'solo' "
         "assoluto per i lipidi."),
        "EDITORIAL_AND_SCIENTIFIC_QUALIFIER_REVIEW",
        None,
    ),
    Amendment(
        "CONDENSATION_HYDROLYSIS",
        TITLES[2],
        ("Nel promemoria: condensazione «unisce + acqua», "
         "idrolisi «rompe - acqua»."),
        ("In breve: la condensazione unisce liberando acqua; "
         "l'idrolisi rompe consumando acqua."),
        ("La spiegazione originale era corretta, ma il promemoria con i segni "
         "+/- poteva suggerire il verso opposto dell'acqua."),
        "COURSE_COMPLETE_SAME_MECHANISM_EDIT_FOR_CLARITY",
        None,
    ),
)


def _decode(payload: bytes) -> tuple[str, str, bytes]:
    bom = b"\xef\xbb\xbf" if payload.startswith(b"\xef\xbb\xbf") else b""
    text = payload.decode("utf-8-sig")
    if text.count("\r\n") and text.count("\n") != text.count("\r\n"):
        raise ValueError("Convenzione di newline mista: nessun output.")
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n"), newline, bom


def _encode(text: str, newline: str, bom: bytes) -> bytes:
    return bom + text.replace("\n", newline).encode("utf-8")


def apply_clarifications(v10_text: str) -> tuple[str, list[dict]]:
    """Replace each exact V10 claim once in its known chapter only."""
    spans = _spans(v10_text)
    if not set(TITLES).issubset(spans):
        raise ValueError("Mancano i tre capitoli V10 richiesti.")
    revisions: dict[str, str] = {}
    observations = []
    for title in TITLES:
        start, stop = spans[title]
        original = v10_text[start:stop]
        updated = original
        for patch in (p for p in AMENDMENTS if p.title == title):
            if updated.count(patch.before) != 1 or patch.after in updated:
                raise ValueError(f"Ancoraggio non univoco o già alterato: {patch.ident}")
            updated = updated.replace(patch.before, patch.after, 1)
            numbers = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?")
            observations.append({
                **asdict(patch),
                "numeric_tokens_before": numbers.findall(patch.before),
                "numeric_tokens_after": numbers.findall(patch.after),
                "numeric_change_disclosed": (
                    numbers.findall(patch.before) != numbers.findall(patch.after)
                ),
                "requires_original_pdf_check": True,
                "automatically_scientifically_verified": False,
            })
        revisions[title] = updated

    candidate = _replace(v10_text, spans, revisions)
    candidate_spans = _spans(candidate)
    reverted = _replace(candidate, candidate_spans, {
        title: v10_text[slice(*spans[title])] for title in TITLES
    })
    if reverted != v10_text:
        raise ValueError("Differenze fuori dai tre capitoli o cambiamenti non reversibili.")
    if len(observations) != len(AMENDMENTS):
        raise ValueError("Modifica attesa mancante.")
    return candidate, observations


def run_scientific_clarifications(
    scan: CourseScan,
    complete_file: Path,
    v10_candidate_file: Path,
    v10_report_file: Path,
    output_dir: Path,
) -> Path:
    """Stage an unapproved V10.1 preview after hashing all evidence inputs."""
    paths = [
        path.expanduser().resolve()
        for path in (complete_file, v10_candidate_file, v10_report_file)
    ]
    output = output_dir.expanduser().resolve()
    root = scan.path.resolve()
    if len(set(paths)) != len(paths) or any(not p.is_file() for p in paths):
        raise ValueError("Percorsi input assenti o duplicati.")
    if (
        output.exists() or output == root or root in output.parents
        or any(output == p.parent or p.parent in output.parents for p in paths)
    ):
        raise ValueError("La cartella anteprima deve essere nuova e separata.")
    originals = [p.read_bytes() for p in paths]
    if (
        sha(originals[0]) != EXPECTED_COMPLETE_SHA256
        or sha(originals[1]) != EXPECTED_V10_CANDIDATE_SHA256
        or sha(originals[2]) != EXPECTED_V10_REPORT_SHA256
    ):
        raise ValueError("Hash della Completa/V10/report diversi dal pilot esaminato.")
    full, _, _ = _decode(originals[0])
    prior, newline, bom = _decode(originals[1])
    report = json.loads(originals[2].decode("utf-8-sig"))
    if (
        report.get("version") != "structural-compression-v10"
        or report.get("status") != "PREVIEW_UNPUBLISHED_MANUAL_REVIEW_REQUIRED"
        or report.get("publication_allowed") is not False
        or report.get("complete_sha256") != sha(originals[0])
        or report.get("candidate_sha256") != sha(originals[1])
        or [chapter["title"] for chapter in report.get("chapters", [])] != list(TITLES)
    ):
        raise ValueError("Report V10 non coerente con candidata o integrità.")
    manifest, _mapping = _validated_manifest(scan, full)
    if manifest["fingerprint"] != report["source_fingerprint"]:
        raise ValueError("Fingerprint delle dispense differente dal report V10.")
    full_spans, prior_spans = _spans(full), _spans(prior)
    if not _ordered_title_match(list(full_spans), list(prior_spans))[0]:
        raise ValueError("Indice Completa e V10 incompatibile.")
    candidate, revisions = apply_clarifications(prior)
    # Explicitly preserve all non-pilot chapters and front matter exactly.
    candidate_spans = _spans(candidate)
    reverted = _replace(candidate, candidate_spans, {
        title: prior[slice(*prior_spans[title])] for title in TITLES
    })
    if _encode(reverted, newline, bom) != originals[1]:
        raise ValueError("Byte esterni ai capitoli modificati.")
    before = audit_fast_essential(full, prior)
    after = audit_fast_essential(full, candidate)
    if after.status == "FAIL" and before.status != "FAIL":
        new_missing = sorted(
            set(getattr(after, "missing_exam_number_chapters", ()))
            - set(getattr(before, "missing_exam_number_chapters", ()))
        )
        detail = (
            " Numeri d'esame non preservati: " + ", ".join(new_missing) + "."
            if new_missing else ""
        )
        raise ValueError(
            "Un nuovo FAIL dell'audit blocca la candidata." + detail
        )
    if _fingerprint(_snapshot(scan)) != manifest["fingerprint"] or any(
        p.read_bytes() != original for p, original in zip(paths, originals, strict=True)
    ):
        raise ValueError("Dispense o file input cambiati durante la revisione.")

    chapter_metrics = []
    for title in TITLES:
        original_part = full[slice(*full_spans[title])]
        candidate_part = candidate[slice(*candidate_spans[title])]
        reduction = round(
            100 * (1 - len(candidate_part.split()) / len(original_part.split())), 2,
        )
        chapter_metrics.append({
            "title": title,
            "complete_words": len(original_part.split()),
            "candidate_words": len(candidate_part.split()),
            "reduction_percent": reduction,
            "reduction_target_met": 30 <= reduction <= 45,
            "review_required": True,
        })

    encoded = _encode(candidate, newline, bom)
    result = {
        "version": VERSION,
        "status": "PREVIEW_UNPUBLISHED_SCIENCE_REVIEW_REQUIRED",
        "publication_allowed": False,
        "scientifically_verified": False,
        "model_used": False,
        "source_pdf_checked_for_every_amendment": False,
        "complete_sha256": sha(originals[0]),
        "v10_candidate_sha256": sha(originals[1]),
        "v10_report_sha256": sha(originals[2]),
        "source_fingerprint": manifest["fingerprint"],
        "candidate_sha256": sha(encoded),
        "unchanged_other_chapters": len(prior_spans) - len(TITLES),
        "changes": revisions,
        "chapters": chapter_metrics,
        "before_audit": before.status,
        "after_audit": after.status,
        "manual_review_gate": "BLOCKED_UNTIL_ORIGINAL_PDF_AND_SCIENCE_REVIEW",
    }
    dossier = [
        "# BC Science — Spark V10.1 · Revisione scientifica mirata", "",
        "**CANDIDATA NON PUBBLICATA. VIETATO MERGE SENZA VERIFICA UMANA.**", "",
        ("Questi interventi distinguono esplicitamente il contenuto della Completa "
         "dalle precisazioni scientifiche esterne. I PDF originali non sono stati "
         "confrontati pagina per pagina per ogni modifica: nessun fatto è certificato."),
        "",
        f"Audit: {before.status} → {after.status}",
        f"Capitoli esterni invariati: {result['unchanged_other_chapters']}", "",
    ]
    for metric in chapter_metrics:
        dossier += [
            f"## {metric['title']}",
            (f"Completa {metric['complete_words']} parole; V10.1 "
             f"{metric['candidate_words']} parole; "
             f"riduzione {metric['reduction_percent']}%."),
            "",
        ]
        for entry in revisions:
            if entry["title"] != metric["title"]:
                continue
            dossier += [
                f"### {entry['ident']}",
                f"Stato: {entry['status']}",
                f"Provenienza: {entry['provenance']}",
                f"Prima: {entry['before']}",
                f"Dopo: {entry['after']}",
                f"Motivo: {entry['explanation']}",
                "Numeri prima: " + ", ".join(entry["numeric_tokens_before"]),
                "Numeri dopo: " + ", ".join(entry["numeric_tokens_after"]),
                f"Numeri modificati: {entry['numeric_change_disclosed']}",
                "Controllo dispense originali: **ancora obbligatorio**.",
            ]
            if entry["reference"]:
                dossier.append(f"Riferimento scientifico esterno: {entry['reference']}")
            dossier.append("")
    dossier += [
        "## Blocchi di pubblicazione", "",
        "- Esaminare ogni nuova formulazione con i PDF originali della materia.",
        ("- Krebs: verificare la convenzione ATP effettivamente richiesta dal docente "
         "e non confondere resa storica e moderna."),
        ("- Gli aggiornamenti da fonti esterne non devono diventare correzioni "
         "silenziose delle dispense eCampus."),
        "- Confrontare il diff, approvare ogni modifica oppure rifiutarla.",
        "- Nessuna verifica automatica di equivalenza semantica.", "",
    ]
    output.mkdir(parents=True, exist_ok=False)
    candidate_path = output / "essenziale-v10.1-CANDIDATA-NON-PUBBLICATA.md"
    candidate_path.write_bytes(encoded)
    (output / "v10.1-report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    (output / "v10.1-dossier.md").write_text(
        "\n".join(dossier), encoding="utf-8",
    )
    diff = "".join(difflib.unified_diff(
        prior.splitlines(keepends=True),
        candidate.splitlines(keepends=True),
        fromfile="essenziale-v10-CANDIDATA.md",
        tofile="essenziale-v10.1-CANDIDATA.md",
    ))
    (output / "v10.1-vs-v10.diff").write_text(diff, encoding="utf-8")
    artifacts = sorted(output.iterdir())
    (output / "SHA256SUMS.json").write_text(
        json.dumps({
            path.name: sha(path.read_bytes()) for path in artifacts
        }, indent=2), encoding="utf-8",
    )
    return output
