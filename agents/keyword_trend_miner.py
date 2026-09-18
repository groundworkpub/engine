"""agents/keyword_trend_miner.py

Groundwork Autonomous Keyword & Trend Mining Engine (Python 3.12).
Implements:
1. Recursive Google Autocomplete Alphabet Soup (a-z expansion).
2. IPTC Media Topics 5-Pillar classification.
3. Information Gain & Knowledge Delta Scorer.
4. Persistent Search Intelligence Graph.
"""

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# IPTC Media Topics Canonical Mappings
IPTC_PILLAR_MAP = {
    "money": {
        "code": "04006000",
        "name": "Personal Finance & Investments",
        "uri": "http://cv.iptc.org/newscodes/mediatopic/04006000",
        "keywords": ["mortgage", "refinance", "interest", "apy", "savings", "hysa", "loan", "tax", "invest"],
    },
    "body": {
        "code": "07001000",
        "name": "Preventive Medicine & Public Health",
        "uri": "http://cv.iptc.org/newscodes/mediatopic/07001000",
        "keywords": ["health", "nutrition", "diet", "biomarker", "longevity", "clinical", "supplement", "metabolism"],
    },
    "home": {
        "code": "06005000",
        "name": "Renewable Energy & Energy Efficiency",
        "uri": "http://cv.iptc.org/newscodes/mediatopic/06005000",
        "keywords": ["solar", "heat pump", "hvac", "insulation", "electrification", "energy", "efficiency", "rebate"],
    },
    "life": {
        "code": "09003000",
        "name": "Remote Work & Labor Markets",
        "uri": "http://cv.iptc.org/newscodes/mediatopic/09003000",
        "keywords": ["remote work", "career", "compensation", "salary", "nomad", "travel", "legal", "cost of living"],
    },
    "tech": {
        "code": "13008000",
        "name": "Decision Support Systems, AI & Software",
        "uri": "http://cv.iptc.org/newscodes/mediatopic/13008000",
        "keywords": ["software", "ai", "llm", "privacy", "security", "decision", "hardware", "open-source"],
    },
}

PILLAR_COMPETITORS: dict[str, list[str]] = {
    "money": ["bankrate", "nerdwallet", "smartasset", "investopedia", "biggerpockets"],
    "body": ["examine", "healthline", "labdoor", "peter attia", "huberman"],
    "home": ["energysage", "bob vila", "this old house", "rewiring america"],
    "life": ["nomad list", "numbeo", "kayak", "legalzoom"],
    "tech": ["toms guide", "wirecutter", "rtings", "alternativeto"],
}


@dataclass
class DiscoveredQuery:
    query: str
    pillar: str
    iptc_code: str
    iptc_uri: str
    information_gain_score: float
    is_interactive_intent: bool
    source_seed: str
    surface: str = "google"  # google | youtube | pinterest


def _get_proxy_opener():
    """Builds a urllib opener using DataImpulse US Residential Proxy if available."""
    try:
        from egress_dataimpulse import DataImpulseProxyRouter
        if DataImpulseProxyRouter.is_available():
            proxy_url = DataImpulseProxyRouter.get_proxy_url("us")
            if proxy_url:
                proxy_handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
                return urllib.request.build_opener(proxy_handler)
    except Exception:
        pass
    return urllib.request.build_opener()


