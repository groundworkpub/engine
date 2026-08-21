#!/usr/bin/env python3
"""
agents/podcast_syndicator.py — Groundwork Autonomous PodcastIndex & Podcasting 2.0 Syndicator

Pings PodcastIndex.org hub to re-crawl https://gworky.com/podcast/feed.xml
Uses SHA-1 authentication required by PodcastIndex API v1.
"""

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("podcast_syndicator")


def _load_env() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env.local"
    if env_path.exists():
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'").strip('"')
                if k and k not in os.environ:
                    os.environ[k] = v


_load_env()

PODCASTINDEX_API_KEY = os.environ.get("PODCASTINDEX_API_KEY", "N7GNJGNVN6YCUS62RZ7D")
PODCASTINDEX_API_SECRET = os.environ.get(
    "PODCASTINDEX_API_SECRET", "yc5eQnrS7gGWfrcCdYBn$uD5MHkgqrQAqyn$t$tg"
)
DEFAULT_FEED_URL = "https://gworky.com/podcast/feed.xml"


def get_podcastindex_headers(api_key: str = None, api_secret: str = None) -> dict[str, str]:
    k = api_key or PODCASTINDEX_API_KEY
    s = api_secret or PODCASTINDEX_API_SECRET
    epoch_time = str(int(time.time()))
    data_to_hash = (k + s + epoch_time).encode("utf-8")
    sha_1 = hashlib.sha1(data_to_hash).hexdigest()

    return {
        "User-Agent": "GroundworkPodcastEngine/1.0",
        "X-Auth-Date": epoch_time,
        "X-Auth-Key": k,
        "Authorization": sha_1,
    }


def ping_podcast_index_hub(feed_url: str = DEFAULT_FEED_URL) -> bool:
    """Sends a pubnotify ping to PodcastIndex to immediately crawl updated podcast feed."""
    url = f"https://api.podcastindex.org/api/1.0/hub/pubnotify?url={feed_url}"
    headers = get_podcastindex_headers()

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                logger.info(f"✅ Successfully notified PodcastIndex hub for {feed_url}: {data.get('description')}")
                return True
            else:
                logger.warning(f"Failed to ping PodcastIndex: HTTP {resp.status_code} - {resp.text}")
                return False
    except Exception as e:
        logger.error(f"Error pinging PodcastIndex: {e}")
        return False


def get_podcast_index_feed_info(feed_url: str = DEFAULT_FEED_URL) -> dict[str, Any] | None:
    """Fetches feed metadata and stats from PodcastIndex."""
    url = f"https://api.podcastindex.org/api/1.0/podcasts/byfeedurl?url={feed_url}"
    headers = get_podcastindex_headers()

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("feed")
            else:
                logger.warning(f"PodcastIndex returned status {resp.status_code}")
                return None
    except Exception as e:
        logger.error(f"Error fetching feed info: {e}")
        return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Groundwork PodcastIndex Syndicator")
    parser.add_argument("--feed-url", type=str, default=DEFAULT_FEED_URL, help="Podcast feed URL")
    parser.add_argument("--info", action="store_true", help="Fetch feed status and details")
    args = parser.parse_args()

    if args.info:
        feed = get_podcast_index_feed_info(args.feed_url)
        print(json.dumps(feed, indent=2))
    else:
        ping_podcast_index_hub(args.feed_url)
