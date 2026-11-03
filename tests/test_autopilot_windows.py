"""Long study chapters are reviewed one grounded window at a time."""
from bc_science.config import AppConfig
from bc_science.evidence_engine import OriginalEvidence
from bc_science.scientific_autopilot import (
    _grounded_windows,
    review_with_agents,
)
from bc_science.targeted_review import ReviewOutcome

MAIN = (
    "La membrana cellulare è composta da un doppio strato fosfolipidico "
    "che separa l'ambiente interno da quello esterno. "
    "Le proteine di membrana contribuiscono al trasporto delle sostanze "
    "e permettono alcune comunicazioni tra cellule differenti. "
    "Questi processi sono importanti per mantenere l'omeostasi e "
    "permettere gli scambi necessari al metabolismo cellulare."
)
SHORTER = (
    "La membrana cellulare è composta da un doppio strato fosfolipidico "
    "che separa l'ambiente interno da quello esterno. "
    "Le proteine di membrana contribuiscono al trasporto delle sostanze "
    "e alla comunicazione tra cellule."
)
OTHER = "\n\n".join(
    "Il capitolo affronta diversi aspetti della fisiologia cellulare "
    f"nella lezione {i} e descrive la regolazione dell'ambiente interno "
    "attraverso processi di trasporto e le interazioni con il metabolismo."
    for i in range(18)
)
CHAPTER = "## Cellula\n\n" + MAIN + "\n\n" + OTHER
EVIDENCE = [
    OriginalEvidence(
        source_path="dispensa.pdf", page=3, block=None,
        excerpt=MAIN, rank=1, provenance="original_pdf_page",
    ),
]


class NoModel:
    model = "spark-x2.5"


def test_grounded_windows_find_short_source_backed_fragment():
    windows = _grounded_windows("Cellula", CHAPTER, CHAPTER, EVIDENCE)
    assert windows
    assert windows[0][0] == MAIN
    assert len(windows[0][0].split()) < len(CHAPTER.split())
    assert all(line in windows[0][1] for line in MAIN.split(". "))
    assert windows[0][2] == EVIDENCE


def test_long_chapter_only_rewrites_window_leaving_everything_else(monkeypatch):
    import bc_science.scientific_autopilot as agents

    calls = []

    def fake_writer(title, source, previous, _config, **kwargs):
        calls.append((source, previous, kwargs))
        assert MAIN in previous
        assert OTHER not in previous
        assert kwargs["autopilot_mode"]
        return ReviewOutcome(
            title, True, "## Cellula\n\n" + SHORTER,
            "proposta solo per la porzione",
        )

    monkeypatch.setattr(agents, "review_chapter", fake_writer)
    monkeypatch.setattr(agents, "_critic_verdict", lambda *_args: (True, "review"))
    monkeypatch.setattr(agents, "_issues", lambda *_args: (0, 0, 0, 0))

    result = review_with_agents(
        "Cellula", CHAPTER, CHAPTER, AppConfig(),
        summarizer=NoModel(), original_evidence=EVIDENCE,
        max_attempts=3,
    )
    assert len(CHAPTER.split()) > 320
    assert result.status == "CANDIDATE_UNVERIFIED"
    assert result.requires_human_review
    assert len(result.attempts) == 1
    assert result.candidate == CHAPTER.replace(MAIN, SHORTER, 1)
    assert result.candidate.endswith(OTHER)
    assert len(calls) == 1


def test_no_original_page_overlap_blocks_without_model_call(monkeypatch):
    import bc_science.scientific_autopilot as agents

    unrelated = [OriginalEvidence(
        source_path="altro.pdf", page=9, block=None,
        excerpt="Le onde di gravità vengono descritte con una teoria fisica distinta.",
        rank=1, provenance="original_pdf_page",
    )]
    monkeypatch.setattr(
        agents, "review_chapter",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Never call Spark without matching original page")
        ),
    )
    result = review_with_agents(
        "Cellula", CHAPTER, CHAPTER, AppConfig(),
        summarizer=NoModel(), original_evidence=unrelated,
    )
    assert result.status == "BLOCKED_NO_GROUNDED_WINDOWS"
    assert result.candidate == CHAPTER


def test_numeric_facts_in_target_window_cannot_be_deleted(monkeypatch):
    import bc_science.scientific_autopilot as agents

    with_number = MAIN.replace(
        "un doppio strato", "un doppio strato di 2 livelli",
    )
    chapter = CHAPTER.replace(MAIN, with_number)
    source = [OriginalEvidence(
        source_path="dispensa.pdf", page=3, block=None, excerpt=with_number,
        rank=1, provenance="original_pdf_page",
    )]
    monkeypatch.setattr(
        agents, "review_chapter",
        lambda title, _source, _previous, _config, **_kwargs: (
            ReviewOutcome(title, True, "## Cellula\n\n" + SHORTER, "proposta")
        ),
    )
    monkeypatch.setattr(agents, "_issues", lambda *_args: (0, 0, 0, 0))
    monkeypatch.setattr(
        agents, "_critic_verdict",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("The critic should not run if a number disappeared")
        ),
    )
    result = review_with_agents(
        "Cellula", chapter, chapter, AppConfig(),
        summarizer=NoModel(), original_evidence=source,
        max_attempts=1,
    )
    assert result.status == "BLOCKED_FOR_MANUAL_REVIEW"
    assert result.attempts[0].status == "REJECTED"
    assert "Numeri" in result.attempts[0].reason
    assert result.candidate == chapter


def test_grounded_windows_do_not_select_exam_bullets():
    chapter = (
        "## Cellula\n\n"
        + MAIN
        + "\n\n### Da ricordare per l'esame\n\n"
        + "- Un promemoria non deve essere selezionato come testo da riscrivere.\n\n" + OTHER
    )
    windows = _grounded_windows("Cellula", chapter, chapter, EVIDENCE)
    assert windows
    assert all(not span.startswith("- ") for span, _source, _hits in windows)
    assert all(span in chapter for span, _source, _hits in windows)
