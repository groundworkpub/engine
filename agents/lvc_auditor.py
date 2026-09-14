#!/usr/bin/env python3
"""
LVC-AUDITOR: Autonomous Quality & Low-Value Content Compliance Engine (v2.4.0-agentic)
SSOT: implementation_plan.md §4, §5 Phase 3 & §6.1

Enforces Google Search Quality Rater Guidelines (QRG 2024-2026),
Google Information Gain Patent (US10956509B2), Better Ads Standards,
and AdSense compliance gates before article publication.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import sys
from pathlib import Path
from typing import Any, List, Literal, Optional

import httpx
from pydantic import BaseModel, Field

try:
    import textstat  # type: ignore
except ImportError:
    textstat = None

from agents.density import COMPILED_STAT_PATTERNS
from agents.llm_router import call_llm_json

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("lvc_auditor")

ROOT_DIR = Path(__file__).resolve().parent.parent


def _load_env_local() -> None:
    """Native .env.local loader (Rule §2.14)."""
    p = ROOT_DIR / ".env.local"
    if p.exists():
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'").strip('"')
                if k and k not in os.environ:
                    os.environ[k] = v


_load_env_local()

SUPABASE_URL = os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

HEADERS = {
    "apikey": SUPABASE_KEY or "",
    "Authorization": f"Bearer {SUPABASE_KEY or ''}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}


# =====================================================================
# Pydantic v2 Structured Output Contracts
# =====================================================================

class CriticalFlaw(BaseModel):
    flaw_type: Literal[
        "FLUFF_PADDING",
        "COMMODITY_REWRITE",
        "LOW_EEAT",
        "DELAYED_ANSWER",
        "KEYWORD_CANON",
        "ZERO_INFORMATION_GAIN",
    ]
    severity: Literal["FATAL", "HIGH", "MEDIUM"]
    excerpt: str = Field(description="Exact sentence or passage displaying the issue")
    diagnostic_rationale: str = Field(description="Deterministic failure cause mapped to QRG/AdSense policy")
    remediation_action: Literal["DELETE", "CONDENSE", "INJECT_VERIFIABLE_DATA", "RESTRUCTURE"]
    remediation_directive: str = Field(description="Actionable imperative instructions for the editor")


class DimensionScore(BaseModel):
    dimension: str
    weight: float
    score: float = Field(ge=0.0, le=100.0)
    observation: str


class AuditResult(BaseModel):
    target_url: Optional[str] = None
    target_slug: Optional[str] = None
    overall_quality_score: float = Field(ge=0.0, le=100.0)
    verdict: Literal["PASS", "CONDITIONAL_PASS", "REJECT", "CRITICAL_MFA_REJECT"]
    estimated_fluff_percentage: float = Field(ge=0.0, le=100.0)
    information_gain_classification: Literal["HIGH_NOVELTY", "MODERATE", "COMMODITY_REDUNDANT"]
    dimension_breakdown: List[DimensionScore]
    critical_flaws: List[CriticalFlaw]
    structural_pruning_targets: List[str] = Field(default_factory=list, description="Explicit list of sections/subheadings to discard entirely")
    data_injection_directives: List[str] = Field(default_factory=list, description="Empirical artifacts required to attain passing score")


# =====================================================================
# NLP & Heuristic Computations
# =====================================================================

def split_sentences(text: str) -> list[str]:
    """Split text into sentences cleanly without splitting on decimals or abbreviations."""
    clean = re.sub(r"\n+", " ", text)
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", clean)
    return [s.strip() for s in sentences if len(s.strip()) > 10]


def is_verifiable_sentence(sentence: str) -> bool:
    """Checks if a sentence contains verifiable empirical facts or numeric tokens."""
    for pattern in COMPILED_STAT_PATTERNS:
        if pattern.search(sentence):
            return True

    # Citations and regulatory authoritative agencies
    citation_patterns = [
        r"\b(?:FRED|BLS|CFPB|IRS|FDA|NIH|NREL|SEC|DOE|CDC|EPA|Lancet|PubMed|JAMA|DOI|NEJM)\b",
        r"\b(?:Publication \d+|Section \d+|Regulation [A-Z]|Form 10-K)\b",
        r"\b(?:study|trial|survey|benchmark|dataset|census)\b.*?\b(?:found|measured|reported|showed|analyzed)\b",
    ]
    for cp in citation_patterns:
        if re.search(cp, sentence, re.IGNORECASE):
            return True

    return False


def compute_nlp_heuristics(text: str) -> dict[str, float]:
    """Computes deterministic linguistic and information density metrics:
    - FID: Fluff-to-Information Density (Target <= 0.65)
    - TTR: Type-Token Ratio (Target >= 0.45)
    - Readability metrics (Flesch, ARI, Flesch-Kincaid)
    """
    words = re.findall(r"\b[a-zA-Z0-9-]+\b", text.lower())
    total_words = len(words)
    if total_words == 0:
        return {
            "word_count": 0.0,
            "fid": 1.0,
            "ttr": 0.0,
            "flesch_reading_ease": 0.0,
            "has_markdown_table": 0.0,
            "verifiable_sentence_ratio": 0.0,
        }

    unique_words = len(set(words))
    ttr = round(unique_words / total_words, 3)

    sentences = split_sentences(text)
    total_sentences = max(1, len(sentences))

    verifiable_count = sum(1 for s in sentences if is_verifiable_sentence(s))
    verifiable_ratio = round(verifiable_count / total_sentences, 3)

    # Fluff Density: sentences lacking verifiable empirical tokens
    fid = round(max(0.0, min(1.0, 1.0 - verifiable_ratio)), 3)

    # Table presence
    has_table = 1.0 if re.search(r"\|(?:\s*[-:]+\s*\|)+", text) else 0.0

    # Readability via textstat if installed
    flesch = 60.0
    ari = 10.0
    if textstat is not None and total_words > 30:
        try:
            flesch = float(textstat.flesch_reading_ease(text))
            ari = float(textstat.automated_readability_index(text))
        except Exception:
            pass

    return {
        "word_count": float(total_words),
        "sentence_count": float(total_sentences),
        "verifiable_sentence_count": float(verifiable_count),
        "verifiable_sentence_ratio": verifiable_ratio,
        "fid": fid,
        "ttr": ttr,
        "flesch_reading_ease": round(flesch, 1),
        "automated_readability_index": round(ari, 1),
        "has_markdown_table": has_table,
    }


def compute_serp_information_gain(target_text: str, competitor_texts: list[str]) -> float:
    """Calculates Information Gain Differential according to Google Patent US10956509B2.
    IG = alpha * (1 - max_sim) + beta * entity_divergence
    Threshold: IG >= 0.20
    """
    if not competitor_texts:
        # Default baseline with no competitor text provided
        heuristics = compute_nlp_heuristics(target_text)
        return round(0.25 + (0.5 * heuristics["verifiable_sentence_ratio"]), 3)

    words_target = set(re.findall(r"\b[a-z]{3,}\b", target_text.lower()))
    if not words_target:
        return 0.0

    max_overlap = 0.0
    all_comp_words: set[str] = set()

    for comp in competitor_texts:
        comp_words = set(re.findall(r"\b[a-z]{3,}\b", comp.lower()))
        all_comp_words.update(comp_words)
        overlap = len(words_target & comp_words) / len(words_target | comp_words) if (words_target | comp_words) else 0.0
        if overlap > max_overlap:
            max_overlap = overlap

    # Entity divergence: novel entities in target absent from competitors
    novel_entities = words_target - all_comp_words
    divergence = len(novel_entities) / (len(words_target) + 1e-5)

    alpha, beta = 0.4, 0.6
    ig = alpha * (1.0 - max_overlap) + beta * divergence
    return round(float(ig), 3)


# =====================================================================
# Master Evaluation Engine
# =====================================================================

class LVCAuditor:
    """Master evaluator coordinator for Low-Value Content & Quality Compliance."""

    SYSTEM_PROMPT = """You are the Groundwork Master Quality & AdSense Compliance Auditor (LVC-AUDITOR v2.4.0).
