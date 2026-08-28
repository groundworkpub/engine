"""Groundwork Link Weaver Engine.

Enriches drafted articles with natural, contextual internal links:
1. Homepage Authority Anchor: [Groundwork](https://gworky.com)
2. Pillar Hub Navigation: /[pillar]
3. Decision Tool Companion: /tools/[slug]
4. Topical Sibling Cross-Link: /article/[slug] (Strict Topical Silo: same pillar only)
"""

from __future__ import annotations

import re
from typing import Any

CANONICAL_TOOLS = [
    # Money
    {"slug": "mortgage-refinance", "title": "Mortgage Refinance Calculator", "pillar": "money", "keywords": ["mortgage refinance", "mortgage rate", "refinancing", "home loan", "monthly mortgage payment", "mortgage interest"]},
    {"slug": "hysa-compound-interest", "title": "HYSA Compound Interest Calculator", "pillar": "money", "keywords": ["compound interest", "high-yield savings", "hysa", "savings yield", "annual percentage yield", "interest compounding"]},
    {"slug": "inflation-purchasing-power", "title": "Inflation & Purchasing Power Calculator", "pillar": "money", "keywords": ["purchasing power", "inflation rate", "real purchasing power", "cost inflation", "consumer price index"]},
    {"slug": "emergency-fund-calculator", "title": "Emergency Fund Calculator", "pillar": "money", "keywords": ["emergency fund", "cash reserve", "living expenses reserve", "emergency savings", "rainy day fund"]},
    {"slug": "retirement-readiness-benchmark", "title": "Retirement Readiness Benchmark", "pillar": "money", "keywords": ["retirement savings", "retirement readiness", "401k balance", "nest egg", "retirement portfolio"]},
    {"slug": "rent-vs-buy-calculator", "title": "Rent vs Buy Calculator", "pillar": "money", "keywords": ["rent vs buy", "renting versus buying", "homeownership cost", "buying a home", "property purchase"]},
    {"slug": "dollar-cost-averaging-calculator", "title": "Dollar-Cost Averaging Calculator", "pillar": "money", "keywords": ["dollar-cost averaging", "dca strategy", "systematic investing", "recurring investment"]},
    {"slug": "savings-goal-calculator", "title": "Savings Goal Calculator", "pillar": "money", "keywords": ["savings goal", "target savings", "monthly savings target", "saving strategy"]},
    {"slug": "bond-vs-equity-allocation", "title": "Bond vs Equity Asset Allocation Model", "pillar": "money", "keywords": ["asset allocation", "bond allocation", "stock portfolio", "equity allocation", "portfolio rebalancing"]},

    # Body
    {"slug": "heart-rate-zones-calculator", "title": "Heart Rate Zones Calculator", "pillar": "body", "keywords": ["heart rate zones", "target heart rate", "cardiovascular zone", "aerobic zone", "vo2 max", "maximum heart rate"]},
    {"slug": "daily-calorie-tdee", "title": "Daily Calorie & TDEE Calculator", "pillar": "body", "keywords": ["calorie intake", "tdee", "total daily energy expenditure", "basal metabolic rate", "macronutrient balance"]},
    {"slug": "sleep-cycle-planner", "title": "Sleep Cycle & Circadian Calculator", "pillar": "body", "keywords": ["sleep cycle", "circadian rhythm", "rem sleep", "sleep latency", "sleep duration", "deep sleep"]},

    # Home
    {"slug": "solar-roi", "title": "Rooftop Solar & Battery Payback Estimator", "pillar": "home", "keywords": ["solar payback", "rooftop solar", "solar panels", "battery storage", "solar energy savings", "photovoltaic system"]},
    {"slug": "heat-pump-roi-calculator", "title": "Heat Pump ROI & Efficiency Calculator", "pillar": "home", "keywords": ["heat pump", "hvac efficiency", "heating and cooling", "heat pump installation", "hvac upgrade", "seer2 rating"]},
    {"slug": "commute-ev-vs-gas-calculator", "title": "EV vs Gas Commute Cost Calculator", "pillar": "home", "keywords": ["electric vehicle", "ev charging cost", "gas vs electric", "commute cost", "fuel savings"]},

    # Life
    {"slug": "freelance-rate-calculator", "title": "Freelance & Consulting Hourly Rate Calculator", "pillar": "life", "keywords": ["freelance rate", "consulting hourly rate", "contractor rate", "hourly billing", "freelance income"]},
    {"slug": "cost-of-living", "title": "Cost of Living & Relocation Calculator", "pillar": "life", "keywords": ["cost of living", "relocation cost", "moving expenses", "purchasing power by city", "interstate move"]},
    {"slug": "life-insurance-needs-calculator", "title": "Life Insurance Needs Calculator", "pillar": "life", "keywords": ["life insurance", "term life coverage", "insurance policy", "death benefit", "income replacement"]},

    # Tech
    {"slug": "subscription-stack-auditor", "title": "SaaS & Subscription Stack Auditor", "pillar": "tech", "keywords": ["subscription cost", "recurring subscriptions", "saas audit", "software stack", "monthly subscription fees"]},
    {"slug": "smart-home-roi", "title": "Smart Home Energy ROI Calculator", "pillar": "tech", "keywords": ["smart thermostat", "home automation", "smart home energy", "matter protocol", "connected home devices"]},
]

