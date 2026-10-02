from __future__ import annotations

import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .config import AppConfig, app_home
from .courses import CourseScan, course_output_path, workspace_path
from .ollama_client import OllamaClient
from .summarizer import _extract_summary_chapters

RESEARCH_VERSION = "scientific-enrichment-v1"
NCBI_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
NCBI_TOOL = "bc-science"
DEFAULT_REQUEST_INTERVAL = 0.36


class ResearchError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ScientificSource:
    pmid: str
    title: str
    journal: str
    year: str
    abstract: str
    doi: str = ""
    publication_types: tuple[str, ...] = ()

    @property
    def url(self) -> str:
        return f"https://pubmed.ncbi.nlm.nih.gov/{self.pmid}/"


@dataclass(frozen=True, slots=True)
class ResearchBuildResult:
    course: str
    state: str
    output_path: Path | None
    chapters: int = 0
    sources: int = 0


class PubMedClient:
    def __init__(
        self,
        email: str,
        *,
        api_key: str = "",
        timeout: float = 25.0,
        request_interval: float = DEFAULT_REQUEST_INTERVAL,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not email or "@" not in email:
            raise ValueError(
                "Per PubMed/NCBI serve una email valida. "
                "Configura prima 'bc-science research configure --email ...'."
            )
        self.email = email
        self.api_key = api_key
        self.timeout = timeout
        self.request_interval = request_interval
        self.transport = transport
        self._last_request = 0.0

    def _common_params(self) -> dict[str, str]:
        params = {
            "tool": NCBI_TOOL,
            "email": self.email,
        }
        if self.api_key:
            params["api_key"] = self.api_key
        return params

    def _wait_for_rate_limit(self) -> None:
        if self.request_interval <= 0:
            return
        elapsed = time.monotonic() - self._last_request
        remaining = self.request_interval - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def _get(self, endpoint: str, params: dict[str, str]) -> httpx.Response:
        self._wait_for_rate_limit()
        merged = self._common_params()
        merged.update(params)
        try:
            with httpx.Client(
                timeout=self.timeout,
                transport=self.transport,
                headers={"User-Agent": "BC-Science/Scientific-Enrichment"},
            ) as client:
                response = client.get(f"{NCBI_BASE_URL}/{endpoint}", params=merged)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ResearchError(f"NCBI non raggiungibile: {exc}") from exc
        finally:
            self._last_request = time.monotonic()
        return response

    def search(self, query: str, *, retmax: int = 5) -> list[str]:
        response = self._get(
            "esearch.fcgi",
            {
                "db": "pubmed",
                "term": query,
                "retmax": str(retmax),
                "retmode": "json",
                "sort": "relevance",
            },
        )
        try:
            payload = response.json()
            return [str(item) for item in payload["esearchresult"]["idlist"]]
        except (ValueError, KeyError, TypeError) as exc:
            raise ResearchError("Risposta ESearch PubMed non valida.") from exc

    def fetch(self, pmids: list[str]) -> list[ScientificSource]:
        if not pmids:
            return []
        response = self._get(
            "efetch.fcgi",
            {
                "db": "pubmed",
                "id": ",".join(pmids),
                "retmode": "xml",
            },
        )
        try:
            root = ET.fromstring(response.text)
        except ET.ParseError as exc:
            raise ResearchError("Risposta EFetch PubMed non valida.") from exc
        return _parse_pubmed_xml(root)

    def search_sources(self, query: str, *, max_sources: int = 3) -> list[ScientificSource]:
        priority_query = (
            f"({query}) AND "
            "(systematic review[Publication Type] OR meta-analysis[Publication Type] "
            "OR review[Publication Type] OR practice guideline[Publication Type])"
        )
        pmids = self.search(priority_query, retmax=max_sources)
        if not pmids:
            pmids = self.search(query, retmax=max_sources)
        return self.fetch(pmids[:max_sources])


def _text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return " ".join("".join(node.itertext()).split())


def _publication_year(article: ET.Element) -> str:
    pub_date = article.find("./MedlineCitation/Article/Journal/JournalIssue/PubDate")
    if pub_date is None:
        return ""
    year = _text(pub_date.find("Year"))
    if year:
        return year
    medline = _text(pub_date.find("MedlineDate"))
    match = re.search(r"\b(19|20)\d{2}\b", medline)
    return match.group(0) if match else medline


def _parse_pubmed_xml(root: ET.Element) -> list[ScientificSource]:
    sources: list[ScientificSource] = []
    for article in root.findall(".//PubmedArticle"):
        pmid = _text(article.find("./MedlineCitation/PMID"))
        title = _text(article.find("./MedlineCitation/Article/ArticleTitle"))
        journal = _text(article.find("./MedlineCitation/Article/Journal/Title"))
        year = _publication_year(article)

        abstract_parts: list[str] = []
        for abstract in article.findall("./MedlineCitation/Article/Abstract/AbstractText"):
            value = _text(abstract)
            if not value:
                continue
            label = abstract.attrib.get("Label", "").strip()
            abstract_parts.append(f"{label}: {value}" if label else value)
        abstract = "\n".join(abstract_parts).strip()

        doi = ""
        for identifier in article.findall("./PubmedData/ArticleIdList/ArticleId"):
            if identifier.attrib.get("IdType") == "doi":
                doi = _text(identifier)
                break

        publication_types = tuple(
            value
            for node in article.findall(
                "./MedlineCitation/Article/PublicationTypeList/PublicationType"
            )
            if (value := _text(node))
        )

        if pmid and title:
            sources.append(
                ScientificSource(
                    pmid=pmid,
                    title=title,
                    journal=journal,
                    year=year,
                    abstract=abstract,
                    doi=doi,
                    publication_types=publication_types,
                )
            )
    return sources


def research_output_path(scan: CourseScan) -> Path:
    return app_home() / "outputs" / "research" / scan.name / "approfondimento-scientifico.md"


def research_manifest_path(scan: CourseScan) -> Path:
    return workspace_path(scan) / "research-manifest.json"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_manifest(scan: CourseScan) -> dict[str, Any] | None:
    path = research_manifest_path(scan)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        return None
    return payload


def _write_manifest(
    scan: CourseScan,
    *,
    signature: str,
    output: Path,
    chapters: list[dict[str, Any]],
) -> None:
    path = research_manifest_path(scan)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": 1,
        "version": RESEARCH_VERSION,
        "course": scan.name,
        "signature": signature,
        "output": str(output),
        "chapters": chapters,
    }
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _research_signature(
    summary_text: str,
    *,
    model: str,
    max_sources: int,
) -> str:
    raw = json.dumps(
        {
            "version": RESEARCH_VERSION,
            "summary_sha256": _sha256_text(summary_text),
            "model": model,
            "max_sources": max_sources,
        },
        sort_keys=True,
    )
    return _sha256_text(raw)


