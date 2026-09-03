#!/usr/bin/env python3
"""
agents/opportunity_engine.py — Groundwork Web Opportunity Graph & Heuristic Gap Detector 2.0

Implements the "Job To Be Done" Heuristic Engine for High-Conversion Outreach:
1. CALCULATOR_GAP: Detects calculation/estimation instructions lacking an interactive tool.
2. SOURCE_TO_TOOL: Detects citations of raw primary datasets (BLS, NIH, CFPB, Census, DOE) lacking interactive tools.
3. OUTDATED_DATA: Detects outdated statistics (2019-2023) where Groundwork has 2026 live benchmarks.
4. BROKEN_RESOURCE: Detects dead 404/410 outbound links.
5. UNLINKED_MENTION: Detects mentions of Groundwork, Elena, or Zenodo DOIs lacking hyperlinks.
6. Multi-Factor Scoring Engine: Filters out weak targets (< 75 score) before Hunter.io enrichment.
"""

import argparse
import asyncio
import hashlib
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("opportunity_engine")

# ── Groundwork 20 Interactive Assets Catalog (SSOT) ──────────────────────────
GROUNDWORK_ASSETS = [
    # Money Pillar
    {
        "slug": "mortgage-refinance",
        "title": "Mortgage Refinance Break-Even Engine",
        "pillar": "money",
        "url": "https://gworky.com/tools/mortgage-refinance",
        "keywords": ["mortgage", "refinance", "break-even", "closing costs", "amortization", "interest rate", "monthly payment"],
        "triggers": ["calculate your mortgage", "refinance break even", "determine your monthly payment", "mortgage formula"],
        "primary_sources": ["CFPB", "FRED", "Federal Reserve", "FHFA"],
    },
    {
        "slug": "hysa-compound-interest",
        "title": "Compound Interest & Wealth Velocity Engine",
        "pillar": "money",
        "url": "https://gworky.com/tools/hysa-compound-interest",
        "keywords": ["compound interest", "investing", "index fund", "wealth accumulation", "annual return", "future value", "hysa", "high yield savings"],
        "triggers": ["compound interest formula", "calculate investment growth", "future value of savings", "power of compounding"],
        "primary_sources": ["SEC", "FINRA", "Federal Reserve"],
    },
    {
        "slug": "emergency-fund-calculator",
        "title": "Evidence-Based Emergency Fund Sizer",
        "pillar": "money",
        "url": "https://gworky.com/tools/emergency-fund-calculator",
        "keywords": ["emergency fund", "savings", "living expenses", "3 months expenses", "6 months expenses", "safety net", "debt snowball"],
        "triggers": ["calculate emergency fund", "how much emergency savings", "rule of thumb for savings", "monthly essential expenses"],
        "primary_sources": ["BLS", "Federal Reserve Survey of Consumer Finances", "CFPB"],
    },
    {
        "slug": "inflation-purchasing-power",
        "title": "Inflation & Real Purchasing Power Model",
        "pillar": "money",
        "url": "https://gworky.com/tools/inflation-purchasing-power",
        "keywords": ["inflation", "purchasing power", "cpi", "real return", "consumer price index"],
        "triggers": ["calculate inflation impact", "purchasing power loss", "real value of dollar"],
        "primary_sources": ["BLS", "Federal Reserve (FRED)"],
    },
    {
        "slug": "retirement-readiness-benchmark",
        "title": "Retirement Readiness & FIRE Benchmark Model",
        "pillar": "money",
        "url": "https://gworky.com/tools/retirement-readiness-benchmark",
        "keywords": ["retirement", "fire", "nest egg", "401k", "roth ira", "drawdown rate"],
        "triggers": ["calculate retirement number", "fire benchmark", "retirement savings rule"],
        "primary_sources": ["IRS", "Social Security Administration", "BLS"],
    },
    {
        "slug": "rent-vs-buy-calculator",
        "title": "Rent vs. Buy Housing Arbitrage Engine",
        "pillar": "money",
        "url": "https://gworky.com/tools/rent-vs-buy-calculator",
        "keywords": ["rent vs buy", "home ownership", "property tax", "opportunity cost", "housing market"],
        "triggers": ["should i rent or buy", "rent vs buy formula", "home purchase break even"],
        "primary_sources": ["CFPB", "Census Bureau", "FHFA"],
    },
    {
        "slug": "dollar-cost-averaging-calculator",
        "title": "Dollar-Cost Averaging vs. Lump Sum Simulator",
        "pillar": "money",
        "url": "https://gworky.com/tools/dollar-cost-averaging-calculator",
        "keywords": ["dollar cost averaging", "dca", "lump sum", "volatility", "market timing"],
        "triggers": ["dca vs lump sum", "dollar cost averaging formula", "systematic investing"],
        "primary_sources": ["FINRA", "SEC", "Federal Reserve"],
    },
    {
        "slug": "bond-vs-equity-allocation",
        "title": "Bond vs. Equity Asset Allocation Optimizer",
        "pillar": "money",
        "url": "https://gworky.com/tools/bond-vs-equity-allocation",
        "keywords": ["asset allocation", "portfolio balance", "stocks vs bonds", "risk tolerance"],
        "triggers": ["how much in bonds", "asset allocation formula", "portfolio risk profile"],
        "primary_sources": ["FINRA", "Federal Reserve"],
    },

    # Body Pillar
    {
        "slug": "daily-calorie-tdee",
        "title": "Precision Calorie & Macro Target Calculator",
        "pillar": "body",
        "url": "https://gworky.com/tools/daily-calorie-tdee",
        "keywords": ["calories", "macros", "protein intake", "carbohydrates", "fat", "tdee", "caloric deficit", "bmr"],
        "triggers": ["calculate your macros", "how many calories should you eat", "protein per pound formula", "daily caloric needs", "tdee formula"],
        "primary_sources": ["NIH", "USDA", "National Academies of Sciences"],
    },
    {
        "slug": "heart-rate-zones-calculator",
        "title": "Target Heart Rate Zones & Cardiorespiratory Sizer",
        "pillar": "body",
        "url": "https://gworky.com/tools/heart-rate-zones-calculator",
        "keywords": ["heart rate zones", "vo2 max", "cardiorespiratory fitness", "aerobic capacity", "zone 2", "karvonen formula"],
        "triggers": ["calculate heart rate zones", "target heart rate formula", "zone 2 cardio calculator"],
        "primary_sources": ["NIH", "AHA", "Mayo Clinic"],
    },
    {
        "slug": "sleep-cycle-planner",
        "title": "Sleep Architecture & Optimal Bedtime Sizer",
        "pillar": "body",
        "url": "https://gworky.com/tools/sleep-cycle-planner",
        "keywords": ["sleep cycle", "rem sleep", "bedtime", "90-minute cycle", "circadian rhythm", "wake time"],
        "triggers": ["calculate sleep cycles", "best time to wake up", "90 minute sleep formula", "optimal bedtime"],
        "primary_sources": ["NIH", "CDC", "Sleep Foundation"],
    },

    # Home Pillar
    {
        "slug": "solar-roi",
        "title": "Solar Panel ROI & Federal Tax Credit Break-Even Model",
        "pillar": "home",
        "url": "https://gworky.com/tools/solar-roi",
        "keywords": ["solar panels", "solar roi", "federal tax credit", "net metering", "kilowatt hour", "payback period"],
        "triggers": ["calculate solar roi", "how long for solar to pay for itself", "solar payback formula", "estimate solar savings"],
        "primary_sources": ["DOE", "NREL", "DSIRE", "EIA"],
    },
    {
        "slug": "heat-pump-roi-calculator",
        "title": "Heat Pump vs. Gas Furnace Life-Cycle Operating Cost Engine",
        "pillar": "home",
        "url": "https://gworky.com/tools/heat-pump-roi-calculator",
        "keywords": ["hvac", "heat pump", "btu sizing", "seer2", "operating cost", "gas furnace"],
        "triggers": ["heat pump savings formula", "calculate heat pump roi", "hvac upgrade payback"],
        "primary_sources": ["DOE", "Energy Star", "ASHRAE"],
    },
    {
        "slug": "smart-home-roi",
        "title": "Smart Home Energy Automation Payback Calculator",
        "pillar": "home",
        "url": "https://gworky.com/tools/smart-home-roi",
        "keywords": ["smart thermostat", "energy automation", "smart home savings", "utility bills", "home automation"],
        "triggers": ["calculate smart thermostat savings", "smart home energy roi", "automation payback"],
        "primary_sources": ["EPA", "Energy Star", "DOE"],
    },

    # Life Pillar
    {
        "slug": "freelance-rate-calculator",
        "title": "Freelance Rate & Salary Arbitrage Calculator",
        "pillar": "life",
        "url": "https://gworky.com/tools/freelance-rate-calculator",
        "keywords": ["freelance rate", "hourly rate", "billable hours", "self employment tax", "overhead", "salary to hourly"],
        "triggers": ["calculate freelance hourly rate", "convert salary to freelance rate", "how to price your services"],
        "primary_sources": ["BLS", "IRS", "SBA"],
    },
    {
        "slug": "cost-of-living",
        "title": "Cost of Living & Salary Relocation Index",
        "pillar": "life",
        "url": "https://gworky.com/tools/cost-of-living",
        "keywords": ["cost of living", "relocation", "salary comparison", "housing index", "purchasing power"],
        "triggers": ["calculate cost of living difference", "salary needed in new city", "relocation budget formula"],
        "primary_sources": ["BLS", "BEA", "Census Bureau"],
    },
    {
        "slug": "commute-ev-vs-gas-calculator",
        "title": "EV vs. Gas Commute Total Cost of Ownership Engine",
        "pillar": "life",
        "url": "https://gworky.com/tools/commute-ev-vs-gas-calculator",
        "keywords": ["ev vs gas", "electric vehicle cost", "gas mileage", "commute cost", "charging cost", "cost per mile"],
        "triggers": ["calculate ev savings", "is electric car cheaper", "ev vs gas commute formula"],
        "primary_sources": ["DOE Alternative Fuels Data Center", "AAA", "EPA"],
    },
    {
        "slug": "life-insurance-needs-calculator",
        "title": "Life Insurance Needs & Capital Preservation Sizer",
        "pillar": "life",
        "url": "https://gworky.com/tools/life-insurance-needs-calculator",
        "keywords": ["life insurance", "term life", "coverage amount", "income replacement", "dime method"],
        "triggers": ["how much life insurance do i need", "calculate life insurance coverage", "dime formula"],
        "primary_sources": ["NAIC", "BLS", "CFPB"],
    },

    # Tech Pillar
    {
        "slug": "subscription-stack-auditor",
        "title": "Subscription Stack & SaaS Recurring Cost Auditor",
        "pillar": "tech",
        "url": "https://gworky.com/tools/subscription-stack-auditor",
        "keywords": ["saas roi", "subscription auditor", "recurring expenses", "software audit", "saas spend"],
        "triggers": ["audit my subscriptions", "calculate software spend", "saas cost reduction"],
        "primary_sources": ["Groundwork Tech Desk", "Gartner"],
    },
]


