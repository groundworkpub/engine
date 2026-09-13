#!/usr/bin/env python3
"""agents/stock_media_harvester.py — Autonomous B-Roll & Visual Asset Harvester for Groundwork.

Downloads high-quality 1080p video clips and photography from Pexels & Pixabay APIs
with automatic tiered failover and local caching.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.request
from pathlib import Path
from typing import Any

import httpx

_ROOT = Path(__file__).resolve().parent.parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("stock_media_harvester")


def _load_env_local() -> None:
    env_file = _ROOT / ".env.local"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip("'").strip('"')
                if k not in os.environ:
                    os.environ[k] = v


_load_env_local()


class StockMediaHarvester:
    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = cache_dir or (_ROOT / "artifacts" / "broll_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.pexels_key = os.getenv("PEXELS_API_KEY", "")
        self.pixabay_key = os.getenv("PIXABAY_API_KEY", "")

    def _file_cache_key(self, url: str) -> Path:
        h = hashlib.md5(url.encode("utf-8")).hexdigest()
        ext = ".mp4" if ".mp4" in url.lower() or "video" in url.lower() else ".jpg"
        return self.cache_dir / f"{h}{ext}"

    def download_file(self, url: str, destination: Path) -> bool:
        if destination.exists() and destination.stat().st_size > 10000:
            return True
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) GroundworkHarvester/1.0"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp, open(destination, "wb") as out:
                out.write(resp.read())
            logger.info(f"Downloaded asset: {destination.name} ({destination.stat().st_size // 1024} KB)")
            return True
        except Exception as e:
            logger.warning(f"Download failed for {url}: {e}")
            if destination.exists():
                destination.unlink()
            return False

    def fetch_pexels_videos(self, query: str, limit: int = 3) -> list[str]:
        """Fetches HD landscape MP4 video download URLs from Pexels API."""
        if not self.pexels_key:
            return []
        url = "https://api.pexels.com/videos/search"
        headers = {"Authorization": self.pexels_key}
        params = {"query": query, "orientation": "landscape", "per_page": limit, "size": "medium"}
        try:
            r = httpx.get(url, headers=headers, params=params, timeout=12)
            if r.status_code == 200:
                data = r.json()
                results = []
                for v in data.get("videos", []):
                    # Find best HD 1080p / 720p mp4 file
                    video_files = v.get("video_files", [])
                    best_file = None
                    for vf in video_files:
                        if vf.get("file_type") == "video/mp4":
                            w = vf.get("width") or 0
                            if 1280 <= w <= 1920:
                                best_file = vf.get("link")
                                break
                    if not best_file and video_files:
                        best_file = video_files[0].get("link")
                    if best_file:
                        results.append(best_file)
                return results
        except Exception as e:
            logger.warning(f"Pexels video query failed for '{query}': {e}")
        return []

    def fetch_pixabay_videos(self, query: str, limit: int = 3) -> list[str]:
        """Fetches HD MP4 video download URLs from Pixabay API."""
        if not self.pixabay_key:
            return []
        url = "https://pixabay.com/api/videos/"
        params = {
            "key": self.pixabay_key,
            "q": query,
            "video_type": "film",
            "per_page": limit,
        }
        try:
            r = httpx.get(url, params=params, timeout=12)
            if r.status_code == 200:
                data = r.json()
                results = []
                for v in data.get("hits", []):
                    vids = v.get("videos", {})
                    # Prefer medium or large
                    target = vids.get("medium") or vids.get("large") or vids.get("small")
                    if target and target.get("url"):
                        results.append(target["url"])
                return results
        except Exception as e:
            logger.warning(f"Pixabay video query failed for '{query}': {e}")
        return []

    def fetch_pexels_photos(self, query: str, limit: int = 3) -> list[str]:
        """Fetches high-res landscape photos from Pexels API."""
        if not self.pexels_key:
            return []
        url = "https://api.pexels.com/v1/search"
        headers = {"Authorization": self.pexels_key}
        params = {"query": query, "orientation": "landscape", "per_page": limit}
        try:
            r = httpx.get(url, headers=headers, params=params, timeout=12)
            if r.status_code == 200:
                data = r.json()
                return [p["src"]["large2x"] for p in data.get("photos", []) if p.get("src", {}).get("large2x")]
        except Exception as e:
            logger.warning(f"Pexels photo query failed for '{query}': {e}")
        return []

    def harvest_broll_for_chapter(self, keywords: list[str], target_clips: int = 3) -> list[Path]:
        """Harvests local video/photo asset files for a single chapter with failover."""
        harvested_paths = []

        for kw in keywords:
            if len(harvested_paths) >= target_clips:
                break

            logger.info(f"Searching stock B-roll for: '{kw}'...")
            # 1. Try Pexels Video
            v_urls = self.fetch_pexels_videos(kw, limit=2)
            for vu in v_urls:
                dest = self._file_cache_key(vu)
                if self.download_file(vu, dest):
                    harvested_paths.append(dest)
                    if len(harvested_paths) >= target_clips:
                        break

            # 2. Try Pixabay Video fallback
            if len(harvested_paths) < target_clips:
                p_urls = self.fetch_pixabay_videos(kw, limit=2)
                for pu in p_urls:
                    dest = self._file_cache_key(pu)
                    if self.download_file(pu, dest):
                        harvested_paths.append(dest)
                        if len(harvested_paths) >= target_clips:
                            break

            # 3. Try High-Res Photo fallback with Ken Burns
            if len(harvested_paths) < target_clips:
                img_urls = self.fetch_pexels_photos(kw, limit=2)
                for iu in img_urls:
                    dest = self._file_cache_key(iu)
                    if self.download_file(iu, dest):
                        harvested_paths.append(dest)
                        if len(harvested_paths) >= target_clips:
                            break

        logger.info(f"Harvested {len(harvested_paths)} visual assets for chapter keywords: {keywords}")
        return harvested_paths


if __name__ == "__main__":
    harvester = StockMediaHarvester()
    clips = harvester.harvest_broll_for_chapter(["financial planning desk", "mortgage home signing"], target_clips=2)
    print(f"Total clips harvested: {len(clips)}")
    for c in clips:
        print(f"- {c} ({c.stat().st_size // 1024} KB)")
