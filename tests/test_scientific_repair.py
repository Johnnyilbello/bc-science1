"""Scientific repair remains non-destructive until evidence is reviewed."""
import json

import pymupdf
import pytest

from bc_science import courses
from bc_science.scientific_repair import (
    CHAPTER,
    REPAIRS,
    apply_staged_mitochondrial_repair,
    repair_verified_chapter,
    stage_mitochondrial_repair,
    _replace_single_study_chapter,
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
    old_study = courses.course_study_output_path(scan)
    old_study.write_text(
        "# Fondamenti - Essenziale\n\n"
        "## Indice degli argomenti\n"
        "- Fosforilazione ossidativa\n\n"
        "---\n\n"
        "## Fosforilazione ossidativa\n\n"
        "La catena respiratoria pompa protoni nella direzione errata. "
        "L'ADP riceve protoni e forma ATP.\n",
        encoding="utf-8",
    )
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
    assert courses.course_study_output_path(scan).is_file()
    assert (stage / "backup-originali" / "riassunto-studio.md").is_file()


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



def test_apply_requires_explicit_confirmation(tmp_path, monkeypatch):
    scan, _source, output, _manifest = _fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="Conferma esplicita"):
        apply_staged_mitochondrial_repair(
            scan, tmp_path / "anything", confirmed=False,
        )
    assert REPAIRS[0][0] in output.read_text(encoding="utf-8")


def test_apply_staged_repair_updates_manifest_with_recoverable_backup(tmp_path, monkeypatch):
    scan, source, output, manifest = _fixture(tmp_path, monkeypatch)
    before_source = source.read_bytes()
    previous_complete = output.read_text(encoding="utf-8")
    previous_manifest = manifest.read_bytes()
    stage = tmp_path / "preview" / "approved"
    stage_mitochondrial_repair(scan, stage)

    backup = apply_staged_mitochondrial_repair(scan, stage, confirmed=True)

    assert REPAIRS[0][0] not in output.read_text(encoding="utf-8")
    assert REPAIRS[1][0] not in output.read_text(encoding="utf-8")
    assert courses.course_study_output_path(scan).is_file()
    assert courses.course_pdf_path(scan).is_file()
    assert courses.course_study_pdf_path(scan).is_file()
    assert source.read_bytes() == before_source
    assert (backup / "riassunto-unico.md").read_text(encoding="utf-8") == previous_complete
    assert (backup / "manifest.json").read_bytes() == previous_manifest
    data = courses._load_manifest(scan)
    assert courses._study_is_current(scan, data, output.read_text(encoding="utf-8"))
    assert (stage / "riparazione-pubblicata.txt").is_file()
    with pytest.raises(ValueError, match="mutati|già presente|modificato|cambiato"):
        apply_staged_mitochondrial_repair(scan, stage, confirmed=True)


def test_apply_rejects_modified_staged_content_without_writing(tmp_path, monkeypatch):
    scan, source, output, manifest = _fixture(tmp_path, monkeypatch)
    stage = tmp_path / "preview" / "tampered"
    stage_mitochondrial_repair(scan, stage)
    original_complete = output.read_bytes()
    original_manifest = manifest.read_bytes()
    (stage / "riassunto-studio.md").write_text("Alterato", encoding="utf-8")

    with pytest.raises(ValueError, match="alterata|incompatibile"):
        apply_staged_mitochondrial_repair(scan, stage, confirmed=True)

    assert output.read_bytes() == original_complete
    assert manifest.read_bytes() == original_manifest
    assert source.is_file()


