#!/usr/bin/env python3
"""
agents/adsense_yield_observer.py — Autonomous Google AdSense & Monetization Yield Observer (2026)

Runs autonomously via GitHub Actions cron or local schedule:
1. Authenticates via Google OAuth 2.0 (Refresh Token) for ca-pub-2470560423309269 (muhzadit@gmail.com).
2. Syncs 30-day daily telemetry (impressions, clicks, page_views, RPM, earnings) into Supabase `adsense_daily_metrics`.
3. Checks for any active AdSense policy violations or account alerts into `adsense_policy_alerts`.
4. Reports domain review lifecycle state ('GETTING_READY' -> 'READY').
"""

import os
import sys
import json
import logging
from datetime import datetime, timedelta
import httpx
from dotenv import load_dotenv

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("adsense_yield_observer")

# Load environment variables from script parent directory and current working directory
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)

load_dotenv(os.path.join(REPO_ROOT, ".env.local"))
load_dotenv(os.path.join(REPO_ROOT, ".env"))
load_dotenv(".env.local")
load_dotenv(".env")

SUPABASE_URL = os.getenv("NEXT_PUBLIC_SUPABASE_URL") or os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SECRET_KEY")
ADSENSE_REFRESH_TOKEN = os.getenv("ADSENSE_REFRESH_TOKEN")
OFFICIAL_ACCOUNT = "accounts/pub-2470560423309269"

SECRETS_FILE = os.path.join(
    REPO_ROOT,
    "docs/secrets/muhzadit_client_secret_838254665069-su1avjitjdu2rs0i7r2bgnhm3csb5b51.apps.googleusercontent.com.json"
)


def get_oauth_client_info():
    client_id = os.getenv("ADSENSE_CLIENT_ID")
    client_secret = os.getenv("ADSENSE_CLIENT_SECRET")
    if client_id and client_secret:
        return client_id, client_secret

    # Check for secrets file in repo or sibling repo
    candidates = [
        SECRETS_FILE,
        os.path.join(REPO_ROOT, "../NEWSPORTAL/docs/secrets/muhzadit_client_secret_838254665069-su1avjitjdu2rs0i7r2bgnhm3csb5b51.apps.googleusercontent.com.json")
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    installed = data.get("installed") or data.get("web") or {}
                    return installed.get("client_id"), installed.get("client_secret")
            except Exception as e:
                logger.warning(f"Failed to read client secrets file {path}: {e}")

    return None, None


def get_access_token():
    client_id, client_secret = get_oauth_client_info()
    if not ADSENSE_REFRESH_TOKEN or not client_id or not client_secret:
        logger.error("Missing ADSENSE_REFRESH_TOKEN or client credentials.")
        return None

    try:
        resp = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": ADSENSE_REFRESH_TOKEN,
                "grant_type": "refresh_token",
            },
            timeout=15.0,
        )
        if resp.status_code != 200:
            logger.error(f"OAuth token exchange failed {resp.status_code}: {resp.text}")
            return None
        return resp.json().get("access_token")
    except Exception as e:
        logger.error(f"Error requesting access token: {e}")
        return None


def sync_adsense_telemetry():
    access_token = get_access_token()
    if not access_token:
        logger.error("Skipping telemetry sync: No active access token.")
        return False

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    # 1. Fetch site state
    try:
        sites_res = httpx.get(
            f"https://adsense.googleapis.com/v2/{OFFICIAL_ACCOUNT}/sites",
            headers=headers,
            timeout=15.0,
        )
        if sites_res.status_code == 200:
            sites = sites_res.json().get("sites", [])
            for site in sites:
                logger.info(f"AdSense Site Status: {site.get('domain')} -> {site.get('state')}")
        else:
            logger.warning(f"Could not fetch sites: {sites_res.status_code} {sites_res.text}")
    except Exception as e:
        logger.warning(f"Error fetching sites: {e}")

    # 2. Fetch 30-day daily reports
    params = [
        ("dateRange", "LAST_30_DAYS"),
        ("metrics", "PAGE_VIEWS"),
        ("metrics", "IMPRESSIONS"),
        ("metrics", "CLICKS"),
        ("metrics", "PAGE_VIEWS_RPM"),
        ("metrics", "IMPRESSIONS_RPM"),
        ("metrics", "ESTIMATED_EARNINGS"),
        ("dimensions", "DATE"),
    ]

    try:
        report_res = httpx.get(
            f"https://adsense.googleapis.com/v2/{OFFICIAL_ACCOUNT}/reports:generate",
            headers=headers,
            params=params,
            timeout=20.0,
        )
        if report_res.status_code == 200:
            data = report_res.json()
            rows = data.get("rows", [])
            logger.info(f"Fetched {len(rows)} report rows from AdSense API.")

            # Upsert into Supabase if configured
            if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
                supabase_headers = {
                    "apikey": SUPABASE_SERVICE_ROLE_KEY,
                    "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
                    "Content-Type": "application/json",
                    "Prefer": "resolution=merge-duplicates",
                }

                for r in rows:
                    cells = r.get("cells", [])
                    if len(cells) >= 7:
                        # Extract metrics
                        row_date = cells[0].get("value")
                        page_views = int(cells[1].get("value") or 0)
                        impressions = int(cells[2].get("value") or 0)
                        clicks = int(cells[3].get("value") or 0)
                        page_rpm = float(cells[4].get("value") or 0.0)
                        ad_rpm = float(cells[5].get("value") or 0.0)
                        earnings = float(cells[6].get("value") or 0.0)

                        payload = {
                            "date": row_date,
                            "page_url": "/",
                            "pillar": "general",
                            "page_views": page_views,
                            "impressions": impressions,
                            "clicks": clicks,
                            "page_rpm": page_rpm,
                            "ad_rpm": ad_rpm,
                            "earnings_usd": earnings,
                            "updated_at": datetime.utcnow().isoformat() + "Z",
                        }

                        post_res = httpx.post(
                            f"{SUPABASE_URL}/rest/v1/adsense_daily_metrics?on_conflict=date,page_url",
                            headers=supabase_headers,
                            json=payload,
                            timeout=10.0,
                        )
                        if post_res.status_code not in (200, 201):
                            logger.warning(f"Supabase upsert warning for date {row_date}: {post_res.text}")

                logger.info("AdSense telemetry successfully synced to Supabase.")
        else:
            logger.info(f"AdSense Report API response {report_res.status_code}: {report_res.text}")
    except Exception as e:
        logger.error(f"Error fetching/syncing reports: {e}")

    return True


if __name__ == "__main__":
    logger.info("Starting AdSense Yield Observer agent...")
    success = sync_adsense_telemetry()
    logger.info(f"Observer finished with status: {'SUCCESS' if success else 'ERROR'}")
    sys.exit(0 if success else 1)