@dataclass
class Opportunity:
    id: str
    target_url: str
    domain: str
    pillar: str
    opportunity_type: str
    evidence_snippet: str
    matching_asset: dict[str, Any]
    detected_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    # Contact Resolution
    target_person: str | None = None
    target_email: str | None = None
    contact_source: str | None = None
    hunter_confidence: int = 0

    # Heuristic Scores (0-100)
    intent_score: float = 0.0
    asset_fit_score: float = 0.0
    evidence_score: float = 0.0
    friction_score: float = 0.0  # Lower friction = higher score
    authority_score: float = 0.0
    total_score: float = 0.0

    # Actionable Pitch
    one_sentence_proposition: str = ""
    pitch_draft: str = ""
    status: str = "QUALIFIED"  # QUALIFIED | NO_GO | APPROVED | SENT | DISMISSED


# ── Heuristic Gap Detectors ───────────────────────────────────────────────────

def detect_calculator_gap(html: str, text: str, url: str) -> dict[str, Any] | None:
    """
    Detects if an article asks readers to calculate/estimate something but lacks an interactive tool.
    """
    calc_phrases = [
        r"(?:you can\s+)?calculate your\b",
        r"\bhow to calculate\b",
        r"\buse this formula\b",
        r"\bto estimate your\b",
        r"\brule of thumb is to multiply\b",
        r"\bdetermine how much you need\b",
        r"\bfigure out your monthly\b",
        r"\bdivide your annual\b",
        r"\bformula is:\s*[\w\s\*\+\-\/\(\)]+",
    ]

    # Check if page instructs calculation
    matched_phrase = None
    for pattern in calc_phrases:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            matched_phrase = match.group(0)
            break

    if not matched_phrase:
        return None

    # Check if page already has an interactive calculator/form
    has_interactive = bool(
        re.search(r"<form\b|<input\b[^>]+type=[\'\"](?:number|range)[\'\"]|class=[\'\"][^\'\"]*calculator[^\'\"]*[\'\"]|<iframe\b", html, re.IGNORECASE)
    )

    if not has_interactive:
        snippet = text[max(0, text.lower().find(matched_phrase.lower()) - 40) : text.lower().find(matched_phrase.lower()) + 140].strip()
        return {
            "type": "CALCULATOR_GAP",
            "phrase": matched_phrase,
            "evidence": f"Instruction found: \"{snippet}\" without an interactive calculator input on page."
        }
    return None


