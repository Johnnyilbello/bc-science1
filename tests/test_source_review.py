from bc_science.summarizer import Summarizer


def test_source_review_adds_only_missing_material():
    summarizer = object.__new__(Summarizer)
    summarizer.model = "test"
    prompts = []
    replies = iter(['["F = m × a"]', "[]"])

    def chat(key, system, prompt, **kwargs):
        prompts.append(prompt)
        return next(replies)

    summarizer._cached_chat = chat
    result = summarizer._review_source_chunks(
        "Forza", "## Forza\nLa forza accelera un corpo.",
        ["F = m × a", "Altra fonte"], "system",
    )
    assert "F = m × a" in result
    assert "NESSUNA" not in result
    assert "F = m × a" in prompts[0]
    assert "F = m × a" in prompts[1]

def test_essential_mode_builds_review_from_each_complete_chapter():
    summarizer = object.__new__(Summarizer)
    summarizer.model = "test"
    prompts = []
    def chat(key, system, prompt, **kwargs):
        prompts.append(prompt)
        return "## Forza\n- La forza può accelerare un corpo."
    summarizer._cached_chat = chat
    source = "# Corso\n\n## Indice degli argomenti\n- Forza\n\n---\n\n## Forza\nLa forza può accelerare un corpo.\n"
    result = summarizer.build_study_summary(source, "Ripasso")
    assert "Modalità essenziale" in result
    assert "## Forza" in result
    assert "per chi lo ha gia studiato" in prompts[0]
    assert "La forza può accelerare" in prompts[0]

def test_source_review_discloses_unverifiable_quote_without_inserting_it():
    summarizer = object.__new__(Summarizer)
    summarizer.model = "test"
    calls = []
    def chat(*args, **kwargs):
        calls.append(args)
        return '["Fatto inventato"]'
    summarizer._cached_chat = chat
    messages = []
    result = summarizer._review_source_chunks(
        "Forza", "## Forza", ["F = m × a"], "system", progress=messages.append,
    )
    assert "Fatto inventato" not in result
    assert "Verifica materiale: controllo omissioni automatico incompleto" in result
    assert len(calls) == 2
    assert any("non verificabile" in item for item in messages)


def test_source_review_retries_quote_and_accepts_only_literal_quote():
    summarizer = object.__new__(Summarizer)
    summarizer.model = "test"
    replies = iter(['["La forza dipende da massa"]', '["F = m × a"]'])
    summarizer._cached_chat = lambda *args, **kwargs: next(replies)
    result = summarizer._review_source_chunks(
        "Forza", "## Forza", ["F = m × a"], "system",
    )
    assert "F = m × a" in result
    assert "Verifica materiale" not in result
    assert "La forza dipende da massa" not in result


def test_source_review_invalid_json_does_not_halt_generation():
    summarizer = object.__new__(Summarizer)
    summarizer.model = "test"
    summarizer._cached_chat = lambda *args, **kwargs: "non JSON"
    result = summarizer._review_source_chunks(
        "Forza", "## Forza", ["F = m × a"], "system",
    )
    assert result.startswith("## Forza")
    assert "Verifica materiale" in result


def test_source_review_skips_duplicate_original_blocks():
    summarizer = object.__new__(Summarizer)
    summarizer.model = "test"
    calls = []

    def chat(key, system, prompt, **kwargs):
        calls.append(prompt)
        return "[]"

    summarizer._cached_chat = chat
    source = "La forza netta puo modificare il moto di un corpo."
    draft = "## Forza\nSpiegazione introduttiva da completare."
    result = summarizer._review_source_chunks("Forza", draft, [source, source], "system")
    assert result == draft
    assert len(calls) == 1


def test_source_review_skips_chunk_already_present_verbatim():
    summarizer = object.__new__(Summarizer)
    summarizer.model = "test"
    calls = []
    summarizer._cached_chat = lambda *args, **kwargs: calls.append(args) or "[]"
    source = "La forza netta puo modificare il moto di un corpo."
    draft = "## Forza\n" + source
    result = summarizer._review_source_chunks("Forza", draft, [source], "system")
    assert result == draft
    assert calls == []