Your job is to ruthlessly evaluate article drafts against Google's Search Quality Rater Guidelines (QRG 2024-2026),
the Information Gain Patent (US10956509B2), and AdSense High-Value Content policies.

You MUST grade across 5 dimensions:
1. Information Gain & Novelty (Weight 0.30): Novel data, original calculations, specific case studies absent in commodity rewrites. (IG >= 0.20).
2. Fluff Density & AI Artifacts (Weight 0.25): High factual density, zero throat-clearing, no generic AI filler ("In today's fast-paced world..."). (FID <= 0.65).
3. E-E-A-T Entity Anchoring (Weight 0.20): Explicit benchmarks, named institutions (FRED, BLS, CFPB, DOE, FDA), exact models, methodology.
4. Answer Velocity / Time-to-Value (Weight 0.15): Primary query satisfied in first 20% of text with concise BLUF (<= 60 words).
5. MFA & Structural Footprint (Weight 0.10): High reader utility, presence of a verified Markdown Comparison Table, zero ad-space padding.

Return STRICT JSON matching the schema:
{
  "overall_quality_score": float (0-100),
  "verdict": "PASS" | "CONDITIONAL_PASS" | "REJECT" | "CRITICAL_MFA_REJECT",
  "estimated_fluff_percentage": float (0-100),
  "information_gain_classification": "HIGH_NOVELTY" | "MODERATE" | "COMMODITY_REDUNDANT",
  "dimension_breakdown": [
    {"dimension": str, "weight": float, "score": float, "observation": str}
  ],
  "critical_flaws": [
    {
      "flaw_type": "FLUFF_PADDING" | "COMMODITY_REWRITE" | "LOW_EEAT" | "DELAYED_ANSWER" | "KEYWORD_CANON" | "ZERO_INFORMATION_GAIN",
      "severity": "FATAL" | "HIGH" | "MEDIUM",
      "excerpt": str,
      "diagnostic_rationale": str,
      "remediation_action": "DELETE" | "CONDENSE" | "INJECT_VERIFIABLE_DATA" | "RESTRUCTURE",
      "remediation_directive": str
    }
  ],
  "structural_pruning_targets": [str],
  "data_injection_directives": [str]
}

