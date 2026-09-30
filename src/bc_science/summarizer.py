from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .cache import CacheDB
from .config import AppConfig
from .documents import chunk_text, extract_document
from .knowledge import classify_domain, system_prompt
from .ollama_client import OllamaClient

PROMPT_VERSION = "summary-v4-source-safe"


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


def _normalize_chapter_heading(chapter: str, title: str) -> str:
    lines = chapter.strip().splitlines()
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


def _course_overview_prompt(topic_previews: str, title: str) -> str:
    return f"""Crea l'introduzione e il ripasso globale per un unico riassunto eCampus intitolato
"{title}".

Hai sotto l'elenco dei capitoli gia completi con una breve anteprima. NON devi riscrivere
i capitoli e NON devi aggiungere contenuti esterni.

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

        budget = num_predict or self.config.num_predict
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        pieces: list[str] = []

        for attempt in range(3):
            result = self.client.chat_stream(
                self.model,
                messages,
                on_token=None,
                num_ctx=self.config.num_ctx,
                num_predict=budget,
                keep_alive=self.config.keep_alive,
            )
            self.stats.generated_calls += 1
            self.stats.generated_tokens += result.eval_count
            self.stats.prompt_tokens += result.prompt_eval_count
            self.stats.ollama_seconds += result.total_seconds

            pieces.append(result.content)
            if result.done_reason != "length":
                break

            self.stats.continuation_calls += 1
            messages.append({"role": "assistant", "content": result.content})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Continua ESATTAMENTE dal punto in cui il testo si e interrotto. "
                        "Non ripetere contenuti gia scritti, non aggiungere conoscenza esterna "
                        "e completa tutte le sezioni e le frasi rimaste aperte."
                    ),
                }
            )
        merged = "".join(pieces).strip()
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
        for path in group.files:
            extracted = extract_document(path)
            if extracted.text:
                source_parts.append(
                    f"\n--- DISPENSA: {path.stem} ---\n{extracted.text}"
                )

        source_text = "\n".join(source_parts).strip()
        if not source_text:
            return ""

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
                _topic_prompt(group.title, chunks[0], domain),
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
            _topic_merge_prompt(group.title, notes, domain),
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

        chapters: list[str] = []
        for index, group in enumerate(groups, start=1):
            if progress:
                progress(
                    f"[{index}/{len(groups)}] {group.title} "
                    f"({len(group.files)} {'documento' if len(group.files) == 1 else 'documenti'})"
                )
            chapter = self._summarize_topic(group, progress=progress)
            if chapter:
                chapters.append(chapter.strip())

        if not chapters:
            return ""

        # The overview sees only compact previews. The complete chapters are never compressed
        # again, which prevents the final pass from silently dropping exam-relevant details.
        previews: list[str] = []
        for chapter in chapters:
            lines = [line.strip() for line in chapter.splitlines() if line.strip()]
            heading = next((line for line in lines if line.startswith("## ")), "## Argomento")
            body = " ".join(line for line in lines if not line.startswith("#"))
            previews.append(f"{heading}\n{body[:900]}")

        preview_text = "\n\n".join(previews)
        overview_system = _summary_system_prompt("Scienze Motorie")
        overview_key = _key(
            PROMPT_VERSION,
            self.model,
            "course-overview",
            title,
            preview_text,
        )
        overview = self._cached_chat(
            overview_key,
            overview_system,
            _course_overview_prompt(preview_text, title),
            num_predict=1800,
        ).strip()

        toc_lines = ["## Indice degli argomenti"]
        for chapter in chapters:
            heading = next(
                (
                    line[3:].strip()
                    for line in chapter.splitlines()
                    if line.startswith("## ")
                ),
                "Argomento",
            )
            toc_lines.append(f"- {heading}")

        separator = "\n\n---\n\n"
        return (
            overview
            + "\n\n"
            + "\n".join(toc_lines)
            + separator
            + separator.join(chapters)
            + "\n"
        )

    def summarize_file(self, path: Path) -> str:
        extracted = extract_document(path)
        return self.summarize_text(extracted.text, title=path.stem)
