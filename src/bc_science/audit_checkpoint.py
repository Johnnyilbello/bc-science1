"""Atomic, locally stored checkpoints for read-only scientific audits.

The metadata binds a checkpoint to one exact source document and audit
configuration; partial results are never a completed scientific report.
"""
from __future__ import annotations

import json
from pathlib import Path


class AuditCheckpoint:
    SCHEMA = "bc-science-claim-audit-checkpoint-v1"

    def __init__(self, target: Path, metadata: dict, *, resume: bool = False) -> None:
        self.target = target
        self.path = target / "audit-checkpoint.json"
        self.metadata = dict(metadata)
        if resume:
            if not target.is_dir() or not self.path.is_file():
                raise ValueError("Checkpoint precedente non trovato per --resume")
            if (target / "report.json").exists():
                raise ValueError("Audit già completato: non riprendere il report finale")
            stored = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                not isinstance(stored, dict)
                or stored.get("schema") != self.SCHEMA
                or stored.get("metadata") != self.metadata
                or not isinstance(stored.get("claims"), list)
            ):
                raise ValueError(
                    "Checkpoint incompatibile con sorgente, modello o parametri attuali"
                )
            self.claims = stored["claims"]
            if len(self.claims) > metadata["max_claims"] or any(
                not isinstance(item, dict) for item in self.claims
            ):
                raise ValueError("Checkpoint con affermazioni non valide")
        else:
            target.mkdir(parents=True, exist_ok=False)
            self.claims = []
            self._write()

    def _write(self) -> None:
        document = {
            "schema": self.SCHEMA,
            "metadata": self.metadata,
            "claims": self.claims,
            "complete": False,
        }
        staging = self.target / "audit-checkpoint.pending.json"
        staging.write_text(
            json.dumps(document, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
        staging.replace(self.path)

    def store(self, index: int, total: int, verdict: dict) -> None:
        if index != len(self.claims) + 1 or total > self.metadata["max_claims"]:
            raise ValueError("Ordine di checkpoint non valido")
        if not isinstance(verdict, dict) or "claim" not in verdict:
            raise ValueError("Verdetto di checkpoint non valido")
        self.claims.append(verdict)
        self._write()

    def finish(self) -> None:
        """Remove resumable checkpoint only after both final reports are saved."""
        self.path.unlink(missing_ok=True)