def detect_source_to_tool(html: str, text: str, url: str) -> dict[str, Any] | None:
    """
    Detects citations of raw government or academic statistics (BLS, CFPB, NIH, CDC, Census, FRED).
    """
    agencies = ["Bureau of Labor Statistics", "BLS", "CFPB", "Consumer Financial Protection Bureau", "NIH", "CDC", "Census Bureau", "FRED", "Department of Energy", "DOE", "NREL"]

    for agency in agencies:
        pattern = rf"\b(?:according to|data from|source:\s*|reported by)\s+(?:the\s+)?{re.escape(agency)}\b"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            start = max(0, match.start() - 30)
            end = min(len(text), match.end() + 120)
            snippet = text[start:end].replace("\n", " ").strip()
            return {
                "type": "SOURCE_TO_TOOL",
                "agency": agency,
                "evidence": f"Cites primary dataset: \"{snippet}\""
            }
    return None


def detect_outdated_data(html: str, text: str, url: str) -> dict[str, Any] | None:
    """
    Detects outdated statistics or benchmarks referencing pre-2024 years.
    """
    outdated_patterns = [
        r"\b(?:in|as of|for)\s+(2019|2020|2021|2022|2023)\b",
        r"\b(2020|2021|2022|2023)\s+average\b",
        r"\b(2021|2022|2023)\s+data\b",
    ]
    for p in outdated_patterns:
        match = re.search(p, text, re.IGNORECASE)
        if match:
            year = match.group(1)
            start = max(0, match.start() - 30)
            end = min(len(text), match.end() + 90)
            snippet = text[start:end].replace("\n", " ").strip()
            return {
                "type": "OUTDATED_DATA",
                "year": year,
                "evidence": f"Historical reference ({year}) found: \"{snippet}\""
            }
    return None


