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
