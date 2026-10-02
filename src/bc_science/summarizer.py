from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from .cache import CacheDB
from .clarity import normalize_inline_headings, normalize_novice_layout, novice_audit
from .config import AppConfig
from .documents import chunk_text, extract_document
from .knowledge import classify_domain, system_prompt
from .ollama_client import OllamaClient

PROMPT_VERSION = "summary-v8-novice-first"
REFINE_PROMPT_VERSION = "refine-v8.3-integrity"
STUDY_PROMPT_VERSION = "study-v1-semantic-dedupe"


@dataclass(slots=True)
class SummaryStats:
    generated_calls: int = 0
    cache_hits: int = 0
    source_files: int = 0
    topic_groups: int = 0
    continuation_calls: int = 0
    generated_tokens: int = 0
    prompt_tokens: int = 0
    ollama_seconds: float = 0.0
    eval_seconds: float = 0.0
    quality_repairs: int = 0
    source_warnings: int = 0
    structural_fixes: int = 0
    novice_repairs: int = 0
    novice_score_total: int = 0
    novice_chapters: int = 0
    novice_layout_fixes: int = 0


@dataclass(slots=True)
class TopicGroup:
    title: str
    files: list[Path]


def _key(*parts: str) -> str:
    payload = "\x1f".join(parts).encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()


_SEMANTIC_TRAILING_NUMBER_PREFIXES = {
    "tipo",
    "fase",
    "stadio",
    "classe",
    "livello",
    "gruppo",
    "zona",
    "omega",
    "polimerasi",
    "complesso",
}


def _strip_trailing_file_variant(text: str) -> str:
    """Remove filename-only sequence markers without erasing scientific numbers."""
    value = text.strip()

    # Explicit lesson/part labels are always editorial filename markers.
    value = re.sub(
        r"(?i)\b(?:parte|lezione|capitolo)\s*(?:\d+|[ivxlcdm]+)\b",
        " ",
        value,
    )

    # Supplementary exercise files belong to the base topic.
    value = re.sub(r"(?i)\s+(?:faq|quiz)\s*$", " ", value)

    # Common export/scan artefacts seen in real eCampus filenames.
    value = re.sub(r"(?i)\s+\d+\s*pdf\s*$", " ", value)
    value = re.sub(r"(?i)\s+\d+p\s*$", " ", value)

    # Attached duplicate suffix, e.g. "esercizio2", but keep B12, CO2, D3.
    attached = re.search(r"(?i)([A-Za-zÀ-ÿ]{4,})(\d+)\s*$", value)
    if attached:
        value = value[: attached.start(2)]

    # Sequence number immediately before a closing bracket, e.g.
    # "Omeostasi (in allenamento 3)".
    bracketed = re.search(r"(?i)^(.*\S)\s+(\d+)(\s*[)\]])\s*$", value)
    if bracketed:
        prefix = bracketed.group(1).rstrip()
        words = re.findall(r"[A-Za-zÀ-ÿ]+", prefix)
        previous = words[-1].casefold() if words else ""
        if (
            previous not in _SEMANTIC_TRAILING_NUMBER_PREFIXES
            and len(previous) > 2
        ):
            value = prefix + bracketed.group(3)

    # Standalone trailing sequence number, e.g. "Fotosintesi 2".
    trailing = re.search(r"(?i)^(.*\S)\s+(\d+)\s*$", value)
    if trailing:
        prefix = trailing.group(1).rstrip()
        words = re.findall(r"[A-Za-zÀ-ÿ]+", prefix)
        previous = words[-1].casefold() if words else ""
        # Short scientific symbols such as pH 7 / B 12 are kept.
        if (
            previous not in _SEMANTIC_TRAILING_NUMBER_PREFIXES
            and len(previous) > 2
        ):
            value = prefix

    return value


