"""Unit tests for density measurement (T2.2/T2.3) and the Scribe rubric integration."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.density import audit_density, extract_entities, extract_stats
from agents.scribe import ReasoningEngine

DENSE = (
    "The Federal Reserve held rates at 5.25% in 2026, while the BLS reported "
    "inflation cooled to 2.9% in June. According to the CFPB, Americans paid "
    "$14.3 billion in overdraft fees last year. A Harvard study of 4,200 households "
    "found that 68% of respondents underestimated the DOE's estimate of $1,200 in "
    "annual energy costs. The Inflation Reduction Act covers 30% of costs. "
    "NerdWallet and Bankrate both rate the Wells Fargo Active Cash card 4.5/5. "
    "Medicare Part B premiums rose $9.90 per month. The NIH recommends 150 minutes "
    "of weekly exercise. Goldman Sachs projects 2.1% GDP growth. JPMorgan Chase "
    "agrees, and the University of Michigan Consumer Sentiment Index confirms the "
    "trend while the Social Security Administration adjusts benefits and Zillow "
    "tracks housing."
)

SPARSE = (
    "Saving money is important for your future. Many people find it hard to save. "
    "You should try to put away a little money every month. Over time this adds up "
    "and helps you build better habits for the long run ahead of you."
)


class TestExtractStats:
    def test_counts_dollar_amounts(self):
        assert extract_stats("It costs $1,299 or $14.3 billion total.") >= 2

    def test_counts_percentages(self):
        assert extract_stats("Rates hit 5.25% then fell to 3%") >= 2

    def test_sparse_text_scores_low(self):
        assert extract_stats(SPARSE) <= 1

    def test_dense_text_scores_high(self):
        assert extract_stats(DENSE) >= 10


class TestExtractEntities:
    def test_finds_multiword_phrases(self):
        entities = extract_entities(DENSE)
        assert any("Federal Reserve" in e for e in entities)
        assert any("Inflation Reduction Act" in e for e in entities)

    def test_finds_acronyms(self):
        entities = extract_entities(DENSE)
        assert "BLS" in entities
        assert "CFPB" in entities

    def test_excludes_stopwords(self):
        entities = extract_entities("However, this means the end result is clear.")
        assert "However" not in entities
        assert not any(e.lower() == "this" for e in entities)

    def test_distinct_count(self):
        entities = extract_entities(
            "The Federal Reserve met. The Federal Reserve met again. BLS and NIH agree."
        )
        assert entities == {"Federal Reserve", "BLS", "NIH"}


class TestAuditDensity:
    def test_report_fields(self):
        report = audit_density(DENSE)
        assert report.word_count > 50
        assert report.stat_count >= 10
        assert report.entity_count >= 15
        assert isinstance(report.entities, list)

    def test_gates(self):
        assert audit_density(DENSE).passes_stats_gate is True
        assert audit_density(SPARSE).passes_stats_gate is False
        assert audit_density(SPARSE).passes_entity_gate is False

    def test_empty_content_safe(self):
        report = audit_density("")
        assert report.stats_per_1k == 0.0
        assert report.entity_count == 0


class TestRubricIntegration:
    def _base_content(self) -> str:
        words = " ".join(["analysis"] * 900)
        return f"## Overview\n\n{DENSE} {words}\n\n## Details\n\nMore depth here."

    def test_dense_content_earns_full_evidence_points(self):
        content = self._base_content()
        score, critiques = ReasoningEngine.evaluate_draft(content, "Title", "money", 800)
        joined = " ".join(critiques).lower()
        assert "statistics density" not in joined
        assert "named-entity" not in joined

    def test_sparse_content_triggers_both_critiques(self):
        words = " ".join(["analysis"] * 900)
        content = f"## Overview\n\n{SPARSE} {words}\n\n## Details\n\nMore depth here."
        _, critiques = ReasoningEngine.evaluate_draft(content, "Title", "money", 800)
        joined = " ".join(critiques).lower()
        assert "statistics density" in joined
        assert "named-entity" in joined

    def test_score_never_exceeds_100(self):
        content = self._base_content() * 3
        score, _ = ReasoningEngine.evaluate_draft(content, "Title", "money", 800)
        assert score <= 100