def test_apply_rolls_back_if_publication_fails(tmp_path, monkeypatch):
    import bc_science.scientific_repair as repair

    scan, _source, output, manifest = _fixture(tmp_path, monkeypatch)
    stage = tmp_path / "preview" / "rollback"
    stage_mitochondrial_repair(scan, stage)
    complete_before = output.read_bytes()
    manifest_before = manifest.read_bytes()
    original_copy = repair._atomic_copy
    counter = 0

    def fail_on_second(source, destination):
        nonlocal counter
        counter += 1
        if counter == 2:
            raise OSError("Failure injection before PDF copy")
        return original_copy(source, destination)

    monkeypatch.setattr(repair, "_atomic_copy", fail_on_second)
    with pytest.raises(OSError, match="Failure injection"):
        repair.apply_staged_mitochondrial_repair(scan, stage, confirmed=True)

    assert output.read_bytes() == complete_before
    assert manifest.read_bytes() == manifest_before
    assert courses.course_study_output_path(scan).is_file()
    assert "direzione errata" in courses.course_study_output_path(scan).read_text(
        encoding="utf-8"
    )



def test_single_chapter_replacement_keeps_other_chapters_byte_identical():
    original = (
        "# Studio\n\n## Indice degli argomenti\n"
        "- Introduzione\n- Fosforilazione ossidativa\n- Osservazioni\n\n"
        "---\n\n## Introduzione\n\nLe cellule hanno membrane.\n\n"
        "---\n\n## Fosforilazione ossidativa\n\n"
        "La catena respiratoria sposta i protoni nella direzione errata.\n\n"
        "---\n\n## Osservazioni\n\nNon modificare la sezione di ripasso.\n"
    )
    replacement = (
        "## Fosforilazione ossidativa\n\n"
        "I protoni vengono pompati dalla matrice allo spazio intermembrana.\n"
    )
    result = _replace_single_study_chapter(original, replacement)
    chapters_before = dict(courses._extract_summary_chapters(original))
    chapters_after = dict(courses._extract_summary_chapters(result))
    assert chapters_before["Introduzione"] == chapters_after["Introduzione"]
    assert chapters_before["Osservazioni"] == chapters_after["Osservazioni"]
    assert result.count(replacement.strip()) == 1
    assert len(result.splitlines()) > 5


def test_single_chapter_replacement_rejects_missing_target():
    text = "# Studio\n\n## Indice degli argomenti\n- Altro\n\n---\n\n## Altro\n\nTesto."
    with pytest.raises(ValueError, match="ambiguo|mancante"):
        _replace_single_study_chapter(
            text, "## Fosforilazione ossidativa\n\nNuovo testo."
        )


def test_staging_refuses_missing_original_study(tmp_path, monkeypatch):
    scan, _source, _output, _manifest = _fixture(tmp_path, monkeypatch)
    courses.course_study_output_path(scan).unlink()
    with pytest.raises(ValueError, match="Essenziale originale assente"):
        stage_mitochondrial_repair(scan, tmp_path / "previews" / "missing-study")


def test_apply_rejects_revised_unrelated_study_chapter(tmp_path, monkeypatch):
    scan, _source, output, manifest = _fixture(tmp_path, monkeypatch)
    study = courses.course_study_output_path(scan)
    # Add a second chapter to the existing edition before staging.
    content = study.read_text(encoding="utf-8")
    content = content.replace(
        "- Fosforilazione ossidativa\n",
        "- Fosforilazione ossidativa\n- Un altro capitolo\n",
    )
    content += "\n---\n\n## Un altro capitolo\n\nUna definizione conservata.\n"
    study.write_text(content, encoding="utf-8")
    preview = tmp_path / "previews" / "tamper-other-chapter"
    stage_mitochondrial_repair(scan, preview)

    staged = preview / "riassunto-studio.md"
    staged.write_text(
        staged.read_text(encoding="utf-8").replace(
            "Una definizione conservata.",
            "Una definizione manipolata.",
        ),
        encoding="utf-8",
    )
    before_complete = output.read_bytes()
    before_manifest = manifest.read_bytes()
    with pytest.raises(ValueError, match="extra-capitolo|alterata|incompatibile"):
        apply_staged_mitochondrial_repair(scan, preview, confirmed=True)
    assert output.read_bytes() == before_complete
    assert manifest.read_bytes() == before_manifest
