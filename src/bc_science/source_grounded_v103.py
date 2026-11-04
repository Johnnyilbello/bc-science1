"""Spark V10.3: source-page-grounded, fail-closed compression pilot.

Never publishes/merges; cannot certify semantic equivalence. Only the three
chapter spans in a pinned V10.2 candidate are transformed. The seven course
PDFs must match the independently supplied ZIP manifest byte-for-byte.
"""
from __future__ import annotations

import difflib
import io
import json
import re
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

import pymupdf as fitz

from .autopilot_runner import _validated_manifest
from .courses import CourseScan, _fingerprint, _snapshot
from .fast_audit import _ordered_title_match, audit_fast_essential
from .scientific_clarifications_v101 import _decode, _encode
from .structural_compression import TITLES, _replace, _spans, sha

VERSION = "spark-v10.3-source-grounded-review-only"
SOURCE_ARCHIVE_SHA256 = "1d042ce9a797c528d0a3eec26170a6dbcbba3a7c0f5c5354103f81339d48fcec"
COMPLETE_SHA256 = "72c1dfc066489d5936ebccbef7aaa06df3a3ca5d171cc329279e8bd71c18c40b"
V102_SHA256 = "50aa8e47be8526edbf46c10f940e899798030ca254d2bf51df76345eff67bdb3"
V102_REPORT_SHA256 = "0294c88859d32416626f5ff7203945684494973d8afc9716b29d90b69a0bbc9c"

# Original names and SHA256 from the supplied 7-PDF source manifest.
SOURCE_HASHES: dict[str, str] = {
    "Il ciclo di Krebs 2.pdf":
        "543fa153c878cb5764ce8246d85cfa231489334ccc4140a2e5c9c7a985c577fb",
    "Il ciclo di Krebs.pdf":
        "82c6e87fc6fe347ed01e2c532706ddae02a9c96e98f5227d682d4f0c307b14f3",
    "La struttura terziaria e quaternaria delle proteine ed i lipidi 2.pdf":
        "b5af930242ab31b3f93f016be649e3446a354497bc68c02ca1951119e158130c",
    "La struttura terziaria e quaternaria delle proteine ed i lipidi 3.pdf":
        "0f19a3f55bcdc44744109df538bcbbf319e5b2c9f61acdf3e7fbbb87a3f32705",
    "La struttura terziaria e quaternaria delle proteine ed i lipidi.pdf":
        "1ec0abb63260854b4cde61073b804bd187b7ee27f245b8c96af495e5492eb6f6",
    "CHIMICA ORGANICA e carboidrati 2.pdf":
        "43343aeb466dcd7dc16634cf27b1d0bcc153aa35f6df362f2ce9e0f33bec7efd",
    "CHIMICA ORGANICA e carboidrati.pdf":
        "84ab6b61ec819dc7c88bdd6ec0a4cddf147410f45079c9bb060e88573f6e35cb",
}
KREBS_PDF = "Il ciclo di Krebs 2.pdf"
PROTEIN_PDF = "La struttura terziaria e quaternaria delle proteine ed i lipidi 2.pdf"
CHEM_PDF = "CHIMICA ORGANICA e carboidrati 2.pdf"


@dataclass(frozen=True)
class Patch:
    ident: str
    chapter: str
    before: str
    after: str
    reason: str


KREBS_STAGES = (
    "**Otto tappe del ciclo (dispensa pp. 4–6):**\n"
    "1. Ossalacetato (4 C) + acetil-CoA (2 C) → citrato (6 C) — **citrato sintasi**; libera CoA-SH.\n"
    "2. Citrato → isocitrato — **aconitasi**; isomerizzazione reversibile.\n"
    "3. Isocitrato → α-chetoglutarato + CO2 + NADH — **isocitrato deidrogenasi**.\n"
    "4. α-chetoglutarato → succinil-CoA + CO2 + NADH — **complesso α-chetoglutarato deidrogenasi**.\n"
    "5. Succinil-CoA → succinato + GTP (o ATP) — **succinil-CoA sintetasi**; fosforilazione a livello del substrato.\n"
    "6. Succinato → fumarato + FADH2 — **succinato deidrogenasi**.\n"
    "7. Fumarato + H2O → malato — **fumarasi** (fumarato idratasi).\n"
    "8. Malato → ossalacetato + NADH — **malato deidrogenasi**.\n"
    "**Bilancio per giro:** 2 CO2, 3 NADH, 1 FADH2, 1 GTP (o ATP).\n\n"
    "**Uso biosintetico del citrato (dispensa p. 7):** dopo un pasto ricco di carboidrati, "
    "il citrato in eccesso può essere trasportato nel citoplasma; la **citrato liasi** "
    "lo scinde, fornendo acetil-CoA per sintetizzare **acidi grassi e colesterolo**."
)

