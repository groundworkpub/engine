"""Groundwork Direct Google Indexing API Submitter.

Submits URLs directly to Google Indexing API (https://indexing.googleapis.com/v3/urlNotifications:publish)
using the authenticated 'gwelena@gworky.iam.gserviceaccount.com' service account.

Usage:
    python agents/google_indexer.py --urls https://gworky.com/ https://gworky.com/money
    python agents/google_indexer.py --all-tools
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import jwt
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

load_dotenv(_ROOT / ".env.local")
load_dotenv(".env.local")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("google_indexer")

INDEXING_SCOPE = "https://www.googleapis.com/auth/indexing"
INDEXING_ENDPOINT = "https://indexing.googleapis.com/v3/urlNotifications:publish"


def get_google_indexing_token() -> tuple[str, str]:
    """Generates an OAuth2 access token with indexing scope using the gwelena service account."""
    gsc_b64 = os.environ.get("GSC_SERVICE_ACCOUNT_JSON_B64")
    if not gsc_b64:
        raise ValueError("GSC_SERVICE_ACCOUNT_JSON_B64 not found in environment.")

    sa = json.loads(base64.b64decode(gsc_b64).decode("utf-8"))
    client_email = sa["client_email"]

    now = int(time.time())
    payload = {
        "iss": client_email,
        "sub": client_email,
        "aud": "https://oauth2.googleapis.com/token",
        "iat": now,
        "exp": now + 3600,
        "scope": INDEXING_SCOPE,
    }

    signed_jwt = jwt.encode(payload, sa["private_key"], algorithm="RS256")
    with httpx.Client(timeout=10.0) as client:
        resp = client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": signed_jwt,
            },
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Google OAuth2 token request failed: {resp.text}")
        data = resp.json()
        return data["access_token"], client_email


def publish_url_to_google(url: str, notification_type: str = "URL_UPDATED", token: str | None = None) -> dict[str, Any]:
    """Publishes a single URL notification directly to Google Indexing API."""
    if not token:
        token, _ = get_google_indexing_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "url": url,
        "type": notification_type,
    }

    with httpx.Client(timeout=10.0) as client:
        resp = client.post(INDEXING_ENDPOINT, json=payload, headers=headers)
        if resp.status_code == 200:
            logger.info(f"✅ [Google Indexing API] Successfully notified: {url}")
            return {"url": url, "status": "success", "response": resp.json()}
        else:
            logger.warning(f"⚠️ [Google Indexing API] Notification for {url} returned {resp.status_code}: {resp.text[:120]}")
            return {"url": url, "status": f"http_{resp.status_code}", "error": resp.text[:120]}


def load_intelligent_priority_queue(limit: int = 50) -> list[str]:
    """Dynamically builds a priority URL list for Google Indexing API."""
    site_url = os.getenv("NEXT_PUBLIC_SITE_URL", "https://gworky.com").rstrip("/")
    core_urls = [
        f"{site_url}/",
        f"{site_url}/money",
        f"{site_url}/body",
        f"{site_url}/home",
        f"{site_url}/life",
        f"{site_url}/tech",
        f"{site_url}/citations",
        f"{site_url}/press",
        f"{site_url}/tools",
        f"{site_url}/wire",
        f"{site_url}/tools/mortgage-refinance-calculator",
        f"{site_url}/tools/compound-interest-calculator",
        f"{site_url}/tools/heat-pump-roi-calculator",
        f"{site_url}/tools/solar-roi-vs-tesla-solar",
        f"{site_url}/tools/mortgage-refinance-vs-bankrate",
        f"{site_url}/tools/inflation-purchasing-power",
        f"{site_url}/tools/hysa-compound-interest",
        f"{site_url}/tools/citation-generator",
        f"{site_url}/tools/auto-loan-calculator",
        f"{site_url}/tools/rent-vs-buy-calculator",
        # 🐙 GitHub Pages (DR 96 Authority Node verified in GSC)
        "https://groundworkpub.github.io/",
        "https://groundworkpub.github.io/citations",
        "https://groundworkpub.github.io/press",
        "https://groundworkpub.github.io/tools",
    ]

    # Try loading latest articles from database or output files
    try:
        from lib.supabase import create_supabase_client
        sb = create_supabase_client()
        if sb:
            res = sb.table("articles").select("slug, pillar").eq("status", "published").order("published_at", desc=True).limit(20).execute()
            for row in (res.data or []):
                core_urls.append(f"{site_url}/{row['pillar']}/{row['slug']}")
    except Exception as exc:
        logger.debug(f"Article lookup note: {exc}")

    # Remove duplicates while preserving order
    seen = set()
    unique_urls = []
    for u in core_urls:
        if u not in seen:
            seen.add(u)
            unique_urls.append(u)

    return unique_urls[:limit]


def submit_batch_to_google(urls: list[str] | None = None) -> list[dict[str, Any]]:
    """Submits a batch of priority URLs to Google Indexing API with token caching."""
    target_urls = urls if urls is not None else load_intelligent_priority_queue()
    try:
        token, client_email = get_google_indexing_token()
        logger.info(f"Authenticated with Google Indexing API as {client_email}. Submitting {len(target_urls)} URLs...")
    except Exception as exc:
        logger.error(f"Authentication failed: {exc}")
        return []

    results = []
    for u in target_urls:
        res = publish_url_to_google(u, token=token)
        results.append(res)
        time.sleep(0.2)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Google Direct Indexing API Submitter")
    parser.add_argument("--urls", nargs="+", help="Specific URLs to submit")
    parser.add_argument("--all-tools", action="store_true", help="Submit all 20 decision calculator URLs")
    args = parser.parse_args()

    target_urls = args.urls or [
        "https://gworky.com/",
        "https://gworky.com/money",
        "https://gworky.com/tools/mortgage-refinance-calculator",
        "https://gworky.com/tools/compound-interest-calculator",
    ]
    res = submit_batch_to_google(target_urls)
    print(f"\nDone: {sum(1 for r in res if r.get('status') == 'success')}/{len(res)} URLs published to Google.")
