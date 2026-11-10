from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bc_science.agent_mesh import run_agents, write_agent_reports
from bc_science.agent_registry import AGENTS, AGENT_BY_ID, TASK_GROUPS, plan_agents
from bc_science.cli import app


def _corpus(tmp_path: Path) -> Path:
    root = tmp_path / "MATERIE"
    for name in ("ANATOMIA", "MATEMATICA", "LETTERATURA"):
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "lezione.txt").write_text(
            f"Dispensa introduttiva di {name}.", encoding="utf-8"
        )
    return root


def test_registry_has_exactly_100_distinct_checks_with_stable_ids():
    assert len(AGENTS) == len(AGENT_BY_ID) == 100
    assert len({item.title for item in AGENTS}) == 100
    assert len({item.id for item in AGENTS}) == 100
    assert all(item.rule in range(10) for item in AGENTS)
    assert len(TASK_GROUPS) == 4


def test_task_router_selects_only_relevant_agents_and_can_run_all():
    chosen = plan_agents("ingest", limit=12)
    assert len(chosen) == 12
    assert {agent.group for agent in chosen} == {"SRC", "MAN", "SEC"}
    assert len(plan_agents("publish", all_agents=True)) == 100
    assert len(plan_agents("study", limit=24)) == 24
    with pytest.raises(ValueError, match="Task non valido"):
        plan_agents("nonexistent")
    with pytest.raises(ValueError, match="limit"):
        plan_agents("study", limit=0)
    with pytest.raises(ValueError, match="limit"):
        plan_agents("study", limit=101)


def test_three_subjects_run_without_model_network_or_written_sources(
    tmp_path: Path, monkeypatch,
):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    root = _corpus(tmp_path)
    originals = {
        file.relative_to(root): file.read_bytes() for file in root.rglob("*.txt")
    }
    report = run_agents(root, task="ingest", limit=12)
    assert report.registered_agents == 100
    assert report.selected_agents == 12
    assert report.status == "FAIL"
    assert len(report.subjects) == 3
    assert all(len(item.results) == 12 for item in report.subjects)
    assert all(item.status == "FAIL" for item in report.subjects)
    assert all(result.status in {"PASS", "WARN", "FAIL", "SKIP"}
               for subject in report.subjects for result in subject.results)
    assert originals == {
        file.relative_to(root): file.read_bytes() for file in root.rglob("*.txt")
    }
    assert not list(root.rglob("manifest.json"))

    md, json_path = write_agent_reports(report, tmp_path / "external-reports")
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved["registered_agents"] == 100
    assert saved["status"] == "FAIL"
    assert "Nessuna certificazione" not in md.read_text(encoding="utf-8")


def test_full_run_honours_100_roles_and_reports_disabled_research_skips(
    tmp_path: Path, monkeypatch,
):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    root = _corpus(tmp_path)
    report = run_agents(root, task="publish", all_agents=True)
    assert report.exhaustive is True
    assert report.selected_agents == 100
    assert len(report.subjects[0].results) == 100
    research = [x for x in report.subjects[0].results if x.agent_id.startswith("RES")]
    assert len(research) == 10
    assert all(x.status == "SKIP" for x in research)
    assert report.status != "PASS"


def test_empty_root_never_returns_pass(tmp_path: Path):
    root = tmp_path / "empty"
    root.mkdir()
    assert run_agents(root).status == "FAIL"


def test_report_location_under_sources_rejected(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    root = _corpus(tmp_path)
    report = run_agents(root, limit=3)
    with pytest.raises(ValueError, match="non deve essere interna"):
        write_agent_reports(report, root / "output")


def test_agents_cli_list_plan_run_strict(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BC_SCIENCE_HOME", str(tmp_path / "home"))
    runner = CliRunner()
    listed = runner.invoke(app, ["agents", "list"])
    assert listed.exit_code == 0, listed.output
    assert "SRC-01" in listed.output
    planned = runner.invoke(app, ["agents", "plan", "--task", "ingest", "--limit", "5"])
    assert planned.exit_code == 0, planned.output
    assert "5/100" in planned.output

    root = _corpus(tmp_path)
    dest = tmp_path / "reports"
    actual = runner.invoke(app, [
        "agents", "run", str(root), "--task", "ingest",
        "--limit", "5", "--strict", "--output-dir", str(dest),
    ])
    assert actual.exit_code == 1, actual.output
    assert (dest / "agent-report.json").exists()
    wrong = runner.invoke(app, ["agents", "plan", "--task", "nonexistent"])
    assert wrong.exit_code == 2, wrong.output
