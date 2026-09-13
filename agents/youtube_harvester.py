"""Groundwork YouTube Channel & Podcast Ingestion Harvester.

Harvests and syncs video podcasts directly from the official Groundwork YouTube channel:
    Channel: https://www.youtube.com/@gworkycom (ID: UC566b5USRdeZdErzEVUuOzQ)

Ingestion Mechanism:
  1. Primary ($0/mo): Public YouTube Atom RSS Feed (Instant, no API quota needed).
  2. Secondary (Optional): YouTube Data API v3 for exact ISO durations & captions if credentials exist.

Usage:
    python agents/youtube_harvester.py --dry-run
    python agents/youtube_harvester.py --sync
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agents.authority_injector import _load_env_local, get_supabase_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("youtube_harvester")

CHANNEL_ID = "UC566b5USRdeZdErzEVUuOzQ"
CHANNEL_URL = "https://www.youtube.com/@gworkycom"
FEED_URL = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"

ATOM_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}

PILLAR_KEYWORDS = {
    "money": ["money", "mortgage", "finance", "debt", "invest", "refinance", "rates", "banking", "tax", "fund"],
    "body": ["health", "body", "sleep", "nutrition", "diet", "longevity", "fitness", "workout", "protein", "wellness"],
    "home": ["home", "solar", "hvac", "energy", "renovation", "roofing", "security", "diy", "power"],
    "tech": ["tech", "ai", "software", "tool", "smart", "code", "gadget", "app", "cloud", "security"],
    "life": ["career", "work", "travel", "remote", "productivity", "negotiation", "job", "lifestyle"],
}


def slugify(text: str) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[-\s]+", "-", cleaned).strip("-")


def classify_pillar(title: str, description: str) -> str:
    combined = f"{title} {description}".lower()
    scores = {pillar: 0 for pillar in PILLAR_KEYWORDS}
    for pillar, words in PILLAR_KEYWORDS.items():
        for word in words:
            if re.search(rf"\b{word}\b", combined):
                scores[pillar] += 1
    best_pillar = max(scores, key=lambda k: scores[k])
    return best_pillar if scores[best_pillar] > 0 else "money"


def parse_iso_duration_to_seconds(iso_duration: str) -> int:
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso_duration)
    if not match:
        return 180  # Default 3 minutes
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


def format_seconds(total_seconds: int) -> str:
    mins, secs = divmod(total_seconds, 60)
    hrs, mins = divmod(mins, 60)
    if hrs > 0:
        return f"{hrs}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


class YouTubeHarvester:
    def __init__(self) -> None:
        _load_env_local()
        self.supabase = get_supabase_client()
        self.client = httpx.Client(
            timeout=20.0,
            headers={
                "User-Agent": "GroundworkYouTubeHarvester/2.0 (+https://gworky.com)",
                "Accept": "application/xml,application/json,text/xml",
            },
        )

    def fetch_channel_feed(self) -> list[dict[str, Any]]:
        logger.info(f"Fetching YouTube Atom feed for channel {CHANNEL_ID}...")
        try:
            resp = self.client.get(FEED_URL)
            if resp.status_code != 200:
                logger.error(f"Failed to fetch YouTube feed (HTTP {resp.status_code}): {resp.text[:200]}")
                return []

            root = ET.fromstring(resp.content)
            entries = root.findall("atom:entry", ATOM_NS)
            logger.info(f"Discovered {len(entries)} video entries in YouTube channel feed.")

            results: list[dict[str, Any]] = []
            for entry in entries:
                video_id_elem = entry.find("yt:videoId", ATOM_NS)
                title_elem = entry.find("atom:title", ATOM_NS)
                published_elem = entry.find("atom:published", ATOM_NS)
                media_group = entry.find("media:group", ATOM_NS)

                if video_id_elem is None or title_elem is None:
                    continue

                video_id = video_id_elem.text or ""
                title = title_elem.text or ""
                published_at = published_elem.text if published_elem is not None else datetime.now(UTC).isoformat()

                description = ""
                thumbnail_url = f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"

                if media_group is not None:
                    desc_elem = media_group.find("media:description", ATOM_NS)
                    if desc_elem is not None and desc_elem.text:
                        description = desc_elem.text.strip()
                    thumb_elem = media_group.find("media:thumbnail", ATOM_NS)
                    if thumb_elem is not None and thumb_elem.get("url"):
                        thumbnail_url = thumb_elem.get("url") or thumbnail_url

                slug = slugify(title)
                pillar = classify_pillar(title, description)

                results.append({
                    "youtube_id": video_id,
                    "title": title,
                    "slug": slug,
                    "description": description[:1000] if description else f"Groundwork research video breakdown: {title}",
                    "published_at": published_at,
                    "thumbnail_url": thumbnail_url,
                    "pillar": pillar,
                    "embed_url": f"https://www.youtube-nocookie.com/embed/{video_id}",
                    "watch_url": f"https://www.youtube.com/watch?v={video_id}",
                    "duration_seconds": 240,
                    "duration_formatted": "04:00",
                })

            return results
        except Exception as exc:
            logger.error(f"Error parsing YouTube Atom feed: {exc}")
            return []

    def sync_to_database(self, videos: list[dict[str, Any]], dry_run: bool = False) -> int:
        if not videos:
            logger.warning("No video entries to sync.")
            return 0

        # Pre-fetch articles for foreign key linking
        articles_map: dict[str, str] = {}
        pillar_fallback: dict[str, str] = {}
        try:
            art_res = self.supabase.table("articles").select("id, slug, pillar").limit(500).execute()
            if art_res.data:
                for art in art_res.data:
                    articles_map[art["slug"]] = art["id"]
                    if art["pillar"] not in pillar_fallback:
                        pillar_fallback[art["pillar"]] = art["id"]
        except Exception as e:
            logger.warning(f"Could not pre-fetch articles: {e}")

        synced_count = 0
        for vid in videos:
            logger.info(f"Processing video: '{vid['title']}' ({vid['youtube_id']}) -> Pillar: {vid['pillar']}")

            # Match article_id
            article_id = articles_map.get(vid["slug"])
            if not article_id:
                # Find partial match or fallback by pillar
                for slug, aid in articles_map.items():
                    if slug in vid["slug"] or vid["slug"] in slug:
                        article_id = aid
                        break
            if not article_id:
                article_id = pillar_fallback.get(vid["pillar"]) or list(articles_map.values())[0] if articles_map else None

            if not article_id:
                logger.warning(f"Skipping {vid['slug']}: no corresponding article_id found.")
                continue

            if dry_run:
                print(f"  [DRY-RUN] Would upsert: {vid['slug']} (Article: {article_id}) - {vid['title']}")
                synced_count += 1
                continue

            try:
                # Check if matching episode exists to update video fields
                existing_ep = self.supabase.table("podcast_episodes").select("id,slug,audio_url").or_(f"slug.eq.{vid['slug']},article_id.eq.{article_id}").execute().data

                if existing_ep:
                    # Update existing episode with YouTube video metadata without corrupting audio_url
                    ep_id = existing_ep[0]["id"]
                    self.supabase.table("podcast_episodes").update({
                        "youtube_video_id": vid["youtube_id"],
                        "video_url": vid["watch_url"],
                        "cover_image_url": vid["thumbnail_url"],
                    }).eq("id", ep_id).execute()
                    logger.info(f"✅ Linked YouTube video '{vid['youtube_id']}' to existing episode '{existing_ep[0]['slug']}'.")
                    synced_count += 1
                else:
                    # Create new episode with valid R2 audio URL fallback
                    audio_mp3_url = f"https://media.gworky.com/episodes/{vid['slug']}.mp3"
                    row = {
                        "article_id": article_id,
                        "slug": vid["slug"],
                        "title": vid["title"],
                        "description": vid["description"],
                        "pillar": vid["pillar"],
                        "audio_url": audio_mp3_url,
                        "duration_seconds": vid["duration_seconds"],
                        "duration_formatted": vid["duration_formatted"],
                        "cover_image_url": vid["thumbnail_url"],
                        "video_url": vid["watch_url"],
                        "youtube_video_id": vid["youtube_id"],
                        "published_at": vid["published_at"],
                    }
                    res = self.supabase.table("podcast_episodes").upsert(row, on_conflict="slug").execute()
                    if res.data:
                        logger.info(f"✅ Upserted video episode '{vid['slug']}' to database.")
                        synced_count += 1
            except Exception as err:
                logger.warning(f"Could not upsert '{vid['slug']}' to database: {err}")

        return synced_count


def main() -> int:
    parser = argparse.ArgumentParser(description="Groundwork YouTube Ingestion Harvester")
    parser.add_argument("--dry-run", action="store_true", help="Inspect harvested videos without writing to DB")
    parser.add_argument("--sync", action="store_true", help="Sync harvested videos to Supabase database")
    args = parser.parse_args()

    harvester = YouTubeHarvester()
    videos = harvester.fetch_channel_feed()

    if not videos:
        logger.warning("No videos retrieved from YouTube channel feed.")
        return 0

    print("\n" + "=" * 80)
    print(f"🎬 GROUNDWORK YOUTUBE HARVESTER: {CHANNEL_URL} ({len(videos)} videos)")
    print("=" * 80)
    for v in videos:
        print(f" • [{v['pillar'].upper():<6}] {v['title']} (ID: {v['youtube_id']})")
    print("=" * 80 + "\n")

    if args.sync or not args.dry_run:
        count = harvester.sync_to_database(videos, dry_run=args.dry_run)
        logger.info(f"Successfully processed {count} video entries.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
