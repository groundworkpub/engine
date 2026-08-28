#!/usr/bin/env python3
"""IndexNow pinger — submit fresh article URLs so crawlers skip re-discovery."""
from __future__ import annotations
import logging, os, httpx

logger = logging.getLogger("indexnow")
KEY = os.environ.get("INDEXNOW_KEY", "")
ENDPOINT = "https://api.indexnow.org/indexnow"

def ping_new(supabase, base_url: str = "https://gworky.com", limit: int = 10) -> int:
    if not KEY:
        logger.warning("INDEXNOW_KEY kosong — skip")
        return 0
    rows = (supabase.table("articles").select("slug").eq("status", "published")
            .order("published_at", desc=True).limit(limit).execute().data or [])
    urls = [f"{base_url}/article/{r['slug']}" for r in rows]
    if not urls:
        return 0
    try:
        resp = httpx.post(ENDPOINT, json={
            "host": "gworky.com", "key": KEY, "keyLocation": f"{base_url}/{KEY}.txt",
            "urlList": urls}, timeout=15.0,
            headers={"Content-Type": "application/json; charset=utf-8"})
        logger.info("IndexNow %s urls -> HTTP %s", len(urls), resp.status_code)
        return len(urls) if resp.status_code in (200, 202) else 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("IndexNow fail: %s", exc)
        return 0
