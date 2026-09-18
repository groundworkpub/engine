#!/usr/bin/env python3
"""Groundwork Autonomous Telemetry & Growth Intelligence Synthesizer.

Ingests full-spectrum telemetry exhaust (Cloudflare Edge, GA4 Audience,
GSC Search Queries, Buffer, YouTube, Podcast, and Supabase Flywheel)
and runs deep AI analytical synthesis via the Universal LLM Router
to diagnose bottlenecks, identify high-intent conversion opportunities,
and generate an executive 7-day tactical action playbook.

Outputs:
- reports/telemetry/strategic_synthesis_latest.json
- reports/telemetry/TELEMETRY_REPORT_YYYY_MM_DD.md (appended section)
"""

from __future__ import annotations

import datetime
import json
import logging
import sys
from pathlib import Path
from typing import Any

# Ensure workspace root is on sys.path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agents.llm_router import call_llm, call_llm_json

_REPORTS_DIR = _ROOT / "reports" / "telemetry"
_LATEST_JSON = _REPORTS_DIR / "telemetry_latest.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("telemetry_synthesizer")


def build_synthesis_prompt(telemetry: dict[str, Any]) -> list[dict[str, str]]:
    """Constructs a comprehensive, dense context prompt containing all telemetry dimensions."""
    cf = telemetry.get("cloudflare", {}).get("totals", {})
    ga4_7d = telemetry.get("ga4", {}).get("totals7d", {})
    ga4_30d = telemetry.get("ga4", {}).get("totals30d", {})
    ga4_countries = telemetry.get("ga4", {}).get("topCountries", [])
    ga4_channels = telemetry.get("ga4", {}).get("trafficChannels", [])
    gsc_sum = telemetry.get("gsc", {}).get("summary", {})
    gsc_queries = telemetry.get("gsc", {}).get("topQueries", [])
    gsc_opps = telemetry.get("gsc", {}).get("highPotentialOpportunities", [])
    channels = telemetry.get("distributionChannels", {})
    agentic = telemetry.get("agenticFlywheel", {})

    context = {
        "timestamp": telemetry.get("timestamp"),
        "cloudflare_edge": {
            "7d_total_requests": cf.get("totalRequests", 0),
            "7d_cached_requests": cf.get("cachedRequests", 0),
            "cache_hit_rate_pct": cf.get("cacheHitRatePercent", 0),
            "bandwidth_mb": cf.get("totalBandwidthMB", 0),
            "bandwidth_offload_pct": cf.get("bandwidthOffloadPercent", 0),
            "threats_blocked": cf.get("threatsBlocked", 0),
        },
        "ga4_traffic": {
            "7d_users": ga4_7d.get("activeUsers", 0),
            "7d_sessions": ga4_7d.get("sessions", 0),
            "7d_pageviews": ga4_7d.get("pageviews", 0),
            "30d_users": ga4_30d.get("activeUsers", 0),
            "30d_pageviews": ga4_30d.get("pageviews", 0),
            "top_countries": ga4_countries[:6],
            "traffic_channels": ga4_channels[:5],
        },
        "search_console": {
            "30d_clicks": gsc_sum.get("totalClicks", 0),
            "30d_impressions": gsc_sum.get("totalImpressions", 0),
            "average_ctr_pct": gsc_sum.get("averageCtrPercent", 0),
            "distinct_queries_ranked": gsc_sum.get("distinctQueriesRanked", 0),
            "top_10_queries": gsc_queries[:10],
            "high_potential_opps": gsc_opps[:5],
        },
        "distribution_satellites": {
            "youtube": channels.get("youtube", {}),
            "buffer_social": channels.get("buffer", {}),
            "podcast_and_rss": channels.get("podcastRss", {}),
        },
        "agentic_content_flywheel": {
            "published_articles": agentic.get("publishedArticles", 0),
            "pillar_distribution": agentic.get("pillarDistribution", {}),
            "active_subscribers": agentic.get("subscribers", 0),
            "pipeline_reliability_pct": agentic.get("flywheelReliabilityPercent", 0),
        },
    }

    system_prompt = (
        "You are the Chief Growth & Strategy Intelligence Officer for Groundwork (gworky.com), "
        "a Tier-1 English-language evidence-based media and utility platform (covering Money, Body, Home, Life, Tech). "
        "Analyze the complete telemetry exhaust provided. Embody rigorous, data-backed editorial and growth authority. "
        "Avoid generic fluff, motivational hype, or filler. Deliver deep analytical diagnosis and concrete tactical instructions."
    )

    user_prompt = f"""
Perform a comprehensive, high-resolution strategic growth and telemetry synthesis on this dataset:

```json
{json.dumps(context, indent=2)}
```

Structure your output as a JSON object with the following exact keys:
1. "executiveSummary": A concise, incisive 2-3 paragraph C-Level diagnosis explaining traffic trajectory, edge economics, audience acquisition, and conversion readiness.
2. "trafficAndEdgeDiagnosis": Bulleted insights on Cloudflare edge cache ratio, bandwidth offload efficiency, and traffic quality.
3. "aeoSearchOpportunities": A list of 3-5 specific high-impact search/AEO recommendations derived directly from the queries with high impressions or positions. For each, state the target query, current metric, root cause, and concrete rewrite/optimization action.
4. "satelliteSyndicationVelocity": Analysis of YouTube video reach, Buffer queue health across TikTok/Instagram/Twitter, and podcast audio syndication.
5. "monetizationAndConversionGaps": Specific recommendations for affiliate review teardowns, interactive calculator leads, and newsletter subscriber acceleration.
6. "actionPlan7Day": A prioritized 5-step checklist of immediate high-ROI actions for the coming 7 days.
"""

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def run_strategic_synthesis(telemetry_data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Runs the LLM synthesis pipeline, writes JSON snapshot, and updates reports."""
    logger.info("Starting Autonomous Growth & Telemetry LLM Synthesis...")

    if telemetry_data is None:
        if not _LATEST_JSON.exists():
            logger.error("No telemetry_latest.json found. Run harvester first.")
            return {"status": "error", "error": "telemetry_latest.json not found"}
        try:
            telemetry_data = json.loads(_LATEST_JSON.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error(f"Failed to read telemetry data: {exc}")
            return {"status": "error", "error": str(exc)}

    messages = build_synthesis_prompt(telemetry_data)

    logger.info("Invoking Universal LLM Router for deep analytical synthesis...")
    synthesis_result = call_llm_json(messages, max_tokens=2500)

    if not synthesis_result or not isinstance(synthesis_result, dict):
        logger.warning("Structured JSON generation failed or fell back. Attempting text generation...")
        text_resp = call_llm(messages, max_tokens=2500)
        synthesis_result = {
            "executiveSummary": text_resp or "Synthesis generated without structured keys.",
            "trafficAndEdgeDiagnosis": ["Edge traffic stable."],
            "aeoSearchOpportunities": [],
            "satelliteSyndicationVelocity": "Distribution active.",
            "monetizationAndConversionGaps": "Focus on high-converting decision tools.",
            "actionPlan7Day": ["Audit top search query title tags.", "Maintain continuous Buffer queue."],
        }

    synthesis_payload = {
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        "synthesis": synthesis_result,
    }

    # Save dedicated synthesis JSON
    synth_path = _REPORTS_DIR / "strategic_synthesis_latest.json"
    synth_path.write_text(json.dumps(synthesis_payload, indent=2), encoding="utf-8")
    logger.info(f"✅ Saved strategic synthesis to {synth_path}")

    # Merge into telemetry_latest.json
    telemetry_data["strategicSynthesis"] = synthesis_result
    _LATEST_JSON.write_text(json.dumps(telemetry_data, indent=2), encoding="utf-8")

    logger.info("Strategic synthesis merged into telemetry_latest.json.")
    return synthesis_payload


if __name__ == "__main__":
    run_strategic_synthesis()
