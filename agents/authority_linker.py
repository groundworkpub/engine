"""Groundwork Decision Linker & Research Companion.

Maps practical guides and briefs to relevant Groundwork
decision calculators and domain hubs using natural editorial voice.
"""

from __future__ import annotations

import logging
import urllib.parse
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("authority_linker")

SITE_URL = "https://gworky.com"

# Master Pillar Hubs Metadata (Human-Centric Copy)
PILLAR_HUBS = {
    "money": {
        "title": "Money & Personal Finance",
        "url": f"{SITE_URL}/money",
        "headline": "Practical guides on mortgages, refinancing, investments, and cash flow.",
        "description": "Calculators and frameworks for refinancing, tax efficiency, and long-term financial planning.",
    },
    "body": {
        "title": "Health & Wellness",
        "url": f"{SITE_URL}/body",
        "headline": "Evidence-backed research on cardiovascular fitness, nutrition, and longevity.",
        "description": "Clear benchmarks for exercise physiology, metabolic health, and preventive wellness.",
    },
    "home": {
        "title": "Home & Energy Systems",
        "url": f"{SITE_URL}/home",
        "headline": "Straightforward guides for HVAC heat pumps, rooftop solar, and home efficiency.",
        "description": "Sizing rules, efficiency benchmarks, solar payback timelines, and rebate estimators.",
    },
    "tech": {
        "title": "Technology & Smart Home",
        "url": f"{SITE_URL}/tech",
        "headline": "Benchmarks for cloud costs, AI tools, and home network security.",
        "description": "Cost models for compute, Matter smart home setup, and local network privacy.",
    },
    "life": {
        "title": "Life & Career Decisions",
        "url": f"{SITE_URL}/life",
        "headline": "Data-backed frameworks for relocation, cost of living, and career milestones.",
        "description": "State tax comparisons, purchasing power estimates, and major life planning tools.",
    },
}

# Flagship Cornerstone Articles per Pillar
FLAGSHIP_ARTICLES = {
    "money": [
        {
            "slug": "understanding-rising-treasury-yields",
            "title": "How Rising Treasury Yields Impact Your Mortgage and Savings",
            "url": f"{SITE_URL}/article/understanding-rising-treasury-yields",
            "description": "How interest rate cycles affect monthly borrowing costs, fixed-income yields, and debt management.",
        },
    ],
    "body": [
        {
            "slug": "understanding-cardiovascular-risk-and-metabolic-syndrome",
            "title": "Cardiovascular Health: Key Biomarkers and Exercise Benchmarks",
            "url": f"{SITE_URL}/article/understanding-cardiovascular-risk-and-metabolic-syndrome",
            "description": "Essential clinical metrics, aerobic capacity benchmarks, and daily health habits for longevity.",
        },
    ],
    "home": [
        {
            "slug": "hvac-heat-pump-efficiency-and-tax-credits-guide",
            "title": "Heat Pump Efficiency, Cold-Climate Sizing, and Energy Rebates",
            "url": f"{SITE_URL}/article/hvac-heat-pump-efficiency-and-tax-credits-guide",
            "description": "How to size a heat pump system, calculate heating seasonal performance, and claim federal rebates.",
        },
    ],
    "tech": [
        {
            "slug": "enterprise-ai-model-inference-cost-optimization",
            "title": "AI Model Inference: Cost Optimization and Architecture",
            "url": f"{SITE_URL}/article/enterprise-ai-model-inference-cost-optimization",
            "description": "Token economics, latency considerations, and compute budgeting for software teams.",
        },
    ],
    "life": [
        {
            "slug": "interstate-relocation-cost-of-living-analysis",
            "title": "Cost of Living and Tax Differences When Moving Interstate",
            "url": f"{SITE_URL}/article/interstate-relocation-cost-of-living-analysis",
            "description": "Comparing state income taxes, housing costs, and net purchasing power before relocating.",
        },
    ],
}

# Decision Utilities & Calculators
GROUNDWORK_TOOLS = {
    "money": [
        {
            "slug": "mortgage-refinance-calculator",
            "title": "Mortgage Refinance Calculator",
            "url": f"{SITE_URL}/tools/mortgage-refinance-calculator",
            "description": "Calculate monthly payment savings, break-even timelines, and total interest over time.",
            "keywords": ["mortgage", "refinance", "rate", "yield", "treasury", "housing", "interest", "home loan", "property", "debt"],
        },
        {
            "slug": "emergency-fund-calculator",
            "title": "Emergency Fund Calculator",
            "url": f"{SITE_URL}/tools/emergency-fund-calculator",
            "description": "Estimate your recommended cash reserve based on fixed monthly living expenses.",
            "keywords": ["emergency fund", "savings", "cash flow", "budget", "layoff", "recession", "income", "inflation", "liquidity"],
        },
    ],
    "body": [
        {
            "slug": "cardiovascular-risk-scorecard",
            "title": "Cardiovascular Health Scorecard",
            "url": f"{SITE_URL}/tools/cardiovascular-risk-scorecard",
            "description": "Review cardiovascular benchmarks and fitness metrics against clinical guidelines.",
            "keywords": ["heart", "cardio", "vo2", "blood pressure", "cholesterol", "artery", "cardiovascular", "longevity", "fitness", "syndrome"],
        },
    ],
    "home": [
        {
            "slug": "hvac-heat-pump-calculator",
            "title": "Heat Pump Sizing & Savings Calculator",
            "url": f"{SITE_URL}/tools/hvac-heat-pump-calculator",
            "description": "Estimate heating loads, seasonal energy costs, and available rebate savings.",
            "keywords": ["heat pump", "hvac", "heating", "cooling", "energy", "utility", "rebate", "electric", "insulation", "geothermal"],
        },
        {
            "slug": "solar-roi-battery-estimator",
            "title": "Solar & Battery Payback Estimator",
            "url": f"{SITE_URL}/tools/solar-roi-battery-estimator",
            "description": "Estimate rooftop solar generation, electricity savings, and estimated payback years.",
            "keywords": ["solar", "battery", "photovoltaic", "grid", "power", "kilowatt", "tax credit", "clean energy", "storage"],
        },
    ],
    "tech": [
        {
            "slug": "ai-inference-cost-estimator",
            "title": "AI Inference Cost Estimator",
            "url": f"{SITE_URL}/tools/ai-inference-cost-estimator",
            "description": "Estimate compute and API costs based on model token volume and requests.",
            "keywords": ["ai", "model", "llm", "cloud", "compute", "gpu", "software", "developer", "serverless", "api", "chatgpt"],
        },
    ],
    "life": [
        {
            "slug": "relocation-cost-of-living-index",
            "title": "Cost of Living & Relocation Calculator",
            "url": f"{SITE_URL}/tools/relocation-cost-of-living-index",
            "description": "Compare income taxes, housing expenses, and take-home pay across different cities.",
            "keywords": ["relocation", "move", "city", "tax", "cost of living", "housing", "salary"],
        },
    ],
}


