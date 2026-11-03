"""Regression tests for cached audit provenance across study editions."""

import hashlib
import json
from pathlib import Path

from bc_science import courses
from bc_science.course_audit import (
    AUDIT_VERSION,
    FAST_ESSENTIAL_AUDIT_VERSION,
    audit_report_is_current,
)
from bc_science.study_editions import FAST_STUDY_VERSION


def _summary() -> str:
    return (
        "# Anatomia\n\n## Indice degli argomenti\n- Scheletro\n\n---\n\n"
        "## Scheletro\n\nLo scheletro assile comprende cranio e gabbia toracica.\n"
    )


def _report(path: Path, complete: str, study: str, version: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "audit_version": version,
                "status": "PASS",
                "complete_sha256": hashlib.sha256(complete.encode("utf-8")).hexdigest(),
                "study_sha256": hashlib.sha256(study.encode("utf-8")).hexdigest(),
            }
        ),
        encoding="utf-8",
    )


def test_cached_audit_must_match_expected_edition_even_with_matching_hashes(tmp_path: Path):
    complete = _summary()
    study = complete.replace("# Anatomia", "# Anatomia Essenziale", 1)
    path = tmp_path / "audit.json"

    _report(path, complete, study, AUDIT_VERSION)
    assert audit_report_is_current(path, complete, study) == (True, "PASS")
    assert audit_report_is_current(
        path, complete, study, expected_version=FAST_ESSENTIAL_AUDIT_VERSION,
    ) == (False, None)
    assert audit_report_is_current(
        path, complete, study, expected_version=AUDIT_VERSION,
    ) == (True, "PASS")

    _report(path, complete, study, FAST_ESSENTIAL_AUDIT_VERSION)
    assert audit_report_is_current(
        path, complete, study, expected_version=AUDIT_VERSION,
    ) == (False, None)
    assert audit_report_is_current(
        path, complete, study, expected_version=FAST_ESSENTIAL_AUDIT_VERSION,
    ) == (True, "PASS")
    assert audit_report_is_current(
        path, complete + " Modificata.", study,
        expected_version=FAST_ESSENTIAL_AUDIT_VERSION,
    ) == (False, None)


def test_course_audit_rebuilds_fast_report_instead_of_trusting_legacy_pass(
    tmp_path: Path, monkeypatch,
):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    subject = tmp_path / "SCIENZE MOTORIE" / "ANATOMIA"
    subject.mkdir(parents=True)
    (subject / "Scheletro.txt").write_text(
        "Lo scheletro assile comprende il cranio.", encoding="utf-8",
    )
    scan = courses.scan_course(subject)
    complete = _summary()
    study = complete.replace("# Anatomia", "# Anatomia Essenziale", 1)
    report = courses.course_audit_json_path(scan)
    _report(report, complete, study, AUDIT_VERSION)
    monkeypatch.setattr(
        courses, "_load_manifest",
        lambda _scan: {"study": {"pipeline": FAST_STUDY_VERSION}},
    )

    status, destination = courses._ensure_course_audit_status(
        scan, complete, study, source_coverage_complete=True,
    )

    saved = json.loads(report.read_text(encoding="utf-8"))
    assert saved["audit_version"] == FAST_ESSENTIAL_AUDIT_VERSION
    assert status == saved["status"]
    assert destination.exists()


def test_manifest_selects_edition_specific_audit_version():
    assert courses._audit_version_for_manifest(None) == AUDIT_VERSION
    assert courses._audit_version_for_manifest(
        {"study": {"pipeline": "older-llm-study"}}
    ) == AUDIT_VERSION
    assert courses._audit_version_for_manifest(
        {"study": {"pipeline": FAST_STUDY_VERSION}}
    ) == FAST_ESSENTIAL_AUDIT_VERSION