PILLAR_KEYWORDS = {
    "money": ["personal finance", "financial planning", "wealth management", "borrowing costs", "interest rates", "investment strategies"],
    "body": ["health decisions", "clinical wellness", "preventive care", "metabolic health", "physical wellness", "evidence-based health"],
    "home": ["home improvement", "energy efficiency", "residential systems", "clean energy upgrades", "home maintenance"],
    "tech": ["technology systems", "software tools", "hardware benchmarks", "digital infrastructure", "computing architecture"],
    "life": ["career decisions", "lifestyle planning", "major life milestones", "relocation benchmarks", "professional development"],
}

STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "from", "your", "have", "more",
    "will", "about", "what", "when", "where", "which", "their", "there", "these",
    "those", "into", "over", "after", "before", "while", "during", "through",
}


def _is_safe_to_link(text: str, phrase: str) -> tuple[int, int, str] | None:
    pattern = r"\b(" + re.escape(phrase) + r"[a-zA-Z]{0,3})\b"
    m = re.search(pattern, text, re.IGNORECASE)
    if not m:
        return None

    idx = m.start()
    matched_text = m.group(1)
    before = text[:idx]
    after = text[idx + len(matched_text):]

    if before.count("[") > before.count("]"):
        return None
    if before.count("](") > before.count(")"):
        return None
    if text.strip().startswith("#"):
        return None

    return idx, idx + len(matched_text), matched_text


def weave_article_links(
    content: str,
    title: str,
    pillar: str,
    sibling_articles: list[dict[str, Any]] | None = None,
) -> str:
    """Weaves homepage, pillar hub, tool, and sibling article links into markdown prose."""
    if not content:
        return content

    paragraphs = re.split(r"\n\n+", content)
    has_homepage = "https://gworky.com" in content or "](/)" in content
    has_pillar = f"/{pillar}" in content or f"https://gworky.com/{pillar}" in content
    has_tool = "/tools/" in content
    has_sibling = "/article/" in content

    # 1. Homepage link
    if not has_homepage:
        injected = False
        for i, p in enumerate(paragraphs):
            if p.startswith("#"):
                continue
            safe = _is_safe_to_link(p, "Groundwork")
            if safe:
                start, end, _ = safe
                paragraphs[i] = p[:start] + "[Groundwork](https://gworky.com)" + p[end:]
                injected = True
                break
        if not injected and paragraphs:
            for i in range(min(3, len(paragraphs))):
                if not paragraphs[i].startswith("#") and len(paragraphs[i]) > 20:
                    if re.search(r"\.\s+[A-Z]", paragraphs[i]):
                        paragraphs[i] = re.sub(
                            r"\.\s+([A-Z])",
                            r". According to editorial research analyzed by [Groundwork](https://gworky.com), \1",
                            paragraphs[i],
                            count=1,
                        )
                    else:
                        paragraphs[i] = paragraphs[i].rstrip(".") + ". Analysis by [Groundwork](https://gworky.com)."
                    if "[Groundwork](https://gworky.com)" in paragraphs[i]:
                        break

    # 2. Pillar Hub link
    if not has_pillar:
        kws = PILLAR_KEYWORDS.get(pillar, PILLAR_KEYWORDS["money"])
        for i in range(1, len(paragraphs)):
            p = paragraphs[i]
            if p.startswith("#") or f"/{pillar}" in p:
                continue
            linked = False
            for kw in kws:
                safe = _is_safe_to_link(p, kw)
                if safe:
                    start, end, match_str = safe
                    paragraphs[i] = p[:start] + f"[{match_str}](/{pillar})" + p[end:]
                    linked = True
                    break
            if linked:
                break

    # 3. Tool link
    if not has_tool:
        pillar_tools = [t for t in CANONICAL_TOOLS if t["pillar"] == pillar]
        for i in range(1, len(paragraphs)):
            p = paragraphs[i]
            if p.startswith("#") or "/tools/" in p:
                continue
            linked = False
            for tool in pillar_tools:
                for kw in tool["keywords"]:
                    safe = _is_safe_to_link(p, kw)
                    if safe:
                        start, end, match_str = safe
                        paragraphs[i] = p[:start] + f"[{match_str}](/tools/{tool['slug']})" + p[end:]
                        linked = True
                        break
                if linked:
                    break
            if linked:
                break

    # 4. Sibling link
    if not has_sibling and sibling_articles:
        same_pillar_siblings = [s for s in sibling_articles if s.get("pillar") == pillar and s.get("title") != title]
        if same_pillar_siblings:
            target = same_pillar_siblings[0]
            clean_words = re.findall(r"[A-Za-z0-9-]+", target.get("title", ""))
            target_phrases = []
            for plen in (3, 2):
                for j in range(len(clean_words) - plen + 1):
                    ph = " ".join(clean_words[j:j + plen])
                    w1, w2 = clean_words[j].lower(), clean_words[j + plen - 1].lower()
                    if w1 not in STOPWORDS and w2 not in STOPWORDS:
                        target_phrases.append(ph)

            for i in range(len(paragraphs) // 2, len(paragraphs)):
                p = paragraphs[i]
                if p.startswith("#") or "/article/" in p:
                    continue
                linked = False
                for ph in target_phrases:
                    safe = _is_safe_to_link(p, ph)
                    if safe and len(safe[2].split()) >= 2:
                        start, end, match_str = safe
                        paragraphs[i] = p[:start] + f"[{match_str}](/article/{target.get('slug')})" + p[end:]
                        linked = True
                        break
                if linked:
                    break

    return "\n\n".join(paragraphs)
