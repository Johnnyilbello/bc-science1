"""Evidence-driven grammar fixes from the user's 80-page Anatomia study PDF.

These tests focus on language and meaning-risk controls, not science truth.
"""
from bc_science.fast_audit import audit_fast_essential
from bc_science.italian_proofreading import (
    conservative_italian_fixes,
    grammar_findings,
)
from bc_science.study_editions import make_fast_essential_chapter
from bc_science.targeted_review import _validate_proposal, review_chapter


def test_obvious_italian_article_and_infinitive_are_fixed():
    original = (
        "Il scheletro assile forma la struttura portante. "
        "L'articolazione permette di piegare e dritto il braccio. "
        "La cavità orale è la prima stanza dell'apparato digerente."
    )
    cleaned, count = conservative_italian_fixes(original)
    assert count == 3
    assert "Lo scheletro assile" in cleaned
    assert "piegare e distendere il braccio" in cleaned
    assert "la prima parte dell'apparato digerente" in cleaned
    assert not grammar_findings(cleaned)


def test_meaning_risk_is_never_silently_rewritten():
    bad = "Esofago — tubo muscolare che trasporta il cibo dallo stomaco allo stomaco."
    cleaned, count = conservative_italian_fixes(bad)
    assert cleaned == bad
    assert count == 0
    flags = grammar_findings(bad)
    assert any(item.category == "meaning" for item in flags)
    assert any(item.needs_source_check for item in flags)


def test_fast_summary_retains_source_order_with_concept_priority():
    source = (
        "## Stomaco\n\n"
        "### In parole semplici\nLo stomaco è un organo della digestione.\n"
        "### Spiegazione ordinata\n"
        "Per prima cosa il cibo arriva allo stomaco. "
        "Poi i muscoli permettono il mescolamento del contenuto. "
        "Infine il chimo procede nell'intestino.\n"
        "### Da ricordare per l'esame\n"
        "Lo stomaco è formato da 3 strati muscolari.\n"
    )
    output = make_fast_essential_chapter("Stomaco", source).markdown
    assert "3 strati muscolari" in output
    if "Per prima cosa" in output and "Infine" in output:
        assert output.index("Per prima cosa") < output.index("Infine")


def test_fast_summary_repairs_italian_without_dropping_exam_number():
    source = (
        "## Ossa\n"
        "### In parole semplici\n"
        "Il scheletro contiene 206 ossa nell'adulto. "
        "Lo scheletro assile costituisce il centro del corpo.\n"
        "### Da ricordare per l'esame\n"
        "Lo scheletro ha 206 ossa nell'adulto.\n"
    )
    output = make_fast_essential_chapter("Ossa", source).markdown
    assert "Il scheletro" not in output
    assert "lo scheletro" in output.casefold()
    assert "206" in output


def test_audit_separates_grammar_from_technical_and_scientific_results():
    complete = (
        "# Anatomia\n\n## Indice degli argomenti\n- Esofago\n\n---\n\n"
        "## Esofago\n\nIl tubo trasporta il cibo dallo stomaco allo stomaco."
    )
    study = (
        "# Anatomia essenziale\n\n## Indice degli argomenti\n- Esofago\n"
        "\n---\n\n## Esofago\n\nIl tubo trasporta il cibo dallo stomaco allo stomaco."
    )
    report = audit_fast_essential(complete, study)
    assert report.status in {"PASS", "WARN"}
    assert report.linguistic_status == "DA_RIVEDERE"
    assert report.chapters_with_language_issues == 1
    assert report.content_review_status == "NON_VERIFICATO_SCIENTIFICAMENTE"
    flags = report.chapter_signals[0]["language_findings"]
    assert any(flag["category"] == "meaning" for flag in flags)



def test_qwen_reviewer_handles_only_grammar_flags_without_mandatory_rewrite():
    source = (
        "## Ossatura\n\nIl scheletro assile comprende gli elementi che "
        "sostengono il tronco e la testa."
    )

    class FakeModel:
        model = "qwen3.5:4b"
        calls = 0

        def _cached_chat(self, *args, **kwargs):
            self.calls += 1
            return (
                '{"rewrite":"## Ossatura\\n\\nLo scheletro assile comprende '
                'gli elementi che sostengono il tronco e la testa.",'
                '"evidence":["Il scheletro assile comprende gli elementi '
                'che sostengono il tronco e la testa."]}'
            )

    model = FakeModel()
    result = review_chapter(
        "Ossatura", source, source, None, summarizer=model,
    )
    assert model.calls == 1
    assert result.applied
    assert "Lo scheletro" in result.text


def test_qwen_must_not_invent_anatomical_direction_even_with_source_quote():
    source = (
        "## Esofago\n\nEsofago — tubo muscolare che trasporta "
        "il cibo dallo stomaco allo stomaco."
    )
    proposal = {
        "rewrite": (
            "## Esofago\n\nEsofago — tubo muscolare che trasporta "
            "il cibo dalla faringe allo stomaco."
        ),
        "evidence": [
            ("Esofago — tubo muscolare che trasporta "
             "il cibo dallo stomaco allo stomaco.")
        ],
    }
    ok, why = _validate_proposal("Esofago", source, source, proposal)
    assert not ok
    assert "dispensa originale" in why



def test_markdown_subheadings_are_not_flagged_as_fragments():
    # A separator or blank line is not required before a valid heading.
    chapter = (
        "## Cellula\n\n"
        "La cellula contiene membrane e un nucleo organizzato.\n"
        "### Concetti chiave\n"
        "### Da ricordare per l'esame\n\n"
        "Le membrane regolano gli scambi tra interno ed esterno.\n"
        "### Parole chiave\n"
        "### Spiegazione ordinata"
    )
    findings = grammar_findings(chapter)
    assert not any(issue.category == "fragment" for issue in findings)


def test_real_unfinished_sentence_before_heading_still_flagged():
    chapter = (
        "## Calcio\n\n"
        "La calmodulina attiva una\n"
        "### Concetti chiave\n"
    )
    findings = grammar_findings(chapter)
    assert any(
        issue.category == "fragment"
        and "La calmodulina attiva una" in issue.quote
        for issue in findings
    )
    assert not any("Concetti chiave" in issue.quote for issue in findings)


def test_repeated_scientific_sentence_is_still_reported():
    from bc_science.structural_checks import find_structural_issues

    sentence = (
        "La cellula è l'unità fondamentale della vita e tutte le "
        "cellule derivano da altre cellule secondo la teoria cellulare."
    )
    chapter = (
        "## Cellula\n\n" + sentence
        + "\n\n### Concetti chiave\n\n" + sentence
    )
    issues = find_structural_issues(chapter)
    assert any(issue.category == "repetition" for issue in issues)
    assert not any(
        issue.category == "fragment" and issue.quote.startswith("### ")
        for issue in issues
    )


def test_fast_audit_no_longer_counts_valid_subheadings_as_language_issues():
    prefix = (
        "# Biologia\n\n## Indice degli argomenti\n"
        "- Cellula\n\n---\n\n"
    )
    complete = prefix + "## Cellula\n\nLa cellula contiene un nucleo."
    essential = (
        prefix
        + "## Cellula\n\nLa cellula contiene un nucleo.\n"
        + "### Concetti chiave\n"
        + "### Da ricordare per l'esame"
    )
    audit = audit_fast_essential(complete, essential)
    assert not audit.chapter_signals[0]["language_findings"]
    assert not any(
        issue["category"] == "fragment"
        for issue in audit.chapter_signals[0]["structural_findings"]
    )
