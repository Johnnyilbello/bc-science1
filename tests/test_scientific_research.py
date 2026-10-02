import json
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
import pytest

from bc_science.config import AppConfig
from bc_science.courses import course_output_path, scan_course
from bc_science.scientific_research import (
    PubMedClient,
    ResearchError,
    ScientificResearchBuilder,
    ScientificSource,
    _parse_pubmed_xml,
    research_output_path,
    research_state,
)

PUBMED_XML = """<?xml version="1.0"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>12345678</PMID>
      <Article>
        <ArticleTitle>Exercise physiology and skeletal muscle adaptation</ArticleTitle>
        <Abstract>
          <AbstractText Label="BACKGROUND">Background text.</AbstractText>
          <AbstractText Label="RESULTS">Training improved the measured adaptation.</AbstractText>
        </Abstract>
        <Journal>
          <JournalIssue><PubDate><Year>2025</Year></PubDate></JournalIssue>
          <Title>Journal of Exercise Science</Title>
        </Journal>
        <PublicationTypeList>
          <PublicationType>Systematic Review</PublicationType>
        </PublicationTypeList>
      </Article>
    </MedlineCitation>
    <PubmedData>
      <ArticleIdList>
        <ArticleId IdType="doi">10.1000/example</ArticleId>
      </ArticleIdList>
    </PubmedData>
  </PubmedArticle>
</PubmedArticleSet>
"""


def test_parse_pubmed_xml_extracts_citable_metadata():
    sources = _parse_pubmed_xml(ET.fromstring(PUBMED_XML))

    assert len(sources) == 1
    source = sources[0]
    assert source.pmid == "12345678"
    assert source.year == "2025"
    assert source.doi == "10.1000/example"
    assert source.publication_types == ("Systematic Review",)
    assert "BACKGROUND: Background text." in source.abstract
    assert source.url == "https://pubmed.ncbi.nlm.nih.gov/12345678/"


def test_pubmed_client_sends_ncbi_identity_and_fetches_sources():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        params = dict(request.url.params)
        assert params["tool"] == "bc-science"
        assert params["email"] == "student@example.com"

        if request.url.path.endswith("/esearch.fcgi"):
            assert params["db"] == "pubmed"
            assert params["sort"] == "relevance"
            return httpx.Response(
                200,
                json={"esearchresult": {"idlist": ["12345678"]}},
            )
        if request.url.path.endswith("/efetch.fcgi"):
            assert params["id"] == "12345678"
            return httpx.Response(200, text=PUBMED_XML)
        raise AssertionError(request.url)

    client = PubMedClient(
        "student@example.com",
        request_interval=0,
        transport=httpx.MockTransport(handler),
    )

    sources = client.search_sources("skeletal muscle adaptation", max_sources=1)

    assert len(calls) == 2
    assert len(sources) == 1
    assert sources[0].pmid == "12345678"


def _write_course_summary(scan) -> Path:
    output = course_output_path(scan)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        """# FISIOLOGIA - Riassunto completo

## Mappa della materia
- Contrazione muscolare.

## Ripasso globale
1. **Contrazione muscolare**: Il calcio partecipa alla contrazione.

## Indice degli argomenti
- Contrazione muscolare

---

## Contrazione muscolare
### In parole semplici
La contrazione muscolare produce forza e movimento.
### Da ricordare per l'esame
- Il calcio partecipa alla contrazione.
""",
        encoding="utf-8",
    )
    return output


class FakePubMed:
    def __init__(self):
        self.calls = []

    def search_sources(self, query: str, *, max_sources: int = 3):
        self.calls.append((query, max_sources))
        return [
            ScientificSource(
                pmid="12345678",
                title="Exercise physiology and skeletal muscle adaptation",
                journal="Journal of Exercise Science",
                year="2025",
                abstract="Training changed skeletal muscle adaptation.",
                doi="10.1000/example",
                publication_types=("Systematic Review",),
            )
        ]