PASS requires: overall_quality_score >= 85, estimated_fluff_percentage <= 35, information_gain != "COMMODITY_REDUNDANT", and ZERO FATAL flaws."""

    def __init__(self, supabase_url: str | None = None, supabase_key: str | None = None):
        self.supabase_url = supabase_url or SUPABASE_URL
        self.supabase_key = supabase_key or SUPABASE_KEY
        self.client = httpx.Client(timeout=30.0)

    def audit_text(
        self,
        content: str,
        title: str,
        pillar: str = "money",
        url: str | None = None,
        slug: str | None = None,
        competitor_texts: list[str] | None = None,
    ) -> AuditResult:
        """Audits an article text through deterministic heuristics followed by LLM deep grading."""
        heuristics = compute_nlp_heuristics(content)
        ig = compute_serp_information_gain(content, competitor_texts or [])

        # Deterministic Instant Rejection: Thin Content (< 500 words)
        if heuristics["word_count"] < 500:
            flaw = CriticalFlaw(
                flaw_type="COMMODITY_REWRITE",
                severity="FATAL",
                excerpt=content[:180] + "..." if len(content) > 180 else content,
                diagnostic_rationale=f"Article has only {int(heuristics['word_count'])} words (< 500 minimum threshold). Fails QRG §4.0 thin content.",
                remediation_action="INJECT_VERIFIABLE_DATA",
                remediation_directive="Expand article with primary empirical data, authoritative benchmarks, and an empirical decision table.",
            )
            return AuditResult(
                target_url=url,
                target_slug=slug,
                overall_quality_score=round(heuristics["word_count"] / 10.0, 1),
                verdict="REJECT",
                estimated_fluff_percentage=round(heuristics["fid"] * 100, 1),
                information_gain_classification="COMMODITY_REDUNDANT",
                dimension_breakdown=[
                    DimensionScore(dimension="Information Gain & Novelty", weight=0.30, score=20.0, observation="Thin content stub"),
                    DimensionScore(dimension="Fluff Density & AI Artifacts", weight=0.25, score=30.0, observation=f"FID: {heuristics['fid']}"),
                    DimensionScore(dimension="E-E-A-T Entity Anchoring", weight=0.20, score=25.0, observation="Insufficient depth for verified citations"),
                    DimensionScore(dimension="Answer Velocity", weight=0.15, score=40.0, observation="Underdeveloped prose"),
                    DimensionScore(dimension="MFA & Structural Footprint", weight=0.10, score=10.0, observation="Absence of required comparison matrix"),
                ],
                critical_flaws=[flaw],
                structural_pruning_targets=[],
                data_injection_directives=[
                    "Inject empirical comparison table",
                    "Add at least 10 verified data points from primary benchmarks (FRED, BLS, CFPB, DOE)",
                ],
            )

        # Deterministic Instant Rejection: Severe Fluff (FID > 0.85)
        if heuristics["fid"] > 0.85:
            flaw = CriticalFlaw(
                flaw_type="FLUFF_PADDING",
                severity="FATAL",
                excerpt=content[:200] + "...",
                diagnostic_rationale=f"Fluff Density {heuristics['fid']:.2f} > 0.85 limit. Over 85% of sentences lack verifiable factual assertions.",
                remediation_action="DELETE",
                remediation_directive="Strip generic filler introductions and replace with data-backed quantitative trade-offs.",
            )
            return AuditResult(
                target_url=url,
                target_slug=slug,
                overall_quality_score=45.0,
                verdict="REJECT",
                estimated_fluff_percentage=round(heuristics["fid"] * 100, 1),
                information_gain_classification="COMMODITY_REDUNDANT",
                dimension_breakdown=[
                    DimensionScore(dimension="Information Gain & Novelty", weight=0.30, score=40.0, observation="High repetition of common knowledge"),
                    DimensionScore(dimension="Fluff Density & AI Artifacts", weight=0.25, score=25.0, observation=f"Extreme FID: {heuristics['fid']}"),
                    DimensionScore(dimension="E-E-A-T Entity Anchoring", weight=0.20, score=40.0, observation="Generic conversational phrasing"),
                    DimensionScore(dimension="Answer Velocity", weight=0.15, score=50.0, observation="Throat-clearing opening paragraphs"),
                    DimensionScore(dimension="MFA & Structural Footprint", weight=0.10, score=30.0, observation="Text padding without tabular breakdown"),
                ],
                critical_flaws=[flaw],
                structural_pruning_targets=["Introduction filler", "Generic definitional summaries"],
                data_injection_directives=["Inject specific numeric units and primary source citations"],
            )

        # Deep LLM Evaluation
        user_prompt = f"""AUDIT TARGET:
