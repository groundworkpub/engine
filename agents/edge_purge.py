"""agents/edge_purge.py.

Cloudflare Edge Cache Purging Engine for Groundwork (Rule §2.9).
Purges specific URLs on Cloudflare Zone 67e2be0fcddb82637428c64471050fd8
whenever an article is published, updated, or unpublished.
"""

import json
import logging
import os
import sys
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env.local")

logger = logging.getLogger("edge_purge")

CF_ZONE_ID = "67e2be0fcddb82637428c64471050fd8"  # gworky.com Zone SSOT
CF_PURGE_ENDPOINT = f"https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/purge_cache"


def get_auth_headers() -> dict[str, str]:
    """Resolves authoritative Cloudflare authentication headers (Global Key or Bearer Token)."""
    email = os.getenv("CLOUDFLARE_EMAIL")
    global_key = os.getenv("CLOUDFLARE_GLOBAL_API_KEY")
    if email and global_key:
        return {
            "X-Auth-Email": email.strip(),
            "X-Auth-Key": global_key.strip(),
            "Content-Type": "application/json",
        }

    token = (
        os.getenv("CF_TOK_ZONE_ADMIN")
        or os.getenv("CLOUDFLARE_API_TOKEN")
        or os.getenv("CF_AGENTS_TOKEN")
    )
    if token:
        return {
            "Authorization": f"Bearer {token.strip()}",
            "Content-Type": "application/json",
        }

    raise ValueError("Missing Cloudflare credentials (CLOUDFLARE_GLOBAL_API_KEY + EMAIL, or CF token)")


def purge_urls(urls: list[str]) -> bool:
    """Dispatches targeted cache purge for explicit URLs on Cloudflare Edge CDN.

    Args:
        urls: List of full URLs to purge (e.g. ['https://gworky.com/llms.txt'])

    Returns:
        True if Cloudflare confirmed purge success, False otherwise.
    """
    if not urls:
        return True

    # Normalize URLs
    clean_urls = list({u.strip() for u in urls if u and u.startswith("http")})
    if not clean_urls:
        return True

    try:
        headers = get_auth_headers()
    except Exception as e:
        logger.error(f"Cannot purge cache: {e}")
        return False

    payload = {"files": clean_urls}

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(CF_PURGE_ENDPOINT, headers=headers, json=payload)
            data = resp.json()
            if resp.status_code == 200 and data.get("success"):
                logger.info(f"✅ Successfully purged {len(clean_urls)} URLs from Cloudflare Edge cache: {clean_urls}")
                return True
            else:
                errors = data.get("errors", [])
                logger.error(f"❌ Cloudflare cache purge failed (HTTP {resp.status_code}): {errors}")
                return False
    except Exception as exc:
        logger.error(f"❌ Network error while purging Cloudflare cache: {exc}")
        return False


def purge_article_cache(slug: str, pillar: str = "") -> bool:
    """Convenience helper to purge all potential route permutations for an article."""
    base = "https://gworky.com"
    urls_to_purge = [
        f"{base}/article/{slug}",
        f"{base}/llms.txt",
    ]
    if pillar:
        urls_to_purge.append(f"{base}/{pillar}/{slug}")
    return purge_urls(urls_to_purge)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    target_urls = sys.argv[1:] if len(sys.argv) > 1 else [
        "https://gworky.com/llms.txt",
        "https://gworky.com/article/high-impact-new-york-city-itinerary-strategy",
        "https://gworky.com/life/high-impact-new-york-city-itinerary-strategy",
    ]
    print(f"Purging {len(target_urls)} target URLs from Cloudflare Edge...")
    success = purge_urls(target_urls)
    print(f"Result: {'SUCCESS' if success else 'FAILED'}")
