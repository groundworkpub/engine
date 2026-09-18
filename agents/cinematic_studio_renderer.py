#!/usr/bin/env python3
"""agents/cinematic_studio_renderer.py — 3-Layer Cinematic Studio HUD & Thumbnail Engine.

Compiles 1080p 16:9 Master Documentary Videos for Groundwork using:
  - Layer 1: Dynamic B-Roll loops with slow Ken Burns zoom/pan and dark overlay.
  - Layer 2: Synchronized Infographic Studio Cards + Dynamic Soundwave audio spectrum.
  - Layer 3: Bottom Chapter Progress Bar.
  - Generates custom 1280x720 Elena branding YouTube Thumbnail.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_ROOT = Path(__file__).resolve().parent.parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("cinematic_studio_renderer")

PILLAR_COLORS = {
    "money": (16, 185, 129),   # #10b981 Emerald
    "tech": (59, 130, 246),    # #3b82f6 Blue
    "body": (244, 63, 94),     # #f43f5e Rose
    "home": (245, 158, 11),    # #f59e0b Amber
    "life": (139, 92, 246),    # #8b5cf6 Purple
}


class CinematicStudioRenderer:
    def __init__(self, pillar: str = "money", work_dir: Path | None = None):
        self.pillar = pillar.lower()
        self.work_dir = work_dir or (_ROOT / "artifacts" / "master_video" / self.pillar)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.pillar_color = PILLAR_COLORS.get(self.pillar, (16, 185, 129))
        self.encoder = self._detect_hardware_encoder()

    def _detect_hardware_encoder(self) -> tuple[str, list[str]]:
        """Detects whether VideoToolbox hardware acceleration is available."""
        try:
            res = subprocess.run(["ffmpeg", "-encoders"], capture_output=True, text=True, check=False)
            if "h264_videotoolbox" in res.stdout:
                logger.info("Using Apple Silicon hardware-accelerated encoder: h264_videotoolbox")
                return "h264_videotoolbox", ["-b:v", "6000k"]
        except Exception:
            pass
        logger.info("Using software encoder: libx264 -preset veryfast")
        return "libx264", ["-preset", "veryfast", "-crf", "22"]

    def _load_font(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        system_fonts = [
            "/System/Library/Fonts/SFPro-Bold.otf" if bold else "/System/Library/Fonts/SFPro-Regular.otf",
            "/System/Library/Fonts/HelveticaNeue.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
        for font_path in system_fonts:
            if os.path.exists(font_path):
                try:
                    return ImageFont.truetype(font_path, size)
                except Exception:
                    pass
        return ImageFont.load_default()

    def generate_youtube_thumbnail(
        self,
        main_headline: str = "THE 2026 CAPITAL BLUEPRINT",
        sub_headline: str = "Debt • Mortgages • Backdoor Roth • High-Yield",
        callout_badge: str = "$12,400 BREAK-EVEN MATH",
        output_path: Path | None = None,
    ) -> Path:
        """Renders high-converting 1280x720 YouTube Master Thumbnail."""
        out = output_path or (self.work_dir / "thumbnail.jpg")
        width, height = 1280, 720

        # Background: Dark navy studio canvas
        img = Image.new("RGB", (width, height), color=(8, 12, 20))
        draw = ImageDraw.Draw(img)

        # Subtle emerald corner glow
        c_r, c_g, c_b = self.pillar_color
        for r in range(400, 0, -20):
            alpha_fill = (c_r // 4, c_g // 4, c_b // 4)
            draw.ellipse((width - r, -r // 2, width + r, r // 2 + r), fill=alpha_fill)

        margin = 72
        font_brand = self._load_font(28, bold=True)
        font_badge = self._load_font(22, bold=True)
        font_main = self._load_font(68, bold=True)
        font_sub = self._load_font(32, bold=True)
        font_callout = self._load_font(30, bold=True)

        # 1. Top Brand Bar
        draw.text((margin, 60), "GROUNDWORK", fill=(255, 255, 255), font=font_brand)
        draw.text((margin + 205, 62), "MASTER SUITE", fill=(148, 163, 184), font=self._load_font(24, bold=False))

        # Pillar Badge
        badge_text = f"● {self.pillar.upper()} • AGES 35–48"
        badge_w = int(draw.textlength(badge_text, font=font_badge)) + 40
        draw.rounded_rectangle((width - margin - badge_w, 54, width - margin, 96), radius=16, fill=(15, 23, 42))
        draw.rounded_rectangle((width - margin - badge_w, 54, width - margin, 96), radius=16, outline=self.pillar_color, width=2)
        draw.text((width - margin - badge_w + 20, 64), badge_text, fill=self.pillar_color, font=font_badge)

        # 2. Main Headline (Bold, Multi-line)
        y_pos = 180
        for line in main_headline.split("\n"):
            draw.text((margin, y_pos), line, fill=(255, 255, 255), font=font_main)
            y_pos += 82

        # 3. Sub-headline
        draw.text((margin, y_pos + 20), sub_headline, fill=(203, 213, 225), font=font_sub)

        # 4. High-Contrast Callout Card
        callout_y = 520
        callout_text = f"⚡ {callout_badge}"
        callout_w = int(draw.textlength(callout_text, font=font_callout)) + 48
        draw.rounded_rectangle((margin, callout_y, margin + callout_w, callout_y + 68), radius=14, fill=self.pillar_color)
        draw.text((margin + 24, callout_y + 16), callout_text, fill=(10, 12, 16), font=font_callout)

        # 5. Footer Line
        draw.line((margin, 640, width - margin, 640), fill=(51, 65, 85), width=2)
        draw.text((margin, 656), "Elena & Research Desk Leads • Unvarnished Empirical Math • gworky.com", fill=(148, 163, 184), font=self._load_font(20, bold=False))

        img.save(out, "JPEG", quality=95)
        logger.info(f"Generated YouTube Master Thumbnail: {out}")
        return out

    def generate_chapter_hud_card(
        self,
        chapter_idx: int,
        total_chapters: int,
        chapter_title: str,
        key_stat: str,
        output_png: Path,
    ) -> Path:
        """Renders transparent 1920x1080 studio card with chapter metadata."""
        width, height = 1920, 1080
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Studio Card Box: Glassmorphism container (left-aligned)
        card_x, card_y = 100, 120
        card_w, card_h = 1080, 520

        # Semi-transparent dark slate backdrop
        draw.rounded_rectangle(
            (card_x, card_y, card_x + card_w, card_y + card_h),
            radius=24,
            fill=(10, 15, 26, 215),
            outline=(51, 65, 85, 180),
            width=2,
        )

        font_brand = self._load_font(26, bold=True)
        font_pill = self._load_font(22, bold=True)
        font_title = self._load_font(52, bold=True)
        font_stat = self._load_font(30, bold=True)

        # Brand / Pillar Header
        draw.text((card_x + 48, card_y + 42), "GROUNDWORK MASTER SUITE", fill=(148, 163, 184), font=font_brand)

        # Chapter Number Badge
        ch_pill = f"CHAPTER {chapter_idx:02d} OF {total_chapters:02d}"
        pill_w = int(draw.textlength(ch_pill, font=font_pill)) + 32
        pill_x = card_x + card_w - pill_w - 48
        draw.rounded_rectangle((pill_x, card_y + 36, pill_x + pill_w, card_y + 76), radius=12, fill=self.pillar_color)
        draw.text((pill_x + 16, card_y + 44), ch_pill, fill=(10, 15, 26), font=font_pill)

        # Chapter Title (Word-wrapped)
        words = chapter_title.split()
        lines = []
        curr = ""
        for w in words:
            probe = f"{curr} {w}".strip()
            if draw.textlength(probe, font=font_title) <= card_w - 96:
                curr = probe
            else:
                lines.append(curr)
                curr = w
        if curr:
            lines.append(curr)

        y_text = card_y + 115
        for line in lines[:3]:
            draw.text((card_x + 48, y_text), line, fill=(255, 255, 255), font=font_title)
            y_text += 68

        # Key Statistic / Empirical Metric Callout Card
        stat_y = card_y + card_h - 110
        draw.rounded_rectangle((card_x + 48, stat_y, card_x + card_w - 48, stat_y + 70), radius=14, fill=(20, 30, 50, 220))
        draw.rounded_rectangle((card_x + 48, stat_y, card_x + card_w - 48, stat_y + 70), radius=14, outline=self.pillar_color, width=1)
        draw.text((card_x + 72, stat_y + 18), f"📊 Key Finding: {key_stat}", fill=(241, 245, 249), font=font_stat)

        # Save transparent HUD card
        img.save(output_png, "PNG")
        return output_png

    def render_chapter_clip(
        self,
        broll_clip_path: Path,
        hud_card_png: Path,
        audio_mp3_path: Path,
        output_mp4_path: Path,
        duration: float,
    ) -> Path:
        """Renders 1 single chapter video segment using FFmpeg 3-layer HUD."""
        enc_name, enc_args = self.encoder

        # FFmpeg Filter Graph:
        # [0:v] B-Roll -> scale to 1920x1080 -> dark contrast color overlay
        # [hud] HUD card overlay on top of B-roll
        # [1:a] Audio -> showwaves soundwave spectrum positioned on bottom right
        # [comp] Progress bar on very bottom
        filter_complex = (
            "[0:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,"
            "drawbox=x=0:y=0:w=iw:h=ih:color=black@0.65:t=fill[darkbg];"
            "[darkbg][2:v]overlay=0:0[base];"
            "[1:a]compand,showwaves=s=560x100:mode=line:colors=0x10b981[wave];"
            "[base][wave]overlay=1260:540[v_wave];"
            "[v_wave]drawbox=x=0:y=1072:w=iw:h=8:color=0x10b981@0.9:t=fill[outv]"
        )

        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-i", str(broll_clip_path),
            "-i", str(audio_mp3_path),
            "-i", str(hud_card_png),
            "-filter_complex", filter_complex,
            "-map", "[outv]",
            "-map", "1:a",
            "-c:v", enc_name,
            *enc_args,
            "-c:a", "aac",
            "-b:a", "192k",
            "-t", str(duration),
            "-shortest",
            str(output_mp4_path),
        ]

        logger.info(f"Rendering chapter clip: {output_mp4_path.name} ({duration:.1f}s)...")
        subprocess.run(cmd, check=True, capture_output=True)
        return output_mp4_path

    def compile_master_video(
        self,
        meta_json_path: Path,
        broll_clips: list[Path],
        output_video_path: Path | None = None,
    ) -> Path:
        """Renders all chapter segments and concatenates into final 1080p Master Video."""
        with open(meta_json_path) as f:
            meta = json.load(f)

        segments = meta["audio_segments"]
        total_segs = len(segments)
        rendered_clips = []

        logger.info(f"Compiling 3-Layer Master Video for {total_segs} chapters...")

        for idx, seg in enumerate(segments):
            seg_name = seg["name"]
            dur = seg["duration"]
            audio_file = Path(seg["file"])

            # Assign B-roll clip round-robin
            broll = broll_clips[idx % len(broll_clips)]

            # Generate transparent HUD Card
            hud_card = self.work_dir / f"hud_card_{idx}.png"
            key_stat = "Empirical Capital Benchmark"
            if idx > 0 and idx <= len(meta.get("chapters", [])):
                ch_obj = meta["chapters"][idx - 1]
                key_stat = ch_obj.get("key_stat", key_stat)

            self.generate_chapter_hud_card(
                chapter_idx=idx,
                total_chapters=total_segs - 1,
                chapter_title=seg_name,
                key_stat=key_stat,
                output_png=hud_card,
            )

            # Render Segment MP4
            clip_out = self.work_dir / f"segment_{idx:02d}.mp4"
            self.render_chapter_clip(
                broll_clip_path=broll,
                hud_card_png=hud_card,
                audio_mp3_path=audio_file,
                output_mp4_path=clip_out,
                duration=dur,
            )
            rendered_clips.append(clip_out)

        # Concatenate all rendered segments
        concat_list = self.work_dir / "video_concat_list.txt"
        with open(concat_list, "w") as f:
            for c in rendered_clips:
                f.write(f"file '{c.name}'\n")

        final_out = output_video_path or (self.work_dir / f"master_video_{self.pillar}_2026.mp4")
        logger.info(f"Concatenating {len(rendered_clips)} clips into master video: {final_out.name}...")

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list),
            "-c", "copy",
            str(final_out),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        logger.info(f" Master Video Compilation Complete: {final_out} ({final_out.stat().st_size // (1024*1024)} MB)")
        return final_out


if __name__ == "__main__":
    renderer = CinematicStudioRenderer(pillar="money")
    thumb = renderer.generate_youtube_thumbnail()
    print(f"Thumbnail created: {thumb}")