PROTEIN_FACTS = (
    "**Esempi di struttura quaternaria (dispensa p. 2):** "
    "l'**emoglobina** ha **4 catene** (2 α e 2 β); "
    "l'**anticorpo** ne ha **4** (2 pesanti e 2 leggere, legate anche da ponti disolfuro); "
    "il **collagene** comprende **3 catene** polipeptidiche.\n\n"
    "**Acidi grassi (dispensa p. 3):** in questa lezione sono descritti generalmente "
    "con **4–24 atomi di carbonio**, spesso in numero pari. "
    "Sono **monoinsaturi** se hanno un doppio legame, **polinsaturi** se ne hanno almeno due. "
    "La scrittura **18:1 Δ9** per l'**acido oleico** indica 18 carboni, un doppio "
    "legame con inizio al carbonio 9 (C9–C10).\n\n"
    "**Triacilgliceroli (dispensa p. 4):** glicerolo + **3 acidi grassi** "
    "formano **3 legami esterei**, liberando **3 molecole di H2O**; "
    "la sequenza progressiva comprende **monoacilglicerolo**, **diacilglicerolo** "
    "e **triacilglicerolo**.\n\n"
    "**Fosfolipidi (dispensa p. 4):** nella struttura a base di glicerolo "
    "si trovano **2 acidi grassi** e un **gruppo fosfato**, spesso legato "
    "a un gruppo polare come la **colina**; sono anfipatici e formano doppi strati.\n\n"
    "**Altre classi (dispensa p. 5):** gli **sfingolipidi** comprendono "
    "**sfingosina**, un acido grasso e un gruppo polare, importante anche per "
    "il riconoscimento cellulare. I **terpeni** derivano da unità di "
    "**isoprene (5 C)**; tra gli esempi vi sono i **carotenoidi**, precursori "
    "della **vitamina A**. Gli **steroidi** hanno **4 anelli** condensati "
    "(3 esagonali e 1 pentagonale); il **colesterolo** è uno steroide "
    "presente nelle membrane cellulari."
)

CHEM_FACTS = (
    "**Isomeria (dispensa p. 2):** **etanolo e dimetil etere** (C2H6O) "
    "sono isomeri strutturali; **cis-2-butene e trans-2-butene** "
    "sono isomeri geometrici.\n\n"
    "**Gruppi funzionali (dispensa p. 5):** il **fosfato** può "
    "perdere protoni ed essere carico negativamente; il **solfidrilico (–SH)** "
    "contiene zolfo legato a idrogeno.\n\n"
    "**Monosaccaridi (dispensa pp. 6–7):** generalmente **3–7 carboni** "
    "nella classificazione proposta; esempi sono i pentosi **ribosio** "
    "e **desossiribosio**, e gli esosi **glucosio**, **fruttosio** "
    "e **galattosio**. Il glucosio è un aldoso, il fruttosio un chetoso. "
    "**Precisazione esterna rispetto alla dispensa p. 7:** il lattosio "
    "non è un esoso, ma un disaccaride; glucosio e galattosio "
    "sono epimeri al C4, non isomeri strutturali.\n\n"
    "**Ciclizzazione (dispensa p. 8):** nel glucosio il gruppo aldeidico "
    "del **C1** reagisce con l'ossidrile del **C5**, formando un "
    "**emiacetale ciclico**. Il C1 diventa **carbonio anomerico**; "
    "le forme **α-glucosio** e **β-glucosio** differiscono "
    "per l'orientamento dell'OH anomerico.\n\n"
    "**Disaccaridi e legami (dispensa p. 9):** "
    "**maltosio** = glucosio + glucosio, legame **α(1→4)**; "
    "**saccarosio** = glucosio + fruttosio, legame **α(1→2)β** "
    "(C1 del glucosio e C2 del fruttosio); "
    "**lattosio** = galattosio + glucosio, legame **β(1→4)**."
)

