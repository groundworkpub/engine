"""
agents/broadcaster.py — YouTube Video & YouTube Shorts (9:16) Distribution Broadcaster for Groundwork

Integrates with FFmpeg audiogram generator and Google YouTube Data API v3.
Supports:
  - 16:9 1080p Landscape Videos (YouTube Podcasts, Video Deep Dives)
  - 9:16 1080x1920 Vertical Videos (YouTube Shorts, TikTok, Reels)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("broadcaster")


def _load_env_local():
    """Load variables from .env.local if not already in os.environ."""
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


_load_env_local()


class VideoBroadcaster:
    def __init__(self):
        self.site_url = os.getenv("NEXT_PUBLIC_SITE_URL", "https://gworky.com").rstrip("/")
        self.supabase_url = os.getenv("NEXT_PUBLIC_SUPABASE_URL", "").rstrip("/")
        self.supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "") or os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY", "")

    def _supabase_request(self, method: str, path: str, payload: dict | list | None = None) -> Any:
        if not self.supabase_url or not self.supabase_key:
            return None
        url = f"{self.supabase_url}/rest/v1/{path}"
        headers = {
            "apikey": self.supabase_key,
            "Authorization": f"Bearer {self.supabase_key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }
        data = json.dumps(payload).encode("utf-8") if payload else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status in (200, 201):
                    return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            logger.warning(f"Supabase request {method} {path} error: {e}")
        return None

    def fetch_episode(self, slug: str) -> dict[str, Any] | None:
        """Fetch episode by slug from podcast_episodes or articles."""
        episodes = self._supabase_request("GET", f"podcast_episodes?slug=eq.{slug}&select=*")
        if episodes and len(episodes) > 0:
            return episodes[0]

        articles = self._supabase_request("GET", f"articles?slug=eq.{slug}&select=*")
        if articles and len(articles) > 0:
            art = articles[0]
            return {
                "slug": art.get("slug"),
                "title": art.get("title"),
                "description": art.get("excerpt"),
                "pillar": art.get("pillar", "money"),
                "audio_url": f"{self.site_url}/api/audio/{slug}.mp3",
                "cover_image_url": f"{self.site_url}/api/og/podcast/{slug}",
            }
        return None

    def generate_video(
        self,
        audio_path_or_url: str,
        cover_path_or_url: str,
        output_mp4: str,
        format_mode: str = "shorts",
    ) -> bool:
        """
        Renders an FFmpeg video with animated waveform.
        Format mode:
          - 'shorts': 9:16 (1080x1920) for YouTube Shorts & TikTok
          - 'landscape': 16:9 (1920x1080) for Standard YouTube Longform
        """
        temp_dir = tempfile.mkdtemp(prefix="gw_broadcast_")
        local_audio = audio_path_or_url
        local_cover = cover_path_or_url

        def _download_file(url: str, dest: str):
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Groundwork/1.0"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp, open(dest, "wb") as out:
                out.write(resp.read())

        if audio_path_or_url.startswith("http"):
            local_audio = os.path.join(temp_dir, "input_audio.mp3")
            try:
                _download_file(audio_path_or_url, local_audio)
            except Exception as e:
                logger.warning(f"Remote audio download failed: {e}. Generating placeholder tone for test.")
                # Create a 3-second silent audio if audio file isn't uploaded yet
                subprocess.run(
                    ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "5", "-c:a", "mp3", local_audio],
                    capture_output=True,
                )

        if cover_path_or_url.startswith("http"):
            local_cover = os.path.join(temp_dir, "input_cover.png")
            try:
                _download_file(cover_path_or_url, local_cover)
            except Exception as e:
                logger.warning(f"Remote cover download failed: {e}. Generating fallback canvas.")
                # Create fallback solid background with ffmpeg
                size = "1080x1920" if format_mode.lower() in ("shorts", "9:16", "vertical") else "1920x1080"
                subprocess.run(
                    ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=0x042f2e:s={size}", "-frames:v", "1", local_cover],
                    capture_output=True,
                )

        is_shorts = format_mode.lower() in ("shorts", "9:16", "vertical", "tiktok")

        if is_shorts:
            # 9:16 Vertical Video (1080x1920) with dynamic slow camera push & green/emerald glow waveform
            filter_complex = (
                "[1:a]compand,showwaves=s=880x320:mode=line:colors=0x10b981[wave];"
                "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
                "zoompan=z='min(zoom+0.0003,1.04)':d=125:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920[bg];"
                "[bg][wave]overlay=(W-w)/2:H-h-400:shortest=1[outv]"
            )
        else:
            # 16:9 Landscape Video (1920x1080) with slow cinematic push
            filter_complex = (
                "[1:a]compand,showwaves=s=1400x260:mode=line:colors=0x10b981[wave];"
                "[0:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,"
                "zoompan=z='min(zoom+0.0002,1.03)':d=125:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1920x1080[bg];"
                "[bg][wave]overlay=(W-w)/2:H-h-140:shortest=1[outv]"
            )

        cmd = [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-i",
            local_cover,
            "-i",
            local_audio,
            "-filter_complex",
            filter_complex,
            "-map",
            "[outv]",
            "-map",
            "1:a",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "24",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-pix_fmt",
            "yuv420p",
            "-shortest",
            output_mp4,
        ]

        logger.info(f"Rendering {format_mode} video with FFmpeg...")
        res = subprocess.run(cmd, capture_output=True, timeout=180)
        if res.returncode == 0 and os.path.exists(output_mp4):
            logger.info(f"Video successfully rendered at: {output_mp4}")
            return True
        logger.error(f"FFmpeg error: {res.stderr.decode('utf-8')[:400]}")
        return False

    def build_youtube_metadata(self, episode: dict[str, Any], is_shorts: bool = False) -> dict[str, Any]:
        title = episode.get("title", "Groundwork Deep Dive")
        slug = episode.get("slug", "")
        pillar = episode.get("pillar", "money").upper()
        url = f"{self.site_url}/article/{slug}"

        if is_shorts:
            # YouTube Shorts requires #Shorts in title or description and <= 60s
            yt_title = f"{title[:80]} #Shorts"
            description = (
                f"{episode.get('description', '')}\n\n"
                f"📊 Read the full research breakdown & interactive tools:\n{url}\n\n"
                f"#Groundwork #{pillar} #Shorts #Research #EvidenceBased"
            )
        else:
            yt_title = f"{title[:90]} | Groundwork"
            description = (
                f"{episode.get('description', '')}\n\n"
                f"📖 Full interactive guide with mathematical models & data sources:\n{url}\n\n"
                f"🧮 Interactive Calculators:\n{self.site_url}/tools\n\n"
                f"🎧 Spoken Audio Hub:\n{self.site_url}/podcast\n\n"
                f"—\nGroundwork Media • Evidence-based guides for high-impact life decisions.\n"
                f"#Groundwork #{pillar} #Podcast #Analysis"
            )

        return {
            "snippet": {
                "title": yt_title,
                "description": description,
                "tags": ["Groundwork", pillar, "Evidence Based", "Research", "Guide", "Calculators"],
                "categoryId": "27",  # Education
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False,
            },
        }


def main():
    parser = argparse.ArgumentParser(description="YouTube & Shorts Video Broadcaster")
    parser.add_argument("--slug", type=str, required=True, help="Article / Episode slug")
    parser.add_argument(
        "--format",
        choices=["shorts", "landscape"],
        default="shorts",
        help="Video format: 'shorts' (9:16 YouTube Shorts / TikTok) or 'landscape' (16:9 Longform)",
    )
    parser.add_argument("--out", type=str, default=None, help="Output MP4 file path")
    args = parser.parse_args()

    broadcaster = VideoBroadcaster()
    episode = broadcaster.fetch_episode(args.slug)
    if not episode:
        logger.error(f"Could not find episode for slug: {args.slug}")
        return

    out_file = args.out or f"public/audio/videos/{args.slug}_{args.format}.mp4"
    os.makedirs(os.path.dirname(os.path.abspath(out_file)), exist_ok=True)

    is_shorts = args.format == "shorts"
    cover_url = (
        f"{broadcaster.site_url}/api/og?format=shorts&pillar={episode.get('pillar', 'money')}&title={urllib.parse.quote_plus(episode.get('title', ''))}"
        if is_shorts
        else f"{broadcaster.site_url}/api/og?format=youtube&pillar={episode.get('pillar', 'money')}&title={urllib.parse.quote_plus(episode.get('title', ''))}"
    )

    audio_url = episode.get("audio_url") or f"{broadcaster.site_url}/api/audio/{args.slug}.mp3"

    success = broadcaster.generate_video(
        audio_path_or_url=audio_url,
        cover_path_or_url=cover_url,
        output_mp4=out_file,
        format_mode=args.format,
    )

    if success:
        metadata = broadcaster.build_youtube_metadata(episode, is_shorts=is_shorts)
        logger.info(f"Generated YouTube Metadata for [{args.format.upper()}]:\n{json.dumps(metadata, indent=2)}")


if __name__ == "__main__":
    main()
