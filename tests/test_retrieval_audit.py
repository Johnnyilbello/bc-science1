from bc_science.retrieval_audit import RetrievalCase, evaluate_retrieval


def test_retrieval_audit_scores_ranked_expected_sources():
    cases = [
        RetrievalCase("contrazione?", ("contrazione muscolare",)),
        RetrievalCase("ginocchio?", ("articolazione del ginocchio",)),
    ]
    hits = [
        [
            {"source": "Contrazione muscolare 2.pdf"},
            {"source": "Altro.pdf"},
        ],
        [
            {"source": "Altro.pdf"},
            {"source": "Articolazione del ginocchio.pdf"},
        ],
    ]

    report = evaluate_retrieval(cases, hits)

    assert report["cases"] == 2
    assert report["hit_at_k"] == 1.0
    assert report["top1"] == 0.5
    assert report["mrr"] == 0.75
    assert report["results"][0]["rank"] == 1
    assert report["results"][1]["rank"] == 2


def test_retrieval_audit_normalizes_apostrophes():
    cases = [
        RetrievalCase(
            "termoregolazione?",
            ("termoregolazione durante l’esercizio fisico",),
        )
    ]
    hits = [[{"source": "Termoregolazione durante l'esercizio fisico.pdf"}]]

    report = evaluate_retrieval(cases, hits)

    assert report["hit_at_k"] == 1.0
    assert report["results"][0]["rank"] == 1


def test_retrieval_audit_fails_when_expected_source_is_missing():
    cases = [RetrievalCase("rene?", ("tubuli renali",))]
    hits = [[{"source": "Sistema respiratorio.pdf"}]]

    report = evaluate_retrieval(cases, hits)

    assert report["status"] == "FAIL"
    assert report["hit_at_k"] == 0.0
    assert report["mrr"] == 0.0
