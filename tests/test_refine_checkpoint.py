from bc_science.summarizer import (
    _load_refine_checkpoint,
    _save_refine_checkpoint,
)


def test_refine_checkpoint_restores_completed_prefix(tmp_path):
    path = tmp_path / "refine.checkpoint.json"
    completed = [
        ("Primo", "## Primo\n\n### In parole semplici\nTesto.\n\n### Da ricordare per l'esame\n- Punto."),
        ("Secondo", "## Secondo\n\n### In parole semplici\nTesto.\n\n### Da ricordare per l'esame\n- Punto."),
    ]

    _save_refine_checkpoint(path, signature="sig-1", completed=completed)

    restored = _load_refine_checkpoint(
        path,
        signature="sig-1",
        expected_titles=["Primo", "Secondo", "Terzo"],
    )

    assert restored == completed


def test_refine_checkpoint_is_ignored_when_source_signature_changes(tmp_path):
    path = tmp_path / "refine.checkpoint.json"
    _save_refine_checkpoint(
        path,
        signature="old-source",
        completed=[("Primo", "capitolo")],
    )

    restored = _load_refine_checkpoint(
        path,
        signature="new-source",
        expected_titles=["Primo"],
    )

    assert restored == []


def test_refine_checkpoint_only_restores_matching_prefix(tmp_path):
    path = tmp_path / "refine.checkpoint.json"
    _save_refine_checkpoint(
        path,
        signature="sig",
        completed=[
            ("Primo", "capitolo 1"),
            ("Titolo diverso", "capitolo 2"),
            ("Terzo", "capitolo 3"),
        ],
    )

    restored = _load_refine_checkpoint(
        path,
        signature="sig",
        expected_titles=["Primo", "Secondo", "Terzo"],
    )

    assert restored == [("Primo", "capitolo 1")]


def test_refine_checkpoint_ignores_invalid_json(tmp_path):
    path = tmp_path / "refine.checkpoint.json"
    path.write_text("{non-json", encoding="utf-8")

    restored = _load_refine_checkpoint(
        path,
        signature="sig",
        expected_titles=["Primo"],
    )

    assert restored == []
