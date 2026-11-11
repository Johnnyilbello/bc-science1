"""Bounded two-model scientific repair proposals, kept outside published outputs.

Qwen is the local diagnostician; Nemotron is a separately queried critic.
Their agreement is *not* proof of scientific correctness. Sources are original
SHA-pinned course excerpts; every model quote is validated on the local host.
No tool execution, commits, PDF updates, or unattended publication.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
from collections.abc import Callable
from pathlib import Path

import httpx

from .autonomous_claim_audit import _json_object, extract_claim_candidates
from .evidence_engine import OriginalEvidence, validate_original_quote

NEMOTRON_MODEL = "nvidia/nemotron-3-super-120b-a12b"
_NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
_ALLOWED_LABELS = frozenset({"SUSPECT", "SUPPORTED", "UNVERIFIED"})
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}")
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\s().-]?){9,16}(?!\w)")
_HEADING = re.compile(r"(?m)^##[ \t]+([^\r\n]+?)[ \t]*\r?$")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _cloud_text(value: str, *, limit: int = 700) -> str:
    """Remove obvious direct identifiers; explicit consent still required."""
    value = _EMAIL.sub("[EMAIL_REDACTED]", value)
    value = _PHONE.sub("[PHONE_REDACTED]", value)
    return value[:limit]


def _cloud_evidence(evidence: list[OriginalEvidence]) -> list[dict]:
    return [
        {"id": i, "excerpt": _cloud_text(e.excerpt)}
        for i, e in enumerate(evidence[:3], start=1)
    ]


def _local_evidence(evidence: list[OriginalEvidence]) -> list[dict]:
    return [
        {"id": i, "excerpt": e.excerpt[:1250]}
        for i, e in enumerate(evidence[:3], start=1)
    ]


def _normalise_judgment(response: str, evidence: list[OriginalEvidence]) -> dict:
    default = {
        "verdict": "UNVERIFIED", "reason": "Risposta non verificata",
        "replacement": "", "quotes": [], "valid": False,
    }
    obj = _json_object(response)
    if not isinstance(obj, dict):
        return {**default, "diagnostic": "INVALID_JSON"}
    label = obj.get("verdict")
    reason = obj.get("reason")
    replacement = obj.get("replacement", "")
    raw_quotes = obj.get("quotes", [])
    if (
        label not in _ALLOWED_LABELS or not isinstance(reason, str)
        or not 1 <= len(reason.strip()) <= 600
        or not isinstance(replacement, str)
        or len(replacement) > 750
        or not isinstance(raw_quotes, list)
        or len(raw_quotes) > 3
    ):
        return {**default, "diagnostic": "INVALID_SCHEMA"}
    citations = []
    for item in raw_quotes:
        if not isinstance(item, dict):
            return {**default, "diagnostic": "INVALID_QUOTE"}
        index = item.get("source_id")
        if type(index) is not int or not 1 <= index <= min(3, len(evidence)):
            return {**default, "diagnostic": "INVALID_SOURCE_ID"}
        source = evidence[index - 1]
        canonical = {
            "source": source.source_path, "page": source.page,
            "block": source.block, "quote": item.get("quote"),
        }
        if not validate_original_quote(canonical, evidence):
            return {**default, "diagnostic": "NON_LITERAL_SOURCE_QUOTE"}
        citations.append(canonical)
    if label == "SUSPECT" and not citations:
        return {**default, "diagnostic": "MISSING_SUSPECT_PROOF"}
    if label == "SUSPECT" and replacement.strip() and (
        "\n" in replacement or "\r" in replacement
        or replacement.strip().startswith(("#", "- ", "* "))
    ):
        return {**default, "diagnostic": "UNSAFE_REPLACEMENT_SHAPE"}
    return {
        "verdict": label, "reason": reason.strip(), "replacement": replacement.strip(),
        "quotes": citations, "valid": True, "diagnostic": "VALID",
    }


def _query_prompt(
    claim: str, evidence: list[dict], *, role: str,
    candidate: str = "", feedback: str = "",
) -> list[dict[str, str]]:
    instructions = (
        "Agisci come un revisore scientifico indipendente valido per QUALUNQUE "
        "materia. Non fidarti del testo generato; non inventare fonti. "
        "Verifica significato, soggetto, oggetto, quantita, direzione, "
        "negazioni, relazioni e affermazioni controintuitive. "
        "Una coincidenza di parole non dimostra la proposizione. "
        "Se una fonte non prova il fatto, UNVERIFIED. "
        "Se trovi un problema, SUSPECT e indica una controcitazione o "
        "un passaggio pertinente nell'originale. "
        "Le stesse dispense potrebbero essere errate: non dichiarare "
        "verita scientifica indipendente. "
        "Rispondi SOLO con JSON: "
        '{"verdict":"SUSPECT|SUPPORTED|UNVERIFIED",'
        '"reason":"motivazione specifica",'
        '"replacement":"eventuale frase corretta o stringa vuota",'
        '"quotes":[{"source_id":1,"quote":"frase letterale originale di almeno 16 caratteri"}]}. '
        "Non includere Markdown, ragionamenti riservati o altri campi."
    )
    if role == "independent":
        instructions += (
            " Svolgi un giudizio autonomo: non conoscere le proposte di altri."
        )
    elif role == "review":
        instructions += (
            " Valuta rigorosamente la sostituzione proposta. Se non è interamente "
            "dimostrata da queste fonti o contiene un dettaglio non dimostrato, "
            "rispondi UNVERIFIED. SUSPECT significa che hai individuato "
            "un problema nella proposta e deve essere respinta. "
            "SUPPORTED significa che le fonti forniscono sostegno alla "
            "proposta integrale; cita il passaggio pertinente."
        )
    elif role == "revise":
        instructions += (
            " Il revisore ha respinto la proposta. Proponi una revisione "
            "soltanto se le citazioni dimostrano il nuovo testo, "
            "altrimenti rispondi UNVERIFIED."
        )
    detail = {
        "claim": claim,
        "evidence": evidence,
        "task": role,
    }
    if candidate:
        detail["candidate_replacement"] = candidate
    if feedback:
        detail["critic_feedback"] = feedback[:450]
    return [
        {"role": "system", "content": instructions},
        {"role": "user", "content": json.dumps(detail, ensure_ascii=False)},
    ]


class NemotronNvidia:
    """Fixed HTTPS free-prototyping catalog endpoint, no fallback or retries."""

    def __init__(self, api_key: str, *, max_calls: int = 16) -> None:
        if not isinstance(api_key, str) or not api_key.startswith("nvapi-"):
            raise ValueError("NVIDIA_API_KEY del Developer Program mancante/non valida")
        if not 1 <= max_calls <= 30:
            raise ValueError("Limite richieste fuori intervallo")
        self._key = api_key
        self._remaining = max_calls
        self.used_calls = 0

    def __call__(self, messages: list[dict[str, str]]) -> str:
        if self._remaining <= 0:
            raise RuntimeError("Limite hard di richieste Nemotron esaurito")
        self._remaining -= 1
        self.used_calls += 1
        payload = {
            "model": NEMOTRON_MODEL, "messages": messages,
            "max_tokens": 1024, "temperature": 0, "stream": False,
        }
        # No retries, no automatic fallbacks to paid endpoints.
        try:
            with httpx.Client(timeout=100.0, follow_redirects=False) as client:
                response = client.post(
                    _NVIDIA_URL, json=payload,
                    headers={"Authorization": "Bearer " + self._key},
                )
                response.raise_for_status()
                data = response.json()
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("Contenuto Nemotron non testuale")
            return content
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            # Never log the exception's repr: it could contain transport headers.
            raise RuntimeError(
                "Nemotron non disponibile: richiesta non ripetuta; "
                "verificare il piano gratuito, l'endpoint e i limiti."
            ) from None


def _patch_one_chapter(document: str, chapter: str, old: str, new: str) -> str:
    """One exact textual edit confined to a unique H2; fail closed."""
    if (
        not old.strip() or old == new or not new.strip()
        or "\n" in new or "\r" in new or len(new) > 750
        or new.strip().startswith(("#", "- ", "* "))
    ):
        raise ValueError("Testo di modifica non sicuro")
    headings = list(_HEADING.finditer(document))
    chosen = [
        i for i, heading in enumerate(headings)
        if heading.group(1).strip().casefold() == chapter.casefold()
    ]
    if len(chosen) != 1:
        raise ValueError("Capitolo non univoco")
    i = chosen[0]
    start = headings[i].end()
    stop = headings[i + 1].start() if i + 1 < len(headings) else len(document)
    body = document[start:stop]
    if body.count(old) != 1:
        raise ValueError("Affermazione assente o ripetuta nel capitolo")
    return document[:start] + body.replace(old, new, 1) + document[stop:]


def _number_guard(before: str, after: str, quotes: list[dict]) -> bool:
    """Never introduce unsupported numeric literals into a correction draft."""
    numeric = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?")
    added = set(numeric.findall(after)) - set(numeric.findall(before))
    cited = " ".join(str(row["quote"]) for row in quotes)
    return added.issubset(set(numeric.findall(cited)))


def run_dual_repair(
    document: str,
    chapter: str,
    *,
    max_claims: int,
    find_evidence: Callable[[str], list[OriginalEvidence]],
    local_chat: Callable[[list[dict[str, str]]], str],
    cloud_chat: Callable[[list[dict[str, str]]], str],
    max_rounds: int = 2,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict:
    """Independent diagnosis -> cross-review -> bounded draft or abstention.

    No network or file IO here. Callers inject clients and write separate files.
    """
    if max_claims < 1 or max_claims > 8 or max_rounds not in {1, 2}:
        raise ValueError("Limiti agenti non validi")
    selected = extract_claim_candidates(document, chapter, limit=max_claims)
    results: list[dict] = []
    staged: list[tuple[int, str]] = []
    for i, claim in enumerate(selected, start=1):
        row: dict = {
            "claim": claim.to_dict(), "status": "UNVERIFIED",
            "local": None, "independent": None, "reviews": [],
            "proposal_sha256": None, "reason": "Prove insufficienti",
        }
        try:
            original = find_evidence(claim.text)[:3]
        except (OSError, ValueError):
            original = []
        row["evidence_count"] = len(original)
        if not original:
            row["reason"] = "Nessun passaggio originale recuperato"
            results.append(row)
            if progress:
                progress(i, len(selected), row["status"])
            continue
        try:
            local = _normalise_judgment(
                local_chat(_query_prompt(
                    claim.text, _local_evidence(original), role="local",
                )),
                original,
            )
            # Nemotron receives only bounded excerpts and text, never local
            # file paths, full PDFs, secrets or absolute course locations.
            independent = _normalise_judgment(
                cloud_chat(_query_prompt(
                    _cloud_text(claim.text, limit=500),
                    _cloud_evidence(original), role="independent",
                )),
                original,
            )
            row["local"], row["independent"] = local, independent
            if not local["valid"] or not independent["valid"]:
                row["reason"] = "Risposta non verificabile di un agente"
            elif local["verdict"] != "SUSPECT" or independent["verdict"] != "SUSPECT":
                row["reason"] = "Gli agenti non concordano su un errore documentato"
                row["status"] = "DISAGREEMENT"
            elif not local["replacement"]:
                row["reason"] = "Problema sospetto senza proposta di correzione"
                row["status"] = "NEEDS_RESEARCH"
            else:
                replacement = local["replacement"]
                for round_number in range(1, max_rounds + 1):
                    reviewer = _normalise_judgment(
                        cloud_chat(_query_prompt(
                            _cloud_text(claim.text, limit=500),
                            _cloud_evidence(original),
                            role="review", candidate=_cloud_text(replacement),
                        )),
                        original,
                    )
                    row["reviews"].append({
                        "round": round_number, **reviewer,
                    })
                    if reviewer["valid"] and reviewer["verdict"] == "SUPPORTED":
                        if not _number_guard(
                            claim.text, replacement, reviewer["quotes"],
                        ):
                            row["reason"] = "Nuovi valori non attestati da citazioni"
                            row["status"] = "NEEDS_RESEARCH"
                            break
                        try:
                            proposed = _patch_one_chapter(
                                document, chapter, claim.text, replacement,
                            )
                        except ValueError as exc:
                            row["reason"] = str(exc)
                            row["status"] = "NEEDS_RESEARCH"
                            break
                        row["status"] = "DRAFT_REVIEW"
                        row["reason"] = (
                            "Modifica isolata accettata da due agenti; "
                            "verifica scientifica indipendente ancora necessaria"
                        )
                        row["proposal_sha256"] = _hash(proposed)
                        staged.append((i, proposed))
                        break
                    if round_number == max_rounds:
                        row["reason"] = "Correzione contestata o non dimostrata"
                        row["status"] = "NEEDS_RESEARCH"
                        break
                    updated = _normalise_judgment(
                        local_chat(_query_prompt(
                            claim.text, _local_evidence(original),
                            role="revise", candidate=replacement,
                            feedback=reviewer["reason"],
                        )), original,
                    )
                    if not updated["valid"] or not updated["replacement"]:
                        row["reason"] = "Qwen non ha prodotto una revisione verificabile"
                        row["status"] = "NEEDS_RESEARCH"
                        break
                    replacement = updated["replacement"]
        except RuntimeError:
            # Do not make further cloud calls after provider errors or caps.
            row["reason"] = "Cloud non disponibile o budget Nemotron esaurito"
            row["status"] = "CLOUD_STOPPED"
            results.append(row)
            if progress:
                progress(i, len(selected), row["status"])
            break
        results.append(row)
        if progress:
            progress(i, len(selected), row["status"])
    return {
        "schema": "bc-science-qwen-nemotron-dual-v1",
        "chapter": chapter, "source_sha256": _hash(document),
        "claims_planned": len(selected), "claims_processed": len(results),
        "results": results, "staged_drafts": staged,
        "published_modified": False, "scientifically_verified": False,
        "scope": (
            "Proof-of-concept for source-grounded corrections. "
            "Agreement is not independent scientific verification."
        ),
    }


def save_dual_repair(
    report: dict, document: str, output_dir: Path,
    *, source_root: Path, published_root: Path,
) -> Path:
    """Write only isolated reports and one full draft/diff per proposal."""
    target = output_dir.expanduser().resolve()
    protected = [source_root.resolve(), published_root.resolve()]
    for root in protected:
        if (
            target == root or target.is_relative_to(root)
            or root.is_relative_to(target)
        ):
            raise ValueError("Destinazione non isolata dagli originali o output live")
    if target.exists():
        raise ValueError("Directory report già esistente")
    if _hash(document) != report["source_sha256"]:
        raise ValueError("Documento cambiato durante la revisione")
    target.mkdir(parents=True, exist_ok=False)
    staged = report.pop("staged_drafts")
    records = []
    for index, corrected in staged:
        if _hash(corrected) != report["results"][index - 1]["proposal_sha256"]:
            raise ValueError("Hash proposta differente dal report")
        stem = f"proposta-{index:02d}"
        draft_path = target / f"{stem}.md"
        diff_path = target / f"{stem}.diff"
        draft_path.write_text(corrected, encoding="utf-8")
        diff_path.write_text(
            "".join(difflib.unified_diff(
                document.splitlines(keepends=True),
                corrected.splitlines(keepends=True),
                fromfile="originale", tofile=stem,
            )), encoding="utf-8",
        )
        records.append({
            "claim_index": index, "draft": str(draft_path),
            "diff": str(diff_path), "sha256": _hash(corrected),
        })
    report["proposals"] = records
    report_path = target / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    (target / "report.md").write_text(
        "\n".join([
            "# BC Science — Qwen + Nemotron · bozze non pubblicate", "",
            f"Affermazioni elaborate: {report['claims_processed']}",
            f"Proposte isolate: {len(records)}", "",
            "Due modelli concordi non certificano la verità scientifica.",
            "Le dispense originali possono contenere errori. Nessuna modifica live.",
            "",
            *[
                f"- {row['claim']['text'][:140]}: "
                f"**{row['status']}** — {row['reason']}"
                for row in report["results"]
            ],
        ]) + "\n", encoding="utf-8",
    )
    return report_path


def nvidia_key_from_environment() -> str:
    return os.environ.get("NVIDIA_API_KEY", "")
