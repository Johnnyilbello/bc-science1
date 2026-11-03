"""Regression tests for V9: contextual triage, exact duplicates, grammar-only edit."""
import hashlib
import json

import pytest

import bc_science.chapter_context_review as review
from bc_science.courses import CourseScan
from bc_science.fact_repair_preview import CASES
from bc_science.summarizer import _extract_summary_chapters

KREBS_OTHER = (
    "Reazioni anaplerotiche: Reazioni che reintroducono nuovi intermedi "
    "nel ciclo quando questi vengono sottratti per altre funzioni metaboliche."
)
FOSFO_OTHER = (
    "La loro natura anfipatica permette l'organizzazione spontanea in un "
    "doppio strato: le teste polari si rivolgono verso l'acqua mentre "
    "le code apolari si nascondono all'interno della membrana."
)


def _source_bundle(tmp_path):
    course = tmp_path / "FONDAMENTI DI BIOLOGIA  E CHIMICA"
    course.mkdir()
    pdf = course / "lezione.pdf"
    pdf.write_bytes(b"not used by the mocked source-scoped evidence")
    scan = CourseScan(course.name, course, (pdf.resolve(),), 1, 0, 1)
    titles = [case.title for case in CASES] + ["la cellula e il nucleo"]
    index = "\n".join("- " + title for title in titles)
    prefix = "# Biologia\n\n## Indice degli argomenti\n" + index + "\n\n---\n\n"
    source = prefix + "\n\n---\n\n".join(
        f"## {case.title}\n\n{case.complete_anchor}" for case in CASES
    ) + "\n\n---\n\n## la cellula e il nucleo\n\nLe cellule possiedono una membrana.\n"
    essential = prefix + (
        f"## {CASES[0].title}\n\n{CASES[0].old}\n\n"
        f"{KREBS_OTHER}\n\n{KREBS_OTHER}\n\n"
        "---\n\n"
        f"## {CASES[1].title}\n\n{CASES[1].old}\n\n"
        f"{FOSFO_OTHER}\n\n"
        "---\n\n"
        "## la cellula e il nucleo\n\nLe cellule possiedono una membrana.\n"
    )
    source_dir = tmp_path / "inputs"
    source_dir.mkdir()
    complete_file = source_dir / "complete.md"
    study_file = source_dir / "essential.md"
    complete_file.write_text(source, encoding="utf-8")
    study_file.write_text(essential, encoding="utf-8")
    return scan, complete_file, study_file, source, essential


def _setup(monkeypatch):
    monkeypatch.setattr(
        review, "_validated_manifest",
        lambda *_args: (
            {"fingerprint": "stable"},
            {"lezione.pdf": CASES[0].title},
        ),
    )
    monkeypatch.setattr(review, "_fingerprint", lambda _value: "stable")
    monkeypatch.setattr(review, "_snapshot", lambda _scan: [])
    monkeypatch.setattr(
        review, "_source_fact_evidence",
        lambda _scan, _map, case: {
            "source_path": "lezione.pdf",
            "page": 8 if case == CASES[0] else 4,
            "block": None,
            "excerpt": "Passaggio restituito dalla fonte, non prova automatica.",
        },
    )


def test_contextual_review_finds_coverage_but_does_not_add_science(tmp_path, monkeypatch):
    scan, complete_file, study_file, complete, essential = _source_bundle(tmp_path)
    _setup(monkeypatch)
    initial_complete_hash = hashlib.sha256(complete_file.read_bytes()).hexdigest()
    initial_study_hash = hashlib.sha256(study_file.read_bytes()).hexdigest()
    folder = tmp_path / "v9-preview"
    review.run_whole_chapter_review(
        scan, complete_file, study_file, folder,
    )
    proposal = (
        folder / "essenziale-v9-GRAMMATICA-CANDIDATA-NON-PUBBLICATA.md"
    ).read_text(encoding="utf-8")
    expected = essential.replace("rientrano", "rientrino", 1)
    assert proposal == expected
    report = json.loads((folder / "v9-report.json").read_text(encoding="utf-8"))
    assert report["chapters_checked"] == 3
    assert report["scientific_additions"] == 0
    assert report["automatic_deletions"] == 0
    assert report["exact_duplicate_groups_total"] == 1
    assert report["excess_exact_occurrences_total"] == 1
    assert report["grammar_proposal"]["status"] == "GRAMMAR_CANDIDATE_UNVERIFIED"
    assert report["status"] == "PREVIEW_UNPUBLISHED"
    assert not report["scientifically_verified"]
    assert report["model_used"] is False
    assert all(
        case["status"] == "POSSIBLY_COVERED_ELSEWHERE_REVIEW"
        for case in report["targeted_concepts"]
    )
    assert report["targeted_concepts"][0]["supporting_study_statements"] == [
        KREBS_OTHER
    ]
    assert report["targeted_concepts"][1]["supporting_study_statements"] == [
        FOSFO_OTHER
    ]
    dossier = (folder / "v9-dossier.md").read_text(encoding="utf-8")
    assert "Reazioni anaplerotiche" in dossier
    assert "Correzione grammaticale proposta" in dossier
    assert "2× " + KREBS_OTHER in dossier
    assert hashlib.sha256(complete_file.read_bytes()).hexdigest() == initial_complete_hash
    assert hashlib.sha256(study_file.read_bytes()).hexdigest() == initial_study_hash
    assert complete_file.read_text(encoding="utf-8") == complete
    with pytest.raises(ValueError, match="Percorsi"):
        review.run_whole_chapter_review(
            scan, complete_file, study_file, folder,
        )


