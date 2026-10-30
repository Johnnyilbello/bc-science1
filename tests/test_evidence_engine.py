"""Regression for original-source grounding, evidence identity and safe review."""
import json
from pathlib import Path

from bc_science import courses
from bc_science.cache import CacheDB
from bc_science.evidence_engine import (
    OriginalEvidence,
    OriginalEvidenceIndex,
    validate_original_quote,
)
from bc_science.fast_audit import audit_fast_essential
from bc_science.structural_checks import find_structural_issues
from bc_science.study_editions import make_fast_essential
from bc_science.targeted_review import _validate_proposal, review_chapter


def _create_db(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "workspace.db"
    source = str(tmp_path / "ANATOMIA" / "Faringe ed esofago.pdf")
    db = CacheDB(path)
    try:
        db.set_extracted_document(
            source, "hash-v1", "extraction-v1",
            (
                "--- Pagina 1 ---\nLa faringe partecipa alla deglutizione.\n"
                "--- Pagina 2 ---\nL'esofago trasporta il bolo alimentare "
                "dalla faringe allo stomaco tramite contrazioni peristaltiche.\n"
            ),
            pages=2, ocr_pages=0,
        )
    finally:
        db.close()
    return path, source


def test_cached_original_pdf_retrieval_has_exact_page_and_no_new_ocr(tmp_path: Path):
    db_path, source = _create_db(tmp_path)
    with OriginalEvidenceIndex(db_path, {source: "hash-v1"}) as index:
        results = index.search(
            "Il bolo alimentare va dalla faringe allo stomaco",
            chapter_title="Faringe ed esofago", limit=5,
        )
        assert results
        assert any(
            e.page == 2 and e.provenance == "cached_page"
            and "dalla faringe allo stomaco" in e.excerpt
            for e in results
        )
        matching = next(e for e in results if e.page == 2)
        quote = {
            "source": source,
            "page": 2,
            "block": None,
            "quote": (
                "L'esofago trasporta il bolo alimentare dalla faringe "
                "allo stomaco tramite contrazioni peristaltiche."
            ),
        }
        assert validate_original_quote(quote, results)
        assert not validate_original_quote(
            {**quote, "page": 1}, results,
        )
        assert not validate_original_quote(
            {**quote, "quote": "I muscoli funzionano sempre diversamente."},
            results,
        )
        assert matching.source_path == source


def test_only_exact_hashes_and_course_sources_are_allowed(tmp_path: Path):
    db_path, source = _create_db(tmp_path)
    with OriginalEvidenceIndex(db_path, {source: "changed-hash"}) as index:
        assert not index.search("faringe esofago")
    with OriginalEvidenceIndex(db_path, {source: "hash-v1"}) as index:
        assert index.search("faringe esofago")
    with OriginalEvidenceIndex(db_path, {}) as index:
        assert not index.search("faringe esofago")


def test_cached_chunks_are_usable_when_pdf_extraction_is_missing(tmp_path: Path):
    db_path = tmp_path / "workspace.db"
    source = str(tmp_path / "Articolazione del ginocchio.txt")
    db = CacheDB(db_path)
    try:
        db.replace_document(
            source, "correct-hash", "Ginocchio",
            ["Il ginocchio comprende articolazioni e legamenti di sostegno."],
            [[1.0, 0.0, 0.0]],
        )
    finally:
        db.close()
    with OriginalEvidenceIndex(db_path, {source: "correct-hash"}) as index:
        hits = index.search("legamenti ginocchio")
        assert len(hits) == 1
        assert hits[0].page is None
        assert hits[0].block == 1
        assert hits[0].provenance == "indexed_chunk"
        semantic = index.search_semantic([1.0, 0.0, 0.0])
        assert semantic and semantic[0].source_path == source
        assert semantic[0].block == 1
    with OriginalEvidenceIndex(db_path, {source: "stale-hash"}) as index:
        assert not index.search_semantic([1.0, 0.0, 0.0])


def test_detect_repeated_text_and_orphaned_headings_without_rewriting():
    chapter = (
        "## Vie sensitive\n\nLa via spino-talamica trasporta informazioni "
        "sensoriali e collega diverse strutture nervose. "
        "La via spino-talamica trasporta informazioni "
        "sensoriali e collega diverse strutture nervose.\n\n"
        "Riepilogo delle relazioni causa-effetto"
    )
    issues = find_structural_issues(chapter)
    assert {"repetition", "dangling_heading"} <= {
        issue.category for issue in issues
    }
    assert "Riepilogo delle relazioni causa-effetto" in chapter


def test_fast_audit_reports_structure_separately_from_technical_status():
    source = (
        "# Anatomia\n\n## Indice degli argomenti\n- Vie sensitive\n\n---\n\n"
        "## Vie sensitive\n\nLe vie sensitive trasportano segnali dal corpo "
        "al cervello. L'encefalo interpreta stimoli sensitivi."
    )
    study = (
        "# Anatomia essenziale\n\n## Indice degli argomenti\n- Vie sensitive\n\n---\n\n"
        "## Vie sensitive\n\nLe vie sensitive trasportano segnali dal corpo "
        "al cervello. Le vie sensitive trasportano segnali dal corpo al cervello."
    )
    result = audit_fast_essential(source, study)
    assert result.status in {"PASS", "WARN"}
    assert result.chapters_with_structural_issues == 1
    assert result.content_review_status == "NON_VERIFICATO_SCIENTIFICAMENTE"
    assert result.chapter_signals[0]["structural_findings"]


def test_qwen_grounded_rewrite_requires_real_original_page_quote():
    source = (
        "## Scheletro\n\nIl scheletro assile costituisce la parte centrale "
        "del corpo e comprende il cranio."
    )
    before = source
    doc = OriginalEvidence(
        source_path="C:/Dispense/Anatomia.pdf", page=3, block=None,
        excerpt=(
            "Lo scheletro assile costituisce la parte centrale del corpo "
            "e comprende il cranio."
        ),
        rank=1, provenance="cached_page",
    )
    proposal = {
        "rewrite": (
            "## Scheletro\n\nLo scheletro assile costituisce la parte centrale "
            "del corpo e comprende il cranio."
        ),
        "evidence": [
            ("Il scheletro assile costituisce la parte centrale "
             "del corpo e comprende il cranio.")
        ],
        "original_quotes": [
            {"source": doc.source_path, "page": 3, "block": None,
             "quote": doc.excerpt}
        ],
    }
    assert _validate_proposal(
        "Scheletro", source, before, proposal, original_evidence=[doc],
    )[0]
    assert not _validate_proposal(
        "Scheletro", source, before,
        {**proposal, "original_quotes": []}, original_evidence=[doc],
    )[0]
    assert not _validate_proposal(
        "Scheletro", source, before,
        {**proposal, "original_quotes": [
            {**proposal["original_quotes"][0], "page": 2}
        ]}, original_evidence=[doc],
    )[0]


def test_grounded_model_cannot_pass_unverifiable_citations(tmp_path: Path):
    source = (
        "## Ossa\n\nIl scheletro assile sostiene il cranio e il torace."
    )
    evidence = OriginalEvidence(
        source_path=str(tmp_path / "Dispensa.pdf"), page=1, block=None,
        excerpt="Lo scheletro assile comprende cranio e gabbia toracica.",
        rank=1, provenance="cached_page",
    )

    class Model:
        model = "qwen3.5:4b"

        def _cached_chat(self, *_args, **_kwargs):
            return json.dumps({
                "rewrite": "## Ossa\n\nLo scheletro assile sostiene cranio e torace.",
                "evidence": ["Il scheletro assile sostiene il cranio e il torace."],
                "original_quotes": [{
                    "source": evidence.source_path,
                    "page": 1,
                    "block": None,
                    "quote": "Il cranio ha 99 ossa",
                }],
            })

    outcome = review_chapter(
        "Ossa", source, source, None,
        summarizer=Model(), original_evidence=[evidence],
    )
    assert not outcome.applied
    assert outcome.text == source



def test_course_evidence_reads_original_not_complete_and_rejects_edits(
    tmp_path: Path, monkeypatch,
):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    folder = tmp_path / "SCIENZE MOTORIE" / "ANATOMIA"
    folder.mkdir(parents=True)
    source = folder / "Scheletro.txt"
    original = "Lo scheletro assile comprende il cranio e la gabbia toracica."
    source.write_text(original, encoding="utf-8")
    scan = courses.scan_course(folder)
    complete = (
        "# Anatomia\n\n## Indice degli argomenti\n- Scheletro\n\n---\n\n"
        "## Scheletro\n### In parole semplici\n"
        "Lo scheletro assile comprende il cranio.\n"
    )
    output = courses.course_output_path(scan)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(complete, encoding="utf-8")
    study, _ = make_fast_essential(complete, "Anatomia essenziale")
    study_path = courses.course_study_output_path(scan)
    study_path.write_text(study, encoding="utf-8")
    study_pdf = courses.course_study_pdf_path(scan)
    study_pdf.write_bytes(b"%PDF-test")

    courses._write_manifest(
        scan, courses._snapshot(scan), output, complete,
        {"Scheletro.txt": "Scheletro"},
        pdf_path=None, pdf_pages=0,
        study_path=study_path, study_text=study,
        study_pdf_path=study_pdf, study_pdf_pages=1,
        summary_pipeline="summary-older",
        study_pipeline=courses.FAST_STUDY_VERSION,
    )
    db = CacheDB(courses.workspace_db_path(scan))
    try:
        db.set_extracted_document(
            str(source.resolve()), courses.file_sha256(source),
            "extraction-v1", original, pages=0, ocr_pages=0,
        )
    finally:
        db.close()

    report = courses.inspect_original_evidence(scan, "Scheletro")
    assert report["status"] == "PROVE_ORIGINALI_RECUPERATE_DA_VERIFICARE"
    assert not report["scientifically_verified"]
    assert any(original in item["excerpt"] for item in report["evidence"])
    assert any(item["source_path"] == str(source.resolve()) for item in report["evidence"])
    assert all(item["provenance"] == "cached_page" for item in report["evidence"])

    source.write_text("L'originale universitario è cambiato.", encoding="utf-8")
    import pytest

    with pytest.raises(ValueError, match="dispense|verificabili"):
        courses.inspect_original_evidence(courses.scan_course(folder), "Scheletro")



def test_complete_grounding_and_review_invalidates_dependent_essential(
    tmp_path: Path, monkeypatch,
):
    """A grounded Complete edit keeps backups and marks Essential outdated."""
    from bc_science.config import AppConfig
    from bc_science.targeted_review import ReviewOutcome

    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    folder = tmp_path / "SCIENZE MOTORIE" / "ANATOMIA"
    folder.mkdir(parents=True)
    source = folder / "Scheletro.txt"
    source.write_text(
        "Lo scheletro assile contiene cranio e torace.", encoding="utf-8",
    )
    scan = courses.scan_course(folder)
    complete = (
        "# Anatomia\n\n## Indice degli argomenti\n- Scheletro\n\n---\n\n"
        "## Scheletro\n\nIl scheletro assile contiene cranio e torace.\n"
    )
    full = courses.course_output_path(scan)
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(complete, encoding="utf-8")
    study, _ = make_fast_essential(complete, "Anatomia Essenziale")
    study_path = courses.course_study_output_path(scan)
    study_path.write_text(study, encoding="utf-8")
    study_pdf = courses.course_study_pdf_path(scan)
    study_pdf.write_bytes(b"%PDF-old-study")
    full_pdf = courses.course_pdf_path(scan)
    full_pdf.write_bytes(b"%PDF-old-complete")
    courses._write_manifest(
        scan, courses._snapshot(scan), full, complete,
        {"Scheletro.txt": "Scheletro"},
        pdf_path=full_pdf, pdf_pages=1,
        study_path=study_path, study_text=study,
        study_pdf_path=study_pdf, study_pdf_pages=1,
        summary_pipeline="summary-old",
        study_pipeline=courses.FAST_STUDY_VERSION,
    )
    db = CacheDB(courses.workspace_db_path(scan))
    try:
        db.set_extracted_document(
            str(source.resolve()), courses.file_sha256(source),
            "extraction-v1",
            "Lo scheletro assile contiene cranio e torace.",
            pages=0, ocr_pages=0,
        )
    finally:
        db.close()

    report = courses.inspect_original_evidence(
        scan, "Scheletro", edition="complete",
    )
    assert report["edition"] == "complete"
    assert report["evidence"]

    class FakeSummarizer:
        def __init__(self, *_args, **_kwargs):
            pass

        def close(self):
            pass

    def fake_review(title, source_text, short, config, **kwargs):
        assert kwargs["edition"] == "complete"
        assert kwargs["original_evidence"]
        return ReviewOutcome(
            title, True,
            "## Scheletro\n\nLo scheletro assile contiene cranio e torace.",
            "Revisione con prove originali",
        )

    def fake_pdf(text, path, title):
        path.write_bytes(b"%PDF-new")
        return 2

    monkeypatch.setattr(courses, "Summarizer", FakeSummarizer)
    monkeypatch.setattr(courses, "review_chapter", fake_review)
    monkeypatch.setattr(courses, "export_pdf", fake_pdf)
    out = courses.review_complete_chapter(
        scan, AppConfig(), "Scheletro",
    )
    assert out.state == "capitolo Completa rivisto"
    assert "Lo scheletro" in full.read_text(encoding="utf-8")
    assert study_path.read_text(encoding="utf-8") == study
    manifest = courses._load_manifest(scan)
    assert manifest["study"] is None
    assert not courses._study_is_current(
        scan, manifest, full.read_text(encoding="utf-8"),
    )
    backups = list((courses.workspace_path(scan) / "complete_history").glob("*.md"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == complete
    assert full_pdf.read_bytes() == b"%PDF-new"


def test_original_grounding_allows_complete_without_existing_essential(
    tmp_path: Path, monkeypatch,
):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    folder = tmp_path / "SCIENZE MOTORIE" / "ANATOMIA"
    folder.mkdir(parents=True)
    source = folder / "Esofago.txt"
    source.write_text(
        "L'esofago collega la faringe allo stomaco.", encoding="utf-8",
    )
    scan = courses.scan_course(folder)
    complete = (
        "# Anatomia\n\n## Indice degli argomenti\n- Esofago\n\n---\n\n"
        "## Esofago\n\nL'esofago collega la faringe allo stomaco."
    )
    output = courses.course_output_path(scan)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(complete, encoding="utf-8")
    courses._write_manifest(
        scan, courses._snapshot(scan), output, complete,
        {"Esofago.txt": "Esofago"}, pdf_path=None, pdf_pages=0,
        summary_pipeline="summary-old",
    )
    db = CacheDB(courses.workspace_db_path(scan))
    try:
        db.set_extracted_document(
            str(source.resolve()), courses.file_sha256(source),
            "extraction-v1",
            "L'esofago collega la faringe allo stomaco.",
            pages=0, ocr_pages=0,
        )
    finally:
        db.close()
    result = courses.inspect_original_evidence(
        scan, "Esofago", edition="complete",
    )
    assert result["edition"] == "complete"
    assert result["evidence"]

def test_reference_ids_resolve_to_exact_original_passages():
    import pytest

    from bc_science.evidence_engine import OriginalEvidence
    from bc_science.targeted_review import _resolve_original_refs
    passage = OriginalEvidence('dispensa.pdf', 4, None, 'Potenziali meccanismi associati alla funzione neuronale.', 1, 'cached_page')
    result = _resolve_original_refs({'original_ids':['E1']}, [passage])
    assert result['original_quotes'][0] == {'source':'dispensa.pdf','page':4,'block':None,'quote':passage.excerpt}
    with pytest.raises(ValueError,match='non validi'):
        _resolve_original_refs({'original_ids':['E2']}, [passage])

def test_original_evidence_refreshes_when_extraction_improves_without_pdf_changes(tmp_path: Path):
    db_path, source = _create_db(tmp_path)
    with OriginalEvidenceIndex(db_path, {source:'hash-v1'}) as index:
        assert index.search('deglutizione')
    db = CacheDB(db_path)
    try:
        db.set_extracted_document(source,'hash-v1','extraction-vision',
            '--- Pagina 2 ---\nLo schema descrive la flessibilità del tratto lombosacrale.',2,1)
    finally:
        db.close()
    with OriginalEvidenceIndex(db_path, {source:'hash-v1'}) as index:
        hits=index.search('flessibilità lombosacrale')
        assert hits
        assert any('lombosacrale' in hit.excerpt for hit in hits)
        assert not any('deglutizione' in hit.excerpt for hit in hits)


def test_fast_audit_warns_when_most_chapters_need_factual_review():
    """Technical provenance PASS must not conceal pervasive unresolved facts."""
    titles = ("Cellule", "Muscoli", "Ossa", "Sistema nervoso")
    first = (
        "Il tessuto epiteliale comprende cellule vicine che rivestono "
        "e proteggono le superfici del corpo."
    )
    omitted = (
        "Il tessuto muscolare produce movimento attraverso la "
        "contrazione delle fibre muscolari specializzate."
    )
    def document(edition: str, body: str) -> str:
        toc = "\n".join(f"- {title}" for title in titles)
        chapters = "\n\n---\n\n".join(
            f"## {title}\n\n{body}" for title in titles
        )
        return f"# {edition}\n\n## Indice degli argomenti\n{toc}\n\n---\n\n{chapters}\n"

    complete = document("Completa", first + " " + omitted)
    essential = document("Essenziale", first)
    result = audit_fast_essential(complete, essential)
    assert result.chapters_total == 4
    assert result.chapters_needing_review == 4
    assert result.missing_exam_number_chapters == ()
    assert result.status == "WARN"
    assert result.content_review_status == "NON_VERIFICATO_SCIENTIFICAMENTE"


def test_fast_essential_keeps_glossary_definitions_not_just_labels():
    from bc_science.study_editions import make_fast_essential_chapter

    chapter = (
        "## Anatomia dei segmenti scheletrici\n\n"
        "### Classificazione\n"
        "- **Ossa pneumatiche**: Ossa con numerose cellette aeree interne.\n"
        "- **Ossa suturali**: Piccole ossa irregolari lungo le suture del cranio.\n"
        "- **Ossa sesamoidi**: Piccole ossa che si sviluppano nei tendini.\n"
    )
    result = make_fast_essential_chapter("Anatomia dei segmenti scheletrici", chapter)
    for essential_detail in (
        "cellette aeree interne", "suture del cranio", "sviluppano nei tendini",
    ):
        assert essential_detail in result.markdown


def test_fast_essential_preserves_named_mechanism_and_negative_condition():
    from bc_science.study_editions import make_fast_essential_chapter

    chapter = (
        "## La concentrazione di calcio\n\n"
        "### Definizioni\n"
        "Calmodulina — Proteina che lega il calcio per attivare altre proteine cellulari.\n"
        "Chinasi — Enzima che aggiunge gruppi fosforici a proteine come la miosina.\n\n"
        "### Meccanismo\n"
        "La calmodulina attiva una chinasi specifica che fosforila la miosina.\n"
        "Questo processo avviene senza che si verifichi un cambiamento elettrico "
        "sulla superficie della cellula.\n"
    )
    result = make_fast_essential_chapter("La concentrazione di calcio", chapter)
    text = result.markdown.casefold()
    assert "calmodulina" in text
    assert "chinasi" in text
    assert "fosforila la miosina" in text
    assert "senza che si verifichi un cambiamento elettrico" in text


def test_indexed_chunk_with_explicit_markers_exposes_real_page(tmp_path: Path):
    source = str(tmp_path / "La concentrazione di calcio.pdf")
    db_path = tmp_path / "workspace.db"
    db = CacheDB(db_path)
    try:
        db.replace_document(
            source, "hash-v2", "Calcio",
            [
                "--- Pagina 1 ---\nI muscoli lisci utilizzano segnali "
                "cellulari diversi dai muscoli striati.\n"
                "--- Pagina 2 ---\nLa calmodulina attiva una chinasi "
                "specifica che fosforila la miosina."
            ],
            [[1.0, 0.0, 0.0]],
        )
    finally:
        db.close()
    with OriginalEvidenceIndex(db_path, {source: "hash-v2"}) as index:
        results = index.search("calmodulina chinasi", limit=5)
        assert results
        assert results[0].page == 2
        assert "calmodulina attiva" in results[0].excerpt.casefold()
        assert results[0].provenance == "indexed_chunk"


def test_fallback_chunk_without_page_marker_never_invents_page(tmp_path: Path):
    source = str(tmp_path / "Documento.pdf")
    db_path = tmp_path / "workspace.db"
    db = CacheDB(db_path)
    try:
        db.replace_document(
            source, "hash-v1", "Ossa",
            ["Le ossa pneumatiche contengono cellette aeree interne."],
            [[1.0, 0.0, 0.0]],
        )
    finally:
        db.close()
    with OriginalEvidenceIndex(db_path, {source: "hash-v1"}) as index:
        results = index.search("ossa pneumatiche", limit=5)
        assert results and results[0].page is None
        assert results[0].block == 1


def test_glossary_detection_rejects_label_only_as_fact_coverage():
    from bc_science.quality_signals import assess_chapter

    source = (
        "## Ossa\n\n"
        "Ossa pneumatiche: Ossa con numerose cellette aeree interne.\n"
        "Ossa sesamoidi: Ossa sviluppate all'interno dei tendini.\n"
    )
    essential = (
        "## Ossa\n\n"
        "Un gruppo comprende le ossa pneumatiche e le ossa sesamoidi."
    )
    report = assess_chapter("Ossa", source, essential)
    missing = [f.quote for f in report.possible_missing]
    assert any("cellette aeree" in quote for quote in missing)
    assert any("tendini" in quote for quote in missing)
    assert report.review_priority != "nessuna"