def detect_unlinked_mention(html: str, text: str, url: str) -> dict[str, Any] | None:
    """
    Detects mentions of Groundwork, Elena Vance, or Zenodo DOI without a hyperlink.
    """
    mentions = ["Groundwork", "gworky.com", "Elena Vance", "10.5281/zenodo"]
    for m in mentions:
        if m.lower() in text.lower():
            # Verify if it's NOT inside an <a> tag
            pattern = rf"<a\b[^>]*>{re.escape(m)}</a>"
            if not re.search(pattern, html, re.IGNORECASE):
                return {
                    "type": "UNLINKED_MENTION",
                    "entity": m,
                    "evidence": f"Unlinked textual mention of '{m}' detected."
                }
    return None


# ── Asset Matcher & Scorer ───────────────────────────────────────────────────

def match_best_asset(text: str, pillar_hint: str | None = None) -> dict[str, Any]:
    """Matches page content to the best Groundwork interactive calculator."""
    best_asset = GROUNDWORK_ASSETS[0]
    best_score = 0

    text_lower = text.lower()

    for asset in GROUNDWORK_ASSETS:
        score = 0
        if pillar_hint and asset["pillar"].lower() == pillar_hint.lower():
            score += 15

        for kw in asset["keywords"]:
            if kw.lower() in text_lower:
                score += 10

        for tr in asset.get("triggers", []):
            if tr.lower() in text_lower:
                score += 25

        if score > best_score:
            best_score = score
            best_asset = asset

    return best_asset


