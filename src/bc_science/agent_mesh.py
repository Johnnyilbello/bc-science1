"""BC Science agent mesh: selective, observable, read-only orchestration.

The catalogue has 100 distinct checks. Only task-relevant agents run by
default. Running 100 Qwen instances would be slower, not more trustworthy.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .agent_checks import execute_check, make_context
from .agent_registry import AGENTS, AgentSpec, plan_agents
from .config import app_home
from .courses import scan_courses

REPORT_SCHEMA = "bc-agent-mesh-v1"


@dataclass(frozen=True, slots=True)
class AgentOutcome:
    agent_id: str
    title: str
    status: str
    evidence: str


@dataclass(frozen=True, slots=True)
class SubjectRun:
    subject: str
    status: str
    results: tuple[AgentOutcome, ...]


@dataclass(frozen=True, slots=True)
class AgentRun:
    schema: str
    created_utc: str
    root: str
    task: str
    registered_agents: int
    selected_agents: int
    exhaustive: bool
    status: str
    subjects: tuple[SubjectRun, ...]


def _aggregate(statuses: list[str]) -> str:
    if not statuses or any(
        status not in {"PASS", "WARN", "FAIL", "SKIP", "N/A", "INCOMPLETE"}
        for status in statuses
    ) or "FAIL" in statuses:
        return "FAIL"
    if any(status in {"WARN", "SKIP", "INCOMPLETE"} for status in statuses):
        return "INCOMPLETE"
    if "PASS" in statuses:
        return "PASS"
    # All N/A is not evidence of a successful review.
    return "INCOMPLETE"


def run_agents(
    root: Path,
    *,
    task: str = "study",
    limit: int = 24,
    all_agents: bool = False,
) -> AgentRun:
    """No network requests, model calls, shell execution or source modifications."""
    selected = plan_agents(task, limit=limit, all_agents=all_agents)
    scans = scan_courses(root)
    subjects = []
    for scan in scans:
        results: list[AgentOutcome] = []
        try:
            context = make_context(scan)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            # An unsafe corpus must not be misreported as PASS.
            for agent in selected:
                results.append(AgentOutcome(
                    agent.id, agent.title, "FAIL",
                    f"Preparazione corpus fallita ({type(exc).__name__})",
                ))
        else:
            for agent in selected:
                try:
                    status, evidence = execute_check(context, agent.group, agent.rule)
                except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
                    status, evidence = "FAIL", (
                        f"Errore nella regola {agent.id} ({type(exc).__name__})"
                    )
                results.append(AgentOutcome(
                    agent_id=agent.id, title=agent.title,
                    status=status, evidence=evidence,
                ))
        subjects.append(SubjectRun(
            subject=scan.name,
            status=_aggregate([result.status for result in results]),
            results=tuple(results),
        ))

    return AgentRun(
        schema=REPORT_SCHEMA,
        created_utc=datetime.now(timezone.utc).isoformat(),
        root=str(root.expanduser().resolve()),
        task=task,
        registered_agents=len(AGENTS),
        selected_agents=len(selected),
        exhaustive=all_agents,
        status=(
            "INCOMPLETE"
            if not all_agents and subjects
            and _aggregate([subject.status for subject in subjects]) == "PASS"
            else _aggregate([subject.status for subject in subjects])
        ),
        subjects=tuple(subjects),
    )


def write_agent_reports(
    report: AgentRun,
    output_dir: Path | None = None,
) -> tuple[Path, Path]:
    root = Path(report.root).resolve()
    target = (output_dir or (app_home() / "reports" / "agent-mesh" / report.task))
    target = target.expanduser().resolve()
    if target.is_relative_to(root):
        raise ValueError("La cartella dei report non deve essere interna alle fonti")
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / "agent-report.json"
    md_path = target / "agent-report.md"
    json_path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# BC Science — Agent Mesh",
        "",
        f"Esito: **{report.status}**",
        (f"Task: **{report.task}** · Agenti registrati: {report.registered_agents}"
         f" · selezionati: {report.selected_agents}"),
        "",
        ("**Limite:** i controlli sono deterministici e non garantiscono"
         " correttezza scientifica, completezza semantica o zero errori."),
        "SKIP significa non verificato. N/A significa controllo facoltativo",
        "non applicabile e non viene contato come PASS.",
        "INCOMPLETE non equivale a PASS.",
        "",
        "## Problemi da verificare",
        "",
    ]
    findings = [
        (subject, item)
        for subject in report.subjects
        for item in subject.results
        if item.status in {"FAIL", "WARN"}
    ]
    if findings:
        for subject, item in findings:
            evidence = item.evidence.replace("\\n", " ").replace("\\r", " ")
            lines.append(
                f"- **{subject.subject} · {item.agent_id} · {item.status}**: {evidence}"
            )
    else:
        lines.append("- Nessun FAIL/WARN rilevato; controllare comunque SKIP e N/A.")
    lines.append("")
    if not report.subjects:
        lines.append("Nessuna materia trovata: FAIL.")
    for subject in report.subjects:
        lines.extend([
            f"## {subject.subject} — {subject.status}",
            "",
            "| Agente | Verifica | Esito | Evidenza |",
            "| --- | --- | --- | --- |",
        ])
        for item in subject.results:
            parts = [item.agent_id, item.title, item.status, item.evidence]
            lines.append(
                "| " + " | ".join(part.replace("|", "\\|").replace("\n", " ")
                                   for part in parts) + " |"
            )
        lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path, json_path


def available_agents() -> tuple[AgentSpec, ...]:
    return AGENTS
