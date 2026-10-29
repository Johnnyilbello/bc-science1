"""Regression for original-source grounding, evidence identity and safe review."""
import json
from pathlib import Path

from bc_science.cache import CacheDB
from bc_science.evidence_engine import (
    OriginalEvidence,
    OriginalEvidenceIndex,
    validate_original_quote,
)
from bc_science.fast_audit import audit_fast_essential
from bc_science.structural_checks import find_structural_issues
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
            "Il scheletro assile costituisce la parte centrale "
            "del corpo e comprende il cranio."
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
