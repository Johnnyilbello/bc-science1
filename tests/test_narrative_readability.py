from bc_science.clarity import audit_narrative_document


def document(body):
    return '# Corso\n\n> Modalità essenziale: ripasso.\n\n## Indice degli argomenti\n- Neuroni\n\n---\n\n## Neuroni\n' + body


def test_narrative_readability_does_not_require_repeated_sections():
    passed, rows = audit_narrative_document(document('I neuroni trasmettono segnali. Gli interneuroni collegano le vie nervose.'))
    assert passed
    assert rows[0][1].metrics.score == 100


def test_narrative_readability_still_rejects_dense_sentences():
    passed, rows = audit_narrative_document(document(' '.join(['neuroni'] * 80) + '.'))
    assert not passed
    assert any('lunghe' in issue or '30 parole' in issue for issue in rows[0][1].issues)

def test_narrative_layout_splits_dense_text_without_dropping_words():
    from bc_science.study_editions import readable_complete_chapter
    body = ('I neuroni raccolgono stimoli. Gli stimoli possono essere luminosi. '
            'I neuroni trasmettono segnali. Gli interneuroni ricevono segnali. '
            'Gli interneuroni elaborano informazioni. I neuroni motori inviano segnali.')
    original = '## Neuroni\n\n' + body
    result = readable_complete_chapter('Neuroni', original)
    assert result.split() == original.split()
    assert result.count('\n\n') >= 2
