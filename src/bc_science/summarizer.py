from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .cache import CacheDB
from .config import AppConfig
from .documents import chunk_text, extract_document
from .knowledge import classify_domain, system_prompt
from .ollama_client import OllamaClient

PROMPT_VERSION = "summary-v6-validated-refine"


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


@dataclass(slots=True)
class TopicGroup:
    title: str
    files: list[Path]


def _key(*parts: str) -> str:
    payload = "\x1f".join(parts).encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()


def normalize_topic_stem(stem: str) -> str:
    """Collapse lesson-number variants into one human-readable topic title."""
    text = stem.replace("_", " ").strip()
    text = re.sub(
        r"(?i)\b(?:parte|lezione|capitolo)?\s*\d+\b",
        " ",
        text,
    )
    text = re.sub(r"\s+([)\]])", r"\1", text)
    text = re.sub(r"([(\[])\s+", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip(" -_.,")
    return text or stem.strip()


def group_course_files(files: list[Path]) -> list[TopicGroup]:
    grouped: dict[str, TopicGroup] = {}
    order: list[str] = []

    for path in files:
        title = normalize_topic_stem(path.stem)
        key = title.casefold()
        key = re.sub(r"^(?:i|il|lo|la|gli|le)\s+", "", key).strip()
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
- Le dispense fornite sono l'unica fonte ammessa.
- NON aggiungere conoscenza generale, neppure in sezioni chiamate "Chiarimento".
- NON correggere scientificamente le dispense usando conoscenze esterne.
- Puoi correggere solo refusi ortografici evidenti quando il significato e inequivocabile.
- Se il contenuto dei documenti non corrisponde al titolo del gruppo, segnalalo in una breve
  nota "Verifica materiale" e riassumi comunque il contenuto realmente presente, senza chiedere
  conferma all'utente.
- Non lasciare mai una frase, una lista o una parola incompleta.
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
- massima comprensibilita;
- nessuna perdita di concetti potenzialmente valutabili;
- elimina solo ripetizioni e frasi decorative;
- conserva definizioni, classificazioni, passaggi causali, eccezioni e numeri;
- spiega sigle e termini tecnici alla prima occorrenza;
- usa soltanto cio che e supportato dalla fonte.

FORMATO:
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
1. Semplice da capire anche alla prima lettura.
2. Completo rispetto alle dispense: non eliminare fatti diversi solo per accorciare.
3. Rimuovi duplicati tra lezioni 1/2/3/4 e ripetizioni dello stesso concetto.
4. Mantieni terminologia, numeri, classificazioni, definizioni, eccezioni e sequenze.
5. Spiega subito ogni termine tecnico difficile con parole semplici.
6. Per processi fisiologici usa sequenze numerate causa -> effetto.
7. Non aggiungere conoscenze esterne e non correggere silenziosamente le dispense.
8. Non citare i nomi dei file nel corpo del capitolo.
9. Non inventare "domande ufficiali eCampus".
10. NON creare sezioni "Chiarimento" basate su conoscenza generale.
11. Se titolo e contenuto non corrispondono, usa una breve nota "Verifica materiale" e poi
    riassumi il contenuto effettivamente presente: non chiedere conferma all'utente.
12. Termina sempre il capitolo con una frase completa.

STRUTTURA:
## {title}
Apri con 2-4 frasi che fanno capire subito l'argomento.
Poi usa sottosezioni solo quando servono, con paragrafi brevi e liste.
Chiudi con:
### Da ricordare per l'esame
con i punti davvero essenziali, senza ripetere tutto il capitolo.

DISPENSE DELL'ARGOMENTO:
{source_text}
"""


def _topic_merge_prompt(title: str, notes: str, domain: str) -> str:
    return f"""Fondi gli appunti parziali seguenti nel capitolo definitivo "{title}" ({domain}).

REGOLE:
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
[testo organizzato e semplice]
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

    if require_source_warning and "Verifica materiale" not in chapter:
        issues.append("manca la nota obbligatoria Verifica materiale")

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
    lines = normalized.splitlines()
    changed = False

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
- renderlo molto semplice da capire;
- conservare TUTTE le informazioni distinte presenti;
- eliminare ripetizioni e blocchi duplicati;
- correggere frasi spezzate, heading incollati nel testo e refusi evidenti;
- mantenere numeri, definizioni, classificazioni, sequenze, eccezioni e contenuti utili all'esame;
- NON aggiungere conoscenze che non siano gia presenti nel capitolo;
- NON correggere scientificamente il contenuto usando conoscenza esterna;
- non ripetere il titolo dentro il capitolo;
- non ripetere piu volte "Da ricordare per l'esame";
- termina con una frase completa.
{warning}
VINCOLI STRUTTURALI:
- esattamente UN heading H2: "## {title}", solo come prima riga;
- nessun altro heading H2 nel capitolo;
- esattamente UNA sezione finale "### Da ricordare per l'esame";
- dopo "Da ricordare per l'esame" usa punti brevi, senza riscrivere tutto il capitolo.

CAPITOLO DA RIFINIRE:
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
            lines.append(f"{index}. **{title}**: rivedi il capitolo.")
            continue

        selected: list[str] = []
        total = 0
        for point in points[:3]:
            plain = re.sub(r"\s+", " ", point).strip()
            if not plain:
                continue
            projected = total + len(plain)
            if selected and projected > 280:
                break
            selected.append(plain)
            total = projected

        recap = " ".join(selected) if selected else points[0]
        lines.append(f"{index}. **{title}**: {recap}")
    return "\n".join(lines)


def _refine_map_prompt(topic_titles: list[str]) -> str:
    topics = "\n".join(f"- {item}" for item in topic_titles)
    return f"""Crea SOLO la sezione Markdown:

## Mappa della materia

Raggruppa i capitoli seguenti in 4-7 macro-aree logiche e mostra in modo molto breve
i collegamenti principali. Massimo 12 punti complessivi. Non aggiungere conoscenza
esterna e non creare altre sezioni.

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


class Summarizer:
    def __init__(self, config: AppConfig, profile: str | None = None) -> None:
        self.config = config
        self.model = config.model_for_profile(profile)
        self.client = OllamaClient(config.ollama_url)
        self.db = CacheDB()
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

    def refine_summary(
        self,
        text: str,
        title: str,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> str:
        chapters = _extract_summary_chapters(text)
        if not chapters:
            raise ValueError(
                "Il file non contiene un indice BC Science valido con capitoli rifinibili."
            )

        refined: list[tuple[str, str]] = []
        system = _summary_system_prompt("Scienze Motorie")

        for index, (chapter_title, chapter) in enumerate(chapters, start=1):
            if progress:
                progress(f"[{index}/{len(chapters)}] Rifinisco {chapter_title}")

            deduped = _dedupe_exact_blocks(chapter)
            source_warning = not _chapter_title_supported(chapter_title, deduped)
            if source_warning:
                self.stats.source_warnings += 1

            cache_key = _key(
                PROMPT_VERSION,
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

            if issues:
                self.stats.quality_repairs += 1
                if progress:
                    progress(
                        f"    controllo qualita: rigenero {chapter_title} "
                        f"({'; '.join(issues)})"
                    )
                repair_key = _key(
                    PROMPT_VERSION,
                    self.model,
                    "refine-repair",
                    chapter_title,
                    str(source_warning),
                    deduped,
                    "|".join(issues),
                )
                polished = self._cached_chat(
                    repair_key,
                    system,
                    _repair_refined_chapter_prompt(
                        chapter_title,
                        deduped,
                        issues,
                        source_warning,
                    ),
                    num_predict=3800,
                )
                polished, structure_changed = _normalize_refined_structure(
                    polished,
                    chapter_title,
                )
                if structure_changed:
                    self.stats.structural_fixes += 1
                remaining = _chapter_quality_issues(
                    polished,
                    chapter_title,
                    require_source_warning=source_warning,
                )
                if remaining:
                    raise ValueError(
                        f"Il capitolo '{chapter_title}' non supera il controllo qualita: "
                        + "; ".join(remaining)
                    )

            refined.append((chapter_title, polished))

        topic_titles = [chapter_title for chapter_title, _chapter in refined]
        topic_list = "\n".join(topic_titles)

        map_key = _key(
            PROMPT_VERSION,
            self.model,
            "refine-map-v1",
            topic_list,
        )
        map_section = self._cached_chat(
            map_key,
            system,
            _refine_map_prompt(topic_titles),
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
        return (
            header
            + "\n"
            + overview
            + "\n\n"
            + "\n".join(toc)
            + separator
            + separator.join(chapter for _title, chapter in refined)
            + "\n"
        )

    def summarize_file(self, path: Path) -> str:
        extracted = extract_document(path)
        return self.summarize_text(extracted.text, title=path.stem)
