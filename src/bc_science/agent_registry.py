"""Registry of 100 independently addressable, deterministic review agents.

Each role has one distinct check implemented in agent_mesh.py. These are not
100 running LLM instances; the router executes only relevant checks.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentSpec:
    id: str
    title: str
    group: str
    phase: str
    rule: int


_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("SRC", "ingest", (
        "presenza dei documenti",
        "esistenza dei file",
        "tipologia file regolare",
        "contenuto non vuoto",
        "formato supportato",
        "unicità dei percorsi",
        "identificazione duplicati SHA256",
        "assenza di link simbolici",
        "contenimento nella materia",
        "dimensione file ragionevole",
    )),
    ("MAN", "ingest", (
        "presenza manifest",
        "leggibilità JSON manifest",
        "schema manifest riconosciuto",
        "identità della materia",
        "percorso radice delle fonti",
        "lista documenti strutturata",
        "fingerprint documentale",
        "insieme file coincidente",
        "integrità SHA256 file",
        "integrità dimensioni file",
    )),
    ("MAP", "summarize", (
        "presenza mappa documenti",
        "integrità struttura mappa",
        "assenza fonti duplicate nella mappa",
        "assenza file orfani in mappa",
        "copertura di ogni file",
        "nome capitolo non vuoto",
        "capitolo di destinazione esistente",
        "unicità dei capitoli normalizzati",
        "copertura di tutti gli argomenti",
        "verifica copertura globale",
    )),
    ("CMP", "summarize", (
        "presenza riassunto completo",
        "decodifica Unicode riassunto",
        "riassunto non vuoto",
        "titolo di apertura",
        "presenza di capitoli",
        "unicità titoli capitoli",
        "capitoli senza corpo vuoto",
        "indice degli argomenti",
        "assenza di segnaposto editoriali",
        "SHA256 del riassunto completo",
    )),
    ("PDF", "publish", (
        "presenza PDF completo",
        "firma formato PDF completo",
        "PDF completo non vuoto",
        "metadata PDF nel manifest",
        "percorso PDF nel manifest",
        "numero pagine PDF valido",
        "legame PDF con hash riassunto",
        "assenza PDF temporaneo",
        "posizione PDF nella cartella corretta",
        "soglia minima byte PDF",
    )),
    ("STD", "study", (
        "presenza riassunto essenziale",
        "decodifica Unicode essenziale",
        "essenziale non vuoto",
        "titolo di apertura essenziale",
        "capitoli essenziale presenti",
        "capitoli essenziale senza corpo vuoto",
        "assenza di segnaposto essenziale",
        "provenienza dal completo corrente",
        "SHA256 essenziale corrente",
        "numeri d'esame preservati secondo audit",
    )),
    ("SPF", "publish", (
        "presenza PDF essenziale",
        "firma formato PDF essenziale",
        "PDF essenziale non vuoto",
        "metadata PDF essenziale",
        "percorso PDF essenziale",
        "numero pagine essenziale valido",
        "legame PDF essenziale con hash",
        "co-localizzazione PDF e testo",
        "nome PDF coerente al testo",
        "dimensione PDF essenziale ragionevole",
    )),
    ("AUD", "study", (
        "presenza report audit",
        "leggibilità JSON audit",
        "versione audit dichiarata",
        "stato audit riconosciuto",
        "audit riferito al completo corrente",
        "audit riferito all'essenziale corrente",
        "numero fonti corretto",
        "copertura documentale dichiarata",
        "assenza di omissioni numeriche rilevate",
        "blocco su audit FAIL",
    )),
    ("RES", "publish", (
        "presenza approfondimento opt-in",
        "approfondimento non vuoto",
        "etichetta bibliografica PubMed",
        "presenza identificativi PMID",
        "riferimenti citati numerati",
        "assenza di segnaposto in ricerca",
        "separazione ricerca da completa",
        "separazione ricerca da essenziale",
        "citazioni con ancoraggi bibliografici",
        "percorso research isolato",
    )),
    ("SEC", "crosscutting", (
        "output fuori dalle cartelle fonti",
        "assenza artefatti generati tra le fonti",
        "assenza link simbolici tra le fonti",
        "confine dei percorsi sorgente",
        "assenza path escape nel manifest",
        "assenza chiavi segrete nel manifest",
        "assenza script HTML nel completo",
        "assenza script HTML nell'essenziale",
        "assenza script HTML nella ricerca",
        "endpoint Ollama locale",
    )),
)
AGENTS: tuple[AgentSpec, ...] = tuple(
    AgentSpec(
        id=f"{code}-{index:02d}",
        title=title,
        group=code,
        phase=phase,
        rule=index - 1,
    )
    for code, phase, titles in _GROUPS
    for index, title in enumerate(titles, 1)
)
AGENT_BY_ID = {agent.id: agent for agent in AGENTS}
TASK_GROUPS: dict[str, tuple[str, ...]] = {
    "ingest": ("SRC", "MAN", "SEC"),
    "summarize": ("SRC", "MAN", "MAP", "CMP", "SEC"),
    "study": ("SRC", "MAN", "MAP", "CMP", "STD", "AUD", "SEC"),
    "publish": tuple(code for code, _, _ in _GROUPS),
}
assert len(AGENTS) == len(AGENT_BY_ID) == 100


def plan_agents(task: str = "study", *, limit: int = 24, all_agents: bool = False) -> tuple[AgentSpec, ...]:
    """Deterministic round-robin routing; no network, no inference, no cost."""
    if task not in TASK_GROUPS:
        raise ValueError(f"Task non valido: {task}")
    if not 1 <= limit <= 100:
        raise ValueError("limit deve essere tra 1 e 100")
    if all_agents:
        return AGENTS
    active = TASK_GROUPS[task]
    result = []
    for idx in range(10):
        for group in active:
            agent = AGENT_BY_ID[f"{group}-{idx + 1:02d}"]
            result.append(agent)
            if len(result) == limit:
                return tuple(result)
    return tuple(result)