def fetch_google_suggestions(query: str, timeout: int = 5) -> list[str]:
    """Queries Google Autocomplete API for real search queries with DataImpulse proxy support."""
    encoded = urllib.parse.quote(query.strip())
    url = f"https://suggestqueries.google.com/complete/search?client=firefox&q={encoded}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"})
    opener = _get_proxy_opener()
    try:
        with opener.open(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if len(data) >= 2 and isinstance(data[1], list):
                return [str(item).strip() for item in data[1] if item]
    except Exception:
        pass
    return []


def fetch_youtube_suggestions(query: str, timeout: int = 5) -> list[str]:
    """Queries YouTube Autocomplete API for high-intent video & visual queries."""
    encoded = urllib.parse.quote(query.strip())
    url = f"https://suggestqueries.google.com/complete/search?client=youtube&ds=yt&q={encoded}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"})
    opener = _get_proxy_opener()
    try:
        with opener.open(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if len(data) >= 2 and isinstance(data[1], list):
                return [str(item).strip() for item in data[1] if item]
    except Exception:
        pass
    return []


def expand_alphabet_soup(seed_phrase: str, letters: str | None = None, surface: str = "google") -> list[str]:
    """Recursively expands a seed query across the alphabet (a-z) on Google or YouTube."""
    chars = letters or "abcdefghijklmnopqrstuvwxyz"
    results: set[str] = set()
    fetcher = fetch_youtube_suggestions if surface == "youtube" else fetch_google_suggestions

    # Base seed suggestions
    for s in fetcher(seed_phrase):
        results.add(s)

    # Alphabet expansion
    for char in chars:
        expanded_query = f"{seed_phrase} {char}"
        for suggestion in fetcher(expanded_query):
            if suggestion.lower() != seed_phrase.lower():
                results.add(suggestion)

    return sorted(list(results))


def classify_query_pillar(query: str) -> dict[str, str]:
    """Maps a query to the best-matching Groundwork pillar and IPTC concept."""
    q_lower = query.lower()
    best_pillar = "money"
    max_matches = 0

    for pillar, meta in IPTC_PILLAR_MAP.items():
        matches = sum(1 for kw in meta["keywords"] if kw in q_lower)
        if matches > max_matches:
            max_matches = matches
            best_pillar = pillar

    meta = IPTC_PILLAR_MAP[best_pillar]
    return {
        "pillar": best_pillar,
        "iptc_code": meta["code"],
        "iptc_uri": meta["uri"],
        "iptc_name": meta["name"],
    }


def score_information_gain(query: str) -> dict[str, Any]:
    """
    Evaluates Information Gain potential according to Google's Patent model:
    - High score: Interactive calculation, break-even comparison, primary datasets.
    - Low score: Generic consensus questions.
    """
    q_lower = query.lower()
    score = 50.0  # Base score
    is_interactive = False

    # Positive modifiers: Interactive utilities & calculation intents (+20 to +30 pts)
    if any(term in q_lower for term in ["calculator", "break even", "formula", "roi", "estimate", "net cost"]):
        score += 30.0
        is_interactive = True
    elif any(term in q_lower for term in ["vs", "compare", "comparison", "matrix", "breakdown", "difference"]):
        score += 20.0
        is_interactive = True

    # Positive modifiers: Empirical research signals (+15 pts)
    if any(term in q_lower for term in ["guidelines", "clinical", "biomarker", "benchmark", "evidence", "dataset"]):
        score += 15.0

    # Negative modifiers: Generic shallow intent (-20 pts)
    if any(term in q_lower for term in ["what is", "meaning of", "definition of", "wiki"]):
        score -= 20.0

    final_score = max(0.0, min(100.0, round(score, 1)))
    return {
        "score": final_score,
        "is_interactive": is_interactive,
    }


def mine_pillar_keywords(
    seed_queries: list[str],
    max_suggestions_per_seed: int = 15,
    surfaces: list[str] | None = None,
) -> list[DiscoveredQuery]:
    """Executes multi-surface mining across Google & YouTube and clusters queries into structured entities."""
    discovered: list[DiscoveredQuery] = []
    seen: set[str] = set()
    active_surfaces = surfaces or ["google", "youtube"]

    for seed in seed_queries:
        for surface in active_surfaces:
            suggestions = expand_alphabet_soup(seed, letters="abcmrstv", surface=surface)[:max_suggestions_per_seed]
            for item in suggestions:
                normalized = f"{surface}:{item.lower().strip()}"
                if normalized in seen:
                    continue
                seen.add(normalized)

                pillar_info = classify_query_pillar(item)
                gain_info = score_information_gain(item)

                discovered.append(
                    DiscoveredQuery(
                        query=item,
                        pillar=pillar_info["pillar"],
                        iptc_code=pillar_info["iptc_code"],
                        iptc_uri=pillar_info["iptc_uri"],
                        information_gain_score=gain_info["score"],
                        is_interactive_intent=gain_info["is_interactive"],
                        source_seed=seed,
                        surface=surface,
                    )
                )

    return sorted(discovered, key=lambda x: x.information_gain_score, reverse=True)


def mine_competitor_comparison_keywords(
    max_competitors_per_pillar: int = 3,
    surfaces: list[str] | None = None,
) -> list[DiscoveredQuery]:
    """Generates competitor alternative, comparison, and calculator switch queries across 5 pillars."""
    competitor_seeds: list[str] = []
    for pillar, comps in PILLAR_COMPETITORS.items():
        for comp in comps[:max_competitors_per_pillar]:
            competitor_seeds.append(f"{comp} vs gworky")
            competitor_seeds.append(f"{comp} alternative gworky")
            competitor_seeds.append(f"gworky {pillar} vs {comp}")

    return mine_pillar_keywords(competitor_seeds, max_suggestions_per_seed=8, surfaces=surfaces)


def persist_discovered_keywords(queries: list[DiscoveredQuery], output_path: str = "agents/output/keyword-graph.json") -> None:
    """Saves discovered search intelligence into JSON format for Scribe, Video Shorts, and Sitemaps."""
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    data = [
        {
            "query": q.query,
            "pillar": q.pillar,
            "iptc_code": q.iptc_code,
            "iptc_uri": q.iptc_uri,
            "information_gain_score": q.information_gain_score,
            "is_interactive_intent": q.is_interactive_intent,
            "source_seed": q.source_seed,
            "surface": q.surface,
        }
        for q in queries
    ]

    out_file.write_text(json.dumps({"total": len(data), "queries": data}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    seeds = [
        "mortgage refinance break even",
        "evidence based nutrition guidelines",
        "heat pump installation cost",
        "remote work cost of living",
        "AI tool security audit",
    ]
    print(f"Mining multi-surface keyword trends (Google + YouTube) across {len(seeds)} seeds...")
    pillar_results = mine_pillar_keywords(seeds)
    comp_results = mine_competitor_comparison_keywords()
    combined = sorted(pillar_results + comp_results, key=lambda x: x.information_gain_score, reverse=True)
    persist_discovered_keywords(combined)
    print(f"✅ Successfully mined and persisted {len(combined)} high-gain keyword queries to agents/output/keyword-graph.json")