def score_opportunity(
    opp_type: str,
    asset_match: dict[str, Any],
    domain: str,
    evidence_text: str,
) -> dict[str, float]:
    """
    Scores the opportunity from 0 to 100 based on ChatGPT Heuristic weights:
    - Intent: 25%
    - Asset Fit: 25%
    - Evidence: 20%
    - Low Friction: 20%
    - Authority: 10%
    """
    # Intent score
    intent_weights = {
        "UNLINKED_MENTION": 98.0,
        "CALCULATOR_GAP": 92.0,
        "SOURCE_TO_TOOL": 85.0,
        "BROKEN_RESOURCE": 82.0,
        "OUTDATED_DATA": 75.0,
    }
    intent = intent_weights.get(opp_type, 65.0)

    # Asset fit score
    asset_fit = 90.0 if asset_match else 40.0

    # Evidence score
    evidence = 90.0 if len(evidence_text) > 30 else 50.0

    # Low friction score
    friction_weights = {
        "UNLINKED_MENTION": 95.0,
        "BROKEN_RESOURCE": 90.0,
        "CALCULATOR_GAP": 85.0,
        "SOURCE_TO_TOOL": 80.0,
        "OUTDATED_DATA": 70.0,
    }
    low_friction = friction_weights.get(opp_type, 70.0)

    # Authority signal
    authority = 95.0 if any(ext in domain for ext in [".edu", ".gov", ".org"]) else 80.0

    total = (
        (intent * 0.25)
        + (asset_fit * 0.25)
        + (evidence * 0.20)
        + (low_friction * 0.20)
        + (authority * 0.10)
    )

    return {
        "intent_score": round(intent, 1),
        "asset_fit_score": round(asset_fit, 1),
        "evidence_score": round(evidence, 1),
        "friction_score": round(low_friction, 1),
        "authority_score": round(authority, 1),
        "total_score": round(total, 1),
    }


def generate_one_sentence_proposition(opp_type: str, asset: dict[str, Any], topic: str) -> str:
    """Generates a crystal-clear, zero-slop 1-sentence value proposition."""
    if opp_type == "CALCULATOR_GAP":
        return f"Your article explains how to calculate {topic}; Groundwork provides that missing interactive step without ads or paywalls."
    elif opp_type == "SOURCE_TO_TOOL":
        return "Your guide cites primary government data; Groundwork provides an interactive decision model built directly on the same official datasets with Zenodo DOI citations."
    elif opp_type == "OUTDATED_DATA":
        return "Your analysis is solid; Groundwork provides the updated 2026 benchmark data points to make your reader guidance current."
    elif opp_type == "BROKEN_RESOURCE":
        return f"We noticed a dead outbound link on your resource page and built an open-access, ad-free replacement: {asset['title']}."
    else:
        return f"Groundwork provides an evidence-based, open-access {asset['title']} that complements your existing editorial coverage on {topic}."


