"""Scientific repair remains non-destructive until evidence is reviewed."""
import json

import pymupdf
import pytest

from bc_science import courses
from bc_science.scientific_repair import (
    CHAPTER,
    REPAIRS,
    repair_verified_chapter,
    stage_mitochondrial_repair,
)
from bc_science.structural_checks import find_structural_issues
from bc_science.study_editions import _sentences


def _complete():
    return (
        "# Fondamenti di Biologia e Chimica\n\n"
        "## Indice degli argomenti\n- Fosforilazione ossidativa\n\n"
        "---\n\n## Fosforilazione ossidativa\n\n"
        "### In parole semplici\n"
        "Il gradiente protonico alimenta la sintesi di ATP.\n\n"
        "### Meccanismo\n"
        + REPAIRS[0][0] + "\n"
        + REPAIRS[1][0]
        + ", creando un gradiente elettrochimico.\n"
        + REPAIRS[2][0] + "\n\n"
        "### Parole chiave\n"
        "* **Disaccoppianti**: Molecole (es. DNP) che consentono "
        "il flusso di protoni senza produrre ATP.\n"
    )


def test_verified_corrections_preserve_unmodified_course_structure():
    complete = _complete()
    revised, occurrences = repair_verified_chapter(complete)
    assert occurrences == (1, 1, 1)
    assert revised.startswith("# Fondamenti di Biologia")
    assert "dalla matrice mitocondriale allo spazio intermembrana" in revised
    assert "ricevere i protoni e formare ATP" not in revised
    assert "Disaccoppianti" in revised
    assert len(courses._chapter_titles(revised)) == 1


def test_unknown_variant_fails_closed():
    with pytest.raises(ValueError, match="differisce dal dossier"):
        repair_verified_chapter(
            _complete().replace("riflusso", "ritorno protonico", 1)
        )


def test_abbreviation_does_not_split_scientific_glossary():
    original = "Disaccoppianti: Molecole (es. DNP) che dissipano il gradiente."
    parts = _sentences(original)
    assert parts == [original]
    assert not any(
        i.category == "detached_glossary_fragment"
        for i in find_structural_issues("## Fosforilazione\n\n" + original)
    )


def test_dangling_dnp_still_flagged():
    chapter = (
        "## Fosforilazione\n\n"
        "Il gradiente consente la sintesi di ATP. "
        "DNP) che permette la perdita del gradiente."
    )
    assert any(
        issue.category == "detached_glossary_fragment"
        for issue in find_structural_issues(chapter)
    )


def _fixture(tmp_path, monkeypatch):
    from bc_science.courses import (
        CourseScan,
        _fingerprint,
        _manifest_path,
        _snapshot,
        _summary_sha256,
    )

    monkeypatch.setattr(courses, "app_home", lambda: tmp_path / "app-home")
    folder = tmp_path / "materiali" / "FONDAMENTI DI BIOLOGIA  E CHIMICA"
    folder.mkdir(parents=True)
    source = folder / "Fosforilazione ossidativa.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text(
            (45, 70),
            "I protoni ritornano nella matrice attraverso ATP sintetasi.",
        )
        doc.save(source)
    scan = CourseScan(
        name="FONDAMENTI DI BIOLOGIA  E CHIMICA",
        path=folder,
        supported_files=(source,),
        total_files=1,
        ignored_files=0,
        folders_visited=1,
    )
    original = _complete()
    output = courses.course_output_path(scan)
    output.parent.mkdir(parents=True)
    output.write_text(original, encoding="utf-8")
    snap = _snapshot(scan)
    manifest = {
        "schema": 3,
        "course": scan.name,
        "source": str(scan.path),
        "summary_pipeline": "legacy-complete",
        "fingerprint": _fingerprint(snap),
        "files": snap,
        "output": str(output),
        "summary_sha256": _summary_sha256(original),
        "chapters": [CHAPTER],
        "coverage": [{
            "source": source.name,
            "chapter": CHAPTER,
        }],
        "pdf": None,
        "study": None,
    }
    workspace_manifest = _manifest_path(scan)
    workspace_manifest.parent.mkdir(parents=True, exist_ok=True)
    workspace_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    return scan, source, output, workspace_manifest


def test_stage_generates_backups_pdfs_and_manifest_without_publishing(tmp_path, monkeypatch):
    scan, source, output, original_manifest = _fixture(tmp_path, monkeypatch)
    original_text = output.read_text(encoding="utf-8")
    original_pdf_hash = source.read_bytes()
    old_manifest = original_manifest.read_bytes()
    stage = tmp_path / "previews" / "new-correction"

    target = stage_mitochondrial_repair(scan, stage)

    assert target == stage.resolve()
    assert (stage / "riassunto-unico.md").is_file()
    assert (stage / "riassunto-studio.md").is_file()
    assert (stage / "riassunto-unico.pdf").stat().st_size > 0
    assert (stage / "riassunto-studio.pdf").stat().st_size > 0
    assert (stage / "backup-originali" / "riassunto-unico.md").is_file()
    assert (stage / "backup-originali" / "manifest.json").is_file()
    assert (stage / "riparazione-prove.json").is_file()
    proposed = json.loads((stage / "manifest-PROPOSTO.json").read_text(encoding="utf-8"))
    assert proposed["summary_sha256"] != json.loads(old_manifest)["summary_sha256"]
    assert proposed["study"]["source_summary_sha256"] == proposed["summary_sha256"]
    assert proposed["files"] == json.loads(old_manifest)["files"]
    assert output.read_text(encoding="utf-8") == original_text
    assert original_manifest.read_bytes() == old_manifest
    assert source.read_bytes() == original_pdf_hash
    assert not courses.course_study_output_path(scan).exists()


def test_stage_refuses_existing_path_and_changed_original_pdf(tmp_path, monkeypatch):
    scan, source, _output, _manifest = _fixture(tmp_path, monkeypatch)
    stage = tmp_path / "previews" / "existing"
    stage.mkdir(parents=True)
    with pytest.raises(ValueError, match="già esistente"):
        stage_mitochondrial_repair(scan, stage)
    source.write_bytes(source.read_bytes() + b"\nmodified")
    with pytest.raises(ValueError, match="cambiati"):
        stage_mitochondrial_repair(scan, tmp_path / "previews" / "no-publish")
    assert not (tmp_path / "previews" / "no-publish").exists()


def test_stage_never_writes_into_course_materials(tmp_path, monkeypatch):
    scan, _source, _output, _manifest = _fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="dispense"):
        stage_mitochondrial_repair(scan, scan.path / "scienza-riparata")