def test_contextual_coverage_is_not_triggered_by_an_isolated_concept_word():
    case = CASES[0]
    chapter = (
        f"## {case.title}\n\n{case.old}\n\n"
        "Il docente menziona le reazioni anaplerotiche ma non le descrive.\n"
    )
    result = review._coverage_in_other_statements(
        chapter, kind="krebs", target=case.old,
    )
    assert not result
    pho = (
        f"## {CASES[1].title}\n\n"
        "Le teste polari e le code apolari sono importanti per la biologia.\n"
    )
    assert review._coverage_in_other_statements(
        pho, kind="fosfolipidi", target=CASES[1].old,
    ) == []


def test_duplicate_check_does_not_equate_paraphrases_or_short_fragments():
    body = (
        "## Argomento\n\n"
        "Il composto può reagire soltanto in una precisa direzione durante "
        "la trasformazione metabolica.\n\n"
        "Il composto non può reagire in una precisa direzione durante "
        "la trasformazione metabolica.\n\n"
        "Il composto può reagire soltanto in una precisa direzione durante "
        "la trasformazione metabolica.\n\n"
        "Acqua.\n\nAcqua.\n"
    )
    results = review._exact_duplicates(body)
    assert results["duplicate_groups_total"] == 1
    assert results["excess_exact_occurrences_total"] == 1
    assert results["examples"][0]["occurrences"] == 2


def test_grammar_preview_never_changes_other_chapters(tmp_path):
    _scan, _f, _s, complete, essential = _source_bundle(tmp_path)
    original_chapters = _extract_summary_chapters(essential)
    chapter = dict(original_chapters)[CASES[0].title]
    result, status = review._grammar_preview(essential, complete, chapter)
    assert status["status"] == "GRAMMAR_CANDIDATE_UNVERIFIED"
    changed = _extract_summary_chapters(result)
    for (title, before), (_, after) in zip(
        original_chapters, changed, strict=True,
    ):
        if title != CASES[0].title:
            assert before == after
    assert result.replace("rientrino", "rientrano", 1) == essential


def test_grammar_preview_blocks_unexpected_scientific_condition(tmp_path):
    _scan, _f, _s, complete, essential = _source_bundle(tmp_path)
    wrong = essential.replace("rientrano", "non rientrano", 1)
    chapter = dict(_extract_summary_chapters(wrong))[CASES[0].title]
    proposed, status = review._grammar_preview(wrong, complete, chapter)
    assert proposed == wrong
    assert status["status"] == "BLOCKED_ESSENTIAL_ANCHOR_NOT_UNIQUE"


def test_no_scientific_source_is_never_fabricated(tmp_path, monkeypatch):
    scan, complete_file, study_file, _complete, _study = _source_bundle(tmp_path)
    _setup(monkeypatch)
    monkeypatch.setattr(review, "_source_fact_evidence", lambda *_args: None)
    folder = tmp_path / "review"
    review.run_whole_chapter_review(
        scan, complete_file, study_file, folder,
    )
    report = json.loads((folder / "v9-report.json").read_text(encoding="utf-8"))
    assert all(
        item["mapped_original_context"] is None
        for item in report["targeted_concepts"]
    )
    assert report["scientific_additions"] == 0


def test_preview_refuses_folder_inside_original_course(tmp_path, monkeypatch):
    scan, complete_file, study_file, _complete, _study = _source_bundle(tmp_path)
    _setup(monkeypatch)
    with pytest.raises(ValueError, match="Percorsi"):
        review.run_whole_chapter_review(
            scan, complete_file, study_file, scan.path / "new-output",
        )