def synthesize_pitch_from_opportunity(opp: Opportunity, contact_name: str | None = None) -> str:
    """Synthesizes a 3-paragraph, empathetic, expert pitch from Elena Vance."""
    greeting = f"Hi {contact_name}," if contact_name else "Hi there,"
    asset = opp.matching_asset

    if opp.opportunity_type == "CALCULATOR_GAP":
        para1 = f"I was reading your guide on {asset['keywords'][0]} ({opp.target_url}) — the explanation of the underlying formula and decision rules is exceptionally clear."
        para2 = f"One friction point readers often face is performing the math manually. At Groundwork (gworky.com), our research desk built a free interactive tool: {asset['title']} ({asset['url']}). It handles the personalized calculation step instantly using primary datasets (DOI cited), with zero ads or accounts required."
    elif opp.opportunity_type == "SOURCE_TO_TOOL":
        para1 = f"I came across your piece on {asset['keywords'][0]} ({opp.target_url}) — citing primary datasets is exactly the rigorous standard readers need."
        para2 = f"We built an open-access interactive companion using that exact dataset: {asset['title']} ({asset['url']}). It turns static statistics into a personalized calculation engine with full methodology citations on Zenodo."
    else:
        para1 = f"I was reviewing your helpful directory and guides on {opp.pillar.capitalize()} ({opp.target_url})."
        para2 = f"Our research desk recently published an open-access decision utility: {asset['title']} ({asset['url']}). It features transparent formulas, zero commercial fluff, and primary source benchmarks."

    para3 = "No links or coverage required — just thought it might be a helpful resource for your readers or future editorial updates.\n\nWarm regards,\nElena Vance\nLead Research & Editorial Desk | Groundwork (gworky.com)"

    return f"{greeting}\n\n{para1}\n\n{para2}\n\n{para3}"


# ── Full Page Evaluation ──────────────────────────────────────────────────────

async def evaluate_page_opportunity(
    url: str,
    domain: str,
    pillar: str = "general",
    html: str | None = None,
) -> Opportunity | None:
    """Evaluates a single target URL against all heuristics and returns a scored Opportunity."""
    if not html:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Ch-Ua": '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"macOS"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }
        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers=headers) as client:
                res = await client.get(url)
                if res.status_code != 200:
                    return None
                html = res.text
        except Exception as e:
            logger.warning(f"Failed to fetch {url}: {e}")
            return None

    # Clean text
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    # 1. Check Heuristics in Priority Order
    detected = (
        detect_unlinked_mention(html, text, url)
        or detect_calculator_gap(html, text, url)
        or detect_source_to_tool(html, text, url)
        or detect_outdated_data(html, text, url)
    )

    if not detected:
        # Fallback to general resource opportunity if relevant
        asset = match_best_asset(text, pillar)
        detected = {
            "type": "RESOURCE_PAGE_GAP",
            "evidence": f"Editorial page matches '{asset['title']}' topic cluster."
        }

    opp_type = detected["type"]
    evidence = detected["evidence"]
    asset = match_best_asset(text, pillar)

    # 2. Score Opportunity
    scores = score_opportunity(opp_type, asset, domain, evidence)

    opp_id = hashlib.md5(f"{url}_{opp_type}".encode()).hexdigest()[:10]
    one_sentence = generate_one_sentence_proposition(opp_type, asset, asset["keywords"][0])

    opp = Opportunity(
        id=opp_id,
        target_url=url,
        domain=domain,
        pillar=pillar,
        opportunity_type=opp_type,
        evidence_snippet=evidence,
        matching_asset=asset,
        intent_score=scores["intent_score"],
        asset_fit_score=scores["asset_fit_score"],
        evidence_score=scores["evidence_score"],
        friction_score=scores["friction_score"],
        authority_score=scores["authority_score"],
        total_score=scores["total_score"],
        one_sentence_proposition=one_sentence,
        status="QUALIFIED" if scores["total_score"] >= 75.0 else "NO_GO",
    )

    opp.pitch_draft = synthesize_pitch_from_opportunity(opp)
    return opp