def normalize_topic_stem(stem: str) -> str:
    """Collapse filename variants into one conservative human-readable topic title."""
    text = stem.replace("_", " ").strip()
    text = _strip_trailing_file_variant(text)
    text = re.sub(r"\s+([)\]])", r"\1", text)
    text = re.sub(r"([(\[])\s+", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip(" -_.,")
    return text or stem.strip()


def topic_key(title: str) -> str:
    """Return a stable comparison key for course topic titles."""
    normalized = normalize_topic_stem(title).casefold()
    normalized = re.sub(r"^(?:i|il|lo|la|gli|le)\s+", "", normalized).strip()
    return re.sub(r"\s+", " ", normalized)


def group_course_files(files: list[Path]) -> list[TopicGroup]:
    grouped: dict[str, TopicGroup] = {}
    order: list[str] = []

    for path in files:
        title = normalize_topic_stem(path.stem)
        key = topic_key(title)
        if key not in grouped:
            grouped[key] = TopicGroup(title=title, files=[])
            order.append(key)
        grouped[key].files.append(path)

    return [grouped[key] for key in order]


def _summary_system_prompt(domain: str) -> str:
    return (
        system_prompt(domain)
        + """
MODALITA RIASSUNTO SOURCE-ONLY:
- Il lettore target non ha mai studiato la materia: non dare per scontate conoscenze pregresse.
- Le dispense fornite sono l'unica fonte ammessa.
- NON aggiungere conoscenza generale, neppure in sezioni chiamate "Chiarimento".
- NON correggere scientificamente le dispense usando conoscenze esterne.
- Puoi correggere solo refusi ortografici evidenti quando il significato e inequivocabile.
- Usa "Verifica materiale" SOLO quando titolo e contenuto non corrispondono.
- "Verifica materiale" non deve mai certificare correttezza scientifica, assenza di errori o validita
  fattuale: in modalita source-only non puoi stabilirlo.
- Se titolo e contenuto non corrispondono, segnala soltanto il mismatch e riassumi comunque il
  contenuto realmente presente, senza chiedere conferma all'utente.
- Non lasciare mai una frase, una lista o una parola incompleta.
- Una frase deve esprimere preferibilmente una sola idea.
- Definisci i termini tecnici necessari al primo uso usando solo informazioni presenti nelle fonti.
- Spezza i contenuti complessi in paragrafi brevi e sottosezioni descrittive.
"""
    )


def _clean_obvious_typos(text: str) -> str:
    replacements = {
        "ricucinato": "ricaptato",
        "ricucinata": "ricaptata",
        "fissizione": "fissazione",
        "stto interventricolare": "setto interventricolare",
        "Esemplo": "Esempio",
        "cardico": "cardiaco",
        "Potenzale": "Potenziale",
        "Nel polmoni": "Nei polmoni",
        "degli RNA ribosomiale": "dell'RNA ribosomiale",
        "rilezione": "rilevazione",
        "sinapsis": "sinapsi",
        "**DOLE:**": "**DOLCE:**",
        "non hanno detriti, assoni": "non hanno dendriti, assoni",
    }
    cleaned = text
    for wrong, right in replacements.items():
        cleaned = cleaned.replace(wrong, right)
    return cleaned


def _normalize_chapter_heading(chapter: str, title: str) -> str:
    lines = [
        line
        for line in _clean_obvious_typos(chapter).strip().splitlines()
        if line.strip() != "---"
    ]
    if not lines:
        return f"## {title}"
    if lines[0].lstrip().startswith("#"):
        lines[0] = f"## {title}"
    else:
        lines.insert(0, f"## {title}")
    return "\n".join(lines).strip()


def _chunk_prompt(text: str, domain: str) -> str:
    return f"""Trasforma il seguente estratto di {domain} in appunti da esame eCampus.

OBIETTIVO:
- comprensibile anche a chi parte da zero e non ha mai studiato la materia;
- prima spiega il significato generale, poi introduci i dettagli;
- massima comprensibilita;
- nessuna perdita di concetti potenzialmente valutabili;
- elimina solo ripetizioni e frasi decorative;
- conserva definizioni, classificazioni, passaggi causali, eccezioni e numeri;
- spiega sigle e termini tecnici alla prima occorrenza;
- usa soltanto cio che e supportato dalla fonte.

FORMATO:
### In parole semplici
[spiega che cosa stiamo studiando, a cosa serve e quale idea bisogna capire per prima]
### Parole chiave
[3-8 termini realmente presenti nella fonte con definizione semplice supportata dalla fonte]
### Spiegazione semplice
### Concetti da ricordare
### Definizioni e termini
### Sequenze / meccanismi
### Attenzione all'esame

FONTE:
{text}
"""


def _topic_prompt(title: str, source_text: str, domain: str) -> str:
    return f"""Crea il capitolo di studio definitivo sull'argomento "{title}" ({domain}).

Devi fondere TUTTE le dispense riportate sotto in un solo testo coerente.

PRIORITA ASSOLUTE:
1. Deve essere comprensibile a una persona che non ha MAI studiato la materia.
2. Prima costruisci il quadro mentale di base, poi aggiungi dettagli e terminologia.
3. Ogni termine tecnico indispensabile va spiegato al primo uso con parole comuni, usando solo la fonte.
4. Completo rispetto alle dispense: non eliminare fatti diversi solo per accorciare.
5. Rimuovi duplicati tra lezioni 1/2/3/4 e ripetizioni dello stesso concetto.
6. Mantieni terminologia, numeri, classificazioni, definizioni, eccezioni e sequenze.
7. Per processi fisiologici usa sequenze numerate causa -> effetto.
8. Preferisci frasi brevi, una idea per frase e paragrafi di massimo 5 frasi quando possibile.
9. Non aggiungere conoscenze esterne e non correggere silenziosamente le dispense.
10. Non citare i nomi dei file nel corpo del capitolo.
11. Non inventare "domande ufficiali eCampus".
12. NON creare sezioni "Chiarimento" basate su conoscenza generale.
13. Se titolo e contenuto non corrispondono, usa una breve nota "Verifica materiale" e poi
    riassumi il contenuto effettivamente presente: non chiedere conferma all'utente.
14. Termina sempre il capitolo con una frase completa.

STRUTTURA:
## {title}
### In parole semplici
3-5 frasi che spiegano che cosa e l'argomento, perché conta nel corso e qual e l'idea di base.
Niente termini tecnici non spiegati.
### Parole chiave
3-8 punti nel formato "**Termine** — spiegazione semplice", solo se la definizione e supportata dalle dispense.
Poi usa sottosezioni descrittive, paragrafi brevi e liste. Introduci i dettagli in ordine progressivo.
Chiudi con:
### Da ricordare per l'esame
con i punti davvero essenziali, senza ripetere tutto il capitolo.

DISPENSE DELL'ARGOMENTO:
{source_text}
"""


def _topic_merge_prompt(title: str, notes: str, domain: str) -> str:
    return f"""Fondi gli appunti parziali seguenti nel capitolo definitivo "{title}" ({domain}).

REGOLE:
- scrivi per una persona che parte da zero;
- apri con "### In parole semplici" e poi "### Parole chiave";
- definisci il lessico tecnico indispensabile usando soltanto gli appunti;
- usa frasi preferibilmente brevi, una idea per frase e paragrafi brevi;
- conserva ogni informazione distinta utile all'esame;
- elimina solo duplicati;
- correggi refusi evidenti di forma senza cambiare il significato;
- usa italiano semplice e terminologia scientifica corretta rispetto alle fonti;
- se due appunti sono in tensione, non inventare una riconciliazione: esponi entrambe
  le formulazioni in modo chiaro;
- mantieni numeri, definizioni, classificazioni, sequenze ed eccezioni;
- niente conoscenze esterne;
- non creare sezioni "Chiarimento" esterne alle fonti;
- termina sempre con una frase completa.

FORMATO:
## {title}
### In parole semplici
[quadro mentale di base per chi parte da zero]
### Parole chiave
[3-8 termini con definizione semplice supportata dagli appunti]
[testo organizzato in ordine progressivo, con paragrafi brevi]
### Da ricordare per l'esame
[5-12 punti, in base alla quantita di contenuto]

APPUNTI PARZIALI:
{notes}
"""


def _dedupe_exact_blocks(text: str) -> str:
    """Remove exact repeated Markdown blocks while preserving first occurrence order."""
    blocks = re.split(r"\n\s*\n", text.strip())
    seen: set[str] = set()
    cleaned: list[str] = []
    for block in blocks:
        normalized = re.sub(r"\s+", " ", block).strip().casefold()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(block.strip())
    return "\n\n".join(cleaned)


def _extract_summary_chapters(text: str) -> list[tuple[str, str]]:
    """Read chapter titles from the deterministic index and pair them with body blocks."""
    index_match = re.search(
        r"(?ms)^## Indice degli argomenti\s*$\n(?P<items>.*?)(?:\n---\n|\Z)",
        text,
    )
    if not index_match:
        return []

    titles = [
        match.group(1).strip()
        for match in re.finditer(r"(?m)^-\s+(.+?)\s*$", index_match.group("items"))
    ]
    body = text[index_match.end():]
    blocks = [part.strip() for part in re.split(r"\n---\n", body) if part.strip()]

    chapters: list[tuple[str, str]] = []
    used: set[int] = set()
    for title in titles:
        heading_re = re.compile(rf"(?mi)^##\s+{re.escape(title)}\s*$")
        selected_index = None
        for index, block in enumerate(blocks):
            if index in used:
                continue
            if heading_re.search(block):
                selected_index = index
                break
        if selected_index is None:
            continue
        used.add(selected_index)
        chapters.append((title, blocks[selected_index]))
    return chapters



_STUDY_TITLE_REPLACEMENTS = {
    "cavitò": "cavità",
    "introduzone": "introduzione",
    "mebrane": "membrane",
    "celulla": "cellula",
    "nuucleo": "nucleo",
}

_STUDY_STOPWORDS = {
    "anche",
    "come",
    "dalla",
    "delle",
    "degli",
    "della",
    "dello",
    "dell",
    "sono",
    "viene",
    "questo",
    "questa",
    "quello",
    "quella",
    "nella",
    "nelle",
    "negli",
    "alla",
    "alle",
    "agli",
    "attraverso",
    "durante",
    "quando",
    "perché",
    "perche",
    "dopo",
    "prima",
    "ogni",
    "tutti",
    "tutte",
}


def _clean_study_title(title: str) -> str:
    """Normalize obvious editorial noise only in the study-layer title."""
    value = re.sub(r"\s+", " ", title).strip()
    for wrong, right in _STUDY_TITLE_REPLACEMENTS.items():
        value = re.sub(rf"(?i)\b{re.escape(wrong)}\b", right, value)

    # Real corpus example: "Dispendio energeticoDispendio energetico".
    repeated = re.match(r"(?is)^(.{6,}?)\1$", value)
    if repeated:
        value = repeated.group(1).strip()

    return re.sub(r"\s+", " ", value).strip(" -_.,") or title.strip()


def _study_token_set(chapter: str) -> set[str]:
    body = re.sub(r"(?m)^#{1,6}\s+.*$", " ", chapter)
    body = re.sub(
        r"(?i)\s*\((?:fonte|fonti)\s*:[^)\n]{1,320}\)",
        " ",
        body,
    )
    return {
        token
        for token in re.findall(r"[A-Za-zÀ-ÿ]{4,}", body.casefold())
        if token not in _STUDY_STOPWORDS
    }


def _study_content_overlap(left: str, right: str) -> float:
    left_tokens = _study_token_set(left)
    right_tokens = _study_token_set(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _study_chapters_should_merge(
    left_title: str,
    left_chapter: str,
    right_title: str,
    right_chapter: str,
) -> bool:
    """Conservatively merge near-duplicate chapters only when content also overlaps."""
    left_key = topic_key(_clean_study_title(left_title))
    right_key = topic_key(_clean_study_title(right_title))
    if left_key == right_key:
        return True

    overlap = _study_content_overlap(left_chapter, right_chapter)

    # A trailing number is treated as an export/lesson variant only when a matching
    # base chapter exists and the actual chapter content substantially overlaps.
    left_base = re.sub(r"\s+\d+$", "", left_key).strip()
    right_base = re.sub(r"\s+\d+$", "", right_key).strip()
    if left_base == right_base and left_key != right_key:
        return overlap >= 0.30

    title_similarity = SequenceMatcher(None, left_key, right_key).ratio()
    return title_similarity >= 0.94 and overlap >= 0.45


def _chapter_without_h2(chapter: str) -> str:
    lines = chapter.strip().splitlines()
    if lines and re.match(r"^##\s+", lines[0]):
        lines = lines[1:]
    return "\n".join(lines).strip()


def _prepare_study_source(text: str, *, title: str) -> str:
    """Build a deduplicated source document before the model performs study compression."""
    chapters = _extract_summary_chapters(text)
    if not chapters:
        raise ValueError(
            "Il riassunto completo non contiene un indice BC Science valido."
        )

    groups: list[dict[str, object]] = []
    for chapter_title, chapter in chapters:
        cleaned_title = _clean_study_title(chapter_title)
        target: dict[str, object] | None = None
        for candidate in groups:
            candidate_title = str(candidate["title"])
            candidate_chapters = candidate["chapters"]
            if not isinstance(candidate_chapters, list) or not candidate_chapters:
                continue
            first_chapter = str(candidate_chapters[0])
            if _study_chapters_should_merge(
                candidate_title,
                first_chapter,
                cleaned_title,
                chapter,
            ):
                target = candidate
                break

        if target is None:
            groups.append({"title": cleaned_title, "chapters": [chapter]})
            continue

        target_chapters = target["chapters"]
        if isinstance(target_chapters, list):
            target_chapters.append(chapter)

        # Prefer the cleaner/shorter title when two variants represent the same topic.
        current_title = str(target["title"])
        if len(cleaned_title) < len(current_title):
            target["title"] = cleaned_title

    records: list[tuple[str, str]] = []
    for group in groups:
        chapter_title = str(group["title"])
        raw_chapters = group["chapters"]
        if not isinstance(raw_chapters, list):
            continue
        bodies = [
            _chapter_without_h2(str(chapter))
            for chapter in raw_chapters
            if str(chapter).strip()
        ]
        merged_body = _dedupe_exact_blocks("\n\n".join(bodies))
        records.append(
            (
                chapter_title,
                f"## {chapter_title}\n\n{merged_body}".strip(),
            )
        )

    toc = "## Indice degli argomenti\n" + "\n".join(
        f"- {chapter_title}" for chapter_title, _chapter in records
    )
    separator = "\n\n---\n\n"
    return (
        f"# {title}\n\n"
        + toc
        + separator
        + separator.join(chapter for _chapter_title, chapter in records)
        + "\n"
    )


def _strip_study_citations(text: str) -> str:
    """Hide source-page noise in the study copy while the complete copy keeps traceability."""
    cleaned = re.sub(
        r"(?i)\s*\((?:fonte|fonti)\s*:[^)\n]{1,320}\)",
        "",
        text,
    )
    cleaned = re.sub(
        r"(?i)\s*\[(?:fonte|fonti)\s*:[^\]\n]{1,320}\]",
        "",
        cleaned,
    )
    cleaned = re.sub(r"[ \t]+([,.;:])", r"\1", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned


def _chapter_title_supported(title: str, chapter: str) -> bool:
    body = re.sub(r"(?m)^#{1,6}\s+.*$", "", chapter).casefold()
    tokens = [
        token
        for token in re.findall(r"[A-Za-zÀ-ÿ]+", title.casefold())
        if len(token) >= 5
    ]
    return not tokens or any(token in body for token in tokens)


def _chapter_quality_issues(
    chapter: str,
    title: str,
    *,
    require_source_warning: bool = False,
) -> list[str]:
    issues: list[str] = []
    h2_lines = re.findall(r"(?m)^##\s+(.+?)\s*$", chapter)
    if len(h2_lines) != 1 or h2_lines[0].strip().casefold() != title.strip().casefold():
        issues.append("deve contenere esattamente un titolo H2 corretto")

    exam_sections = len(
        re.findall(r"(?mi)^###\s+Da ricordare per l['’]esame\s*$", chapter)
    )
    if exam_sections != 1:
        issues.append("deve contenere una sola sezione Da ricordare per l'esame")

    h3_lines = [
        heading.strip().casefold()
        for heading in re.findall(r"(?m)^###\s+(.+?)\s*$", chapter)
    ]
    duplicate_h3 = [name for name, count in Counter(h3_lines).items() if count > 1]
    if duplicate_h3:
        issues.append("contiene sottosezioni H3 duplicate")

    has_verification = "Verifica materiale" in chapter
    if require_source_warning and not has_verification:
        issues.append("manca la nota obbligatoria Verifica materiale")
    if not require_source_warning and has_verification:
        issues.append("contiene una sezione Verifica materiale non richiesta")

    unsupported_validation = re.search(
        r"(?i)(scientificamente corrett|corrett[oa] scientificamente|"
        r"nessun errore fattuale|senza incongruenze evidenti|non richiede verifiche)",
        chapter,
    )
    if unsupported_validation:
        issues.append("contiene una certificazione scientifica non ammessa in modalita source-only")

    if re.search(r"(?m)^.+[ \t]+#{3,6}\s+\S+", chapter):
        issues.append("contiene un heading Markdown incollato alla fine di una frase")

    forbidden_placeholders = (
        "[spiegazione ordinata e semplice]",
        "[un'unica sezione finale",
        "Massimo 5 punti.",
    )
    if any(item.casefold() in chapter.casefold() for item in forbidden_placeholders):
        issues.append("contiene testo-placeholder del prompt")

    last_line = next(
        (line.strip() for line in reversed(chapter.splitlines()) if line.strip()),
        "",
    )
    if (
        not last_line
        or last_line.endswith(("/", "->", "→", ":", ";", ","))
        or last_line[-1:] not in ".!?)]"
    ):
        issues.append("il capitolo sembra terminare con una frase incompleta")

    if len(chapter.strip()) < 500:
        issues.append("capitolo anormalmente corto")
    return issues


def _normalize_refined_structure(chapter: str, title: str) -> tuple[str, bool]:
    """Repair Markdown-only structure without changing the chapter's factual content."""
    normalized = _normalize_chapter_heading(chapter, title)
    normalized, heading_fixes = normalize_inline_headings(normalized)
    changed = heading_fixes > 0
    lines = normalized.splitlines()

    # Only the first line may be an H2. Preserve any later section title by demoting it.
    cleaned: list[str] = []
    for index, line in enumerate(lines):
        h2 = re.match(r"^##\s+(.+?)\s*$", line)
        if index > 0 and h2:
            heading = h2.group(1).strip()
            if heading.casefold() == title.strip().casefold():
                changed = True
                continue
            cleaned.append(f"### {heading}")
            changed = True
            continue
        cleaned.append(line)

    # Normalize common variants of the exam-recap heading.
    exam_heading_re = re.compile(
        r"(?i)^###\s+Da\s+ricordare(?:\s+per)?(?:\s+l['’]?)?esame\s*$"
    )
    exam_indices: list[int] = []
    for index, line in enumerate(cleaned):
        if exam_heading_re.match(line.strip()):
            if line.strip() != "### Da ricordare per l'esame":
                cleaned[index] = "### Da ricordare per l'esame"
                changed = True
            exam_indices.append(index)

    # If the model repeated the recap section, preserve earlier content as "Punti chiave".
    if len(exam_indices) > 1:
        for index in exam_indices[:-1]:
            cleaned[index] = "### Punti chiave"
        changed = True

    # Parse H3 sections so repeated section headings can be merged deterministically.
    first_h3 = next(
        (index for index, line in enumerate(cleaned) if line.startswith("### ")),
        None,
    )
    if first_h3 is None:
        return "\n".join(cleaned).strip(), changed

    prefix = cleaned[:first_h3]
    sections: list[tuple[str, list[str]]] = []
    current_heading: str | None = None
    current_body: list[str] = []

    for line in cleaned[first_h3:]:
        if line.startswith("### "):
            if current_heading is not None:
                sections.append((current_heading, current_body))
            current_heading = line[4:].strip()
            current_body = []
        else:
            current_body.append(line)
    if current_heading is not None:
        sections.append((current_heading, current_body))

    merged_order: list[str] = []
    merged_titles: dict[str, str] = {}
    merged_bodies: dict[str, list[str]] = {}
    for heading, body in sections:
        key = heading.casefold()
        if key not in merged_bodies:
            merged_order.append(key)
            merged_titles[key] = heading
            merged_bodies[key] = list(body)
        else:
            if merged_bodies[key] and body:
                merged_bodies[key].append("")
            merged_bodies[key].extend(body)
            changed = True

    exam_key = "da ricordare per l'esame"
    if exam_key in merged_order and merged_order[-1] != exam_key:
        merged_order.remove(exam_key)
        merged_order.append(exam_key)
        changed = True

    rebuilt = list(prefix)
    for key in merged_order:
        body_text = "\n".join(merged_bodies[key]).strip()
        if body_text:
            body_text = _dedupe_exact_blocks(body_text)
        rebuilt.append(f"### {merged_titles[key]}")
        if body_text:
            rebuilt.extend(["", *body_text.splitlines()])

    return "\n".join(rebuilt).strip(), changed


def _extract_exam_recap(chapter: str, limit: int = 650) -> str:
    match = re.search(
        r"(?mis)^###\s+Da ricordare per l['’]esame\s*$\n(?P<body>.*)$",
        chapter,
    )
    if not match:
        return ""
    body = re.sub(r"\s+", " ", match.group("body")).strip()
    if len(body) <= limit:
        return body
    return body[:limit].rsplit(" ", 1)[0] + "…"


def _refine_chapter_prompt(title: str, chapter: str, source_warning: bool = False) -> str:
    warning = ""
    if source_warning:
        warning = """
NOTA OBBLIGATORIA:
Subito dopo il titolo inserisci:
> Verifica materiale: il titolo del capitolo non e supportato chiaramente dal testo sorgente.
> Il contenuto seguente riassume soltanto cio che e realmente presente nel riassunto di partenza.
"""

    return f"""Riscrivi questo capitolo gia riassunto di BC Science.

OBIETTIVO:
- deve essere comprensibile anche a chi non ha mai studiato la materia;
- costruisci prima il quadro mentale di base e poi i dettagli;
- renderlo molto semplice da capire;
- conservare TUTTE le informazioni distinte presenti;
- eliminare ripetizioni e blocchi duplicati;
- correggere frasi spezzate, heading incollati nel testo e refusi evidenti;
- mantenere numeri, definizioni, classificazioni, sequenze, eccezioni e contenuti utili all'esame;
- NON aggiungere conoscenze che non siano gia presenti nel capitolo;
- NON correggere scientificamente il contenuto usando conoscenza esterna;
- non ripetere il titolo dentro il capitolo;
- non ripetere piu volte "Da ricordare per l'esame";
- NON creare "Verifica materiale" se non e richiesta esplicitamente sopra;
- NON dichiarare mai che il materiale e scientificamente corretto, privo di errori o verificato;
- ogni heading Markdown deve iniziare su una nuova riga, mai alla fine di una frase;
- preferisci una idea per frase e paragrafi di massimo 5 frasi quando possibile;
- termina con una frase completa.
{warning}
VINCOLI STRUTTURALI:
- esattamente UN heading H2: "## {title}", solo come prima riga;
- subito dopo eventuale "Verifica materiale", inserisci "### In parole semplici";
- inserisci "### Concetti chiave" con definizioni o relazioni essenziali supportate dal capitolo;
- inserisci "### Spiegazione ordinata" e concentra qui tutti i dettagli distinti, una sola volta;
- nessun altro heading H2 nel capitolo;
- esattamente UNA sezione finale "### Da ricordare per l'esame";
- dopo "Da ricordare per l'esame" usa punti brevi, senza riscrivere tutto il capitolo.

CAPITOLO DA RIFINIRE:
{chapter}
"""


def _novice_repair_prompt(
    title: str,
    chapter: str,
    issues: list[str],
    source_warning: bool,
) -> str:
    issue_text = "\n".join(f"- {item}" for item in issues)
    warning = ""
    if source_warning:
        warning = (
            "\nMantieni la nota 'Verifica materiale' gia presente e non inventare "
            "il contenuto mancante.\n"
        )
    return f"""Riscrivi il capitolo seguente per un LETTORE PRINCIPIANTE ASSOLUTO.

VINCOLO FONDAMENTALE:
Il capitolo qui sotto e l'UNICA fonte. Non aggiungere fatti, esempi o spiegazioni
che non siano ricavabili dal suo contenuto. Non correggere scientificamente la fonte.

PROBLEMI DI COMPRENSIBILITA DA RISOLVERE:
{issue_text}

OBIETTIVI:
- una persona che non ha mai studiato la materia deve capire il filo logico alla prima lettura;
- ogni frase dovrebbe contenere una sola idea quando possibile;
- usa parole comuni prima del termine tecnico;
- quando serve un termine tecnico, definiscilo al primo uso;
- spezza paragrafi lunghi in blocchi piu piccoli;
- conserva TUTTE le informazioni distinte, i numeri, le definizioni, le eccezioni e le sequenze;
- non semplificare eliminando contenuti utili all'esame;
- non introdurre analogie o esempi non presenti nel capitolo;
- non creare "Verifica materiale" se non e richiesta dal warning;
- non dichiarare che la fonte e scientificamente corretta, verificata o priva di errori;
- ogni heading Markdown deve iniziare su una nuova riga.
{warning}
STRUTTURA OBBLIGATORIA:
## {title}
### In parole semplici
3-5 frasi introduttive, senza conoscenze pregresse richieste.
### Concetti chiave
[termini, relazioni e definizioni davvero necessarie, supportate dal capitolo]
### Spiegazione ordinata
[spiega tutti i contenuti distinti in ordine logico, senza duplicazioni]
### Da ricordare per l'esame
[una sola sezione finale]

CAPITOLO SORGENTE:
{chapter}
"""


def _repair_refined_chapter_prompt(
    title: str,
    chapter: str,
    issues: list[str],
    source_warning: bool,
) -> str:
    issue_text = "\n".join(f"- {item}" for item in issues)
    return (
        _refine_chapter_prompt(title, chapter, source_warning)
        + f"""

La precedente generazione non ha superato il controllo qualita per questi motivi:
{issue_text}

Rigenera l'intero capitolo da zero rispettando rigorosamente tutti i vincoli.
"""
    )


def _extract_exam_points(chapter: str) -> list[str]:
    match = re.search(
        r"(?mis)^###\s+Da ricordare per l['’]esame\s*$\n(?P<body>.*)$",
        chapter,
    )
    if not match:
        return []

    body = match.group("body").strip()
    points: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        bullet = re.match(r"^(?:[-*]|\d+[.)])\s+(.*)$", stripped)
        if bullet:
            value = re.sub(r"\s+", " ", bullet.group(1)).strip()
            if value:
                points.append(value)

    if points:
        return points

    fallback = re.sub(r"\s+", " ", body).strip()
    return [fallback] if fallback else []


def _build_global_recap(refined: list[tuple[str, str]]) -> str:
    """Build one high-yield recap item per chapter without another model call."""
    lines = ["## Ripasso globale"]
    for index, (title, chapter) in enumerate(refined, start=1):
        points = _extract_exam_points(chapter)
        if not points:
            simple = re.search(
                r"(?mis)^###\s+In parole semplici\s*$\n(?P<body>.*?)(?=^###\s+|\Z)",
                chapter,
            )
            fallback = (
                re.sub(r"\s+", " ", simple.group("body")).strip()
                if simple
                else ""
            )
            if not fallback:
                fallback = re.sub(r"(?m)^#{1,6}\s+.*$", "", chapter)
                fallback = re.sub(r"\s+", " ", fallback).strip()
            points = [fallback[:600].rsplit(" ", 1)[0] + "…" if len(fallback) > 600 else fallback]

        selected: list[str] = []
        total = 0
        for point in points[:3]:
            plain = re.sub(r"\s+", " ", point).strip()
            if not plain:
                continue
            if len(plain) > 220:
                plain = plain[:220].rsplit(" ", 1)[0] + "…"
            projected = total + len(plain)
            if selected and projected > 320:
                break
            selected.append(plain)
            total = projected

        recap = " ".join(selected) if selected else points[0]
        display_title = (
            f"{title} (titolo da verificare)"
            if "Verifica materiale" in chapter
            else title
        )
        lines.append(f"{index}. **{display_title}**: {recap}")
    return "\n".join(lines)


def _incremental_route_prompt(
    source_title: str,
    new_chapter: str,
    existing_titles: list[str],
) -> str:
    choices = "\n".join(f"- {title}" for title in existing_titles)
    return f"""Decidi dove appartiene il NUOVO materiale in un riassunto universitario.

Puoi rispondere in UNO SOLO di questi modi:
MATCH: <titolo esatto di un capitolo esistente>
NEW

Usa MATCH solo se il contenuto tratta chiaramente lo stesso argomento del capitolo.
Se il collegamento e incerto, rispondi NEW.
Non aggiungere spiegazioni.

NOME DEL NUOVO MATERIALE:
{source_title}

CAPITOLI ESISTENTI:
{choices}

NUOVO MATERIALE RIASSUNTO:
{new_chapter}
"""


def _parse_incremental_route(response: str, existing_titles: list[str]) -> str | None:
    value = response.strip()
    if value.casefold() == "new":
        return None

    match = re.fullmatch(r"(?i)MATCH:\s*(.+?)\s*", value)
    if not match:
        raise ValueError("routing incrementale non interpretabile")

    requested = match.group(1).strip().casefold()
    for title in existing_titles:
        if title.casefold() == requested:
            return title
    raise ValueError("routing incrementale verso un capitolo inesistente")


def _incremental_merge_prompt(
    title: str,
    existing_chapter: str,
    new_chapter: str,
) -> str:
    return f"""Aggiorna il capitolo "{title}" usando ESCLUSIVAMENTE due fonti:
1. il capitolo BC Science gia esistente;
2. il nuovo materiale appena aggiunto.

OBIETTIVO:
- conserva TUTTE le informazioni distinte gia presenti nel capitolo esistente;
- integra soltanto le informazioni nuove supportate dal nuovo materiale;
- elimina soltanto duplicazioni reali;
- non aggiungere conoscenza esterna;
- non correggere scientificamente le fonti in modo silenzioso;
- mantieni numeri, definizioni, classificazioni, sequenze ed eccezioni;
- scrivi per una persona che parte da zero;
- non citare nomi di file;
- termina con una frase completa.

STRUTTURA OBBLIGATORIA:
## {title}
### In parole semplici
[quadro mentale aggiornato]
### Parole chiave
[solo se utili e supportate]
[testo completo integrato in sottosezioni chiare]
### Da ricordare per l'esame
[punti essenziali che includano anche quelli gia validi prima dell'aggiornamento]

CAPITOLO ESISTENTE:
{existing_chapter}

NUOVO MATERIALE:
{new_chapter}
"""


def _incremental_merge_repair_prompt(
    title: str,
    existing_chapter: str,
    new_chapter: str,
    candidate: str,
) -> str:
    return f"""Correggi il candidato di aggiornamento del capitolo "{title}".

Il candidato ha perso informazioni del capitolo precedente o non rispetta la struttura.
Rigeneralo integralmente usando SOLO il capitolo esistente e il nuovo materiale.

REGOLE NON NEGOZIABILI:
- nessuna informazione distinta del capitolo esistente puo essere eliminata;
- aggiungi le nuove informazioni senza duplicarle;
- nessuna conoscenza esterna;
- un solo H2 "## {title}";
- "### In parole semplici" all'inizio delle sottosezioni;
- una sola "### Da ricordare per l'esame" alla fine;
- frase finale completa.

CAPITOLO ESISTENTE:
{existing_chapter}

NUOVO MATERIALE:
{new_chapter}

CANDIDATO DA CORREGGERE:
{candidate}
"""


def _preserves_existing_exam_points(existing: str, candidate: str) -> bool:
    """Detect catastrophic information loss during an incremental chapter merge."""
    points = _extract_exam_points(existing)
    if not points:
        return len(candidate) >= max(200, int(len(existing) * 0.65))

    candidate_tokens = {
        token
        for token in re.findall(r"[A-Za-zÀ-ÿ0-9]+", candidate.casefold())
        if len(token) >= 4
    }
    for point in points:
        tokens = {
            token
            for token in re.findall(r"[A-Za-zÀ-ÿ0-9]+", point.casefold())
            if len(token) >= 4
        }
        if not tokens:
            continue
        overlap = len(tokens & candidate_tokens) / len(tokens)
        if overlap < 0.55:
            return False
    return True


def _existing_map_section(text: str) -> str:
    match = re.search(
        r"(?ms)^## Mappa della materia\s*$\n.*?(?=^## Ripasso globale\s*$|^## Indice degli argomenti\s*$)",
        text,
    )
    return match.group(0).strip() if match else ""


def _frontmatter_prefix_before_map(text: str) -> str:
    match = re.search(r"(?m)^## Mappa della materia\s*$", text)
    return text[:match.start()].rstrip() if match else ""


def _refine_map_prompt(topic_titles: list[str]) -> str:
    topics = "\n".join(f"- {item}" for item in topic_titles)
    return f"""Crea SOLO la sezione Markdown:

## Mappa della materia

Raggruppa i capitoli seguenti in 4-7 macro-aree logiche e mostra in modo molto breve
i collegamenti principali. Massimo 12 punti complessivi. Non aggiungere conoscenza
esterna e non creare altre sezioni.

Se un titolo contiene "[VERIFICA MATERIALE]", NON dedurre dal titolo che il contenuto
appartenga a quella materia: inseriscilo in una voce separata "Materiale da verificare".

CAPITOLI:
{topics}
"""


def _course_overview_prompt(topic_previews: str, title: str) -> str:
    return f"""Crea l'introduzione e il ripasso globale per un unico riassunto eCampus intitolato
"{title}".

Hai sotto soltanto l'elenco dei capitoli del corso. NON devi riscrivere i capitoli
e NON devi aggiungere contenuti esterni.

Genera soltanto queste sezioni:

# {title}

> Riassunto unico costruito dalle dispense importate. Il contenuto segue le fonti eCampus;
> le spiegazioni sono semplificate senza sostituire o correggere silenziosamente le dispense.

## Come studiare questo riassunto
Spiega in massimo 5 punti come usarlo per preparare l'esame.

## Mappa della materia
Organizza gli argomenti in un ordine di studio comprensibile e mostra i collegamenti
principali tra essi. Deve essere una guida, non un altro riassunto lungo.

## Ripasso globale
Crea 15-30 punti ad alta resa che aiutino a richiamare i concetti dell'intera materia.
Non presentare nulla come domanda ufficiale d'esame.

CAPITOLI DISPONIBILI:
{topic_previews}
"""


def _load_refine_checkpoint(
    path: Path,
    *,
    signature: str,
    expected_titles: list[str],
) -> list[tuple[str, str]]:
    """Restore the valid completed prefix from a refine checkpoint."""
    if not path.exists():
        return []

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return []

    if not isinstance(payload, dict):
        return []
    if payload.get("schema") != 1 or payload.get("signature") != signature:
        return []

    completed = payload.get("completed")
    if not isinstance(completed, list):
        return []

    restored: list[tuple[str, str]] = []
    for expected_title, item in zip(expected_titles, completed, strict=False):
        if not isinstance(item, dict) or item.get("title") != expected_title:
            break
        chapter = item.get("chapter")
        if not isinstance(chapter, str) or not chapter.strip():
            break
        restored.append((expected_title, chapter))
    return restored


def _save_refine_checkpoint(
    path: Path,
    *,
    signature: str,
    completed: list[tuple[str, str]],
) -> None:
    """Atomically persist completed refine chapters so a later run can resume."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": 1,
        "signature": signature,
        "completed": [
            {"title": chapter_title, "chapter": chapter}
            for chapter_title, chapter in completed
        ],
    }
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


class Summarizer:
    def __init__(
        self,
        config: AppConfig,
        profile: str | None = None,
        *,
        db_path: Path | None = None,
    ) -> None:
        self.config = config
        self.model = config.model_for_profile(profile)
        self.client = OllamaClient(config.ollama_url)
        self.db = CacheDB(db_path)
        self.stats = SummaryStats()

    def close(self) -> None:
        self.db.close()

    def _cached_chat(
        self,
        cache_key: str,
        system: str,
        user: str,
        *,
        num_predict: int | None = None,
    ) -> str:
        hit = self.db.get_summary(cache_key)
        if hit:
            self.stats.cache_hits += 1
            return hit

        initial_budget = num_predict or self.config.num_predict
        budgets = [
            initial_budget,
            min(max(initial_budget + 1000, int(initial_budget * 1.6)), 4600),
            min(max(initial_budget + 2200, int(initial_budget * 2.2)), 6800),
        ]
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        final_text = ""

        for attempt, budget in enumerate(budgets):
            # On a retry, regenerate the entire answer with a larger output budget instead
            # of appending a continuation. This avoids semantic loops and duplicated sections.
            retry_ctx = min(16384, max(self.config.num_ctx, budget + 6000))
            result = self.client.chat_stream(
                self.model,
                messages,
                on_token=None,
                num_ctx=retry_ctx,
                num_predict=budget,
                keep_alive=self.config.keep_alive,
            )
            self.stats.generated_calls += 1
            self.stats.generated_tokens += result.eval_count
            self.stats.prompt_tokens += result.prompt_eval_count
            self.stats.ollama_seconds += result.total_seconds
            self.stats.eval_seconds += result.eval_duration / 1_000_000_000
            final_text = result.content

            if result.done_reason != "length":
                break

            if attempt < len(budgets) - 1:
                self.stats.continuation_calls += 1

        merged = _clean_obvious_typos(final_text.strip())
        self.db.set_summary(cache_key, merged)
        return merged

    def summarize_text(self, text: str, title: str = "Riassunto") -> str:
        _, domain = classify_domain(text)
        system = _summary_system_prompt(domain)
        chunks = chunk_text(
            text,
            chunk_chars=self.config.chunk_chars,
            overlap=self.config.chunk_overlap,
        )
        if not chunks:
            return ""

        notes: list[str] = []
        for chunk in chunks:
            cache_key = _key(PROMPT_VERSION, self.model, "chunk", domain, chunk)
            notes.append(
                self._cached_chat(
                    cache_key,
                    system,
                    _chunk_prompt(chunk, domain),
                )
            )

        round_number = 0
        while sum(map(len, notes)) > 26000 and len(notes) > 1:
            reduced: list[str] = []
            for start in range(0, len(notes), 4):
                group = "\n\n".join(notes[start:start + 4])
                prompt = (
                    "Fondi questi appunti in un unico blocco completo. Rimuovi solo duplicati, "
                    "non perdere definizioni, meccanismi, eccezioni o numeri.\n\n" + group
                )
                cache_key = _key(
                    PROMPT_VERSION,
                    self.model,
                    f"reduce-{round_number}",
                    domain,
                    group,
                )
                reduced.append(self._cached_chat(cache_key, system, prompt))
            notes = reduced
            round_number += 1

        merged = "\n\n".join(notes)
        final_key = _key(PROMPT_VERSION, self.model, "legacy-final", domain, title, merged)
        prompt = _topic_merge_prompt(title, merged, domain)
        return self._cached_chat(final_key, system, prompt, num_predict=2200)

    def _summarize_topic(
        self,
        group: TopicGroup,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> str:
        source_parts: list[str] = []
        raw_texts: list[str] = []
        for path in group.files:
            extracted = extract_document(path)
            if extracted.text:
                raw_texts.append(extracted.text)
                source_parts.append(
                    f"\n--- DISPENSA: {path.stem} ---\n{extracted.text}"
                )

        source_text = "\n".join(source_parts).strip()
        source_body = "\n".join(raw_texts).strip()
        if not source_text:
            return ""

        topic_tokens = [
            token
            for token in re.findall(r"[A-Za-zÀ-ÿ]+", group.title.casefold())
            if len(token) >= 5
        ]
        title_supported = (
            not topic_tokens
            or any(token in source_body.casefold() for token in topic_tokens)
        )
        mismatch_note = ""
        if not title_supported:
            mismatch_note = (
                "\n\nNOTA OBBLIGATORIA DA INSERIRE SUBITO DOPO IL TITOLO:\n"
                "> Verifica materiale: il titolo dei file suggerisce questo argomento, "
                "ma il termine chiave non compare nel testo estratto. Il capitolo seguente "
                "riassume soltanto il contenuto realmente presente nelle dispense.\n"
            )

        _, domain = classify_domain(source_text)
        system = _summary_system_prompt(domain)

        # Topic bundles are intentionally larger than ordinary RAG chunks:
        # repeated lesson variants are summarized together before any global synthesis.
        chunks = chunk_text(source_text, chunk_chars=12000, overlap=350)

        if len(chunks) == 1:
            cache_key = _key(
                PROMPT_VERSION,
                self.model,
                "topic-direct",
                group.title,
                domain,
                chunks[0],
            )
            return self._cached_chat(
                cache_key,
                system,
                _topic_prompt(group.title, chunks[0] + mismatch_note, domain),
                num_predict=1800,
            )

        partials: list[str] = []
        for index, chunk in enumerate(chunks, start=1):
            if progress:
                progress(
                    f"    blocco {index}/{len(chunks)} di {group.title}"
                )
            cache_key = _key(
                PROMPT_VERSION,
                self.model,
                "topic-part",
                group.title,
                domain,
                chunk,
            )
            partials.append(
                self._cached_chat(
                    cache_key,
                    system,
                    _chunk_prompt(chunk, domain),
                    num_predict=1300,
                )
            )

        notes = "\n\n".join(partials)
        final_key = _key(
            PROMPT_VERSION,
            self.model,
            "topic-merge",
            group.title,
            domain,
            notes,
        )
        return self._cached_chat(
            final_key,
            system,
            _topic_merge_prompt(group.title, notes + mismatch_note, domain),
            num_predict=2200,
        )

    def summarize_course(
        self,
        files: list[Path],
        title: str,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> str:
        groups = group_course_files(files)
        self.stats.source_files = len(files)
        self.stats.topic_groups = len(groups)

        chapter_records: list[tuple[str, str]] = []
        for index, group in enumerate(groups, start=1):
            if progress:
                progress(
                    f"[{index}/{len(groups)}] {group.title} "
                    f"({len(group.files)} {'documento' if len(group.files) == 1 else 'documenti'})"
                )
            chapter = self._summarize_topic(group, progress=progress)
            if chapter:
                chapter_records.append(
                    (group.title, _normalize_chapter_heading(chapter, group.title))
                )

        if not chapter_records:
            return ""

        # The overview receives only the deterministic topic names. Passing large chapter
        # previews here can consume the context window and truncate the overview itself.
        topic_list = "\n".join(
            f"- {index}. {topic_title}"
            for index, (topic_title, _chapter) in enumerate(chapter_records, start=1)
        )
        overview_system = _summary_system_prompt("Scienze Motorie")
        overview_key = _key(
            PROMPT_VERSION,
            self.model,
            "course-overview",
            title,
            topic_list,
        )
        overview = self._cached_chat(
            overview_key,
            overview_system,
            _course_overview_prompt(topic_list, title),
            num_predict=1400,
        ).strip()

        toc_lines = ["## Indice degli argomenti"]
        toc_lines.extend(f"- {topic_title}" for topic_title, _chapter in chapter_records)

        chapters = [chapter for _topic_title, chapter in chapter_records]
        separator = "\n\n---\n\n"
        return (
            overview
            + "\n\n"
            + "\n".join(toc_lines)
            + separator
            + separator.join(chapters)
            + "\n"
        )

    def incremental_update_course(
        self,
        existing_summary: str,
        new_files: list[Path],
        title: str,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> tuple[str, int, int, dict[str, str]]:
        """Update only chapters affected by newly added source files.

        The returned mapping records which chapter consumed each newly added source file.
        """
        chapters = _extract_summary_chapters(existing_summary)
        if not chapters:
            raise ValueError("Il riassunto esistente non contiene capitoli aggiornabili.")

        groups = group_course_files(new_files)
        if not groups:
            return existing_summary, 0, 0, {}

        records = list(chapters)
        existing_by_key = {
            topic_key(chapter_title): index
            for index, (chapter_title, _chapter) in enumerate(records)
        }
        updated_chapters = 0
        new_chapters = 0
        source_chapters: dict[str, str] = {}

        for group_index, group in enumerate(groups, start=1):
            if progress:
                progress(
                    f"[incrementale {group_index}/{len(groups)}] Analizzo {group.title} "
                    f"({len(group.files)} {'documento' if len(group.files) == 1 else 'documenti'})"
                )

            new_material = self._summarize_topic(group, progress=progress)
            if not new_material:
                raise ValueError(f"Il nuovo materiale {group.title} non produce contenuto.")

            key = topic_key(group.title)
            existing_index = existing_by_key.get(key)
            if existing_index is None:
                existing_titles = [chapter_title for chapter_title, _chapter in records]
                route_key = _key(
                    "incremental-route-v1",
                    self.model,
                    group.title,
                    "\n".join(existing_titles),
                    new_material,
                )
                route = self._cached_chat(
                    route_key,
                    _summary_system_prompt("Scienze Motorie"),
                    _incremental_route_prompt(
                        group.title,
                        new_material,
                        existing_titles,
                    ),
                    num_predict=80,
                )
                routed_title = _parse_incremental_route(route, existing_titles)
                if routed_title is not None:
                    candidate_index = next(
                        index
                        for index, (chapter_title, _chapter) in enumerate(records)
                        if chapter_title == routed_title
                    )
                    if _chapter_title_supported(routed_title, new_material):
                        existing_index = candidate_index
                        if progress:
                            progress(
                                f"Nuovo materiale associato a: {routed_title}"
                            )

            if existing_index is None:
                normalized = _normalize_chapter_heading(new_material, group.title)
                records.append((group.title, normalized))
                existing_by_key[key] = len(records) - 1
                new_chapters += 1
                for source_file in group.files:
                    source_chapters[str(source_file.resolve())] = group.title
                if progress:
                    progress(f"Nuovo capitolo: {group.title}")
                continue

            chapter_title, old_chapter = records[existing_index]
            _, domain = classify_domain(old_chapter + "\n\n" + new_material)
            system = _summary_system_prompt(domain)
            cache_key = _key(
                "incremental-course-v1",
                self.model,
                chapter_title,
                old_chapter,
                new_material,
            )
            merged = self._cached_chat(
                cache_key,
                system,
                _incremental_merge_prompt(chapter_title, old_chapter, new_material),
                num_predict=3000,
            )
            merged = _normalize_chapter_heading(
                _dedupe_exact_blocks(merged),
                chapter_title,
            )

            require_source_warning = "Verifica materiale" in old_chapter
            issues = _chapter_quality_issues(
                merged,
                chapter_title,
                require_source_warning=require_source_warning,
            )
            preserved = _preserves_existing_exam_points(old_chapter, merged)
            if issues or not preserved:
                repair_key = _key(
                    "incremental-course-repair-v1",
                    self.model,
                    chapter_title,
                    old_chapter,
                    new_material,
                    merged,
                )
                merged = self._cached_chat(
                    repair_key,
                    system,
                    _incremental_merge_repair_prompt(
                        chapter_title,
                        old_chapter,
                        new_material,
                        merged,
                    ),
                    num_predict=3400,
                )
                merged = _normalize_chapter_heading(
                    _dedupe_exact_blocks(merged),
                    chapter_title,
                )
                issues = _chapter_quality_issues(
                    merged,
                    chapter_title,
                    require_source_warning=require_source_warning,
                )
                preserved = _preserves_existing_exam_points(old_chapter, merged)

            if issues or not preserved:
                detail = "; ".join(issues) if issues else "perdita di contenuti preesistenti"
                raise ValueError(
                    f"Merge incrementale non sicuro per {chapter_title}: {detail}."
                )

            records[existing_index] = (chapter_title, merged)
            for source_file in group.files:
                source_chapters[str(source_file.resolve())] = chapter_title
            updated_chapters += 1
            if progress:
                progress(f"Capitolo aggiornato: {chapter_title}")

        prefix = _frontmatter_prefix_before_map(existing_summary)
        if not prefix:
            prefix = f"# {title}"

        if new_chapters:
            titles_for_map = [
                (
                    f"{chapter_title} [VERIFICA MATERIALE]"
                    if "Verifica materiale" in chapter
                    else chapter_title
                )
                for chapter_title, chapter in records
            ]
            map_key = _key(
                "incremental-map-v1",
                self.model,
                title,
                "\n".join(titles_for_map),
            )
            map_section = self._cached_chat(
                map_key,
                _summary_system_prompt("Scienze Motorie"),
                _refine_map_prompt(titles_for_map),
                num_predict=900,
            ).strip()
            if not map_section.startswith("## Mappa della materia"):
                map_section = _existing_map_section(existing_summary)
        else:
            map_section = _existing_map_section(existing_summary)

        if not map_section:
            map_section = "## Mappa della materia\n- Consulta l'indice degli argomenti."

        recap = _build_global_recap(records)
        toc = "## Indice degli argomenti\n" + "\n".join(
            f"- {chapter_title}" for chapter_title, _chapter in records
        )
        separator = "\n\n---\n\n"
        updated = (
            prefix
            + "\n\n"
            + map_section
            + "\n\n"
            + recap
            + "\n\n"
            + toc
            + separator
            + separator.join(chapter for _title, chapter in records)
            + "\n"
        )
        return updated, updated_chapters, new_chapters, source_chapters


    def refine_summary(
        self,
        text: str,
        title: str,
        *,
        progress: Callable[[str], None] | None = None,
        checkpoint_path: Path | None = None,
    ) -> str:
        chapters = _extract_summary_chapters(text)
        if not chapters:
            raise ValueError(
                "Il file non contiene un indice BC Science valido con capitoli rifinibili."
            )

        system = _summary_system_prompt("Scienze Motorie")
        checkpoint_signature = _key(
            "refine-checkpoint-v1",
            REFINE_PROMPT_VERSION,
            self.model,
            title,
            text,
        )
        expected_titles = [chapter_title for chapter_title, _chapter in chapters]
        refined: list[tuple[str, str]] = []
        restored_count = 0

        if checkpoint_path is not None:
            checkpoint_path = checkpoint_path.expanduser().resolve()
            refined = _load_refine_checkpoint(
                checkpoint_path,
                signature=checkpoint_signature,
                expected_titles=expected_titles,
            )
            restored_count = len(refined)
            if restored_count and progress:
                progress(
                    f"Checkpoint trovato: riprendo da {restored_count}/{len(chapters)} "
                    "capitoli gia completati."
                )

            # Never trust stale/corrupt checkpoint content just because the metadata matches.
            restored_audits = [
                novice_audit(saved_chapter)
                for _saved_title, saved_chapter in refined
            ]
            if any(not audit.passed for audit in restored_audits):
                refined = []
                restored_count = 0
                if progress:
                    progress("Checkpoint ignorato: contiene un capitolo non valido.")
            else:
                self.stats.novice_score_total += sum(
                    audit.metrics.score for audit in restored_audits
                )
                self.stats.novice_chapters += len(restored_audits)

        for index, (chapter_title, chapter) in enumerate(chapters, start=1):
            if index <= restored_count:
                if progress:
                    progress(
                        f"[{index}/{len(chapters)}] Gia rifinito {chapter_title} (checkpoint)"
                    )
                continue

            if progress:
                progress(f"[{index}/{len(chapters)}] Rifinisco {chapter_title}")

            deduped = _dedupe_exact_blocks(chapter)
            source_warning = not _chapter_title_supported(chapter_title, deduped)
            if source_warning:
                self.stats.source_warnings += 1

            cache_key = _key(
                REFINE_PROMPT_VERSION,
                self.model,
                "refine-chapter",
                chapter_title,
                str(source_warning),
                deduped,
            )
            polished = self._cached_chat(
                cache_key,
                system,
                _refine_chapter_prompt(chapter_title, deduped, source_warning),
                num_predict=2800,
            )
            polished, structure_changed = _normalize_refined_structure(
                polished,
                chapter_title,
            )
            if structure_changed:
                self.stats.structural_fixes += 1
            issues = _chapter_quality_issues(
                polished,
                chapter_title,
                require_source_warning=source_warning,
            )

            quality_attempt = 0
            max_quality_attempts = 3
            while issues and quality_attempt < max_quality_attempts:
                quality_attempt += 1
                self.stats.quality_repairs += 1
                if progress:
                    progress(
                        f"    autocorrezione qualita {quality_attempt}/{max_quality_attempts}: "
                        f"{chapter_title} ({'; '.join(issues)})"
                    )
                repair_source = deduped if quality_attempt == 1 else polished
                repair_key = _key(
                    REFINE_PROMPT_VERSION,
                    self.model,
                    f"refine-repair-{quality_attempt}",
                    chapter_title,
                    str(source_warning),
                    repair_source,
                    "|".join(issues),
                )
                polished = self._cached_chat(
                    repair_key,
                    system,
                    _repair_refined_chapter_prompt(
                        chapter_title,
                        repair_source,
                        issues,
                        source_warning,
                    ),
                    num_predict=3800 + (quality_attempt - 1) * 400,
                )
                polished, structure_changed = _normalize_refined_structure(
                    polished,
                    chapter_title,
                )
                if structure_changed:
                    self.stats.structural_fixes += 1
                issues = _chapter_quality_issues(
                    polished,
                    chapter_title,
                    require_source_warning=source_warning,
                )

            if issues:
                raise ValueError(
                    f"Il capitolo '{chapter_title}' non supera il controllo qualita "
                    f"dopo {max_quality_attempts} autocorrezioni: "
                    + "; ".join(issues)
                )

            polished, layout_fixes = normalize_novice_layout(polished)
            if layout_fixes:
                self.stats.novice_layout_fixes += layout_fixes
                if progress:
                    progress(
                        f"    chiarezza automatica: {layout_fixes} "
                        f"{'paragrafo spezzato' if layout_fixes == 1 else 'paragrafi spezzati'}"
                    )

            clarity = novice_audit(polished)
            max_clarity_attempts = 4
            attempt = 0
            while not clarity.passed and attempt < max_clarity_attempts:
                attempt += 1
                self.stats.novice_repairs += 1
                if progress:
                    progress(
                        f"    autocorrezione principiante {attempt}/{max_clarity_attempts}: "
                        f"{chapter_title} ({'; '.join(clarity.issues)})"
                    )
                novice_key = _key(
                    REFINE_PROMPT_VERSION,
                    self.model,
                    f"novice-repair-{attempt}",
                    chapter_title,
                    str(source_warning),
                    polished,
                    "|".join(clarity.issues),
                )
                polished = self._cached_chat(
                    novice_key,
                    system,
                    _novice_repair_prompt(
                        chapter_title,
                        polished,
                        list(clarity.issues),
                        source_warning,
                    ),
                    num_predict=3800 + (attempt - 1) * 500,
                )
                polished, structure_changed = _normalize_refined_structure(
                    polished,
                    chapter_title,
                )
                if structure_changed:
                    self.stats.structural_fixes += 1

                polished, layout_fixes = normalize_novice_layout(polished)
                if layout_fixes:
                    self.stats.novice_layout_fixes += layout_fixes

                remaining_structure = _chapter_quality_issues(
                    polished,
                    chapter_title,
                    require_source_warning=source_warning,
                )
                if remaining_structure:
                    if progress:
                        progress(
                            f"    struttura da correggere durante autocorrezione: "
                            f"{'; '.join(remaining_structure)}"
                        )
                    repair_key = _key(
                        REFINE_PROMPT_VERSION,
                        self.model,
                        f"novice-structure-repair-{attempt}",
                        chapter_title,
                        polished,
                        "|".join(remaining_structure),
                    )
                    polished = self._cached_chat(
                        repair_key,
                        system,
                        _repair_refined_chapter_prompt(
                            chapter_title,
                            polished,
                            remaining_structure,
                            source_warning,
                        ),
                        num_predict=4200,
                    )
                    polished, structure_changed = _normalize_refined_structure(
                        polished,
                        chapter_title,
                    )
                    if structure_changed:
                        self.stats.structural_fixes += 1
                    polished, layout_fixes = normalize_novice_layout(polished)
                    if layout_fixes:
                        self.stats.novice_layout_fixes += layout_fixes

                clarity = novice_audit(polished)

            if not clarity.passed:
                raise ValueError(
                    f"Il capitolo '{chapter_title}' non e ancora abbastanza comprensibile "
                    f"dopo {max_clarity_attempts} autocorrezioni automatiche: "
                    + "; ".join(clarity.issues)
                )

            self.stats.novice_score_total += clarity.metrics.score
            self.stats.novice_chapters += 1
            refined.append((chapter_title, polished))

            if checkpoint_path is not None:
                _save_refine_checkpoint(
                    checkpoint_path,
                    signature=checkpoint_signature,
                    completed=refined,
                )

        topic_titles = [chapter_title for chapter_title, _chapter in refined]
        map_topics = [
            (
                f"{chapter_title} [VERIFICA MATERIALE]"
                if "Verifica materiale" in chapter
                else chapter_title
            )
            for chapter_title, chapter in refined
        ]
        topic_list = "\n".join(map_topics)

        map_key = _key(
            REFINE_PROMPT_VERSION,
            self.model,
            "refine-map-v2",
            topic_list,
        )
        map_section = self._cached_chat(
            map_key,
            system,
            _refine_map_prompt(map_topics),
            num_predict=650,
        ).strip()
        global_recap = _build_global_recap(refined)
        overview = map_section + "\n\n" + global_recap

        header = f"""# {title}
> Versione rifinita dell'ultimo riassunto BC Science; non sostituisce la verifica sui PDF originali.

## Come studiare questo riassunto
1. Leggi prima la Mappa della materia per capire l'ordine logico degli argomenti.
2. Studia un capitolo alla volta cercando di spiegare i meccanismi con parole semplici.
3. Memorizza definizioni, classificazioni, sequenze e valori presenti in "Da ricordare per l'esame".
4. Usa il Ripasso globale senza guardare i capitoli e verifica cio che non ricordi.
5. Se compare "Verifica materiale", controlla quel punto sui PDF originali prima dell'esame.
"""

        toc = ["## Indice degli argomenti", *[f"- {item}" for item in topic_titles]]
        separator = "\n\n---\n\n"
        result = (
            header
            + "\n"
            + overview
            + "\n\n"
            + "\n".join(toc)
            + separator
            + separator.join(chapter for _title, chapter in refined)
            + "\n"
        )
        if checkpoint_path is not None:
            checkpoint_path.unlink(missing_ok=True)
        return result


    def build_study_summary(
        self,
        text: str,
        title: str,
        *,
        progress: Callable[[str], None] | None = None,
        checkpoint_path: Path | None = None,
    ) -> str:
        """Create the compact study copy from the verified complete course summary."""
        prepared = _prepare_study_source(
            text,
            title=f"{title} - sorgente consolidata",
        )
        if progress:
            source_chapters = len(_extract_summary_chapters(text))
            merged_chapters = len(_extract_summary_chapters(prepared))
            progress(
                "Versione studio: semantic merge "
                f"{source_chapters} -> {merged_chapters} capitoli prima della compressione."
            )

        result = self.refine_summary(
            prepared,
            title=title,
            progress=progress,
            checkpoint_path=checkpoint_path,
        )
        result = _strip_study_citations(result)
        result = result.replace(
            "> Versione rifinita dell'ultimo riassunto BC Science; "
            "non sostituisce la verifica sui PDF originali.",
            "> Versione studio derivata dal riassunto completo verificato; "
            "le citazioni di pagina restano disponibili nella versione completa.",
        )
        return result

    def summarize_file(self, path: Path) -> str:
        extracted = extract_document(path)
        return self.summarize_text(extracted.text, title=path.stem)
