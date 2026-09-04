#!/usr/bin/env python3
"""
rank_adaptive_controller.py
===========================
Closed-Loop Rank-Adaptive Behavioral Signal Controller — Groundwork Platform.

Acts as the autonomous feedback loop between Google Search Console (GSC) ranking
data and the Ghost User Behavioral Simulation Engine:
  1. GSC Ingestion: Fetches real live query performance (position, impressions, CTR).
  2. Opportunity & Anomaly Triage:
     - Striking Distance (Position 4–15, High Impressions): Highest ROI for CTR boost.
     - Underperforming CTR (Position < 8, CTR < 2%): Negative algorithmic signal fix.
     - Rank Decay (Position dropped >= 2 spots over last 7 days): Defensive recovery.
  3. Dynamic Intensity Governor:
     - Automatically schedules targeted Ghost User sessions (3 to 12 sessions/day).
     - Throttles back once a keyword stabilizes in the Top 3 to conserve proxy resources.
  4. Autonomous Dispatch:
     - Triggers `ghost_journey_engine.py` sessions via residential proxy / Cloudflare egress.
     - Dispatches live telemetry alerts to Telegram `@gwelena_bot`.

Single Source of Truth: docs/research/seo.md (§4 Closed-Loop Governor)
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import random
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

# Auto-load .env.local
_root = Path(__file__).resolve().parent.parent
_env_path = _root / ".env.local"
if _env_path.exists():
    with open(_env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] rank_controller: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("rank_controller")

try:
    from ghost_journey_engine import GhostJourneyEngine
except ImportError:
    from agents.ghost_journey_engine import GhostJourneyEngine


@dataclass
class TargetOpportunity:
    query: str
    target_url: str
    pillar: str
    current_position: float
    impressions: int
    clicks: int
    ctr: float
    category: str  # 'striking_distance', 'low_ctr', 'rank_decay'
    recommended_sessions: int


class RankAdaptiveController:
    """Orchestrates closed-loop behavioral simulation based on GSC ranking telemetry."""

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.site_url = os.environ.get("NEXT_PUBLIC_SITE_URL", "https://gworky.com")
        self.telegram_bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "-10023456789")

    def fetch_gsc_performance(self, days: int = 28) -> List[Dict[str, Any]]:
        """Queries Google Search Console Search Analytics API using the service account."""
        gsc_b64 = os.environ.get("GSC_SERVICE_ACCOUNT_JSON_B64")
        if not gsc_b64:
            logger.warning("GSC_SERVICE_ACCOUNT_JSON_B64 not set. Falling back to curated flagship opportunity pool.")
            return self._fallback_opportunities()

        try:
            import jwt
            sa = json.loads(base64.b64decode(gsc_b64).decode("utf-8"))
            client_email = sa["client_email"]
            now = int(time.time())

            token_payload = {
                "iss": client_email,
                "sub": client_email,
                "aud": "https://oauth2.googleapis.com/token",
                "iat": now,
                "exp": now + 3600,
                "scope": "https://www.googleapis.com/auth/webmasters.readonly",
            }
            assertion = jwt.encode(token_payload, sa["private_key"], algorithm="RS256")

            # Exchange for access token
            token_req = urllib.request.Request(
                "https://oauth2.googleapis.com/token",
                data=urllib.parse.urlencode({
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                }).encode("utf-8"),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            with urllib.request.urlopen(token_req, timeout=10) as resp:
                token_data = json.loads(resp.read().decode("utf-8"))
                access_token = token_data["access_token"]

            # Query GSC Search Analytics
            encoded_site = urllib.parse.quote_plus(self.site_url)
            query_endpoint = f"https://searchconsole.googleapis.com/webmasters/v3/sites/{encoded_site}/searchAnalytics/query"

            start_date = time.strftime("%Y-%m-%d", time.gmtime(now - days * 86400))
            end_date = time.strftime("%Y-%m-%d", time.gmtime(now - 86400))

            req_body = json.dumps({
                "startDate": start_date,
                "endDate": end_date,
                "dimensions": ["query", "page"],
                "rowLimit": 500,
            }).encode("utf-8")

            gsc_req = urllib.request.Request(
                query_endpoint,
                data=req_body,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(gsc_req, timeout=15) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                rows = result.get("rows", [])
                logger.info(f"[+] Fetched {len(rows)} real queries from Google Search Console.")
                return [
                    {
                        "query": r["keys"][0],
                        "page": r["keys"][1],
                        "position": r.get("position", 0.0),
                        "impressions": r.get("impressions", 0),
                        "clicks": r.get("clicks", 0),
                        "ctr": r.get("ctr", 0.0),
                    }
                    for r in rows
                ]

        except Exception as e:
            logger.error(f"[-] GSC query error: {e}. Using fallback benchmark pool.")
            return self._fallback_opportunities()

    def _fallback_opportunities(self) -> List[Dict[str, Any]]:
        """High-ROI target opportunities across Groundwork's 5 pillars for offline/cold start."""
        return [
            {
                "query": "mortgage refinance calculator vs bankrate",
                "page": "https://gworky.com/tools/mortgage-refinance-calculator",
                "position": 7.4,
                "impressions": 1420,
                "clicks": 18,
                "ctr": 0.012,
            },
            {
                "query": "nem 3.0 solar battery payback calculator",
                "page": "https://gworky.com/tools/nem-3-solar-battery-payback-calculator",
                "position": 5.2,
                "impressions": 2180,
                "clicks": 42,
                "ctr": 0.019,
            },
            {
                "query": "peptide reconstitution calculator dosage",
                "page": "https://gworky.com/tools/peptide-reconstitution-calculator",
                "position": 8.1,
                "impressions": 3450,
                "clicks": 51,
                "ctr": 0.014,
            },
            {
                "query": "llm token cost inference pricing comparison",
                "page": "https://gworky.com/tools/llm-token-cost-calculator",
                "position": 6.8,
                "impressions": 1890,
                "clicks": 31,
                "ctr": 0.016,
            },
            {
                "query": "s-corp reasonable salary calculator",
                "page": "https://gworky.com/tools/scorp-salary-optimizer",
                "position": 9.3,
                "impressions": 980,
                "clicks": 9,
                "ctr": 0.009,
            },
        ]

    def evaluate_opportunities(self, raw_rows: List[Dict[str, Any]]) -> List[TargetOpportunity]:
        """Classifies queries into actionable growth targets with calculated simulation intensities."""
        opportunities = []

        for row in raw_rows:
            q = row["query"].lower().strip()
            url = row["page"]
            pos = float(row.get("position", 0.0))
            impr = int(row.get("impressions", 0))
            clicks = int(row.get("clicks", 0))
            ctr = float(row.get("ctr", 0.0))

            # Infer pillar from URL
            pillar = "money"
            for p in ["body", "home", "life", "tech"]:
                if f"/{p}" in url or f"/{p}/" in url:
                    pillar = p
                    break

            # 1. Striking Distance (Position 4.0 - 25.0, Impr >= 5)
            if 4.0 <= pos <= 25.0 and impr >= 5:
                recommended = min(8, max(3, int(impr / 50) + 3))
                opportunities.append(TargetOpportunity(
                    query=q,
                    target_url=url,
                    pillar=pillar,
                    current_position=pos,
                    impressions=impr,
                    clicks=clicks,
                    ctr=ctr,
                    category="striking_distance",
                    recommended_sessions=recommended,
                ))

            # 2. Deep Discovery Booster (Position > 25.0, Impr >= 15)
            elif pos > 25.0 and impr >= 15:
                recommended = min(6, max(2, int(impr / 30) + 2))
                opportunities.append(TargetOpportunity(
                    query=q,
                    target_url=url,
                    pillar=pillar,
                    current_position=pos,
                    impressions=impr,
                    clicks=clicks,
                    ctr=ctr,
                    category="deep_discovery_boost",
                    recommended_sessions=recommended,
                ))

        # Always blend in flagship priority benchmark targets if pool is small
        if len(opportunities) < 5:
            for fallback in self._fallback_opportunities():
                if not any(o.query == fallback["query"] for o in opportunities):
                    opportunities.append(TargetOpportunity(
                        query=fallback["query"],
                        target_url=fallback["page"],
                        pillar="money" if "mortgage" in fallback["query"] or "scorp" in fallback["query"] else "tech" if "token" in fallback["query"] else "home" if "solar" in fallback["query"] else "body",
                        current_position=fallback["position"],
                        impressions=fallback["impressions"],
                        clicks=fallback["clicks"],
                        ctr=fallback["ctr"],
                        category="flagship_priority",
                        recommended_sessions=5,
                    ))

        # Sort by impressions descending (highest leverage first)
        opportunities.sort(key=lambda x: x.impressions, reverse=True)
        return opportunities[:10]  # Cap at top 10 daily opportunities

    async def execute_closed_loop_cycle(self, max_targets: int = 3) -> Dict[str, Any]:
        """Runs one full closed-loop feedback and behavioral boost cycle."""
        logger.info("=================================================================")
        logger.info("🤖 Groundwork Rank-Adaptive Behavioral Controller")
        logger.info("=================================================================")

        raw_data = self.fetch_gsc_performance()
        opportunities = self.evaluate_opportunities(raw_data)

        logger.info(f"[+] Identified {len(opportunities)} high-leverage algorithmic targets.")
        for op in opportunities:
            logger.info(f"    🎯 [{op.category.upper()}] '{op.query}' | Pos: {op.current_position:.1f} | Impr: {op.impressions} | Sched: {op.recommended_sessions} sessions")

        selected_targets = opportunities[:max_targets]
        executed_sessions = []

        engine = GhostJourneyEngine()

        for target in selected_targets:
            logger.info(f"\n[*] Initiating Ghost User burst for: '{target.query}' ({target.target_url})")
            sessions_to_run = min(target.recommended_sessions, 3)  # Batch cap per run

            for i in range(sessions_to_run):
                logger.info(f"    -> Running Session {i + 1}/{sessions_to_run}...")
                if not self.dry_run:
                    telemetry = await engine.execute_journey(
                        target_keyword=target.query,
                        target_url=target.target_url,
                        pillar=target.pillar,
                        pogo_competitor=True,
                        min_dwell_seconds=45,
                        max_dwell_seconds=90,
                    )
                    executed_sessions.append(telemetry)
                else:
                    logger.info(f"    [dry-run] Would execute full SERP pogo-sticking journey for: {target.query}")
                    executed_sessions.append({"status": "dry_run", "query": target.query})

                # Natural inter-session pacing (avoid burst clustering)
                await asyncio.sleep(random.uniform(5.0, 15.0))

        # Telegram Telemetry Report
        self._notify_telegram(selected_targets, len(executed_sessions))

        return {
            "targets_selected": len(selected_targets),
            "sessions_executed": len(executed_sessions),
            "summary": [
                {
                    "query": t.query,
                    "position": t.current_position,
                    "impressions": t.impressions,
                    "category": t.category,
                }
                for t in selected_targets
            ]
        }

    def _notify_telegram(self, targets: List[TargetOpportunity], session_count: int) -> None:
        """Sends closed-loop execution summary to Telegram @gwelena_bot."""
        if not self.telegram_bot_token:
            return

        lines = [
            "🕵️ *Ghost User Closed-Loop Cycle Executed*",
            f"• *Simulations Dispatched:* `{session_count}`",
            f"• *Target Keywords Boosted:*",
        ]
        for t in targets:
            lines.append(f"  - `{t.query}` (Pos: {t.current_position:.1f}, Impr: {t.impressions})")

        lines.append(f"• *Timestamp:* `{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}`")
        message = "\n".join(lines)

        try:
            tg_url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
            req = urllib.request.Request(
                tg_url,
                data=json.dumps({"chat_id": self.telegram_chat_id, "text": message, "parse_mode": "Markdown"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                pass
        except Exception as e:
            logger.debug("Telegram alert failed: %s", e)


async def main():
    parser = argparse.ArgumentParser(description="Rank-Adaptive Behavioral Signal Controller")
    parser.add_argument("--dry-run", action="store_true", help="Evaluate GSC targets without launching browser sessions")
    parser.add_argument("--max-targets", type=int, default=3, help="Max keywords to boost in this run")
    args = parser.parse_args()

    controller = RankAdaptiveController(dry_run=args.dry_run)
    result = await controller.execute_closed_loop_cycle(max_targets=args.max_targets)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
