"""Agentic GEO Evaluator & N-Shot AI Visibility Benchmark.

Implements the measurement protocol defined in docs/seo/seranking.md (§8 & §10 Workflow C)
and Aggarwal et al. (KDD 2024, Princeton GEO):
- Evaluates stochastic N-shot LLM answers (10-20 iterations)
- Measures citation share, URL extraction, brand mentions, and competitor share
- Audits local article payloads for Princeton GEO evidence density and BLUF extractability
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from agents.critic import (
    check_temporal_recency,
    grade_evidence_density,
    grade_geo_extractability,
    grade_keyword_stuffing,
    grade_title_content_alignment,
    validate_faq_schema_quality,
)

logger = logging.getLogger(__name__)

TIER1_PARASITE_DOMAINS = [
    "github.com", "zenodo.org", "huggingface.co", "dev.to",
    "arxiv.org", "reddit.com", "quora.com", "substack.com"
]


@dataclass
class GEOCitationDetail:
    sample_index: int
    mentioned: bool
    cited_urls: list[str]
    first_mention_position: int | None
    competitors_found: list[str]
    earned_media_urls: list[str] = field(default_factory=list)


@dataclass
class GEOEvaluationResult:
    query: str
    target_domain: str
    total_samples: int
    mentions_count: int
    citations_count: int
    citation_share_pct: float
    mention_share_pct: float
    avg_mention_position: float | None
    cited_urls: list[str]
    earned_media_share_pct: float
    earned_media_urls: list[str]
    competitor_share: dict[str, int]
    details: list[dict[str, Any]] = field(default_factory=list)


def parse_sample_answer(
    text: str,
    sample_index: int,
    target_domain: str = "gworky.com",
    competitor_domains: list[str] | None = None,
) -> GEOCitationDetail:
    """Parses a single LLM-synthesized answer for target domain mentions and citations."""
    comp_list = competitor_domains or [
        "nerdwallet.com", "bankrate.com", "investopedia.com",
        "healthline.com", "mayoclinic.org", "consumerreports.org",
        "forbes.com", "wirecutter.com"
    ]

    lower_text = text.lower()
    target_clean = target_domain.lower().replace("https://", "").replace("http://", "").rstrip("/")

    # 1. Mention check (domain or brand name)
    brand_variants = [target_clean, "groundwork", "gworky"]
    mentioned = any(b in lower_text for b in brand_variants)

    first_pos: int | None = None
    if mentioned:
        positions = [lower_text.find(b) for b in brand_variants if lower_text.find(b) != -1]
        first_pos = min(positions) if positions else None

    # 2. Direct Citation URLs
    url_pattern = re.compile(rf"https?://[^\s)\]>]*{re.escape(target_clean)}[^\s)\]>]*", re.IGNORECASE)
    cited_urls = list(set(url_pattern.findall(text)))

    # 3. Earned Media & Parasite Citations (84% empirical driver)
    earned_media_urls: list[str] = []
    for parasite in TIER1_PARASITE_DOMAINS:
        parasite_pattern = re.compile(rf"https?://[^\s)\]>]*{re.escape(parasite)}[^\s)\]>]*", re.IGNORECASE)
        found_parasite = parasite_pattern.findall(text)
        if found_parasite:
            earned_media_urls.extend(found_parasite)

    # 4. Competitor mentions
    found_competitors: list[str] = []
    for comp in comp_list:
        if comp in lower_text:
            found_competitors.append(comp)

    return GEOCitationDetail(
        sample_index=sample_index,
        mentioned=mentioned,
        cited_urls=cited_urls,
        first_mention_position=first_pos,
        competitors_found=found_competitors,
        earned_media_urls=list(set(earned_media_urls)),
    )


def evaluate_n_shot_answers(
    query: str,
    answers: list[str],
    target_domain: str = "gworky.com",
    competitor_domains: list[str] | None = None,
) -> GEOEvaluationResult:
    """Evaluates N-shot stochastic answers across generative search engines."""
    total_samples = len(answers)
    if total_samples == 0:
        return GEOEvaluationResult(
            query=query,
            target_domain=target_domain,
            total_samples=0,
            mentions_count=0,
            citations_count=0,
            citation_share_pct=0.0,
            mention_share_pct=0.0,
            avg_mention_position=None,
            cited_urls=[],
            earned_media_share_pct=0.0,
            earned_media_urls=[],
            competitor_share={},
            details=[],
        )

    mentions_count = 0
    citations_count = 0
    earned_media_count = 0
    all_cited_urls: set[str] = set()
    all_earned_urls: set[str] = set()
    positions: list[int] = []
    competitor_counts: dict[str, int] = {}
    details: list[dict[str, Any]] = []

    for i, ans in enumerate(answers):
        detail = parse_sample_answer(ans, i + 1, target_domain, competitor_domains)
        details.append(asdict(detail))

        if detail.mentioned:
            mentions_count += 1
            if detail.first_mention_position is not None:
                positions.append(detail.first_mention_position)

        if detail.cited_urls:
            citations_count += 1
            all_cited_urls.update(detail.cited_urls)

        if detail.earned_media_urls:
            earned_media_count += 1
            all_earned_urls.update(detail.earned_media_urls)

        for comp in detail.competitors_found:
            competitor_counts[comp] = competitor_counts.get(comp, 0) + 1

    avg_pos = round(sum(positions) / len(positions), 1) if positions else None
    citation_share = round((citations_count / total_samples) * 100, 1)
    mention_share = round((mentions_count / total_samples) * 100, 1)
    earned_share = round((earned_media_count / total_samples) * 100, 1)

    return GEOEvaluationResult(
        query=query,
        target_domain=target_domain,
        total_samples=total_samples,
        mentions_count=mentions_count,
        citations_count=citations_count,
        citation_share_pct=citation_share,
        mention_share_pct=mention_share,
        avg_mention_position=avg_pos,
        cited_urls=sorted(list(all_cited_urls)),
        earned_media_share_pct=earned_share,
        earned_media_urls=sorted(list(all_earned_urls)),
        competitor_share=competitor_counts,
        details=details,
    )


def audit_article_geo_readiness(
    title: str,
    content: str,
    faq: list[dict[str, str]] | None = None,
    pillar: str = "",
) -> dict[str, Any]:
    """Audits local article payload against the Princeton GEO & Google Leak rubric.

    Returns comprehensive scorecard:
    - BLUF Extractability (40-80 words direct answer)
    - Title-Content Alignment (titleMatchScore)
    - Evidence Density (numbers, years, units, citations)
    - Keyword Stuffing Check (<= 2.2%)
    - FAQPage Schema (0.71 AEO Correlation)
    - Temporal Recency Guard (>= 2024/2026 recency)
    """
    bluf_score, bluf_issue = grade_geo_extractability(content)
    align_score, align_issue = grade_title_content_alignment(title, content)
    ev_score, ev_signals = grade_evidence_density(content)
    stuff_ok, density_pct, stuff_issue = grade_keyword_stuffing(content, title)

    # FAQ Schema check (0.71 correlation factor)
    faq_score, faq_issues = validate_faq_schema_quality(faq) if faq is not None else (0.8, [])

    # Recency check
    recency_ok, recency_issue = check_temporal_recency(content, pillar)

    # Calculate aggregate GEO Index (0-100)
    geo_index = (
        (bluf_score * 25.0) +
        (align_score * 20.0) +
        (ev_score * 25.0) +
        (faq_score * 15.0) +
        (15.0 if stuff_ok and recency_ok else 5.0)
    )
    geo_index = max(0.0, min(100.0, round(geo_index, 1)))

    critiques: list[str] = []
    if bluf_issue:
        critiques.append(bluf_issue)
    if align_issue:
        critiques.append(align_issue)
    if ev_score < 0.50:
        critiques.append(f"Low empirical evidence density ({ev_signals} signals found, target >= 8)")
    if not stuff_ok and stuff_issue:
        critiques.append(stuff_issue)
    if faq_issues:
        critiques.extend(faq_issues)
    if not recency_ok and recency_issue:
        critiques.append(recency_issue)

    return {
        "geo_index": geo_index,
        "passed": geo_index >= 80.0,
        "bluf_extractability_score": bluf_score,
        "title_alignment_score": align_score,
        "evidence_density_score": ev_score,
        "evidence_signals_count": ev_signals,
        "faq_schema_score": faq_score,
        "temporal_recency_passed": recency_ok,
        "keyword_stuffing_passed": stuff_ok,
        "max_keyword_density_pct": density_pct,
        "critiques": critiques,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Groundwork Agentic GEO Evaluator")
    parser.add_argument("--query", default="", help="Search/evaluation query")
    parser.add_argument("--eval-file", help="Path to JSON file containing sample answers")
    parser.add_argument("--domain", default="gworky.com", help="Target domain to evaluate")

    args = parser.parse_args()

    if args.eval_file:
        with open(args.eval_file, encoding="utf-8") as f:
            data = json.load(f)
            answers = data.get("answers", [])
            query = data.get("query", args.query or "sample query")
            res = evaluate_n_shot_answers(query, answers, args.domain)
            print(json.dumps(asdict(res), indent=2))
    else:
        print("GEO Evaluator loaded. Provide --eval-file to evaluate N-shot answers.")


if __name__ == "__main__":
    main()
