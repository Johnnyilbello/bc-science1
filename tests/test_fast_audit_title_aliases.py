"""Regression: trusted editorial typo pairs, not fuzzy chapter matching."""
import json

from bc_science.fast_audit import audit_fast_essential, write_fast_audit


def _course(chapters):
    index = "".join(f"- {title}\n" for title, _ in chapters)
    blocks = "\n---\n\n".join(
        f"## {title}\n\n{body}" for title, body in chapters
    )
    return "# Biologia\n\n## Indice degli argomenti\n" + index + "\n---\n\n" + blocks


def test_observed_italian_spelling_variants_are_reported_not_missing():
    original = _course([
        ("la celulla e il nuucleo", "La cellula possiede un nucleo."),
        ("Fosforilazione ossidativa", "L'ATP sintetasi sfrutta il flusso protonico."),
        ("Le mebrane cellulari", "Le membrane separano compartimenti."),
    ])
    essential = _course([
        ("la cellula e il nucleo", "La cellula possiede un nucleo."),
        ("Fosforilazione ossidativa", "L'ATP sintetasi sfrutta il flusso protonico."),
        ("Le membrane cellulari", "Le membrane separano compartimenti."),
    ])
    report = audit_fast_essential(original, essential)
    assert report.status == "WARN"
    assert report.chapters_total == report.chapters_present == 3
    assert len(report.chapter_signals) == 3
    assert report.title_spelling_variants == (
        ("la celulla e il nuucleo", "la cellula e il nucleo"),
        ("Le mebrane cellulari", "Le membrane cellulari"),
    )
    assert not any("non corrispondono" in note for note in report.warnings)


def test_ambiguous_canonical_title_collision_must_fail():
    body = "La cellula contiene informazioni genetiche."
    text = _course([
        ("la celulla e il nuucleo", body),
        ("la cellula e il nucleo", body),
    ])
    report = audit_fast_essential(text, text)
    assert report.status == "FAIL"
    assert not report.title_spelling_variants


def test_other_chapter_even_with_similar_words_must_fail():
    complete = _course([
        ("Le membrane cellulari", "Membrane biologiche."),
    ])
    study = _course([
        ("Le membrane vascolari", "Membrane biologiche."),
    ])
    report = audit_fast_essential(complete, study)
    assert report.status == "FAIL"


def test_reordered_chapters_are_never_treated_as_aliases():
    complete = _course([
        ("la celulla e il nuucleo", "Nucleo."),
        ("Le mebrane cellulari", "Membrane."),
    ])
    study = _course([
        ("Le membrane cellulari", "Membrane."),
        ("la cellula e il nucleo", "Nucleo."),
    ])
    report = audit_fast_essential(complete, study)
    assert report.status == "FAIL"


def test_negative_compression_is_signed_and_warned():
    complete = _course([
        ("Apparato", "Il corpo umano comprende molti sistemi."),
    ])
    study = _course([
        ("Apparato", "Il corpo umano comprende molti sistemi. " * 8),
    ])
    report = audit_fast_essential(complete, study)
    assert report.compression_percent < 0
    assert report.study_words > report.complete_words
    assert report.status != "PASS"
    assert any("più parole" in warning for warning in report.warnings)


def test_report_json_and_markdown_preserve_spelling_and_signed_percent(tmp_path):
    complete = _course([
        ("Le mebrane cellulari", "Le membrane sono presenti nelle cellule."),
    ])
    study = _course([
        ("Le membrane cellulari", "Le membrane sono presenti nelle cellule. " * 5),
    ])
    audit = audit_fast_essential(complete, study)
    markdown, metadata = write_fast_audit(audit, tmp_path)
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    assert payload["compression_percent"] < 0
    assert payload["title_spelling_variants"] == [
        ["Le mebrane cellulari", "Le membrane cellulari"]
    ]
    view = markdown.read_text(encoding="utf-8")
    assert "Le mebrane cellulari" in view
    assert "Le membrane cellulari" in view
    assert "Compressione effettiva" in view



def test_expansion_diagnostics_are_per_chapter_and_keep_real_repetitions(tmp_path):
    from bc_science.structural_checks import find_structural_issues

    fact = (
        "La cellula è l'unità fondamentale della vita e tutte le cellule "
        "derivano da altre cellule secondo la teoria cellulare."
    )
    complete = _course([
        ("Cellula", fact),
        ("Membrana", "La membrana separa i compartimenti cellulari e regola gli scambi."),
    ])
    essential = _course([
        ("Cellula", fact + "\n\n" + fact),
        ("Membrana", "La membrana regola gli scambi."),
    ])
    audit = audit_fast_essential(complete, essential)
    assert len(audit.expanded_chapters) == 1
    assert audit.expanded_chapters[0]["title"] == "Cellula"
    assert audit.expanded_chapters[0]["extra_words"] > 0
    chapter = audit.chapter_signals[0]
    assert chapter["word_delta"] > 0
    assert audit.chapter_signals[1]["word_delta"] < 0
    assert any(
        issue.category == "repetition"
        for issue in find_structural_issues(
            "## Cellula\n\n" + fact + "\n\n" + fact
        )
    )
    markdown, metadata = write_fast_audit(audit, tmp_path)
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    assert payload["expanded_chapters"][0]["title"] == "Cellula"
    assert "Capitoli che allungano l'Essenziale" in markdown.read_text(
        encoding="utf-8"
    )
