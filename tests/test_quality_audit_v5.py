"""Regression tests for conservative source-linked fast audit v5."""

from bc_science.fast_audit import audit_fast_essential
from bc_science.quality_signals import assess_chapter, critical_facts


def test_outline_headings_are_not_atomic_facts():
    complete = (
        "## Riflessi\n\n"
        "### Classificazione dei riflessi somatici\n"
        "Classificazione dei riflessi somatici\n"
        "Osmosi: definizione e meccanismo\n"
        "Tipi di secrezione e trasporto\n"
        "Il riflesso flessore causa il ritiro dell'arto da uno stimolo nocivo."
    )
    quotes = [fact.quote for fact in critical_facts(complete)]
    assert quotes == [
        "Il riflesso flessore causa il ritiro dell'arto da uno stimolo nocivo."
    ]


def test_glossary_label_does_not_cover_its_definition():
    original = (
        "## Ossa\n\n"
        "Ossa pneumatiche: Ossa con numerose cellette aeree interne.\n"
        "Ossa sesamoidi: Piccole ossa che si sviluppano nei tendini."
    )
    summary = (
        "## Ossa\n\n"
        "Un gruppo comprende le ossa pneumatiche e le ossa sesamoidi."
    )
    report = assess_chapter("Ossa", original, summary)
    assert len(report.possible_missing) == 2
    assert all(fact.category == "definition" for fact in report.possible_missing)


def test_negation_and_absolute_conditions_are_not_equated():
    source = (
        "## Trasporto\n\n"
        "Il flusso avviene sempre nel verso del gradiente elettrochimico.\n"
        "La cellula si contrae senza che si verifichi un cambiamento elettrico."
    )
    target = (
        "## Trasporto\n\n"
        "Il flusso avviene nel verso del gradiente elettrochimico.\n"
        "La cellula si contrae con un cambiamento elettrico."
    )
    report = assess_chapter("Trasporto", source, target)
    assert {f.category for f in report.possible_missing} == {
        "absolute_condition", "negative_condition"
    }
    assert report.review_priority in {"media", "alta"}


def test_numeric_change_requires_review():
    source = "## Tiroide\n\nIl sangue drena attraverso due o tre coppie di vene tiroidee."
    target = "## Tiroide\n\nIl sangue drena attraverso quattro coppie di vene tiroidee."
    report = assess_chapter("Tiroide", source, target)
    assert len(report.possible_missing) == 1
    assert report.possible_missing[0].category == "number_or_formula"
    assert report.numerical_mismatch


def test_ambiguous_epo_source_subject_is_reported_without_invention():
    passage = (
        "## Endocrino\n\n"
        "Terziariamente produce eritropoietina per stimolare i globuli rossi."
    )
    report = assess_chapter("Endocrino", passage, passage)
    assert report.source_context_warnings == (
        "Terziariamente produce eritropoietina per stimolare i globuli rossi.",
    )
    assert report.review_priority == "bassa"


def test_lymphatic_dead_end_fact_still_flagged_if_dropped():
    source = (
        "## Vasi linfatici\n\n"
        "A differenza dei capillari sanguigni, "
        "i capillari linfatici terminano a fondo cieco."
    )
    target = (
        "## Vasi linfatici\n\n"
        "I capillari linfatici raccolgono il liquido interstiziale."
    )
    report = assess_chapter("Vasi linfatici", source, target)
    assert len(report.possible_missing) == 1
    assert "fondo cieco" in report.possible_missing[0].quote


def test_full_omission_count_is_recorded_even_if_only_eight_quotes_shown():
    words = (
        "alfa", "beta", "gamma", "delta", "epsilon", "zeta",
        "eta", "theta", "iota", "kappa", "lambda", "mu",
    )
    statements = "\n".join(
        f"La proteina {word} attiva una specifica cascata di segnali cellulari."
        for word in words
    )
    prefix = "# Corso\n\n## Indice degli argomenti\n- Proteine\n\n---\n\n"
    source = prefix + "## Proteine\n\n" + statements
    summary = prefix + "## Proteine\n\nLe proteine partecipano a vari processi cellulari."
    audit = audit_fast_essential(source, summary)
    row = audit.chapter_signals[0]
    assert row["possible_missing_total"] == len(words)
    assert len(row["possible_missing"]) == 8
    assert row["possible_missing_by_category"]["mechanism"] == len(words)
    assert audit.content_review_status == "NON_VERIFICATO_SCIENTIFICAMENTE"