def test_research_build_creates_separate_output_and_reuses_unchanged(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))

    subject = tmp_path / "SCIENZE MOTORIE" / "FISIOLOGIA"
    subject.mkdir(parents=True)
    (subject / "lezione.txt").write_text("materiale ecampus", encoding="utf-8")
    scan = scan_course(subject)
    ecampus_output = _write_course_summary(scan)
    original = ecampus_output.read_text(encoding="utf-8")

    config = AppConfig(
        ncbi_email="student@example.com",
        scientific_research_enabled=True,
    )
    pubmed = FakePubMed()
    builder = ScientificResearchBuilder(config, pubmed=pubmed)

    monkeypatch.setattr(
        builder,
        "_make_query",
        lambda title, chapter: "skeletal muscle contraction calcium",
    )
    monkeypatch.setattr(
        builder,
        "_synthesize",
        lambda title, chapter, sources: (
            "### Cosa aggiunge la letteratura\n"
            "La letteratura aggiunge un approfondimento [1].\n"
            "### Per capire meglio\n"
            "Il dato aiuta a contestualizzare il capitolo [1].\n"
            "### Limiti delle evidenze recuperate\n"
            "È stato usato l'abstract PubMed [1]."
        ),
    )

    first = builder.build_course(scan)

    assert first.state == "creata"
    assert first.sources == 1
    assert ecampus_output.read_text(encoding="utf-8") == original

    research_output = research_output_path(scan)
    assert first.output_path == research_output
    text = research_output.read_text(encoding="utf-8")
    assert "Approfondimento scientifico oltre eCampus" in text
    assert "PMID 12345678" in text
    assert "DOI 10.1000/example" in text
    assert "https://pubmed.ncbi.nlm.nih.gov/12345678/" in text
    assert len(pubmed.calls) == 1
    assert research_state(scan, config) == "pronta"

    second = builder.build_course(scan)

    assert second.state == "riutilizzata"
    assert len(pubmed.calls) == 1


def test_research_state_changes_when_ecampus_summary_changes(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))

    subject = tmp_path / "SCIENZE MOTORIE" / "ANATOMIA"
    subject.mkdir(parents=True)
    (subject / "lezione.txt").write_text("ossa", encoding="utf-8")
    scan = scan_course(subject)
    summary = _write_course_summary(scan)

    config = AppConfig(ncbi_email="student@example.com")
    pubmed = FakePubMed()
    builder = ScientificResearchBuilder(config, pubmed=pubmed)
    monkeypatch.setattr(builder, "_make_query", lambda title, chapter: "muscle")
    monkeypatch.setattr(
        builder,
        "_synthesize",
        lambda title, chapter, sources: (
            "### Cosa aggiunge la letteratura\nDato [1].\n"
            "### Per capire meglio\nSpiegazione [1].\n"
            "### Limiti delle evidenze recuperate\nAbstract only [1]."
        ),
    )

    builder.build_course(scan)
    assert research_state(scan, config) == "pronta"

    summary.write_text(
        summary.read_text(encoding="utf-8") + "\nNuova informazione eCampus.\n",
        encoding="utf-8",
    )
    assert research_state(scan, config) == "da aggiornare"


def test_config_serializes_research_settings(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    config = AppConfig(
        scientific_research_enabled=True,
        ncbi_email="student@example.com",
        research_max_sources_per_chapter=4,
    )
    path = config.save()

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["scientific_research_enabled"] is True
    assert payload["ncbi_email"] == "student@example.com"
    assert payload["research_max_sources_per_chapter"] == 4
    assert AppConfig.load().ncbi_email == "student@example.com"


def test_scientific_enrichment_requires_citations_in_each_section():
    valid = """### Cosa aggiunge la letteratura
Dato supportato [1].

### Per capire meglio
Spiegazione supportata [1].

### Limiti delle evidenze recuperate
Limite dell'abstract [1].
"""
    ScientificResearchBuilder._validate_citations(valid, 1)

    invalid = """### Cosa aggiunge la letteratura
Dato supportato [1].

### Per capire meglio
Spiegazione senza fonte.

### Limiti delle evidenze recuperate
Limite dell'abstract [1].
"""
    with pytest.raises(ResearchError, match="Per capire meglio"):
        ScientificResearchBuilder._validate_citations(invalid, 1)
