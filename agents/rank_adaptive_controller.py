#!/usr/bin/env python3
"""
rank_adaptive_controller.py
===========================
Closed-Loop Rank-Adaptive Behavioral Signal Controller — Groundwork Platform.

Triangulated Signal & Closed-Loop Feedback Loop:
  1. GSC Ingestion: Live striking-distance queries (Pos 4–25).
  2. Cold-Start Programmatic SEO Ingestion: 50-State regional calculators.
  3. A/B Testing Engine: Deterministic 70/30 split (Treatment vs Control Holdout).
  4. Sigmoidal Positional CTR Governor: Elastic boost (1.5x–2.2x), capped to <2.5x variance.
  5. Staged Ramp-Up Governor: Phase 1 (10-15/day), Phase 2 (25-35/day), Phase 3 (steady state).
  6. Circadian Diurnal Modulator: Schedules sessions around US/UK/AU active waking hours.
  7. 3-Strike Circuit Breaker: 2h cooldown + Telegram `@gwelena_bot` alerts on consecutive blocks.

Single Source of Truth: docs/research/seo.md (§4 Closed-Loop Governor) & AGENTS.md §2.12
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import math
import os
import random
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


# ── Triangulation & Statistical Formulas ──────────────────────────────────────

def calculate_sigmoidal_ctr(position: float) -> float:
    """Calculates baseline organic CTR by SERP position using calibrated exponential/logistic decay.

    Pos 1: ~28.5%
    Pos 3: ~17.2%
    Pos 5: ~10.4%
    Pos 8: ~4.9%
    Pos 10: ~3.0%
    Pos 15: ~0.9%
    """
    pos = max(1.0, float(position))
    raw_ctr = 0.285 * math.exp(-0.25 * (pos - 1.0))
    return max(0.005, min(0.32, raw_ctr))


def partition_ab_cohort(query: str) -> str:
    """Deterministically partitions a query into Treatment (70%) or Control (30%) cohort."""
    norm_q = query.strip().lower()
    h = 0
    for c in norm_q:
        h = (h * 31 + ord(c)) & 0xFFFFFFFF
    return "treatment" if (h % 10) < 7 else "control"


def calculate_circadian_weight(local_hour: int) -> float:
    """Computes activity multiplier based on target geo local hour (0-23).

    01:00 - 06:00: Sleep trough (0.05x - 0.12x)
    09:00 - 14:00: Daytime work/research peak (0.85x - 1.0x)
    18:00 - 22:00: Evening research peak (0.75x - 0.90x)
    """
    h = local_hour % 24
    if 1 <= h <= 5:
        return 0.08
    elif 6 <= h <= 8:
        return 0.45
    elif 9 <= h <= 14:
        return 1.0
    elif 15 <= h <= 17:
        return 0.70
    elif 18 <= h <= 22:
        return 0.85
    else:
        return 0.25


@dataclass
class TargetOpportunity:
    query: str
    target_url: str
    pillar: str
    current_position: float
    impressions: int
    clicks: int
    ctr: float
    category: str  # 'striking_distance', 'low_ctr', 'rank_decay', 'pseo_coldstart'
    recommended_sessions: int
    cohort: str = "treatment"  # 'treatment' or 'control'


# ── The Closed-Loop Controller ────────────────────────────────────────────────

class RankAdaptiveController:
    """Orchestrates closed-loop behavioral simulation based on GSC ranking telemetry."""

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.site_url = os.environ.get("NEXT_PUBLIC_SITE_URL", "https://gworky.com")
        self.telegram_bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = os.environ.get("TELEGRAM_FOUNDER_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID", "-10023456789")
        self.consecutive_blocks = 0
        self.circuit_breaker_until = 0.0

    def get_ramp_up_limit(self) -> int:
        """Evaluates historical sessions in logs/ghost_journeys.jsonl to determine current phase limit."""
        log_file = _root / "logs" / "ghost_journeys.jsonl"
        if not log_file.exists():
            return 12  # Phase 1 default

        total_sessions = 0
        now = time.time()
        earliest_session_time = now
        try:
            with open(log_file, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        total_sessions += 1
                        data = json.loads(line)
                        t_str = data.get("started_at")
                        if t_str:
                            t_sec = time.mktime(time.strptime(t_str, "%Y-%m-%dT%H:%M:%SZ"))
                            if t_sec < earliest_session_time:
                                earliest_session_time = t_sec
        except Exception:
            pass

        days_running = max(0.1, (now - earliest_session_time) / 86400.0)

        # Staged Ramp-Up:
        # Phase 1 (Days 1–3): 10–15 sessions/day
        # Phase 2 (Days 4–7): 25–35 sessions/day
        # Phase 3 (Day 8+): steady state ~50 sessions/day
        if days_running <= 3.0:
            limit = 12
            logger.info(f"📈 Ramp-Up Phase 1 (Day {days_running:.1f}/3): Daily Limit = {limit} sessions")
        elif days_running <= 7.0:
            limit = 28
            logger.info(f"📈 Ramp-Up Phase 2 (Day {days_running:.1f}/7): Daily Limit = {limit} sessions")
        else:
            limit = 45
            logger.info(f"📈 Ramp-Up Phase 3 (Steady State, Day {days_running:.1f}): Daily Limit = {limit} sessions")

        return limit

    def fetch_gsc_performance(self, days: int = 28) -> list[dict[str, Any]]:
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

    def _fallback_opportunities(self) -> list[dict[str, Any]]:
        """Extracts high-ROI target opportunities from telemetry report or Supabase keyword rankings."""
        telemetry_file = _root / "reports" / "telemetry" / "telemetry_latest.json"
        if telemetry_file.exists():
            try:
                import json
                data = json.loads(telemetry_file.read_text(encoding="utf-8"))
                gsc_data = data.get("gsc", {})
                opps = gsc_data.get("highPotentialOpportunities", [])
                results = []
                for opp in opps:
                    q = opp.get("query", "")
                    if not q:
                        continue
                    results.append({
                        "query": q,
                        "page": opp.get("page") or "https://gworky.com",
                        "position": float(opp.get("position", 10.0)),
                        "impressions": int(opp.get("impressions", 10)),
                        "clicks": int(opp.get("clicks", 0)),
                        "ctr": float(opp.get("ctrPercent", 0.0)) / 100.0,
                    })
                if results:
                    return results
            except Exception as exc:
                logger.warning(f"Could not load GSC opportunities from telemetry: {exc}")

        # Supabase keyword_rankings fallback
        try:
            res = self.supabase.table("keyword_rankings").select("keyword,position,domain").limit(20).execute()
            if res.data:
                return [
                    {
                        "query": row["keyword"],
                        "page": f"https://{row.get('domain', 'gworky.com')}",
                        "position": float(row.get("position", 10.0)),
                        "impressions": 100,
                        "clicks": 1,
                        "ctr": 0.01,
                    }
                    for row in res.data
                ]
        except Exception:
            pass

        return []

    def triage_opportunities(self, gsc_rows: list[dict[str, Any]]) -> list[TargetOpportunity]:
        """Triages queries into striking distance, computes sigmoidal target sessions, and partitions A/B cohorts."""
        opportunities: list[TargetOpportunity] = []

        for row in gsc_rows:
            query = row["query"]
            page = row["page"]
            pos = float(row.get("position", 0.0))
            imps = int(row.get("impressions", 0))
            clicks = int(row.get("clicks", 0))
            ctr = float(row.get("ctr", 0.0))

            # Pillar mapping
            pillar = "money"
            for p in ["body", "home", "life", "tech"]:
                if f"/{p}" in page or p in query:
                    pillar = p
                    break

            category = None
            if 4.0 <= pos <= 20.0 and imps >= 1:
                category = "striking_distance"
            elif pos < 8.0 and ctr < 0.02 and imps >= 1:
                category = "low_ctr"
            elif 20.0 < pos <= 40.0 and imps >= 1:
                category = "pseo_coldstart"

            if not category:
                continue

            # Sigmoidal Positional CTR Allocation
            baseline_ctr = calculate_sigmoidal_ctr(pos)
            # Elastic Boost factor: 1.6x baseline
            target_ctr = min(0.25, baseline_ctr * 1.6)
            # Allocation: 1-4 sessions
            sessions = min(4, max(1, int(math.ceil(imps * target_ctr))))

            # Deterministic A/B Partition
            cohort = partition_ab_cohort(query)

            opportunities.append(
                TargetOpportunity(
                    query=query,
                    target_url=page,
                    pillar=pillar,
                    current_position=pos,
                    impressions=imps,
                    clicks=clicks,
                    ctr=ctr,
                    category=category,
                    recommended_sessions=sessions,
                    cohort=cohort,
                )
            )

        # If no opportunities qualified from GSC, utilize telemetry-derived high potential opportunities
        if not opportunities:
            for fb in self._fallback_opportunities():
                cohort = partition_ab_cohort(fb["query"])
                opportunities.append(
                    TargetOpportunity(
                        query=fb["query"],
                        target_url=fb["page"],
                        pillar="money" if "mortgage" in fb["query"] else ("home" if "solar" in fb["query"] or "heat" in fb["query"] else "body"),
                        current_position=fb["position"],
                        impressions=fb["impressions"],
                        clicks=fb["clicks"],
                        ctr=fb["ctr"],
                        category="striking_distance" if fb["position"] <= 15.0 else "pseo_coldstart",
                        recommended_sessions=2,
                        cohort=cohort,
                    )
                )

        # Sort priority: striking distance first, then higher impressions
        opportunities.sort(key=lambda o: (0 if o.category == "striking_distance" else 1, -o.impressions))
        return opportunities

    async def execute_batch(self, max_sessions: int | None = None, dwell_seconds: int | None = None) -> dict[str, Any]:
        """Executes a governed batch of simulation sessions respecting ramp-up limits and circuit breakers."""
        now = time.time()
        if now < self.circuit_breaker_until:
            wait_rem = int(self.circuit_breaker_until - now)
            logger.warning(f"⛔ Circuit Breaker Active. Ghost User paused for {wait_rem}s. Skipping batch.")
            return {"status": "paused_circuit_breaker", "remaining_cooldown_seconds": wait_rem}

        ramp_limit = self.get_ramp_up_limit()
        effective_limit = min(ramp_limit, max_sessions) if max_sessions else ramp_limit

        # Local hour circadian check (default to US Eastern)
        us_hour = (int(time.strftime("%H", time.gmtime())) - 4) % 24
        circadian_mult = calculate_circadian_weight(us_hour)
        if circadian_mult < 0.20:
            logger.info(f"🌙 Local Target Sleep Hours (US EST: {us_hour:02d}:00, mult={circadian_mult:.2f}). Deferring non-urgent sessions.")
            effective_limit = max(1, int(effective_limit * 0.25))

        logger.info(f"🎯 Rank-Adaptive Controller Starting Batch | Max Sessions: {effective_limit} (Circadian Mult: {circadian_mult:.2f})")

        gsc_rows = self.fetch_gsc_performance()
        all_opps = self.triage_opportunities(gsc_rows)

        # Filter strictly for Treatment Cohort
        treatment_opps = [o for o in all_opps if o.cohort == "treatment"]
        control_opps = [o for o in all_opps if o.cohort == "control"]
        logger.info(f"📊 A/B Testing Cohorts: {len(treatment_opps)} Treatment (Active), {len(control_opps)} Control (Holdout)")

        if not treatment_opps:
            logger.warning("No treatment opportunities found.")
            return {"status": "no_opportunities", "sessions_executed": 0}

        engine = GhostJourneyEngine()
        sessions_run = 0
        successful = 0
        failed = 0

        for opp in treatment_opps:
            if sessions_run >= effective_limit:
                break

            # 25% chance of returning visitor
            is_returning = (random.random() < 0.25)
            # Pogo competitor for striking distance
            pogo = (opp.category == "striking_distance")

            logger.info(f"🚀 Dispatching Journey {sessions_run + 1}/{effective_limit}: '{opp.query}' (Pos #{opp.current_position:.1f}, Returning: {is_returning})")

            if self.dry_run:
                logger.info(f"[DRY-RUN] Would simulate: {opp.query} -> {opp.target_url} (Pogo: {pogo})")
                sessions_run += 1
                successful += 1
                await asyncio.sleep(0.5)
                continue

            dwell_kwargs = {}
            if dwell_seconds:
                dwell_kwargs["min_dwell_seconds"] = max(10, dwell_seconds - 5)
                dwell_kwargs["max_dwell_seconds"] = dwell_seconds + 10

            res = await engine.execute_journey(
                target_keyword=opp.query,
                target_url=opp.target_url,
                pillar=opp.pillar,
                pogo_competitor=pogo,
                is_returning=is_returning,
                **dwell_kwargs
            )

            sessions_run += 1
            if res.get("status") in ("success", "success_chaos"):
                successful += 1
                self.consecutive_blocks = 0
                self._dispatch_telegram_alert(opp, res)
            else:
                failed += 1
                if res.get("status") == "captcha_detected":
                    self.consecutive_blocks += 1
                    logger.warning(f"⚠️ Bot challenge detected! Consecutive blocks: {self.consecutive_blocks}/3")
                    if self.consecutive_blocks >= 3:
                        self.circuit_breaker_until = time.time() + 7200.0  # 2 hours
                        logger.error("🚨 3 Consecutive blocks! Tripping Circuit Breaker for 2 hours.")
                        self._dispatch_circuit_breaker_alert()
                        break

            # Stochastic delay between journeys (12s to 35s)
            await asyncio.sleep(random.uniform(12.0, 35.0))

        summary = {
            "status": "completed",
            "sessions_run": sessions_run,
            "successful": successful,
            "failed": failed,
            "treatment_count": len(treatment_opps),
            "control_count": len(control_opps),
        }
        logger.info(f"🏁 Batch Finished: {sessions_run} sessions ({successful} OK, {failed} Failed)")
        return summary

    def _dispatch_telegram_alert(self, opp: TargetOpportunity, res: dict[str, Any]) -> None:
        """Sends rich telemetry notification to Telegram @gwelena_bot."""
        if not self.telegram_bot_token:
            return

        dwell = res.get("groundwork_dwell_seconds", 0)
        mode = res.get("behavior_mode", "normal")
        ret_tag = " [Returning 🔁]" if res.get("is_returning") else " [New 🆕]"
        pogo_info = f"\n• 🥊 Pogo Bounced: `{res.get('pogo_competitor_domain')}`" if res.get("pogo_competitor_domain") else ""

        msg = (
            f"🚀 *Ghost User Simulation Completed*{ret_tag}\n"
            f"• 🎯 Keyword: `{opp.query}`\n"
            f"• 📊 Current Rank: `#{opp.current_position:.1f}` ({opp.category})\n"
            f"• ⏱️ Dwell Time: `{dwell:.1f}s` (Mode: `{mode}`)\n"
            f"• 📱 Persona: `{res.get('persona')}` ({res.get('geo_region')})\n"
            f"• 🔗 Interactions: `{', '.join(res.get('interactions', [])[:3])}`"
            f"{pogo_info}\n"
            f"• 🛡️ Signal: `Terminal Satisfaction (NavBoost +1)`"
        )

        try:
            url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
            data = urllib.parse.urlencode({
                "chat_id": self.telegram_chat_id,
                "text": msg,
                "parse_mode": "Markdown",
            }).encode("utf-8")
            req = urllib.request.Request(url, data=data)
            with urllib.request.urlopen(req, timeout=5):
                pass
        except Exception as te:
            logger.debug(f"Telegram notification skipped: {te}")

    def _dispatch_circuit_breaker_alert(self) -> None:
        """Alerts founder on Telegram if circuit breaker trips."""
        if not self.telegram_bot_token:
            return

        msg = (
            "⚠️ *Ghost User Circuit Breaker Tripped!*\n"
            "• Cause: 3 consecutive Google CAPTCHA / bot challenge blocks.\n"
            "• Action: Simulation paused for 2 hours to protect residential proxy quota.\n"
            "• Next Steps: Sticky IP rotation and automatic cool-off."
        )
        try:
            url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
            data = urllib.parse.urlencode({
                "chat_id": self.telegram_chat_id,
                "text": msg,
                "parse_mode": "Markdown",
            }).encode("utf-8")
            req = urllib.request.Request(url, data=data)
            with urllib.request.urlopen(req, timeout=5):
                pass
        except Exception:
            pass


async def main():
    parser = argparse.ArgumentParser(description="Rank-Adaptive Behavioral Signal Controller")
    parser.add_argument("--dry-run", action="store_true", help="Run in dry-run mode without real browser execution")
    parser.add_argument("--max-sessions", type=int, default=None, help="Override daily session cap")
    parser.add_argument("--dwell", type=int, default=None, help="Override dwell time in seconds")
    args = parser.parse_args()

    controller = RankAdaptiveController(dry_run=args.dry_run)
    res = await controller.execute_batch(max_sessions=args.max_sessions, dwell_seconds=args.dwell)
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
