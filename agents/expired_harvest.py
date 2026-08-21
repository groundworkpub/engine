"""Groundwork Expired Domain Wayback Harvester (Agent 4e).

Harvests high-backlink historical routes from Archive.org CDX API,
extracts clean HTML content, strips legacy tracking/broken scripts,
and enqueues routes into Supabase `expired_routes` table.

Usage:
    python agents/expired_harvest.py --domain example-expired.com --limit 50
    python agents/expired_harvest.py --domain emailforums.biz --status-only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

# Configure logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("expired_harvest")

_ROOT = Path(__file__).resolve().parent.parent
CDX_API_URL = "https://web.archive.org/cdx/search/cdx"
WAYBACK_SNAPSHOT_BASE = "https://web.archive.org/web"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)


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
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")
    return create_client(url, key)


def clean_html_content(raw_html: str) -> str:
    """Strips tracking scripts, ads, broken tags, and extracts readable text."""
    if not raw_html:
        return ""
    # Remove script, style, noscript, iframe, svg, comments
    cleaned = re.sub(r"<(script|style|noscript|iframe|svg)[^>]*>[\s\S]*?</\1>", "", raw_html, flags=re.IGNORECASE)
    cleaned = re.sub(r"<!--[\s\S]*?-->", "", cleaned)
    # Remove excessive whitespace
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def extract_title_and_main_text(html: str) -> tuple[str, str]:
    """Extracts page title and main body text from HTML snapshot."""
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE)
    title = title_match.group(1).strip() if title_match else "Historical Archived Page"
    title = re.sub(r"<[^>]+>", "", title).strip()

    # Extract text inside main, article, or body
    body_match = re.search(r"<(article|main|body)[^>]*>([\s\S]*?)</\1>", html, re.IGNORECASE)
    body_html = body_match.group(2) if body_match else html

    # Convert basic paragraphs/headings to plain text
    text_content = re.sub(r"<[^>]+>", " ", body_html)
    text_content = re.sub(r"\s+", " ", text_content).strip()
    return title[:200], text_content[:4000]


def fetch_cdx_routes(domain: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Query Wayback CDX Server for historical 200 OK HTML pages."""
    clean_domain = domain.lower().replace("https://", "").replace("http://", "").rstrip("/")
    params = {
        "url": f"{clean_domain}/*",
        "output": "json",
        "fl": "original,timestamp,statuscode,mimetype",
        "filter": ["statuscode:200", "mimetype:text/html"],
        "collapse": "urlkey",
        "limit": str(limit * 2),  # Overfetch to filter assets
    }

    logger.info(f"Querying Wayback CDX API for [{clean_domain}]...")
    try:
        with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": "Groundwork-Harvester/1.0"}) as client:
            resp = client.get(CDX_API_URL, params=params)
            if resp.status_code != 200:
                logger.warning(f"Wayback CDX API returned status {resp.status_code}")
                return []
            rows = resp.json()
            if not rows or len(rows) <= 1:
                return []

            headers = rows[0]
            entries = [dict(zip(headers, row)) for row in rows[1:]]

            # Filter out images, feeds, non-content paths
            valid_routes = []
            seen_paths = set()

            for item in entries:
                orig_url = item.get("original", "")
                parsed = urlparse(orig_url)
                path = parsed.path or "/"

                if path in seen_paths or path == "/" or path.endswith((".jpg", ".png", ".gif", ".css", ".js", ".xml", ".pdf", ".zip")):
                    continue

                seen_paths.add(path)
                valid_routes.append({
                    "original_url": orig_url,
                    "original_path": path,
                    "timestamp": item.get("timestamp"),
                })
                if len(valid_routes) >= limit:
                    break

            logger.info(f"Discovered {len(valid_routes)} valid content routes for [{clean_domain}].")
            return valid_routes
    except Exception as exc:
        logger.error(f"Failed to query CDX API: {exc}")
        return []


def harvest_and_enqueue_domain(domain: str, limit: int = 50, dry_run: bool = False) -> Dict[str, Any]:
    """Harvests historical snapshots and enqueues into Supabase expired_routes."""
    supabase = get_supabase_client()

    # 1. Get or create domain record
    domain_res = supabase.table("expired_domains").select("id,domain").eq("domain", domain).execute()
    domain_rows = domain_res.data or []

    if not domain_rows:
        if dry_run:
            logger.info(f"[DRY-RUN] Would register new expired_domain: {domain}")
            domain_id = "00000000-0000-0000-0000-000000000000"
        else:
            ins_res = supabase.table("expired_domains").insert({
                "domain": domain,
                "status": "harvesting",
            }).execute()
            domain_id = ins_res.data[0]["id"]
    else:
        domain_id = domain_rows[0]["id"]

    # 2. Fetch CDX routes
    routes = fetch_cdx_routes(domain, limit=limit)
    if not routes:
        return {"domain": domain, "enqueued": 0, "status": "no_routes_found"}

    enqueued_count = 0
    with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": "Groundwork-Harvester/1.0"}) as client:
        for route in routes:
            orig_url = route["original_url"]
            orig_path = route["original_path"]
            ts = route["timestamp"]

            snapshot_url = f"{WAYBACK_SNAPSHOT_BASE}/{ts}id_/{orig_url}"
            title = f"Archived: {orig_path}"
            content = ""

            try:
                if not dry_run:
                    resp = client.get(snapshot_url)
                    if resp.status_code == 200:
                        raw_html = resp.text
                        clean_html = clean_html_content(raw_html)
                        title, content = extract_title_and_main_text(clean_html)
                    time.sleep(1.0)  # Respect Archive.org rate limits
            except Exception as e:
                logger.warning(f"Could not fetch snapshot for {orig_url}: {e}")

            if dry_run:
                logger.info(f"[DRY-RUN] Would enqueue route: {orig_path} | Title: {title[:40]}")
                enqueued_count += 1
            else:
                try:
                    supabase.table("expired_routes").upsert({
                        "domain_id": domain_id,
                        "original_url": orig_url,
                        "original_path": orig_path,
                        "historical_title": title,
                        "historical_content": content,
                        "strategy": "reconstruct_wp",
                        "status": "ARCHIVED",
                    }, on_conflict="domain_id,original_path").execute()
                    enqueued_count += 1
                except Exception as db_err:
                    logger.error(f"DB Upsert failed for {orig_path}: {db_err}")

    logger.info(f"Successfully enqueued {enqueued_count} routes for domain [{domain}].")
    return {"domain": domain, "enqueued": enqueued_count, "status": "completed"}


def main() -> None:
    _load_env_local()
    parser = argparse.ArgumentParser(description="Groundwork Expired Domain Wayback Harvester")
    parser.add_argument("--domain", type=str, required=True, help="Expired domain name (e.g. emailforums.biz)")
    parser.add_argument("--limit", type=int, default=30, help="Max routes to harvest")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to DB")
    parser.add_argument("--status-only", action="store_true", help="Show current DB route count")
    args = parser.parse_args()

    supabase = get_supabase_client()

    if args.status_only:
        res = supabase.table("expired_routes").select("status", count="exact").execute()
        print(f"📊 Total Expired Routes in DB: {res.count or 0}")
        return

    result = harvest_and_enqueue_domain(args.domain, limit=args.limit, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
