from bc_science.course_audit import _fact_category


def test_source_uncertainty_is_critical_not_a_secondary_fact():
    text='Potenziali meccanismi che potrebbero essere alla base dell’associazione tra attività fisica e declino cognitivo.'
    category, weight = _fact_category(text, exam=False)
    assert category == 'qualificatore'
    assert weight == 3
    assert _fact_category('Il documento illustra il sistema nervoso.',exam=False) == ('fatto',1)
