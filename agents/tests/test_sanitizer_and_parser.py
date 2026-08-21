"""Unit Tests for EditorialSanitizer, cJSON-Style JSON Extraction, and Schema Validation."""

import json
import pytest
from agents.humanizer import EditorialSanitizer
from agents.seo_optimizer import OptimizedContentResult, optimize_article_content


def test_sanitize_conversational_preamble():
    dirty_text = (
        "Here's the optimized article for Groundwork's MONEY pillar:\n\n"
        "**title**: Mortgage Refinance Guide\n\n"
        "**excerpt**: A great guide to mortgage refinancing.\n\n"
        "**content**:\n\n"
        "Refinancing your mortgage requires calculating closing costs.\n\n"
        "## Comparative Benchmark\n\n"
        "| Rate | Savings |\n| --- | --- |\n| 6.0% | $200 |\n\n"
        "**primary_intent**: informational\n"
        "**seo_score**: 98\n"
    )
    cleaned = EditorialSanitizer.sanitize_article_prose(dirty_text)

    # Must NOT contain conversational preambles or key-value headers
    assert "Here's the optimized article" not in cleaned
    assert "**title**:" not in cleaned
    assert "**excerpt**:" not in cleaned
    assert "**content**:" not in cleaned
    assert "**primary_intent**:" not in cleaned
    assert "**seo_score**:" not in cleaned

    # Must retain actual article prose and table
    assert "Refinancing your mortgage requires calculating closing costs." in cleaned
    assert "## Comparative Benchmark" in cleaned
    assert "| 6.0% | $200 |" in cleaned


def test_sanitize_meta_scaffolding_headings():
    dirty_text = (
        "Refinancing can save thousands over a 30-year amortization schedule.\n\n"
        "## AEO Summary Box\n"
        "The break-even point is calculated by dividing total closing costs by monthly payment savings.\n\n"
        "## Understanding Closing Costs\n"
        "Closing costs range between 2% and 5% of loan balance.\n\n"
        "## LSI Keywords Injected\n"
        "- mortgage refinance\n"
        "- interest rate spread\n"
        "- closing costs\n\n"
        "## Decision Checklist\n"
        "Always check remaining loan duration before locking a rate."
    )
    cleaned = EditorialSanitizer.sanitize_article_prose(dirty_text)

    # Must strip AEO Summary Box heading and LSI Keywords Injected section
    assert "## AEO Summary Box" not in cleaned
    assert "## LSI Keywords Injected" not in cleaned
    assert "- interest rate spread" not in cleaned

    # Must preserve normal headings and text
    assert "Refinancing can save thousands" in cleaned
    assert "## Understanding Closing Costs" in cleaned
    assert "## Decision Checklist" in cleaned
    assert "Always check remaining loan duration" in cleaned


def test_editorial_humanizer_slop_replacement():
    slop_text = (
        "It is crucial to delve into the tapestry of mortgage options, "
        "as a testament to smart financial management in today's fast-paced world."
    )
    cleaned = EditorialSanitizer.sanitize_article_prose(slop_text)

    assert "delve into" not in cleaned
    assert "a testament to" not in cleaned
    assert "in today's fast-paced world" not in cleaned
    assert "crucial" not in cleaned


def test_pydantic_schema_validation_valid():
    valid_payload = {
        "title": "How to calculate your mortgage refinance break-even point",
        "excerpt": "Calculate your exact mortgage refinance break-even timeline by comparing closing costs.",
        "content": (
            "Refinancing a mortgage requires evaluating closing costs and interest rate spreads across time. "
            "Homeowners must determine whether cumulative monthly payment reductions exceed upfront fees."
        ),
        "primary_intent": "informational",
        "aeo_summary": "Divide total closing costs by monthly savings to find your break-even month.",
        "lsi_keywords_injected": ["closing costs", "loan balance"],
        "seo_score": 95,
        "geo_benchmark_present": True,
    }
    result = OptimizedContentResult(**valid_payload)
    assert result.title == valid_payload["title"]
    assert result.seo_score == 95
    assert result.primary_intent == "informational"


def test_fail_closed_on_invalid_output(monkeypatch):
    # Mock LLM returning completely unparseable garbage
    def mock_call_llm_json(*args, **kwargs):
        return None

    def mock_call_llm(*args, **kwargs):
        return "I am unable to optimize this text because of server overload."

    monkeypatch.setattr("agents.seo_optimizer.call_llm_json", mock_call_llm_json)
    monkeypatch.setattr("agents.seo_optimizer.call_llm", mock_call_llm)

    res = optimize_article_content(
        title="Sample Mortgage Title",
        content="Original clean content of sample article.",
        pillar="money",
    )

    # Fail-closed invariant: MUST return None rather than dumping error text
    assert res is None
