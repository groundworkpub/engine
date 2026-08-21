"""Groundwork Multi-Engine Fast Indexer Dispatcher.

Dispatches instant crawl & indexing requests for newly published satellite posts,
guest studies, and 301 edge redirects to:
1. IndexNow API (Bing, Yandex, Seznam, Naver)
2. Google Indexing API (OAuth2 Service Account Batch)

Usage:
    python agents/indexer_dispatcher.py --limit 30
    python agents/indexer_dispatcher.py --url https://emailforums.biz/example/ --dry-run
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

_ROOT = Path(__file__).resolve().parent.parent

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("indexer_dispatcher")

TIMEOUT = httpx.Timeout(30.0, connect=10.0)
INDEXNOW_ENDPOINT = "https://api.indexnow.org/indexnow"


def _load_env_local() -> None:
    env_file = _ROOT / ".env.local"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip("'").strip('"')
            if k not in os.environ:
                os.environ[k] = v


def get_supabase_client() -> Any:
    _load_env_local()
    from supabase import create_client

    url = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY required")
    return create_client(url, key)


def submit_indexnow(urls: List[str], key: str, host: str = "emailforums.biz") -> bool:
    """Submit batch URLs to the IndexNow protocol API."""
    if not urls or not key:
        return False

    payload = {
        "host": host,
        "key": key,
        "keyLocation": f"https://{host}/{key}.txt",
        "urlList": urls,
    }

    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.post(INDEXNOW_ENDPOINT, json=payload)
            if resp.status_code in (200, 202):
                logger.info(f"🚀 IndexNow submitted {len(urls)} URLs for [{host}]. Status: {resp.status_code}")
                return True
            logger.warning(f"IndexNow responded with status {resp.status_code}: {resp.text[:200]}")
            return False
    except Exception as exc:
        logger.error(f"IndexNow submission failed: {exc}")
        return False


def get_google_access_token(service_account_json_b64: str) -> Optional[str]:
    """Retrieves Google OAuth2 Bearer Token using Service Account credentials."""
    try:
        import time
        import jwt  # pyjwt
        raw_json = base64.b64decode(service_account_json_b64).decode("utf-8")
        creds = json.loads(raw_json)

        now = int(time.time())
        claims = {
            "iss": creds["client_email"],
            "scope": "https://www.googleapis.com/auth/indexing",
            "aud": "https://oauth2.googleapis.com/token",
            "iat": now,
            "exp": now + 3600,
        }

        signed_jwt = jwt.encode(claims, creds["private_key"], algorithm="RS256")

        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": signed_jwt,
                },
            )
            if resp.status_code == 200:
                return resp.json().get("access_token")
            logger.warning(f"Google OAuth token request failed: {resp.status_code} {resp.text}")
            return None
    except Exception as exc:
        logger.warning(f"Google Auth Token resolution notice: {exc}")
        return None


def submit_google_indexing(url: str, access_token: str) -> bool:
    """Submit a single URL notification to Google Indexing API."""
    endpoint = "https://indexing.googleapis.com/v3/urlNotifications:publish"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "url": url,
        "type": "URL_UPDATED",
    }

    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.post(endpoint, json=payload, headers=headers)
            if resp.status_code == 200:
                logger.info(f"✅ Google Indexing API accepted: {url}")
                return True
            logger.warning(f"Google Indexing API status {resp.status_code} for {url}: {resp.text[:150]}")
            return False
    except Exception as exc:
        logger.error(f"Google Indexing error for {url}: {exc}")
        return False


def dispatch_pending_indexes(limit: int = 30, dry_run: bool = False) -> Dict[str, Any]:
    """Polls routes and guest studies requiring index notifications."""
    supabase = get_supabase_client()
    indexnow_key = os.getenv("INDEXNOW_KEY", "381df70d54a94794abf07c14c4584a2a")
    gsc_b64 = os.getenv("GSC_SERVICE_ACCOUNT_JSON_B64", "")

    # 1. Fetch routes in WP_PUBLISHED or EDGE_ROUTED status
    res = supabase.table("expired_routes").select("id,original_url,target_gworky_url").in_("status", ["WP_PUBLISHED", "EDGE_ROUTED"]).limit(limit).execute()
    routes = res.data or []

    # 2. Fetch guest submissions published but not indexed
    res_guest = supabase.table("guest_submissions").select("id,wp_post_url").eq("moderation_status", "published").limit(limit).execute()
    guests = res_guest.data or []

    urls_to_index = []
    route_ids = []
    for r in routes:
        if r.get("original_url"):
            urls_to_index.append(r["original_url"])
            route_ids.append(r["id"])

    for g in guests:
        if g.get("wp_post_url") and g["wp_post_url"] not in urls_to_index:
            urls_to_index.append(g["wp_post_url"])

    logger.info(f"Found {len(urls_to_index)} pending URLs to submit for indexing.")

    if not urls_to_index:
        return {"submitted": 0, "status": "no_pending_urls"}

    if dry_run:
        for u in urls_to_index:
            logger.info(f"[DRY-RUN] Would submit for indexing: {u}")
        return {"submitted": len(urls_to_index), "status": "dry_run"}

    # Dispatch IndexNow
    submit_indexnow(urls_to_index, key=indexnow_key, host="emailforums.biz")

    # Dispatch Google Indexing if token available
    google_token = get_google_access_token(gsc_b64) if gsc_b64 else None
    if google_token:
        for u in urls_to_index[:20]:  # Limit batch
            submit_google_indexing(u, google_token)

    # Update status in DB
    now_iso = datetime.now(timezone.utc).isoformat()
    if route_ids:
        supabase.table("expired_routes").update({
            "status": "INDEX_SUBMITTED",
            "indexed_at": now_iso,
        }).in_("id", route_ids).execute()

    return {"submitted": len(urls_to_index), "status": "completed"}


def main() -> None:
    _load_env_local()
    parser = argparse.ArgumentParser(description="Groundwork Indexing Dispatcher")
    parser.add_argument("--limit", type=int, default=30, help="Max URLs to process")
    parser.add_argument("--url", type=str, help="Submit a single specific URL")
    parser.add_argument("--dry-run", action="store_true", help="Preview submissions")
    args = parser.parse_args()

    if args.url:
        indexnow_key = os.getenv("INDEXNOW_KEY", "381df70d54a94794abf07c14c4584a2a")
        submit_indexnow([args.url], key=indexnow_key)
        print(f"Submitted single URL: {args.url}")
    else:
        res = dispatch_pending_indexes(limit=args.limit, dry_run=args.dry_run)
        print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