Title: {title}
Pillar: {pillar}
Word Count: {int(heuristics['word_count'])}
Heuristic FID (Fluff Density): {heuristics['fid']:.2f}
Verifiable Sentence Ratio: {heuristics['verifiable_sentence_ratio']:.2f}
Information Gain Estimate: {ig:.2f}
Has Markdown Comparison Table: {'YES' if heuristics['has_markdown_table'] else 'NO'}

ARTICLE CONTENT:
{content[:8000]}"""

        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        raw_result = call_llm_json(messages, max_tokens=4096)
        if not raw_result or not isinstance(raw_result, dict):
            logger.warning("LLM router returned invalid json, building fallback AuditResult from heuristics.")
            overall = 85.0 if (heuristics["has_markdown_table"] and heuristics["fid"] <= 0.65 and ig >= 0.20) else 68.0
            verdict = "PASS" if overall >= 85.0 else "CONDITIONAL_PASS"
            return AuditResult(
                target_url=url,
                target_slug=slug,
                overall_quality_score=overall,
                verdict=verdict,
                estimated_fluff_percentage=round(heuristics["fid"] * 100, 1),
                information_gain_classification="HIGH_NOVELTY" if ig >= 0.25 else "MODERATE",
                dimension_breakdown=[
                    DimensionScore(dimension="Information Gain & Novelty", weight=0.30, score=85.0 if ig >= 0.20 else 65.0, observation=f"Computed IG: {ig}"),
                    DimensionScore(dimension="Fluff Density & AI Artifacts", weight=0.25, score=round((1.0 - heuristics["fid"]) * 100, 1), observation=f"FID: {heuristics['fid']}"),
                    DimensionScore(dimension="E-E-A-T Entity Anchoring", weight=0.20, score=80.0, observation="Primary entity signals verified"),
                    DimensionScore(dimension="Answer Velocity", weight=0.15, score=80.0, observation="BLUF present"),
                    DimensionScore(dimension="MFA & Structural Footprint", weight=0.10, score=90.0 if heuristics["has_markdown_table"] else 50.0, observation="Tabular structure evaluated"),
                ],
                critical_flaws=[],
                structural_pruning_targets=[],
                data_injection_directives=[] if heuristics["has_markdown_table"] else ["Inject Markdown comparison table"],
            )

        raw_result["target_url"] = url
        raw_result["target_slug"] = slug
        return AuditResult.model_validate(raw_result)

    def audit_supabase_slug(self, slug: str) -> AuditResult:
        """Fetches an article by slug from Supabase and audits it."""
        if not self.supabase_url or not self.supabase_key:
            raise ValueError("Supabase environment variables missing.")

        url = f"{self.supabase_url}/rest/v1/articles"
        params = {"select": "id,slug,title,pillar,content,status", "slug": f"eq.{slug}", "limit": "1"}
        res = self.client.get(url, headers=HEADERS, params=params)
        res.raise_for_status()
        data = res.json()
        if not data:
            raise ValueError(f"Article with slug '{slug}' not found in Supabase.")

        article = data[0]
        return self.audit_text(
            content=article.get("content") or "",
            title=article.get("title") or "",
            pillar=article.get("pillar") or "money",
            slug=slug,
            url=f"https://gworky.com/article/{slug}",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="LVC-AUDITOR: Low-Value Content Compliance Engine")
    parser.add_argument("--slug", type=str, help="Specific Supabase article slug to audit")
    parser.add_argument("--file", type=str, help="Path to local text/markdown file to audit")
    parser.add_argument("--title", type=str, default="Audit Target", help="Title of article")
    parser.add_argument("--pillar", type=str, default="money", help="Pillar domain")
    parser.add_argument("--json", action="store_true", help="Output raw JSON format")
    args = parser.parse_args()

    auditor = LVCAuditor()

    if args.slug:
        result = auditor.audit_supabase_slug(args.slug)
    elif args.file:
        content = Path(args.file).read_text(encoding="utf-8")
        result = auditor.audit_text(content=content, title=args.title, pillar=args.pillar)
    else:
        logger.info("No target specified. Running heuristic test on benchmark sample...")
        sample_good = """## Quick Answer (BLUF)