def research_state(
    scan: CourseScan,
    config: AppConfig,
    *,
    profile: str = "standard",
    max_sources: int | None = None,
) -> str:
    summary = course_output_path(scan)
    output = research_output_path(scan)
    if not summary.exists():
        return "manca riassunto"
    if not config.ncbi_email:
        return "non configurata"

    text = summary.read_text(encoding="utf-8")
    model = config.model_for_profile(profile)
    selected_max = max_sources or config.research_max_sources_per_chapter
    signature = _research_signature(text, model=model, max_sources=selected_max)
    manifest = _load_manifest(scan)

    if manifest and manifest.get("signature") == signature and output.exists():
        return "pronta"
    return "da aggiornare"


class ScientificResearchBuilder:
    def __init__(
        self,
        config: AppConfig,
        *,
        profile: str = "standard",
        pubmed: PubMedClient | None = None,
    ) -> None:
        self.config = config
        self.model = config.model_for_profile(profile)
        self.ollama = OllamaClient(config.ollama_url)
        self.pubmed = pubmed or PubMedClient(
            config.ncbi_email,
            api_key=config.ncbi_api_key,
        )

    def _make_query(self, title: str, chapter: str) -> str:
        excerpt = chapter[:3200]
        prompt = f"""Trasforma questo capitolo universitario in UNA query PubMed breve in inglese.

REGOLE:
- restituisci solo la query, senza spiegazioni e senza virgolette esterne;
- usa 2-6 concetti scientifici specifici collegati da AND/OR;
- non inserire filtri su tipo di studio: li applica BC Science;
- non inventare patologie o concetti assenti;
- privilegia il significato scientifico del capitolo, non la traduzione letterale del titolo.

TITOLO:
{title}

ESTRATTO ECAMPUS:
{excerpt}
"""
        response = self.ollama.chat(
            self.model,
            [
                {
                    "role": "system",
                    "content": (
                        "Sei un assistente che prepara query bibliografiche PubMed. "
                        "Non devi rispondere alla domanda scientifica."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            num_ctx=4096,
            num_predict=120,
            keep_alive=self.config.keep_alive,
        )
        query = response.strip().strip('"').strip("'")
        query = re.sub(r"\s+", " ", query)
        if not query or len(query) > 400:
            raise ResearchError(f"Query PubMed non valida per {title}.")
        return query

    def _synthesize(
        self,
        title: str,
        chapter: str,
        sources: list[ScientificSource],
    ) -> str:
        source_blocks: list[str] = []
        for index, source in enumerate(sources, start=1):
            abstract = source.abstract[:5000] if source.abstract else "(abstract non disponibile)"
            source_blocks.append(
                f"[{index}] {source.title}\n"
                f"Rivista: {source.journal}; anno: {source.year}; PMID: {source.pmid}\n"
                f"Tipo: {', '.join(source.publication_types) or 'non specificato'}\n"
                f"Abstract: {abstract}"
            )

        prompt = f"""Crea un approfondimento scientifico in italiano sul capitolo "{title}".

Questo testo NON fa parte del riassunto eCampus. Deve aiutare a capire cosa aggiunge la
letteratura scientifica recuperata da PubMed.

REGOLE RIGIDE:
- usa SOLO gli abstract e i metadati numerati forniti sotto;
- non inventare risultati, meccanismi, numeri o conclusioni non presenti;
- cita ogni affermazione scientifica aggiuntiva con [1], [2], ecc.;
- se gli abstract non permettono una conclusione, dichiaralo;
- distingui chiaramente evidenza, limiti e implicazioni per lo studio;
- non dire che eCampus e sbagliata salvo che le fonti lo dimostrino esplicitamente;
- non dare consigli medici personali;
- non creare la bibliografia: viene aggiunta deterministicamente da BC Science.

FORMATO:
### Cosa aggiunge la letteratura
[testo sintetico con citazioni]
### Per capire meglio
[spiegazione didattica delle informazioni aggiuntive, sempre con citazioni]
### Limiti delle evidenze recuperate
[limiti pertinenti, incluso abstract-only quando necessario]

CONTESTO DEL CAPITOLO ECAMPUS:
{chapter[:4500]}

FONTI PUBMED:
{chr(10).join(source_blocks)}
"""
        result = self.ollama.chat(
            self.model,
            [
                {
                    "role": "system",
                    "content": (
                        "Sei un revisore scientifico source-grounded. "
                        "Puoi usare esclusivamente le fonti fornite."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            num_ctx=12288,
            num_predict=1500,
            keep_alive=self.config.keep_alive,
        )
        self._validate_citations(result, len(sources))
        return result.strip()

    @staticmethod
    def _validate_citations(text: str, source_count: int) -> None:
        citations = [int(value) for value in re.findall(r"\[(\d+)\]", text)]
        if not citations:
            raise ResearchError("Approfondimento privo di citazioni alle fonti recuperate.")
        if any(value < 1 or value > source_count for value in citations):
            raise ResearchError("Approfondimento contiene citazioni a fonti inesistenti.")

    @staticmethod
    def _bibliography(sources: list[ScientificSource]) -> str:
        lines = ["### Fonti scientifiche"]
        for index, source in enumerate(sources, start=1):
            details = []
            if source.journal:
                details.append(source.journal)
            if source.year:
                details.append(source.year)
            details.append(f"PMID {source.pmid}")
            if source.doi:
                details.append(f"DOI {source.doi}")
            lines.append(
                f"{index}. **{source.title}** — {'; '.join(details)}. "
                f"{source.url}"
            )
        return "\n".join(lines)

    def build_course(
        self,
        scan: CourseScan,
        *,
        max_sources: int | None = None,
        progress: Any = None,
    ) -> ResearchBuildResult:
        summary_path = course_output_path(scan)
        if not summary_path.exists():
            return ResearchBuildResult(scan.name, "manca riassunto", None)

        summary_text = summary_path.read_text(encoding="utf-8")
        chapters = _extract_summary_chapters(summary_text)
        if not chapters:
            raise ResearchError(
                f"Il riassunto di {scan.name} non contiene capitoli BC Science riconoscibili."
            )

        selected_max = max_sources or self.config.research_max_sources_per_chapter
        selected_max = max(1, min(int(selected_max), 5))
        signature = _research_signature(
            summary_text,
            model=self.model,
            max_sources=selected_max,
        )
        output = research_output_path(scan)
        manifest = _load_manifest(scan)
        if manifest and manifest.get("signature") == signature and output.exists():
            return ResearchBuildResult(
                scan.name,
                "riutilizzata",
                output,
                chapters=len(chapters),
                sources=sum(
                    len(item.get("pmids", []))
                    for item in manifest.get("chapters", [])
                    if isinstance(item, dict)
                ),
            )

        sections: list[str] = []
        manifest_chapters: list[dict[str, Any]] = []
        total_sources = 0

        for index, (title, chapter) in enumerate(chapters, start=1):
            if progress is not None:
                progress(f"[{index}/{len(chapters)}] Ricerca scientifica: {title}")

            query = self._make_query(title, chapter)
            sources = self.pubmed.search_sources(query, max_sources=selected_max)
            total_sources += len(sources)

            if not sources:
                section = (
                    f"## {title}\n\n"
                    "Nessuna fonte PubMed sufficientemente pertinente è stata selezionata "
                    "automaticamente per questo capitolo. BC Science non aggiunge contenuti "
                    "esterni senza una fonte recuperata."
                )
            else:
                synthesis = self._synthesize(title, chapter, sources)
                section = (
                    f"## {title}\n\n"
                    f"{synthesis}\n\n"
                    f"{self._bibliography(sources)}"
                )

            sections.append(section)
            manifest_chapters.append(
                {
                    "title": title,
                    "query": query,
                    "pmids": [source.pmid for source in sources],
                }
            )

        header = f"""# {scan.name} — Approfondimento scientifico oltre eCampus

> Documento separato dal riassunto d'esame. Le informazioni qui presenti provengono da
> ricerche PubMed/NCBI e non modificano il contenuto delle dispense eCampus.

## Come usare questo approfondimento

1. Studia prima il riassunto eCampus della materia.
2. Usa questo documento per comprendere meglio i meccanismi e conoscere la letteratura.
3. Le citazioni [1], [2], ecc. rimandano esclusivamente alle fonti del singolo capitolo.
4. La ricerca automatica usa titoli e abstract PubMed: non equivale a una revisione sistematica completa.
5. Se un capitolo non ha fonti sufficienti, BC Science non aggiunge conoscenza non verificata.
"""

        final_text = header + "\n\n---\n\n" + "\n\n---\n\n".join(sections) + "\n"
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(output.name + ".tmp")
        temporary.write_text(final_text, encoding="utf-8")
        temporary.replace(output)
        _write_manifest(
            scan,
            signature=signature,
            output=output,
            chapters=manifest_chapters,
        )

        return ResearchBuildResult(
            scan.name,
            "creata",
            output,
            chapters=len(chapters),
            sources=total_sources,
        )
