"""Safety and readability checks for scientific summaries."""
import json

from bc_science.fast_audit import audit_fast_essential
from bc_science.quality_signals import assess_chapter, critical_facts
from bc_science.study_editions import make_fast_essential_chapter
from bc_science.targeted_review import _validate_proposal, review_chapter


SOURCE = (
    "## Stomaco\n\n### Spiegazione\n"
    "Lo stomaco è costituito da diversi strati e permette la digestione. "
    "Le fibre muscolari consentono il mescolamento del contenuto gastrico.\n"
    "La parete dello stomaco presenta 3 strati muscolari. "
    "La digestione dipende da processi meccanici e chimici.\n"
    "I vasi sanguigni trasportano le sostanze nutritive. "
    "Esistono differenti classificazioni delle componenti anatomiche.\n"
)
ESSENTIAL = (
    "## Stomaco\n\nLo stomaco è costituito da diversi strati e permette la "
    "digestione. La parete dello stomaco presenta 3 strati muscolari."
)


def test_critical_facts_have_source_evidence_and_multiple_types():
    facts = critical_facts(SOURCE)
    categories = {fact.category for fact in facts}
    assert "number_or_formula" in categories
    assert "definition" in categories or "mechanism" in categories
    assert all(fact.quote in SOURCE for fact in facts)


def test_quality_flags_possible_missing_mechanism_without_calling_it_proven():
    report = assess_chapter("Stomaco", SOURCE, ESSENTIAL)
    assert report.key_facts >= 2
    assert report.possible_missing
    assert report.review_priority in {"bassa", "media", "alta"}
    assert not report.numerical_mismatch


def test_new_selector_prioritizes_source_concepts_and_numbers():
    result = make_fast_essential_chapter("Stomaco", SOURCE)
    assert "3 strati muscolari" in result.markdown
    assert "costituito" in result.markdown
    assert result.markdown.count("## Stomaco") == 1
    assert "Spiegazione ordinata" not in result.markdown


def test_audit_does_not_confuse_pass_with_scientific_verification():
    full = "# Anatomia\n\n## Indice degli argomenti\n- Stomaco\n\n---\n\n" + SOURCE
    short = "# Anatomia essenziale\n\n## Indice degli argomenti\n- Stomaco\n\n---\n\n" + ESSENTIAL
    audit = audit_fast_essential(full, short)
    assert audit.status in {"PASS", "WARN"}
    assert audit.content_review_status == "NON_VERIFICATO_SCIENTIFICAMENTE"
    assert audit.chapters_needing_review >= 1
    assert audit.chapter_signals[0]["title"] == "Stomaco"
    assert audit.chapter_signals[0]["possible_missing"]


def test_targeted_qwen_rejects_fake_evidence_or_invented_number():
    invalid = {
        "rewrite": "## Stomaco\n\nLo stomaco ha 99 strati muscolari.",
        "evidence": ["Lo stomaco ha 99 strati muscolari."],
    }
    assert not _validate_proposal("Stomaco", SOURCE, ESSENTIAL, invalid)[0]
    unsupported = {
        "rewrite": "## Stomaco\n\nLo stomaco ha 99 strati muscolari.",
        "evidence": ["La parete dello stomaco presenta 3 strati muscolari."],
    }
    assert not _validate_proposal("Stomaco", SOURCE, ESSENTIAL, unsupported)[0]


def test_targeted_review_requires_exact_quotes_and_keeps_original_on_failure():
    class FakeDb:
        pass

    class FakeSummarizer:
        model = "qwen3.5:4b"
        db = FakeDb()
        calls = 0

        def _cached_chat(self, *args, **kwargs):
            self.calls += 1
            return json.dumps({
                "rewrite": "## Stomaco\n\nLo stomaco contiene sostanze inventate.",
                "evidence": ["Non presente nel documento originale."],
            })
    model = FakeSummarizer()
    result = review_chapter(
        "Stomaco", SOURCE, ESSENTIAL, None, summarizer=model,
    )
    assert not result.applied
    assert result.text == ESSENTIAL
    assert model.calls == 1


def test_local_model_not_called_when_no_critical_signal():
    source = (
        "## Neuroni\n\nIl neurone è costituito da soma e assoni. "
        "I neuroni permettono la trasmissione dei segnali."
    )
    class FakeModel:
        model = "qwen3.5:4b"
        def _cached_chat(self, *args, **kwargs):
            raise AssertionError("Unneeded Ollama call")
    # Exact same text means no missing candidates and good readability.
    result = review_chapter(
        "Neuroni", source, source, None, summarizer=FakeModel(),
    )
    assert not result.applied
