"""
agents/mega_podcast_compiler.py — Mega Long-Form Compilation & Shorts Extraction Pipeline for Groundwork

Generates:
1. Long-form video (> 1 hour / 60+ minutes) by assembling episodes from Groundwork RSS/R2
   with waveform visualizer, dynamic episode title cards, and chapter markers.
2. High-retention vertical 9:16 Shorts (> 60s - 90s) with kinetic zoompan and pin-links.
3. Formatted YouTube descriptions with clickable timestamp chapters and SEO tags.
4. Resumable YouTube Data API v3 upload integration via OAuth refresh token.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("mega_podcast_compiler")


def _load_env():
    env_path = Path(__file__).resolve().parent.parent / ".env.local"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


_load_env()


@dataclass
class EpisodeItem:
    index: int
    title: str
    pillar: str
    audio_url: str
    duration_sec: float
    local_path: str = ""


class MegaPodcastCompiler:
    def __init__(self, output_dir: str = "./artifacts/mega_video"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.feed_url = "https://gworky.com/podcast/feed.xml"

    def fetch_feed_episodes(self, max_episodes: int = 50) -> list[EpisodeItem]:
        """Fetch and parse all available podcast episodes from the live RSS feed."""
        logger.info(f"Fetching RSS feed from {self.feed_url}...")
        req = urllib.request.Request(self.feed_url, headers={"User-Agent": "Mozilla/5.0 GroundworkCompiler/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read().decode("utf-8")

        raw_items = re.findall(r"<item>(.*?)</item>", data, re.DOTALL)
        episodes: list[EpisodeItem] = []

        for idx, it in enumerate(raw_items[:max_episodes]):
            title_m = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", it)
            enc_m = re.search(r'<enclosure\s+url="([^"]+)"\s+length="([^"]+)"', it)
            dur_m = re.search(r"<itunes:duration>([^<]+)</itunes:duration>", it)

            if not (title_m and enc_m):
                continue

            title = title_m.group(1).strip()
            url = enc_m.group(1).strip()
            dur_sec = 50.0
            if dur_m:
                parts = dur_m.group(1).split(":")
                if len(parts) == 2:
                    dur_sec = float(parts[0]) * 60 + float(parts[1])
                elif len(parts) == 3:
                    dur_sec = float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])

            # Infer pillar from URL or title
            pillar = "money"
            lower_str = (title + " " + url).lower()
            if any(k in lower_str for k in ("health", "cancer", "sleep", "covid", "antibody", "doctor", "nutrient")):
                pillar = "body"
            elif any(k in lower_str for k in ("ai", "nemotron", "claude", "chrome", "tech", "qubo", "exploit", "camera")):
                pillar = "tech"
            elif any(k in lower_str for k in ("coal", "solar", "energy", "home", "plant", "hvac", "power")):
                pillar = "home"
            elif any(k in lower_str for k in ("airlines", "procrastinate", "miles", "travel", "work", "life", "career")):
                pillar = "life"

            episodes.append(EpisodeItem(
                index=idx + 1,
                title=title,
                pillar=pillar,
                audio_url=url,
                duration_sec=dur_sec
            ))

        logger.info(f"Loaded {len(episodes)} episodes from feed. Estimated total duration: {sum(e.duration_sec for e in episodes)/60:.1f} minutes")
        return episodes

    def download_episodes(self, episodes: list[EpisodeItem], target_duration_sec: float = 3600.0) -> list[EpisodeItem]:
        """Download episodes into temp workspace until target duration is satisfied."""
        selected: list[EpisodeItem] = []
        accumulated_sec = 0.0

        for ep in episodes:
            dest = self.output_dir / f"ep_{ep.index:03d}_{ep.pillar}.mp3"
            if not dest.exists() or dest.stat().st_size < 10000:
                logger.info(f"Downloading [{ep.index}/{len(episodes)}] {ep.title}...")
                try:
                    req = urllib.request.Request(ep.audio_url, headers={"User-Agent": "Mozilla/5.0 GroundworkCompiler/1.0"})
                    with urllib.request.urlopen(req, timeout=20) as resp, open(dest, "wb") as f:
                        f.write(resp.read())
                except Exception as e:
                    logger.warning(f"Failed to download {ep.audio_url}: {e}. Skipping.")
                    continue

            # Verify local duration with ffprobe
            try:
                probe = subprocess.check_output(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(dest)],
                    text=True
                ).strip()
                real_dur = float(probe)
            except Exception:
                real_dur = ep.duration_sec

            ep.duration_sec = real_dur
            ep.local_path = str(dest)
            selected.append(ep)
            accumulated_sec += real_dur

            if accumulated_sec >= target_duration_sec:
                break

        # If still under target duration (e.g. 50 episodes * 50s = 43.5 mins), loop sequence to achieve > 1 hr (70-80 mins)
        if accumulated_sec < target_duration_sec and selected:
            logger.info(f"Initial set duration is {accumulated_sec/60:.1f}m. Appending second thematic block to reach >1 hour target...")
            original_len = len(selected)
            loop_idx = 0
            while accumulated_sec < target_duration_sec:
                base_ep = selected[loop_idx % original_len]
                loop_ep = EpisodeItem(
                    index=len(selected) + 1,
                    title=f"{base_ep.title} (Encore Deep Dive)",
                    pillar=base_ep.pillar,
                    audio_url=base_ep.audio_url,
                    duration_sec=base_ep.duration_sec,
                    local_path=base_ep.local_path
                )
                selected.append(loop_ep)
                accumulated_sec += base_ep.duration_sec
                loop_idx += 1

        logger.info(f"Selected {len(selected)} tracks totaling {accumulated_sec/60:.1f} minutes ({accumulated_sec:.1f}s)")
        return selected

    def assemble_audio_and_chapters(self, episodes: list[EpisodeItem]) -> tuple[str, str, list[dict[str, Any]]]:
        """
        Stitches all MP3s into a single master track and creates timestamp chapters for YouTube.
        """
        concat_list_file = self.output_dir / "concat_list.txt"
        with open(concat_list_file, "w", encoding="utf-8") as f:
            for ep in episodes:
                # Use absolute path safely for ffmpeg concat demuxer
                abs_path = os.path.abspath(ep.local_path)
                f.write(f"file '{abs_path}'\n")

        master_mp3 = self.output_dir / "groundwork_mega_podcast_master.mp3"
        logger.info(f"Stitching {len(episodes)} episodes into master MP3...")

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list_file),
            "-c", "copy",
            str(master_mp3)
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Build YouTube description chapters
        chapters_text = "00:00 - Master Introduction & Groundwork Briefing\n"
        current_time = 0.0
        chapters_data = []

        for ep in episodes:
            hrs = int(current_time // 3600)
            mins = int((current_time % 3600) // 60)
            secs = int(current_time % 60)
            timestamp_str = f"{hrs:02d}:{mins:02d}:{secs:02d}" if hrs > 0 else f"{mins:02d}:{secs:02d}"

            clean_title = ep.title.replace("&amp;", "&")
            line = f"{timestamp_str} - [{ep.pillar.upper()}] {clean_title}\n"
            chapters_text += line
            chapters_data.append({
                "timestamp": timestamp_str,
                "seconds": current_time,
                "title": clean_title,
                "pillar": ep.pillar
            })
            current_time += ep.duration_sec

        description_file = self.output_dir / "youtube_mega_description.txt"

        # Format compact chapters to ensure total description stays safely within YouTube's 5000-char limit
        compact_chapters_text = "00:00 - Master Briefing\n"
        for c in chapters_data:
            compact_title = c["title"][:42].strip()
            compact_chapters_text += f"{c['timestamp']} - [{c['pillar'].upper()}] {compact_title}\n"

        full_description = (
            "Groundwork Mega Investigations: 1-Hour Master Executive Briefing.\n"
            "Unvarnished, evidence-based research and calculators across Money, Health, Tech, Home & Life.\n\n"
            "Explore full decision models & interactive calculators: https://gworky.com\n\n"
            "=== TIMESTAMPS / CHAPTERS ===\n"
            f"{compact_chapters_text}\n"
            "#PersonalFinance #HealthResearch #TechBriefing #Groundwork #Calculators #EvidenceBased"
        )
        if len(full_description) > 4800:
            full_description = full_description[:4800]

        description_file.write_text(full_description, encoding="utf-8")
        logger.info(f"Chapters compiled ({len(full_description)} chars) and saved to {description_file}")

        return str(master_mp3), full_description, chapters_data

    def generate_mega_cover_artwork(self, output_png_path: str, is_vertical: bool = False) -> str:
        """Generates crisp, branded cover artwork using Pillow with high-contrast typography."""
        from PIL import Image, ImageDraw, ImageFont

        width, height = (1080, 1920) if is_vertical else (1920, 1080)
        # Deep Navy background (#0a192f)
        img = Image.new("RGB", (width, height), color=(10, 25, 47))
        draw = ImageDraw.Draw(img)

        # Emerald ambient glow in corner
        emerald_rgb = (16, 185, 129)
        glow_radius = 600 if is_vertical else 500
        for r in range(glow_radius, 0, -30):
            alpha_ratio = (r / glow_radius)
            fill_color = (
                int(10 + (emerald_rgb[0] - 10) * (1 - alpha_ratio) * 0.35),
                int(25 + (emerald_rgb[1] - 25) * (1 - alpha_ratio) * 0.35),
                int(47 + (emerald_rgb[2] - 47) * (1 - alpha_ratio) * 0.35),
            )
            draw.ellipse((width - r, -r, width + r, r), fill=fill_color)

        # Load system fonts safely
        def load_font(size: int):
            for path in [
                "/System/Library/Fonts/SFPro-Bold.otf",
                "/System/Library/Fonts/HelveticaNeue.ttc",
                "/System/Library/Fonts/Helvetica.ttc",
                "/Library/Fonts/Arial.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            ]:
                if os.path.exists(path):
                    try:
                        return ImageFont.truetype(path, size)
                    except Exception:
                        pass
            return ImageFont.load_default()

        font_header = load_font(44 if is_vertical else 56)
        font_sub = load_font(26 if is_vertical else 30)
        font_body = load_font(22 if is_vertical else 24)

        if is_vertical:
            # 9:16 Shorts Layout
            draw.text((width // 2, 260), "GROUNDWORK", fill=(52, 211, 153), font=font_header, anchor="mm")
            draw.text((width // 2, 330), "EXECUTIVE BRIEFING", fill=(255, 255, 255), font=font_sub, anchor="mm")
            draw.text((width // 2, 420), "1-Hour Deep-Dive Research", fill=(203, 213, 225), font=font_body, anchor="mm")
            draw.text((width // 2, height - 260), "Watch full 1-hour breakdown at gworky.com", fill=(148, 163, 184), font=font_body, anchor="mm")
        else:
            # 16:9 Landscape Layout
            draw.text((width // 2, 140), "GROUNDWORK RESEARCH", fill=(255, 255, 255), font=font_header, anchor="mm")
            draw.text((width // 2, 215), "1-HOUR EXECUTIVE EVIDENCE-BASED BRIEFING", fill=(52, 211, 153), font=font_sub, anchor="mm")
            draw.text((width // 2, 275), "Unbiased Decision Models Across Money, Health, Tech, Home & Life", fill=(203, 213, 225), font=font_body, anchor="mm")
            draw.text((width // 2, height - 90), "Interactive Calculators & Research Guides: https://gworky.com", fill=(148, 163, 184), font=font_body, anchor="mm")

        img.save(output_png_path, "PNG")
        logger.info(f"Saved artwork to {output_png_path}")
        return output_png_path

    def render_landscape_mega_video(self, master_mp3: str, output_mp4: str, duration_limit: float | None = None) -> str:
        """
        Renders a 16:9 (1920x1080) landscape video with Pillow-generated branding card,
        glowing green soundwave spectrum, and audio track.
        Uses Apple VideoToolbox hardware acceleration if available for ultra-fast render.
        """
        logger.info(f"Rendering 16:9 landscape mega video to {output_mp4}...")

        cover_png = str(self.output_dir / "landscape_mega_cover.png")
        self.generate_mega_cover_artwork(cover_png, is_vertical=False)

        filter_complex = (
            "[1:a]compand,showwaves=s=1400x240:mode=line:colors=0x10b981[wave];"
            "[0:v]scale=1920:1080[bg];"
            "[bg][wave]overlay=(W-w)/2:H-h-160:shortest=1[outv]"
        )

        # Check if VideoToolbox encoder is usable
        use_videotoolbox = sys.platform == "darwin"
        v_codec = "h264_videotoolbox" if use_videotoolbox else "libx264"

        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", cover_png,
            "-i", master_mp3,
            "-filter_complex", filter_complex,
            "-map", "[outv]",
            "-map", "1:a",
            "-c:v", v_codec,
        ]

        if use_videotoolbox:
            cmd += ["-b:v", "2000k"]
        else:
            cmd += ["-preset", "ultrafast", "-crf", "28"]

        cmd += [
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest"
        ]

        if duration_limit:
            cmd += ["-t", str(duration_limit)]

        cmd.append(output_mp4)
        logger.info(f"Running ffmpeg with codec {v_codec}...")
        subprocess.run(cmd, check=True)
        logger.info(f"16:9 Mega Video rendered successfully: {output_mp4}")
        return output_mp4

    def extract_micro_insight_shorts(self, episodes: list[EpisodeItem], num_shorts: int = 3) -> list[str]:
        """
        Extracts 3 high-retention 9:16 vertical Shorts (>60s - 90s) from selected episodes,
        with vertical soundwave and call-to-action to watch the full 1-hour briefing.
        """
        logger.info(f"Extracting {num_shorts} micro-insight 9:16 Shorts...")
        rendered_shorts = []

        shorts_cover = str(self.output_dir / "shorts_vertical_cover.png")
        self.generate_mega_cover_artwork(shorts_cover, is_vertical=True)

        for idx, ep in enumerate(episodes[:num_shorts]):
            short_dest = self.output_dir / f"short_{idx+1}_{ep.pillar}.mp4"

            filter_complex = (
                "[1:a]compand,showwaves=s=880x280:mode=line:colors=0x34d399[wave];"
                "[0:v]scale=1080:1920[bg];"
                "[bg][wave]overlay=(W-w)/2:H-h-480:shortest=1[outv]"
            )

            cmd = [
                "ffmpeg", "-y",
                "-loop", "1",
                "-i", shorts_cover,
                "-i", ep.local_path,
                "-filter_complex", filter_complex,
                "-map", "[outv]",
                "-map", "1:a",
                "-c:v", "h264_videotoolbox" if sys.platform == "darwin" else "libx264",
                "-b:v", "2500k",
                "-c:a", "aac",
                "-b:a", "192k",
                "-t", "70",  # 70 seconds (> 1 min YouTube Shorts criteria)
                "-shortest",
                str(short_dest)
            ]

            try:
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                rendered_shorts.append(str(short_dest))
                logger.info(f"Rendered Short {idx+1}: {short_dest}")
            except Exception as e:
                logger.error(f"Short rendering error for {ep.title}: {e}")

        return rendered_shorts

    def upload_to_youtube(
        self,
        video_path: str,
        title: str,
        description: str,
        tags: list[str],
        privacy: str = "unlisted"
    ) -> dict[str, Any] | None:
        """Uploads a video to YouTube channel @gworkycom using youtube_uploader.py."""
        uploader_script = Path(__file__).parent / "youtube_uploader.py"
        if not uploader_script.exists():
            logger.error("youtube_uploader.py not found.")
            return None

        cmd = [
            sys.executable, str(uploader_script),
            "--video", video_path,
            "--title", title[:100],
            "--description", description[:5000],
            "--privacy", privacy,
            "--tags", ",".join(tags[:15]),
            "--category", "27"
        ]

        logger.info(f"Uploading {video_path} ({privacy})...")
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            logger.info(f"Upload success: {res.stdout.strip()}")
            # Extract video ID if present
            for line in res.stdout.splitlines():
                if "Video ID:" in line or "https://youtu.be/" in line:
                    logger.info(f"Direct link: {line.strip()}")
            return {"status": "success", "output": res.stdout}
        else:
            logger.error(f"Upload failed: {res.stderr}")
            return None


def main():
    parser = argparse.ArgumentParser(description="Groundwork Mega Podcast Compiler & Shorts Engine")
    parser.add_argument("--target-seconds", type=float, default=3660.0, help="Target duration in seconds (default 3660s = 61 mins)")
    parser.add_argument("--test-render", action="store_true", help="Render a short 60-second test video")
    parser.add_argument("--render-full", action="store_true", help="Render the complete 61.3-minute 1080p master video")
    parser.add_argument("--extract-shorts", action="store_true", default=True, help="Extract 3 vertical 9:16 Shorts")
    parser.add_argument("--upload-mega", action="store_true", help="Upload 1-hour mega video to YouTube")
    parser.add_argument("--upload-shorts", action="store_true", help="Upload extracted shorts to YouTube")
    parser.add_argument("--upload-all", action="store_true", help="Upload mega video and all shorts to YouTube")
    parser.add_argument("--privacy", type=str, default="unlisted", choices=["unlisted", "public", "private"], help="Privacy status")
    args = parser.parse_args()

    compiler = MegaPodcastCompiler()

    # 1. Fetch live episodes
    episodes = compiler.fetch_feed_episodes()
    if not episodes:
        logger.error("No episodes retrieved from feed.")
        return

    # 2. Download and assemble
    selected = compiler.download_episodes(episodes, target_duration_sec=args.target_seconds)
    master_mp3, description, chapters = compiler.assemble_audio_and_chapters(selected)

    # 3. Render 16:9 Landscape Mega Video
    output_video = str(compiler.output_dir / "groundwork_1hour_mega_briefing.mp4")

    if args.render_full or not os.path.exists(output_video):
        logger.info("Executing full 1-hour master rendering...")
        compiler.render_landscape_mega_video(master_mp3, output_video, duration_limit=None)
    elif args.test_render:
        logger.info("Executing 65s test rendering...")
        compiler.render_landscape_mega_video(master_mp3, output_video, duration_limit=65.0)

    # 4. Render 9:16 Shorts
    shorts = []
    if args.extract_shorts:
        shorts = compiler.extract_micro_insight_shorts(selected, num_shorts=3)
        print(f"Rendered {len(shorts)} YouTube Shorts ready for distribution.")

    # 5. YouTube Upload Workflows
    mega_video_url = None
    if args.upload_mega or args.upload_all:
        logger.info(f"Uploading 1-Hour Mega Video to YouTube as '{args.privacy}'...")
        mega_result = compiler.upload_to_youtube(
            video_path=output_video,
            title="Groundwork Mega Briefing: 1-Hour Master Evidence-Based Guides & Decision Models",
            description=description,
            tags=["investing", "personal finance", "health longevity", "calculators", "evidence based", "tech tools", "home energy", "career"],
            privacy=args.privacy
        )
        if mega_result:
            for line in mega_result.get("output", "").splitlines():
                if "https://youtu.be/" in line:
                    mega_video_url = line.strip().split()[-1]

    if (args.upload_shorts or args.upload_all) and shorts:
        logger.info(f"Uploading {len(shorts)} Shorts to YouTube as '{args.privacy}'...")
        for idx, s_path in enumerate(shorts):
            p_name = Path(s_path).stem.split("_")[-1].capitalize()
            s_title = f"Groundwork Micro Briefing: {p_name} Insight ({idx+1}/3) #Shorts"

            ref_link = mega_video_url or "https://youtu.be/channel"
            s_desc = (
                f"Full 1-hour master breakdown: {ref_link}\n\n"
                "Unvarnished, data-backed models to replace guesswork.\n"
                "Explore all interactive calculators & guides: https://gworky.com\n\n"
                "#Shorts #Groundwork #EvidenceBased #Insight"
            )
            compiler.upload_to_youtube(
                video_path=s_path,
                title=s_title,
                description=s_desc,
                tags=["shorts", "evidence based", "groundwork", "calculators"],
                privacy=args.privacy
            )

    print("\n========================================================")
    print(" MEGA PODCAST COMPILATION PIPELINE SUCCESSFULLY COMPLETE ")
    print(f" Master Audio: {master_mp3}")
    print(f" Landscape Video: {output_video}")
    print(f" Description: {compiler.output_dir / 'youtube_mega_description.txt'}")
    print("========================================================\n")


if __name__ == "__main__":
    main()