PATCHES = (
    Patch(
        "PIRUVATO_BEFORE_KREBS", TITLES[0],
        ("La glicolisi è solo la prima fase: il suo prodotto finale, il piruvato, "
         "non viene ridotto come nelle condizioni anaerobiche di fermentazione, "
         "ma viene trasportato nei mitocondri per essere ossidato nel ciclo."),
        ("La glicolisi è solo la prima fase: in condizioni aerobiche il piruvato "
         "non segue la fermentazione, ma viene trasportato nei mitocondri e "
         "convertito in acetil-CoA dal complesso della piruvato deidrogenasi "
         "**prima** dell'ingresso del gruppo acetilico nel ciclo."),
        "Dispensa Krebs 2.pdf p. 3: conversione irreversibile PDH prima del ciclo.",
    ),
    Patch(
        "KREBS_EIGHT_STAGES_AND_CITRATE", TITLES[0],
        "### Ripasso essenziale\n",
        KREBS_STAGES + "\n\n### Ripasso essenziale\n",
        "Reintegro delle 8 tappe pp. 4–6 e citrato per lipogenesi p. 7.",
    ),
    Patch(
        "KREBS_ATP_SOURCE_PRIORITY", TITLES[0],
        ("La resa teorica è circa 12 ATP equivalenti secondo la convenzione "
         "storica (3 ATP/NADH e 2 ATP/FADH2); con i coefficienti moderni "
         "(2,5 ATP/NADH e 1,5 ATP/FADH2) si stimano circa 10 ATP equivalenti."),
        ("**Resa secondo la dispensa eCampus: circa 12 ATP equivalenti** "
         "per acetil-CoA, con convenzione storica (3 ATP/NADH e 2 ATP/FADH2; "
         "9 + 2 + 1 ATP equivalenti). **Nota scientifica esterna:** "
         "con coefficienti moderni (2,5 ATP/NADH e 1,5 ATP/FADH2) "
         "la stima è di circa 10 ATP equivalenti."),
        "Il PDF Krebs p. 6 adotta 12; 10 è riferimento scientifico esterno.",
    ),
    Patch(
        "PROTEIN_LIPID_SOURCE_UNITS", TITLES[1],
        "### Ripasso essenziale\n",
        PROTEIN_FACTS + "\n\n### Ripasso essenziale\n",
        "Dispensa proteine/lipidi 2.pdf pp. 2–5: esempi, numeri e classi.",
    ),
    Patch(
        "CHEMISTRY_SOURCE_UNITS", TITLES[2],
        "### Ripasso essenziale\n",
        CHEM_FACTS + "\n\n### Ripasso essenziale\n",
        "Dispensa Chimica organica 2.pdf pp. 2,5–9; fonte errata segnalata.",
    ),
)


@dataclass(frozen=True)
class AtomicFact:
    ident: str
    chapter: str
    pdf: str
    page: int
    source_terms: tuple[str, ...]
    witness: str
    provenance: str = "COURSE_PDF"