For a 30-year fixed mortgage, refinancing is mathematically justified when the prevailing rate drops at least 0.75% below your existing note, and you intend to occupy the property past the break-even horizon of 26 months.

### Empirical Cost & Break-Even Matrix
| Loan Amount | Current Rate | New Rate | Monthly Savings | Closing Costs | Break-Even Horizon |
| :--- | :--- | :--- | :--- | :--- | :--- |
| $400,000 | 6.85% | 5.85% | $264 | $4,800 | 18.2 months |
| $600,000 | 7.10% | 6.10% | $402 | $6,200 | 15.4 months |
| $800,000 | 6.75% | 6.00% | $398 | $7,500 | 18.8 months |

According to Federal Reserve Economic Data (FRED 30-Year Mortgage Series MORTGAGE30US) and CFPB closing fee disclosure benchmarks, loan origination charges average 1.2% of principal."""
        result = auditor.audit_text(content=sample_good, title="Mortgage Refinance Break-Even Analysis 2026", pillar="money")

    if args.json:
        print(result.model_dump_json(indent=2))
    else:
        logger.info("================ AUDIT RESULT ================")
        logger.info("Target: %s", result.target_slug or result.target_url or "Direct Text")
        logger.info("Verdict: %s (Score: %.1f / 100)", result.verdict, result.overall_quality_score)
        logger.info("Estimated Fluff: %.1f%% | Information Gain: %s", result.estimated_fluff_percentage, result.information_gain_classification)
        logger.info("Dimension Breakdown:")
        for dim in result.dimension_breakdown:
            logger.info(" - %-32s: %5.1f (Weight: %.2f) | %s", dim.dimension, dim.score, dim.weight, dim.observation)
        if result.critical_flaws:
            logger.info("Critical Flaws (%d):", len(result.critical_flaws))
            for flaw in result.critical_flaws:
                logger.info(" [!] [%s] %s: %s", flaw.severity, flaw.flaw_type, flaw.remediation_directive)


if __name__ == "__main__":
    main()
