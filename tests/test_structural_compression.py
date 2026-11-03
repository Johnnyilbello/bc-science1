"""V10 negative tests: unsafe input, ungrounded edits, out-of-scope bytes."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import bc_science.structural_compression as v10


def test_sentence_inventory_keeps_formula_conditions_and_citations():
    source = ("## Capitolo\n### Definizioni\n"
              "1. **Formula:** $(CH_2O)_n$ (Fonte p.2).\n"
              "Se mancano intermedi il ciclo può rallentare. Non è sempre così.\n")
    units = v10.inventory(source, first_line=10)
    assert units[0].line == 12
    assert units[0].text == "Formula: $(CH_2O)_n$ (Fonte p.2)."
    assert [u.text for u in units[1:]] == [
        "Se mancano intermedi il ciclo può rallentare.", "Non è sempre così.",
    ]


def test_literal_baseline_does_not_merge_different_negations_or_antecedents():
    source = ("## C\nIl ciclo produce 3 NADH.\nIl ciclo non produce 3 NADH.\n"
              "Questo è reversibile.\nQuesto è reversibile.\nIl ciclo produce 3 NADH.\n")
    units = v10.inventory(source)
    body, records = v10.synthesize("C", units)
    assert body.count("Questo è reversibile.") == 2
    assert "Il ciclo non produce 3 NADH." in body
    assert sum(r["action"] == "EXACT_SENTENCE_COALESCED" for r in records) == 1


@pytest.mark.parametrize("before,after", [
    ("3 NADH", "4 NADH"), ("non", ""), ("può", "deve"),
    ("$(CH_2O)_n$", "$(CH_3O)_n$"), ("solo", "sempre"),
])
def test_literal_gate_rejects_changed_number_formula_negation_scope(before, after):
    source = "## C\nIl ciclo può produrre 3 NADH, non sempre, solo se attivo.\n$(CH_2O)_n$\n"
    units = v10.inventory(source)
    body, records = v10.synthesize("C", units)
    with pytest.raises(ValueError, match="non verificati"):
        v10.validate_synthesis("C", units, body.replace(before, after), records)


def bundle(tmp_path, monkeypatch, *, newline="\n", bom=b""):
    course = tmp_path / "course"
    course.mkdir()
    source_dir = tmp_path / "input"
    source_dir.mkdir()
    titles = [*v10.TITLES, "Altro"]
    prefix = "# Materia\n\n## Indice degli argomenti\n" + "\n".join(
        "- " + t for t in titles
    ) + "\n\n---\n\n"
    source_body = (
        "Il ciclo può rallentare se mancano intermedi, non sempre. "
        "Produce 3 NADH e solo 1 GTP. La formula $(CH_2O)_n$ resta invariata. "
        "Il ciclo può rallentare se mancano intermedi, non sempre. "
        "Produce 3 NADH e solo 1 GTP. La formula $(CH_2O)_n$ resta invariata."
    )
    text = prefix + "\n\n---\n\n".join(f"## {t}\n\n{source_body}" for t in titles)
    complete, essential, v9 = [source_dir / n for n in ("c.md", "e.md", "v9.json")]
    payload = bom + text.replace("\n", newline).encode("utf-8")
    complete.write_bytes(payload)
    essential.write_bytes(payload)
    v9.write_text(json.dumps({
        "version": v10.V9_VERSION, "complete_sha256": v10.sha(payload),
        "essential_sha256": v10.sha(payload), "source_fingerprint": "fixed",
    }), encoding="utf-8")
    manifest = {"fingerprint": "fixed", "files": []}
    monkeypatch.setattr(v10, "_validated_manifest", lambda *_: (manifest, {}))
    monkeypatch.setattr(v10, "_snapshot", lambda *_: [])
    monkeypatch.setattr(v10, "_fingerprint", lambda *_: "fixed")
    monkeypatch.setattr(v10, "_original_evidence", lambda *_: [])
    monkeypatch.setattr(v10, "audit_fast_essential", lambda *_: SimpleNamespace(status="WARN"))

    def authored(title, source, units):
        body = f"## {title}\n\n" + source_body[:source_body.index(" Il ciclo", 1)]
        return body, [{**v10.asdict(u), "action": "TEST_MAPPING", "destinations": [],
                       "editorial_rationale": None} for u in units]

    monkeypatch.setattr(v10, "authored_synthesis", authored)
    return SimpleNamespace(path=course), complete, essential, v9, tmp_path / "out"


def test_preview_restores_all_unaffected_bytes_and_artifact_hashes(tmp_path, monkeypatch):
    args = bundle(tmp_path, monkeypatch, newline="\r\n", bom=b"\xef\xbb\xbf")
    hashes = [p.read_bytes() for p in args[1:4]]
    folder = v10.run_structural_compression(*args)
    report = json.loads((folder / "v10-report.json").read_text(encoding="utf-8"))
    assert report["publication_allowed"] is False
    assert report["scientifically_verified"] is False
    assert report["unchanged_chapters"] == 1
    assert all(c["unmapped_source_units"] == 0 for c in report["chapters"])
    candidate = (folder / "essenziale-v10-CANDIDATA-NON-PUBBLICATA.md").read_bytes()
    assert candidate.startswith(b"\xef\xbb\xbf")
    assert b"\n" not in candidate.replace(b"\r\n", b"")
    source = args[2].read_bytes()
    assert source[source.index(b"## Altro"):] == candidate[candidate.index(b"## Altro"):]
    assert [p.read_bytes() for p in args[1:4]] == hashes
    for name, digest in json.loads((folder / "SHA256SUMS.json").read_text()).items():
        assert v10.sha((folder / name).read_bytes()) == digest


@pytest.mark.parametrize("kind", ["hash", "version", "fingerprint", "nested", "existing"])
def test_bad_inputs_fail_before_any_output(tmp_path, monkeypatch, kind):
    args = list(bundle(tmp_path, monkeypatch))
    if kind in {"hash", "version", "fingerprint"}:
        data = json.loads(args[3].read_text())
        key = {"hash": "complete_sha256", "version": "version",
               "fingerprint": "source_fingerprint"}[kind]
        data[key] = "wrong"
        args[3].write_text(json.dumps(data))
    elif kind == "nested":
        args[4] = args[0].path / "out"
    else:
        args[4].mkdir()
    with pytest.raises(ValueError):
        v10.run_structural_compression(*args)
    assert not list(args[4].glob("*"))


def test_new_audit_failure_blocks_candidate(tmp_path, monkeypatch):
    args = bundle(tmp_path, monkeypatch)
    statuses = iter(["WARN", "FAIL"])
    monkeypatch.setattr(v10, "audit_fast_essential",
                        lambda *_: SimpleNamespace(status=next(statuses)))
    with pytest.raises(ValueError, match="Nuovo FAIL"):
        v10.run_structural_compression(*args)
    assert not args[4].exists()


def test_original_change_mid_run_blocks_candidate(tmp_path, monkeypatch):
    args = bundle(tmp_path, monkeypatch)
    def changed(*_):
        args[1].write_bytes(b"changed")
        return "fixed"
    monkeypatch.setattr(v10, "_fingerprint", changed)
    with pytest.raises(ValueError, match="cambiati"):
        v10.run_structural_compression(*args)
    assert not args[4].exists()


def test_duplicate_or_unindexed_chapter_is_rejected():
    text = "## Indice degli argomenti\n- C\n\n---\n\n## C\nTesto\n\n---\n\n## C\nAltro"
    with pytest.raises(ValueError, match="ambigui"):
        v10._spans(text)


def test_real_pilot_plan_is_pinned_and_rejects_other_source():
    payload = Path(v10.__file__).with_name("structural_compression_pilot.json").read_bytes()
    assert v10.sha(payload) == v10.PLAN_SHA256
    plans = json.loads(payload)
    assert [p["title"] for p in plans] == list(v10.TITLES)
    for p in plans:
        with pytest.raises(ValueError, match="diverso"):
            v10.authored_synthesis(p["title"], "different source", [])


def test_authored_gate_rejects_mutated_pinned_output(tmp_path, monkeypatch):
    bundle(tmp_path, monkeypatch)
    title = v10.TITLES[0]
    body, records = v10.authored_synthesis(title, "unused", [])
    for change in (body.replace("3 NADH", "4 NADH"), body.replace("non", ""),
                   body + "\nUn'aggiunta non supportata."):
        with pytest.raises(ValueError, match="piano"):
            v10.validate_authored_candidate(title, "unused", [], change, records)
    changed = copy.deepcopy(records) + [{"extra": "unmapped"}]
    with pytest.raises(ValueError, match="piano"):
        v10.validate_authored_candidate(title, "unused", [], body, changed)