FACTS = (
    AtomicFact("K00_PDH", TITLES[0], KREBS_PDF, 3,
               ("piruvato", "Piruvato deidrogenasi"), "piruvato deidrogenasi **prima**"),
    AtomicFact("K01_CITRATE", TITLES[0], KREBS_PDF, 4,
               ("citrato sintasi",), "1. Ossalacetato (4 C) + acetil-CoA (2 C) → citrato (6 C)"),
    AtomicFact("K02_ACONITASE", TITLES[0], KREBS_PDF, 4,
               ("aconitasi",), "2. Citrato → isocitrato — **aconitasi**"),
    AtomicFact("K03_ISOCITRATE_DH", TITLES[0], KREBS_PDF, 4,
               ("isocitrato deidrogenasi",), "3. Isocitrato → α-chetoglutarato + CO2 + NADH"),
    AtomicFact("K04_AKG_DH", TITLES[0], KREBS_PDF, 5,
               ("chetoglutarato deidrogenasi",), "4. α-chetoglutarato → succinil-CoA + CO2 + NADH"),
    AtomicFact("K05_SUCCINYL_SYNTHETASE", TITLES[0], KREBS_PDF, 5,
               ("succinil-CoA sintetasi",), "5. Succinil-CoA → succinato + GTP (o ATP)"),
    AtomicFact("K06_SUCCINATE_DH", TITLES[0], KREBS_PDF, 5,
               ("succinato deidrogenasi",), "6. Succinato → fumarato + FADH2"),
    AtomicFact("K07_FUMARASE", TITLES[0], KREBS_PDF, 5,
               ("Fumarasi",), "7. Fumarato + H2O → malato"),
    AtomicFact("K08_MALATE_DH", TITLES[0], KREBS_PDF, 6,
               ("malato deidrogenasi",), "8. Malato → ossalacetato + NADH"),
    AtomicFact("K09_BALANCE", TITLES[0], KREBS_PDF, 6,
               ("tre molecole", "GTP"), "**Bilancio per giro:** 2 CO2, 3 NADH, 1 FADH2, 1 GTP"),
    AtomicFact("K10_ATP_HISTORIC", TITLES[0], KREBS_PDF, 6,
               ("12", "NADH"), "secondo la dispensa eCampus: circa 12 ATP equivalenti"),
    AtomicFact("K11_CITRATE_EXPORT", TITLES[0], KREBS_PDF, 7,
               ("citrato liasi", "colesterolo"), "acetil-CoA per sintetizzare **acidi grassi e colesterolo**"),
    AtomicFact("P01_QUATERNARY", TITLES[1], PROTEIN_PDF, 2,
               ("collagene", "pesanti"), "il **collagene** comprende **3 catene**"),
    AtomicFact("P02_ANTIBODY", TITLES[1], PROTEIN_PDF, 1,
               ("anticorpo", "4 catene"), "2 pesanti e 2 leggere"),
    AtomicFact("P03_OLEIC", TITLES[1], PROTEIN_PDF, 3,
               ("oleico",), "**18:1 Δ9**"),
    AtomicFact("P04_MONO_POLY", TITLES[1], PROTEIN_PDF, 3,
               ("monoinsaturo", "polinsaturo"), "**monoinsaturi**"),
    AtomicFact("P05_TAG_H2O", TITLES[1], PROTEIN_PDF, 4,
               ("tre molecole", "acilglicerolo"), "**3 molecole di H2O**"),
    AtomicFact("P06_PHOSPHOLIPID", TITLES[1], PROTEIN_PDF, 4,
               ("fosfolipidi", "fosfato"), "**2 acidi grassi** e un **gruppo fosfato**"),
    AtomicFact("P07_SPHINGO", TITLES[1], PROTEIN_PDF, 5,
               ("sfingosina",), "**sfingolipidi** comprendono **sfingosina**"),
    AtomicFact("P08_TERPE", TITLES[1], PROTEIN_PDF, 5,
               ("terpeni", "isoprene"), "**isoprene (5 C)**"),
    AtomicFact("P09_STEROID", TITLES[1], PROTEIN_PDF, 5,
               ("steroidi", "colesterolo"), "**steroidi** hanno **4 anelli**"),
    AtomicFact("C01_ISOMERS", TITLES[2], CHEM_PDF, 2,
               ("etanolo", "dimetil etere"), "**etanolo e dimetil etere**"),
    AtomicFact("C02_FUNCTIONAL", TITLES[2], CHEM_PDF, 5,
               ("fosfato", "solfidrilico"), "**solfidrilico (–SH)**"),
    AtomicFact("C03_SUGARS", TITLES[2], CHEM_PDF, 7,
               ("ribosio", "fruttosio"), "**desossiribosio**"),
    AtomicFact("C04_ANOMERIC", TITLES[2], CHEM_PDF, 8,
               ("carbonio 1", "ossidrile"), "**carbonio anomerico**"),
    AtomicFact("C05_ANOMERS", TITLES[2], CHEM_PDF, 8,
               ("alfa", "Beta"), "**α-glucosio** e **β-glucosio**"),
    AtomicFact("C06_MALTOSE", TITLES[2], CHEM_PDF, 9,
               ("maltosio",), "**maltosio** = glucosio + glucosio, legame **α(1→4)**"),
    AtomicFact("C07_SUCROSE", TITLES[2], CHEM_PDF, 9,
               ("saccarosio",), "**saccarosio** = glucosio + fruttosio, legame **α(1→2)β**"),
    AtomicFact("C08_LACTOSE", TITLES[2], CHEM_PDF, 9,
               ("lattosio",), "**lattosio** = galattosio + glucosio, legame **β(1→4)**"),
    AtomicFact("C09_SOURCE_ERRORS", TITLES[2], CHEM_PDF, 7,
               ("glucosio", "galattosio", "lattosio"),
               "glucosio e galattosio sono epimeri al C4",
               "SOURCE_DISCREPANCY_RESOLVED_BY_EXTERNAL_BIOCHEMISTRY"),
)


