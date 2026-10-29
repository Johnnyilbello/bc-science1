from pathlib import Path

import pytest

from bc_science.courses import course_output_path, scan_courses
from bc_science.ui import DesktopAPI, render_markdown


def test_document_markup_cannot_execute_html_or_load_remote_images():
    html = render_markdown('<script>alert(1)</script>\n\n![tracking](https://example.com/pixel)\n\n[open](javascript:alert(1))')
    assert '<script>' not in html
    assert '<img' not in html
    assert '<a ' not in html
    assert '&lt;script&gt;' in html


def test_real_collection_reader_and_source_boundary(tmp_path: Path, monkeypatch):
    monkeypatch.setenv('BC_SCIENCE_HOME', str(tmp_path / 'home'))
    root = tmp_path / 'dispense'
    subject = root / 'ANATOMIA'
    subject.mkdir(parents=True)
    (subject / 'Neuroni.txt').write_text('I neuroni trasmettono segnali.', encoding='utf-8')
    api = DesktopAPI(root)
    assert api.snapshot()['courses'][0]['files'] == 1
    assert api.documents('ANATOMIA')[0]['name'] == 'Neuroni.txt'
    assert api.read('ANATOMIA', 'complete')['ready'] is False
    scan = scan_courses(root)[0]
    output = course_output_path(scan)
    output.parent.mkdir(parents=True)
    output.write_text('# Anatomia\n\n## Indice degli argomenti\n- Neuroni\n\n---\n\n## Neuroni\n**Neuroni** e **neuroni** trasmettono segnali.',encoding='utf-8')
    result = api.read('ANATOMIA','complete')
    assert result['ready'] and result['legacy']
    assert result['chapters'][0]['title'] == 'Neuroni'
    assert len(result['chapters'][0]['terms']) == 1
    assert 'structural_findings' in result['chapters'][0]['quality']
    assert 'language_findings' in result['chapters'][0]['quality']
    assert result['quality_status'] == 'NON_VERIFICATO_SCIENTIFICAMENTE'
    with pytest.raises(ValueError, match='non trovato'):
        api.open_source('ANATOMIA','../../private.txt')
    with pytest.raises(ValueError):
        api.documents('../ANATOMIA')


def test_desktop_progress_is_visible_in_terminal_and_job(capsys, tmp_path: Path):
    api = DesktopAPI(tmp_path)
    api._line("Capitolo 2/10: Scheletro")
    assert "[BC Science] Capitolo 2/10: Scheletro" in capsys.readouterr().out
    assert api.progress()["lines"] == ["Capitolo 2/10: Scheletro"]


def test_activity_panel_precedes_long_summary():
    html_path = (
        Path(__file__).resolve().parents[1]
        / "src" / "bc_science" / "desktop" / "index.html"
    )
    html = html_path.read_text(encoding="utf-8")
    assert html.index('id="reader-tools"') < html.index('id="activity"')
    assert html.index('id="activity"') < html.index('id="content"')



def test_both_readers_expose_original_evidence_buttons():
    web = Path(__file__).resolve().parents[1] / "src" / "bc_science" / "desktop"
    html = (web / "index.html").read_text(encoding="utf-8")
    script = (web / "app.js").read_text(encoding="utf-8")
    assert 'id="review-chapter"' in html
    assert 'id="ground-chapter"' in html
    assert "api.evidence(course,title,mode)" in script
    assert "'review_complete'" in script
    assert "panel.hidden=!['complete','essential'].includes(mode)" in script
