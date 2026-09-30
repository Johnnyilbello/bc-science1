from bc_science.summarizer import _dedupe_exact_blocks, _extract_summary_chapters


def test_dedupe_exact_blocks_keeps_first_occurrence():
    text = """## Capitolo

Blocco unico.

### Da ricordare
- Punto A

### Da ricordare
- Punto A

Blocco finale.
"""
    cleaned = _dedupe_exact_blocks(text)
    assert cleaned.count("### Da ricordare") == 1
    assert cleaned.count("- Punto A") == 1
    assert "Blocco unico." in cleaned
    assert "Blocco finale." in cleaned


def test_extract_summary_chapters_uses_deterministic_index():
    text = """# Corso

## Indice degli argomenti
- Primo
- Secondo

---

## Primo

Testo primo.

---

## Secondo

Testo secondo.
"""
    chapters = _extract_summary_chapters(text)
    assert [title for title, _ in chapters] == ["Primo", "Secondo"]
    assert "Testo primo." in chapters[0][1]
    assert "Testo secondo." in chapters[1][1]