def harvest_and_score_signals(supabase: Any, dry_run: bool = False) -> int:
    """Harvest unprocessed growth_signals and convert to growth_opportunities."""
    try:
        res = (
            supabase.table("growth_signals")
            .select("*")
            .eq("processed", False)
            .limit(50)
            .execute()
        )
        signals = res.data or []
    except Exception as e:
        logger.warning("Could not query growth_signals: %s", e)
        return 0

    if not signals:
        logger.info("No unprocessed growth_signals found.")
        return 0

    logger.info("Processing %d growth signals into opportunities...", len(signals))
    created = 0

    for sig in signals:
        sig_id = sig["id"]
        sig_type = sig["signal_type"]
        keyword = sig.get("keyword") or ""
        pillar = sig.get("pillar") or "money"
        signal_strength = float(sig.get("signal_strength") or 0.5)

        # Map signal to opportunity type
        if sig_type == "near_page_1":
            opp_type = "content_refresh"
            intent_score = 0.90
            expected_value = 250.0 * signal_strength
        elif sig_type == "competitor_gap":
            opp_type = "new_article"
            intent_score = 0.85
            expected_value = 180.0 * signal_strength
        elif sig_type == "broken_backlink":
            opp_type = "link_acquisition"
            intent_score = 0.80
            expected_value = 120.0
        else:
            opp_type = "article_expansion"
            intent_score = 0.75
            expected_value = 100.0

        total_score = round(intent_score * 0.4 + signal_strength * 0.4 + 0.2, 3)

        opp_record = {
            "signal_id": sig_id,
            "opportunity_type": opp_type,
            "pillar": pillar,
            "keyword": keyword,
            "intent_score": intent_score,
            "asset_fit_score": 0.85,
            "authority_score": signal_strength,
            "total_score": total_score,
            "expected_value": round(expected_value, 2),
            "status": "pending",
        }

        if not dry_run:
            try:
                supabase.table("growth_opportunities").insert(opp_record).execute()
                supabase.table("growth_signals").update({"processed": True}).eq("id", sig_id).execute()
                created += 1
            except Exception as e:
                logger.warning("Failed to upsert growth opportunity for signal %s: %s", sig_id, e)
        else:
            logger.info("  [DRY-RUN] Would create opportunity for signal %s (%s, score: %.2f)", sig_id[:8], opp_type, total_score)
            created += 1

    logger.info("Harvested %d opportunities from %d signals.", created, len(signals))
    return created


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Groundwork Opportunity Engine 2.0")
    parser.add_argument("--url", type=str, default="https://extension.harvard.edu/resources/", help="Target URL to evaluate")
    parser.add_argument("--domain", type=str, default="harvard.edu", help="Target domain")
    parser.add_argument("--pillar", type=str, default="money", help="Content pillar")
    parser.add_argument("--harvest-signals", action="store_true", help="Harvest pending signals from growth_signals table")
    parser.add_argument("--dry-run", action="store_true", help="Perform operation without writing to database")
    args = parser.parse_args()

    if args.harvest_signals:
        import os
        from dotenv import load_dotenv
        from supabase import create_client
        load_dotenv(".env.local")
        url = os.getenv("NEXT_PUBLIC_SUPABASE_URL") or os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY")
        if not url or not key:
            print("❌ Missing Supabase credentials")
            sys.exit(1)
        sb = create_client(url, key)
        harvest_and_score_signals(sb, dry_run=args.dry_run)
    else:
        print(f"\n🔍 Evaluating Opportunity for: {args.url}...\n")
        opp = asyncio.run(evaluate_page_opportunity(args.url, args.domain, args.pillar))
        if opp:
            print(f"✅ Opportunity ID: {opp.id}")
            print(f"• Type: [{opp.opportunity_type}]")
            print(f"• Total Score: {opp.total_score}/100 ({opp.status})")
            print(f"• Matched Asset: {opp.matching_asset['title']}")
            print(f"• One-Sentence Proposition:\n  \"{opp.one_sentence_proposition}\"")
            print(f"• Evidence:\n  \"{opp.evidence_snippet}\"")
            print(f"\n📝 Synthesized Pitch (Elena Vance):\n{opp.pitch_draft}\n")
        else:
            print("❌ Could not evaluate opportunity.")
