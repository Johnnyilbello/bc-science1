from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RetrievalCase:
    question: str
    expected_sources: tuple[str, ...]


DEFAULT_RETRIEVAL_CASES = (
    RetrievalCase(
        "Quali sono i passaggi fondamentali della contrazione muscolare?",
        ("contrazione muscolare",),
    ),
    RetrievalCase(
        "Spiegami le fasi del ciclo cardiaco.",
        ("ciclo cardiaco",),
    ),
    RetrievalCase(
        "Che cosa si intende per ventilazione alveolare?",
        ("ventilazione alveolare",),
    ),
    RetrievalCase(
        "Descrivi l'articolazione del ginocchio.",
        ("articolazione del ginocchio",),
    ),
    RetrievalCase(
        "Quali strutture formano l'articolazione della spalla?",
        ("articolazione della spalla",),
    ),
    RetrievalCase(
        "Quali muscoli appartengono al cingolo scapolare?",
        ("muscoli del cingolo scapolare",),
    ),
    RetrievalCase(
        "Qual è il ruolo delle vie motorie?",
        ("vie motorie",),
    ),
    RetrievalCase(
        "Come sono organizzate le vie sensitive?",
        ("vie sensitive",),
    ),
    RetrievalCase(
        "Quali sono struttura e funzioni principali del midollo spinale?",
        ("midollo spinale",),
    ),
    RetrievalCase(
        "Come funzionano i tubuli renali?",
        ("tubuli renali", "tubulo renale"),
    ),
    RetrievalCase(
        "Qual è il rapporto tra ipotalamo e ipofisi nel sistema endocrino?",
        ("ipotalamo e ipofisi",),
    ),
    RetrievalCase(
        "Come avvengono digestione e assorbimento dei nutrienti?",
        ("digestione e assorbimento",),
    ),
    RetrievalCase(
        "Che differenza c'è tra potenziale d'azione e potenziale graduato?",
        ("potenziali d’azione", "potenziali d'azione", "propagazione potenziale"),
    ),
    RetrievalCase(
        "Come avvengono gli scambi di gas nell'apparato respiratorio?",
        ("scambi di gas",),
    ),
    RetrievalCase(
        "Come risponde l'organismo allo stress termico durante l'esercizio?",
        ("stress termico",),
    ),
    RetrievalCase(
        "Come funziona la termoregolazione durante l'esercizio fisico?",
        ("termoregolazione durante l’esercizio fisico", "termoregolazione durante l'esercizio fisico"),
    ),
    RetrievalCase(
        "Quali sono le basi del metabolismo energetico?",
        ("metabolismo energetico",),
    ),
    RetrievalCase(
        "Come avviene il reclutamento delle unità motorie?",
        ("reclutamento unità motorie",),
    ),
    RetrievalCase(
        "Da cosa dipende la pressione sanguigna?",
        ("pressione sanguigna",),
    ),
    RetrievalCase(
        "Quali sono i meccanismi della filtrazione glomerulare?",
        ("controllo filtrazione glomerulare", "filtrazione glomerulare"),
    ),
)


def _normalize(value: str) -> str:
    return value.casefold().replace("’", "'").replace("‘", "'")


def _matching_rank(case: RetrievalCase, hits: list[dict]) -> int | None:
    expected = tuple(_normalize(item) for item in case.expected_sources)
    for index, hit in enumerate(hits, start=1):
        source = _normalize(Path(str(hit["source"])).stem)
        if any(fragment in source for fragment in expected):
            return index
    return None


def evaluate_retrieval(
    cases: tuple[RetrievalCase, ...] | list[RetrievalCase],
    hits_by_question: list[list[dict]],
) -> dict:
    if len(cases) != len(hits_by_question):
        raise ValueError("cases e hits_by_question devono avere la stessa lunghezza")

    rows: list[dict] = []
    top1 = 0
    hits = 0
    reciprocal_sum = 0.0

    for case, case_hits in zip(cases, hits_by_question, strict=True):
        rank = _matching_rank(case, case_hits)
        if rank is not None:
            hits += 1
            reciprocal_sum += 1.0 / rank
            if rank == 1:
                top1 += 1

        rows.append(
            {
                "question": case.question,
                "expected_sources": list(case.expected_sources),
                "rank": rank,
                "top_sources": [
                    Path(str(hit["source"])).name
                    for hit in case_hits[:3]
                ],
            }
        )

    total = len(cases)
    hit_rate = hits / total if total else 0.0
    top1_rate = top1 / total if total else 0.0
    mrr = reciprocal_sum / total if total else 0.0

    if hit_rate >= 0.90 and mrr >= 0.65:
        status = "PASS"
    elif hit_rate >= 0.75 and mrr >= 0.45:
        status = "WARN"
    else:
        status = "FAIL"

    return {
        "status": status,
        "cases": total,
        "hit_at_k": hit_rate,
        "top1": top1_rate,
        "mrr": mrr,
        "results": rows,
    }
