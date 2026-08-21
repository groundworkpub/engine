#!/usr/bin/env python3
"""Unit tests for ReasoningEngine and Scribe Critic-Reflect loop."""

import unittest
from unittest.mock import MagicMock, patch

from agents.scribe import ReasoningEngine, refine_decaying_article


class TestScribeReasoning(unittest.TestCase):
    def test_reasoning_engine_evaluation_pass(self) -> None:
        content = """## Core Strategy Breakdown
At Groundwork, our empirical analysis of 450 loan products shows that 15-year fixed mortgages save an average of $68,400 in interest over standard 30-year amortizations. Lower rates reduce lifetime interest costs substantially.

### Key Numerical Benchmarks
- Benchmark Rate: 5.8% APY
- Average Closing Cost: $3,200
- 10-Year Equity Growth: 42%

## Actionable Decision Matrix
Homeowners should evaluate their debt-to-income ratio before proceeding. Refinancing makes sense when the break-even period is under twenty-four months. Calculate every fee carefully. Borrowers must consider origination points, title search costs, and appraisal expenses before signing loan agreements with prospective lenders."""

        score, critiques = ReasoningEngine.evaluate_draft(
            content=content,
            title="How to Refinance Your Mortgage",
            pillar="money",
            min_words=50,
        )
        self.assertGreaterEqual(score, 85)

    def test_reasoning_engine_evaluation_fail(self) -> None:
        # Under-length, no headings, slop phrases
        content = "Furthermore, in conclusion, this game-changer article will delve into modern finances without data."
        score, critiques = ReasoningEngine.evaluate_draft(
            content=content,
            title="Finance Tips",
            pillar="money",
            min_words=500,
        )
        self.assertLess(score, 85)
        self.assertTrue(any("slop" in c.lower() for c in critiques))
        self.assertTrue(any("under-length" in c.lower() for c in critiques))

    @patch("agents.scribe.call_llm_with_fallback")
    def test_refine_decaying_article(self, mock_llm: MagicMock) -> None:
        import json

        mock_payload = {
            "slug": "mortgage-refinance-guide",
            "title": "Refinancing Your Mortgage: 2026 Strategy Guide",
            "content": "## Direct Answer\nRefinancing allows homeowners to lock in 5.8% rates.\n\n### Key Benchmarks\nData shows $64,000 savings.\n" + ("Detailed guidance for home equity decisions. " * 30),
            "excerpt": "Evidence-backed guide on mortgage refinancing in 2026.",
            "schema_type": "Article",
            "takeaway": "Locking lower interest rates saves thousands over the loan lifecycle.",
            "expert_comment": "Review your break-even horizon before paying points.",
            "faq": [
                {"question": "When is refinancing worth it for homeowners?", "answer": "When interest rates drop at least 0.75% below your current note rate."},
                {"question": "How much are closing costs on average?", "answer": "Typically 2% to 5% of the total loan amount."},
                {"question": "Does refinancing hurt your credit score?", "answer": "A temporary 5-point dip from the hard inquiry occurs."}
            ]
        }
        mock_llm.return_value = json.dumps(mock_payload)

        mock_supabase = MagicMock()
        mock_supabase.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = {
            "slug": "mortgage-refinance-guide",
            "title": "Mortgage Refinance",
            "pillar": "money",
            "content": "Old short content.",
        }

        success = refine_decaying_article(
            slug="mortgage-refinance-guide",
            gsc_metrics={"top_queries": ["how to refinance", "mortgage rates 2026"], "impressions": 1200, "ctr": 0.015},
            supabase=mock_supabase,
            config={},
        )
        self.assertTrue(success)
        mock_supabase.table.return_value.update.assert_called()


if __name__ == "__main__":
    unittest.main()