def _clean(s: str) -> str:
    return " ".join(s.casefold().split())


def _archive_sources(archive_path: Path) -> dict[str, bytes]:
    """Read only the exact uploaded ZIP; no extraction or modified originals."""
    if not archive_path.is_file():
        raise ValueError(f"Archivio originale mancante: {archive_path}")
    payload = archive_path.read_bytes()
    if sha(payload) != SOURCE_ARCHIVE_SHA256:
        raise ValueError(
            "ZIP delle fonti differente dall'archivio originale verificato "
            f"(SHA256 rilevato: {sha(payload)})."
        )
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as package:
            names = package.namelist()
            if names.count("manifest.json") != 1:
                raise ValueError("Manifest dell'archivio assente o duplicato.")
            manifest = json.loads(package.read("manifest.json").decode("utf-8-sig"))
            if not isinstance(manifest, list) or len(manifest) != len(SOURCE_HASHES):
                raise ValueError("Manifest: attesi esattamente sette file PDF.")
            records = {}
            for record in manifest:
                if not isinstance(record, dict):
                    raise TypeError("Voce manifest non valida.")
                filename = record.get("File")
                if filename not in SOURCE_HASHES or filename in records:
                    raise ValueError(f"Voce manifest duplicata o ignota: {filename}")
                if str(record.get("SHA256", "")).lower() != SOURCE_HASHES[filename]:
                    raise ValueError(f"SHA manifest incoerente: {filename}")
                records[filename] = record
            if set(records) != set(SOURCE_HASHES):
                raise ValueError("Manifest non contiene tutte le sette dispense.")

            pdf_entries = [
                item for item in package.infolist()
                if not item.is_dir() and item.filename.lower().endswith(".pdf")
            ]
            if len(pdf_entries) != len(SOURCE_HASHES):
                raise ValueError("ZIP con PDF aggiuntivi, mancanti o duplicati.")
            validated = {}
            for filename, digest in SOURCE_HASHES.items():
                matches = []
                for entry in pdf_entries:
                    basename = entry.filename.replace("\\", "/").rsplit("/", 1)[-1]
                    original_name = re.sub(r"^\d{2}-", "", basename)
                    if original_name.casefold() == filename.casefold():
                        matches.append(entry)
                if len(matches) != 1:
                    raise ValueError(f"PDF non univoco dentro lo ZIP: {filename}")
                entry = matches[0]
                if entry.file_size != records[filename].get("DimensioneBytes"):
                    raise ValueError(f"Dimensione incoerente nel manifest: {filename}")
                data = package.read(entry)
                if sha(data) != digest:
                    raise ValueError(f"Contenuto PDF diverso dal manifest: {filename}")
                validated[filename] = data
            return validated
    except (zipfile.BadZipFile, KeyError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("ZIP o manifest originali non leggibili.") from exc


def validate_original_pdfs(
    course: Path, source_archive: Path | None = None,
) -> list[dict]:
    """Verify seven immutable SHA values and each page's literal evidence."""
    if source_archive is not None:
        sources: dict[str, Path | bytes] = _archive_sources(source_archive)
    else:
        candidates = [
            p for p in course.rglob("*")
            if p.is_file() and p.suffix.lower() == ".pdf"
        ]
        sources = {}
        for filename, digest in SOURCE_HASHES.items():
            matches = [
                p for p in candidates if p.name.casefold() == filename.casefold()
            ]
            if not matches:
                raise ValueError(
                    f"PDF non trovato nella cartella del corso: {filename}. "
                    "Usare --source-archive con lo ZIP originale verificato."
                )
            valid = [p for p in matches if sha(p.read_bytes()) == digest]
            if not valid:
                observed = ", ".join(
                    f"{p}: {sha(p.read_bytes())}" for p in matches
                )
                raise ValueError(
                    f"PDF originale con hash del corso non coincidente: {filename}. "
                    f"Copie trovate: {observed}. "
                    "Usare --source-archive con lo ZIP originale verificato."
                )
            if len(valid) != 1:
                raise ValueError(
                    f"Più copie identiche del PDF nel corso: {filename}. "
                    "Usare --source-archive con lo ZIP originale verificato."
                )
            sources[filename] = valid[0]

    checks = []
    for fact in FACTS:
        file = sources[fact.pdf]
        if isinstance(file, bytes):
            doc = fitz.open(stream=file, filetype="pdf")
        else:
            doc = fitz.open(file)
        with doc:
            if len(doc) < fact.page:
                raise ValueError(f"Pagina {fact.page} non presente: {fact.pdf}")
            content = _clean(doc[fact.page - 1].get_text("text"))
        missing = [
            term for term in fact.source_terms
            if _clean(term) not in content
        ]
        if missing:
            raise ValueError(
                f"Fonte non verificabile {fact.ident} p.{fact.page}: "
                + ", ".join(missing)
            )
        checks.append({
            **asdict(fact),
            "source_sha256": SOURCE_HASHES[fact.pdf],
            "page_anchor_text_check": True,
            "human_scientific_review_required": True,
        })
    return checks


def apply_v103(v102: str) -> tuple[str, list[dict]]:
    """Exact-only five modifications and mandatory atomic-fact coverage."""
    spans = _spans(v102)
    if not set(TITLES).issubset(spans):
        raise ValueError("Mancano i tre capitoli del pilot V10.2.")
    replacements = {}
    changes = []
    for title in TITLES:
        old = v102[slice(*spans[title])]
        new = old
        for patch in (p for p in PATCHES if p.chapter == title):
            if new.count(patch.before) != 1:
                raise ValueError(f"Ancoraggio assente o duplicato: {patch.ident}")
            independent = new.replace(patch.before, "", 1)
            if patch.after in independent:
                raise ValueError(f"Sostituzione già presente indipendentemente: {patch.ident}")
            new = new.replace(patch.before, patch.after, 1)
            changes.append(asdict(patch))
        replacements[title] = new
    proposed = _replace(v102, spans, replacements)
    after_spans = _spans(proposed)
    restored = _replace(proposed, after_spans, {
        title: v102[slice(*spans[title])] for title in TITLES
    })
    if restored != v102:
        raise ValueError("Testo fuori dal pilot modificato.")
    if len(changes) != len(PATCHES):
        raise ValueError("Numero patch non corretto.")
    validate_fact_coverage(proposed)
    return proposed, changes


def validate_fact_coverage(proposed: str) -> None:
    """Exact atomic-fact markers: intentionally conservative, not semantic proof."""
    spans = _spans(proposed)
    for f in FACTS:
        if f.chapter not in spans:
            raise ValueError(f"Capitolo mancante per {f.ident}")
        chapter = proposed[slice(*spans[f.chapter])]
        if chapter.count(f.witness) != 1:
            raise ValueError(f"Unita sorgente assente o duplicata: {f.ident}")
    # The order of all eight stage witnesses must match the original PDF.
    section = proposed[slice(*spans[TITLES[0]])]
    steps = [f for f in FACTS if re.match(r"K0[1-8]_", f.ident)]
    positions = [section.index(f.witness) for f in steps]
    if positions != sorted(positions) or len(positions) != 8:
        raise ValueError("Sequenza ordinata delle 8 tappe non conservata.")
    if "piruvato" not in section or "prima" not in section:
        raise ValueError("Ordine piruvato → acetil-CoA → Krebs non esplicito.")


def run_v103(
    scan: CourseScan,
    complete_file: Path,
    v102_candidate: Path,
    v102_report: Path,
    output_dir: Path,
    source_archive: Path | None = None,
) -> Path:
    source_paths = [p.expanduser().resolve() for p in (
        complete_file, v102_candidate, v102_report
    )]
    root = scan.path.resolve()
    dest = output_dir.expanduser().resolve()
    if len(set(source_paths)) != 3 or any(not p.is_file() for p in source_paths):
        raise ValueError("Input V10.3 mancanti o duplicati.")
    if (
        dest.exists() or dest == root or root in dest.parents
        or any(dest == p.parent or p.parent in dest.parents for p in source_paths)
    ):
        raise ValueError("L'output deve essere nuovo e separato dalle fonti.")
    raw_inputs = [p.read_bytes() for p in source_paths]
    if [sha(b) for b in raw_inputs] != [
        COMPLETE_SHA256, V102_SHA256, V102_REPORT_SHA256
    ]:
        raise ValueError("Hash di Completa/V10.2/report differenti dall'audit.")
    complete, _, _ = _decode(raw_inputs[0])
    old, newline, bom = _decode(raw_inputs[1])
    previous = json.loads(raw_inputs[2].decode("utf-8-sig"))
    if (
        previous.get("version") != "scientific-clarifications-v10.2-review-only"
        or previous.get("publication_allowed") is not False
        or previous.get("scientifically_verified") is not False
        or previous.get("complete_sha256") != COMPLETE_SHA256
        or previous.get("candidate_sha256") != V102_SHA256
        or previous.get("unchanged_other_chapters") != 62
        or previous.get("after_audit") != "WARN"
    ):
        raise ValueError("Il report V10.2 non autorizza la generazione.")
    manifest, _ = _validated_manifest(scan, complete)
    if manifest["fingerprint"] != previous.get("source_fingerprint"):
        raise ValueError("Fingerprint delle dispense diverso da quello atteso.")
    evidence = validate_original_pdfs(root, source_archive)
    original_spans, full_spans = _spans(old), _spans(complete)
    if not _ordered_title_match(list(full_spans), list(original_spans))[0]:
        raise ValueError("L'indice V10.2 non corrisponde alla Completa.")
    proposed, changes = apply_v103(old)
    new_spans = _spans(proposed)
    reverted = _replace(proposed, new_spans, {
        title: old[slice(*original_spans[title])] for title in TITLES
    })
    if _encode(reverted, newline, bom) != raw_inputs[1]:
        raise ValueError("Contenuto esterno ai tre capitoli alterato.")
    pre = audit_fast_essential(complete, old)
    post = audit_fast_essential(complete, proposed)
    if post.status == "FAIL" and pre.status != "FAIL":
        missing = sorted(
            set(post.missing_exam_number_chapters)
            - set(pre.missing_exam_number_chapters)
        )
        raise ValueError(f"Nuovo FAIL dell'audit: {', '.join(missing) or 'contenuti'}")
    if (
        _fingerprint(_snapshot(scan)) != manifest["fingerprint"]
        or any(
            p.read_bytes() != original
            for p, original in zip(source_paths, raw_inputs, strict=True)
        )
    ):
        raise ValueError("Dispense o input cambiati durante il test.")
    # Recheck the chosen source mode immediately before staging any output.
    validate_original_pdfs(root, source_archive)
    metrics = []
    for title in TITLES:
        reference = complete[slice(*full_spans[title])]
        candidate_section = proposed[slice(*new_spans[title])]
        source_words = len(reference.split())
        result_words = len(candidate_section.split())
        metrics.append({
            "title": title,
            "complete_words": source_words,
            "candidate_words": result_words,
            "reduction_percent": round(100 * (1 - result_words / source_words), 2),
            "hard_compression_cap": False,
        })
    new_bytes = _encode(proposed, newline, bom)
    report = {
        "version": VERSION,
        "status": "SOURCE_GROUNDED_PILOT_UNPUBLISHED_REVIEW_REQUIRED",
        "publication_allowed": False,
        "scientifically_certified": False,
        "original_pdfs_sha256_verified": True,
        "pdf_page_anchors_verified": True,
        "full_semantic_equivalence_verified": False,
        "required_human_review": True,
        "complete_sha256": sha(raw_inputs[0]),
        "v102_candidate_sha256": sha(raw_inputs[1]),
        "v102_report_sha256": sha(raw_inputs[2]),
        "candidate_sha256": sha(new_bytes),
        "source_fingerprint": manifest["fingerprint"],
        "unchanged_other_chapters": len(original_spans) - len(TITLES),
        "before_audit": pre.status,
        "after_audit": post.status,
        "source_pdf_hashes": SOURCE_HASHES,
        "source_validation_mode": (
            "sha256_pinned_zip" if source_archive is not None else "course_folder"
        ),
        "source_archive_sha256": (
            SOURCE_ARCHIVE_SHA256 if source_archive is not None else None
        ),
        "changes": changes,
        "atomic_fact_matrix": evidence,
        "mandatory_fact_count": len(FACTS),
        "source_discrepancies": [
            {
                "source": f"{CHEM_PDF} p. 7",
                "source_claim": "lattosio elencato fra gli esosi",
                "status": "SOURCE_ERROR_NOT_PROPAGATED",
                "scientific_clarification": "lattosio = disaccaride galattosio + glucosio",
            },
            {
                "source": f"{CHEM_PDF} p. 7",
                "source_claim": "glucosio, fruttosio e galattosio detti tutti isomeri strutturali",
                "status": "SOURCE_ERROR_NOT_PROPAGATED",
                "scientific_clarification": "glucosio e galattosio sono epimeri al C4",
            },
            {
                "source": f"{KREBS_PDF} p. 6",
                "source_claim": "12 ATP equivalenti per acetil-CoA secondo convenzione storica",
                "status": "COURSE_SOURCE_CORRECT_HISTORICAL_CALCULATION",
                "scientific_clarification": "10 ATP equivalenti è una stima moderna esterna",
            },
        ],
        "manual_review_gate": "BLOCKED_UNTIL_HUMAN_ATOMIC_FACT_AND_PDF_REVIEW",
    }
    dossier = [
        "# BC Science V10.3 — Matrice atomica ancorata ai PDF eCampus", "",
        "**PILOT NON PUBBLICATO; NON CERTIFICATO SCIENTIFICAMENTE.**", "",
        ("Verificati gli hash di sette PDF originali. Ogni riga della matrice indica "
         "una pagina fisica e almeno un termine controllato in quel PDF. "
         "La verifica automatica è testuale, non garantisce equivalenza semantica."), "",
        (f"Audit: {pre.status} → {post.status}. "
         f"Fatti obbligatori: {len(FACTS)}. "
         f"Capitoli esterni invariati: {len(original_spans) - len(TITLES)}."),
        "",
        "## Compressione (nessun tetto obbligatorio)", "",
    ]
    for entry in metrics:
        dossier += [
            (f"- {entry['title']}: {entry['candidate_words']}/"
             f"{entry['complete_words']} parole, riduzione {entry['reduction_percent']}%."),
        ]
    dossier += ["", "## Matrice PDF → pagina → fatto atomico → testo", ""]
    for fact in evidence:
        dossier += [
            (f"- **{fact['ident']}** | {fact['pdf']} p. {fact['page']} | "
             f"fatto nel testo: `{fact['witness']}` | "
             f"fonte: {fact['provenance']} | revisione umana ancora necessaria."),
        ]
    dossier += ["", "## Modifiche esatte", ""]
    for change in changes:
        dossier += [
            f"### {change['ident']}", "",
            f"Motivo: {change['reason']}", "",
            f"Prima: {change['before']}", "",
            f"Dopo: {change['after']}", "",
        ]
    dossier += ["## Divergenze della fonte / riferimenti esterni", ""]
    for issue in report["source_discrepancies"]:
        dossier += [
            (f"- {issue['source']}: {issue['source_claim']}. "
             f"Stato: {issue['status']}. "
             f"Chiarimento: {issue['scientific_clarification']}."),
        ]
    dossier += [
        "", "## Limiti e blocchi", "",
        "- Le corrispondenze fra termini e PDF sono letterali: non validano ogni passaggio.",
        "- Controllare manualmente **ogni riga della matrice** e la sequenza del Krebs.",
        "- Non convertire le precisazioni esterne in contenuto eCampus senza etichetta.",
        "- Non imporre il 45% se perde una tappa o un esempio richiesto.",
        "- Nessuna pubblicazione, merge o promozione del riassunto.",
        "",
    ]
    dest.mkdir(parents=True, exist_ok=False)
    (dest / "essenziale-v10.3-CANDIDATA-NON-PUBBLICATA.md").write_bytes(new_bytes)
    (dest / "v10.3-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    (dest / "v10.3-dossier.md").write_text(
        "\n".join(dossier), encoding="utf-8",
    )
    diff = "".join(difflib.unified_diff(
        old.splitlines(keepends=True), proposed.splitlines(keepends=True),
        fromfile="essenziale-v10.2-CANDIDATA.md",
        tofile="essenziale-v10.3-CANDIDATA.md",
    ))
    (dest / "v10.3-vs-v10.2.diff").write_text(diff, encoding="utf-8")
    hashes = {p.name: sha(p.read_bytes()) for p in sorted(dest.iterdir())}
    (dest / "SHA256SUMS.json").write_text(
        json.dumps(hashes, indent=2), encoding="utf-8",
    )
    return dest