def test_source_lists_are_restored_even_when_model_reports_no_omissions():
    from bc_science.summarizer import Summarizer
    summarizer = object.__new__(Summarizer)
    summarizer.model = 'test'
    summarizer._cached_chat = lambda *args, **kwargs: '[]'
    source = [
        'I neuroni sensitivi raccolgono stimoli luminosi, termici, pressori e chimici.',
        'Gli interneuroni elaborano, immagazzinano e rilasciano informazioni.',
    ]
    result = summarizer._review_source_chunks('Neuroni',
        '## Neuroni\nI neuroni raccolgono luce, calore e pressione. Gli interneuroni elaborano e rilasciano informazioni.', source, 'system')
    assert source[0] in result
    assert source[1] in result


def test_recovered_facts_belong_with_related_explanation_without_course_headers():
    from bc_science.summarizer import _integrate_source_additions, _source_review_units
    original = "--- DISPENSA: Neuroni ---\n--- Pagina 1 ---\nCorso di Laurea: Scienze Motorie\nInsegnamento: Anatomia\n\nGli interneuroni immagazzinano informazioni."
    units = _source_review_units([original])
    assert units == ["Gli interneuroni immagazzinano informazioni."]
    draft = "## Neuroni\n\nGli interneuroni elaborano informazioni.\n\nI neuroni motori raggiungono i muscoli."
    result = _integrate_source_additions(draft, units)
    assert result.index(units[0]) < result.index("I neuroni motori")
    assert "Dettagli recuperati" not in result
    assert "Corso di Laurea" not in result


def test_single_file_summary_recovers_formula_lost_during_merge(tmp_path, monkeypatch):
    from bc_science.config import AppConfig
    summarizer = Summarizer(AppConfig(), db_path=tmp_path / "cache.db")
    replies = iter(["La forza modifica il moto.", "## Forza\n\nLa forza modifica il moto.", '["F = m × a"]'])
    monkeypatch.setattr(summarizer, "_cached_chat", lambda *a, **k: next(replies))
    try:
        result = summarizer.summarize_text("F = m × a", "Forza")
        assert "F = m × a" in result
    finally:
        summarizer.close()


def test_pdf_layout_breaks_cannot_split_members_of_a_source_classification():
    from bc_science.summarizer import _source_review_units
    source = "I neuroni sensitivi raccolgono stimoli luminosi, termici,\n\npressori e chimici."
    units = _source_review_units([source])
    assert units == ["I neuroni sensitivi raccolgono stimoli luminosi, termici, pressori e chimici."]
    summarizer = object.__new__(Summarizer)
    summarizer.model = "test"
    summarizer._cached_chat = lambda *a, **k: "[]"
    result = summarizer._review_source_chunks("Neuroni", "## Neuroni\nI neuroni raccolgono luce, calore e pressione.", [source], "system")
    assert "chimici" in result


def test_technical_aliases_are_restored_inline_without_repeating_definitions():
    from bc_science.summarizer import _restore_source_aliases
    source = ["Interneuroni (neuroni associativi) risiedono nel SNC.", "Neuroni motori (efferenti) inviano segnali ai muscoli."]
    draft = "## Interneuroni\n\nGli interneuroni risiedono nel SNC.\n\nI neuroni motori raggiungono i muscoli."
    result = _restore_source_aliases(draft, source)
    assert result.startswith("## Interneuroni\n")
    assert "Gli interneuroni (neuroni associativi)" in result
    assert "I neuroni motori (efferenti)" in result
    assert result.count("risiedono") == 1
    assert _restore_source_aliases(result, source) == result


def test_parenthetical_conditions_are_not_promoted_to_term_aliases():
    from bc_science.summarizer import _restore_source_aliases
    draft = "## Argomento\n\nLe cellule sono presenti."
    assert _restore_source_aliases(draft, ["Le cellule (non sempre) sono presenti."]) == draft