@dataclass
class LinkedResource:
    tool_title: str
    tool_url: str
    tool_desc: str
    pillar_title: str
    pillar_url: str
    pillar_headline: str
    pillar_desc: str
    flagship_title: str
    flagship_url: str
    flagship_desc: str
    search_url: str
    wire_url: str
    author_url: str
    author_name: str
    subscribe_url: str
    callout_html: str
    decision_matrix_html: str


def generate_search_query(title: str) -> str:
    stop_words = {"the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "with", "is", "are", "of", "how", "what", "why"}
    words = [w.strip(".,!?:;\"'()[]{}").lower() for w in title.split()]
    filtered = [w for w in words if w and w not in stop_words and len(w) > 2]
    query_str = " ".join(filtered[:4])
    return query_str or "guide"


def match_groundwork_resource(
    title: str,
    pillar: str,
    description: str,
    sibling_articles: list[dict[str, Any]] | None = None,
) -> LinkedResource:
    """Matches content to Groundwork tools and guides using clean, helpful copy."""
    pillar_key = pillar.lower()
    if pillar_key not in GROUNDWORK_TOOLS:
        pillar_key = "money"

    tools = GROUNDWORK_TOOLS.get(pillar_key, GROUNDWORK_TOOLS["money"])
    content_lower = f"{title} {description}".lower()

    best_tool = tools[0]
    max_matches = -1
    for tool in tools:
        match_count = sum(1 for kw in tool["keywords"] if kw in content_lower)
        if match_count > max_matches:
            max_matches = match_count
            best_tool = tool

    hub = PILLAR_HUBS.get(pillar_key, PILLAR_HUBS["money"])
    flagships = FLAGSHIP_ARTICLES.get(pillar_key, FLAGSHIP_ARTICLES["money"])
    best_flagship = flagships[0]

    pillar_url = hub["url"]
    search_query = generate_search_query(title)
    search_url = f"{SITE_URL}/search?q={urllib.parse.quote_plus(search_query)}"
    wire_url = f"{SITE_URL}/wire"
    
    author_name = "Groundwork Editorial"
    author_url = f"{SITE_URL}"
    subscribe_url = f"{SITE_URL}/subscribe"

    # Contextual Companion Card
    decision_matrix_html = f"""
    <aside class="companion-tools" style="background: #112240; border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; padding: 1.25rem 1.5rem; margin: 2.5rem 0;">
      <div style="font-size: 0.75rem; color: #10B981; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.3rem;">Interactive Tool</div>
      <h3 style="color: #FFFFFF; font-size: 1.05rem; margin: 0 0 0.4rem; font-weight: 600;">
        <a href="{best_tool['url']}" style="color: #FFFFFF; text-decoration: none;">{best_tool['title']} &rarr;</a>
      </h3>
      <p style="color: #8892B0; font-size: 0.9rem; margin-bottom: 0.8rem; line-height: 1.5;">{best_tool['description']}</p>
      <div>
        <a href="{best_tool['url']}" style="display: inline-block; background: #10B981; color: #0A192F; font-weight: 600; text-decoration: none; padding: 0.45rem 0.9rem; border-radius: 6px; font-size: 0.82rem;">
          Try Calculator on Groundwork &rarr;
        </a>
      </div>
    </aside>
    """

    callout_html = decision_matrix_html

    return LinkedResource(
        tool_title=best_tool["title"],
        tool_url=best_tool["url"],
        tool_desc=best_tool["description"],
        pillar_title=hub["title"],
        pillar_url=pillar_url,
        pillar_headline=hub["headline"],
        pillar_desc=hub["description"],
        flagship_title=best_flagship["title"],
        flagship_url=best_flagship["url"],
        flagship_desc=best_flagship["description"],
        search_url=search_url,
        wire_url=wire_url,
        author_url=author_url,
        author_name=author_name,
        subscribe_url=subscribe_url,
        callout_html=callout_html,
        decision_matrix_html=decision_matrix_html,
    )
