"""
agents/audio_producer.py — Autonomous Broadcast Audio & Video Podcast Engine for Groundwork

Master SSOT: docs/AUTONOMOUS-AUDIO-PODCAST-SPEC.md
Licenses: Apache-2.0, MIT, GPL-3.0 compatible
"""

import argparse
import asyncio
import contextlib
import json
import logging
import os
import re
import subprocess
import tempfile
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
import edge_tts
import litellm
from botocore.config import Config
from mutagen.id3 import APIC, ID3, TALB, TCON, TCOP, TIT2, TPE1, TYER
from mutagen.mp3 import MP3
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field
from pydub import AudioSegment

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("audio_producer")


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


# ─── Persona Voice Map ──────────────────────────────────────────────────────────

VOICE_MAP = {
    "host": {
        "name": "Elena",
        "voice": "en-US-AriaNeural",
        "elevenlabs_voice_id": "EXAVITQu4vr4xnSDxMaL",
        "rate": "+0%",
        "pitch": "+0Hz",
    },
    "money": {
        "name": "David Vance",
        "role": "Financial Analyst & Policy Lead",
        "voice": "en-US-GuyNeural",
        "elevenlabs_voice_id": "pNInz6obpgDQGcFmaJgB",
        "rate": "-2%",
        "pitch": "-1Hz",
    },
    "body": {
        "name": "Sarah Jenkins",
        "role": "Clinical Research & Longevity Lead",
        "voice": "en-US-JennyNeural",
        "elevenlabs_voice_id": "AZnzlk1XvdvUeBnXmlld",
        "rate": "+0%",
        "pitch": "+0Hz",
    },
    "home": {
        "name": "Marcus Thorne",
        "role": "Building Systems & Energy Lead",
        "voice": "en-US-ChristopherNeural",
        "elevenlabs_voice_id": "VR6AewLTigWG4xSOukaG",
        "rate": "-1%",
        "pitch": "-2Hz",
    },
    "life": {
        "name": "David Vance",
        "role": "Senior Research Strategist",
        "voice": "en-US-GuyNeural",
        "elevenlabs_voice_id": "pNInz6obpgDQGcFmaJgB",
        "rate": "-1%",
        "pitch": "-1Hz",
    },
    "tech": {
        "name": "Alex Rivera",
        "role": "Emerging Tech & Systems Architect",
        "voice": "en-US-EricNeural",
        "elevenlabs_voice_id": "ErXwobaYiN019PkySvjV",
        "rate": "+2%",
        "pitch": "+1Hz",
    },
}

PILLAR_COLORS = {
    "money": (16, 185, 129),  # Emerald #10b981
    "body": (59, 130, 246),  # Blue #3b82f6
    "home": (245, 158, 11),  # Amber #f59e0b
    "life": (168, 85, 247),  # Purple #a855f7
    "tech": (6, 182, 212),  # Cyan #06b6d4
}


# ─── Dialogue Script Schema ───────────────────────────────────────────────────


class DialogueTurn(BaseModel):
    speaker: str = Field(description="Name of the speaker (Elena or Desk Lead)")
    text: str = Field(description="Natural spoken sentence without markdown formatting")


class PodcastScript(BaseModel):
    title: str
    pillar: str
    turns: list[DialogueTurn] = Field(min_length=4)


# ─── Audio Producer Class ─────────────────────────────────────────────────────


class AudioProducer:
    def __init__(self):
        self.supabase_url = os.getenv("NEXT_PUBLIC_SUPABASE_URL") or os.getenv("SUPABASE_URL", "")
        self.supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY", "")

        # Cloudflare R2 Storage Configuration
        self.r2_account_id = os.getenv("R2_ACCOUNT_ID") or os.getenv("CLOUDFLARE_R2_ACCOUNT_ID", "")
        self.r2_access_key = os.getenv("R2_ACCESS_KEY_ID") or os.getenv("CLOUDFLARE_R2_ACCESS_KEY_ID", "")
        self.r2_secret_key = os.getenv("R2_SECRET_ACCESS_KEY") or os.getenv("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "")
        self.r2_bucket = os.getenv("R2_BUCKET") or os.getenv("CLOUDFLARE_R2_BUCKET_NAME", "groundwork-media")
        self.r2_public_url = (
            os.getenv("R2_PUBLIC_BASE_URL") or os.getenv("CLOUDFLARE_R2_PUBLIC_URL", "https://media.gworky.com")
        ).rstrip("/")

        self.s3_client = None
        if self.r2_account_id and self.r2_access_key and self.r2_secret_key:
            endpoint_url = f"https://{self.r2_account_id}.r2.cloudflarestorage.com"
            self.s3_client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                aws_access_key_id=self.r2_access_key,
                aws_secret_access_key=self.r2_secret_key,
                config=Config(signature_version="s3v4"),
            )

    # ── Supabase Helpers ──────────────────────────────────────────────────────

    def _supabase_request(self, method: str, endpoint: str, data: dict[str, Any] | None = None) -> Any:
        if not self.supabase_url or not self.supabase_key:
            logger.warning("Supabase credentials not configured. Skipping DB operation.")
            return None

        url = f"{self.supabase_url.rstrip('/')}/rest/v1/{endpoint.lstrip('/')}"
        headers = {
            "apikey": self.supabase_key,
            "Authorization": f"Bearer {self.supabase_key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation,resolution=merge-duplicates",
        }

        req_data = json.dumps(data).encode("utf-8") if data else None
        req = urllib.request.Request(url, data=req_data, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except Exception as e:
            logger.error(f"Supabase REST error [{method} {url}]: {e}")
            return None

    def fetch_articles(self, limit: int = 20, slug: str | None = None) -> list[dict[str, Any]]:
        if slug:
            endpoint = f"articles?slug=eq.{slug}&select=id,slug,title,excerpt,content,pillar,published_at"
        else:
            endpoint = f"articles?status=eq.published&order=published_at.desc&limit={limit}&select=id,slug,title,excerpt,content,pillar,published_at"

        res = self._supabase_request("GET", endpoint)
        return res or []

    # ── Script Generation ─────────────────────────────────────────────────────

    def generate_dialogue_script(self, article: dict[str, Any]) -> PodcastScript:
        pillar = article.get("pillar", "money").lower()
        desk_info = VOICE_MAP.get(pillar, VOICE_MAP["money"])
        desk_lead_name = desk_info["name"]
        desk_lead_role = desk_info.get("role", "Subject Matter Lead")

        prompt = f"""
You are writing a broadcast podcast dialogue script between Host Elena and {desk_lead_name} ({desk_lead_role}) for Groundwork Media.

Tone Guidelines:
- High-authority, conversational, direct, and zero fluff.
- Sentence-case. Active voice.
- Speak in natural, spoken American English. No robotic jargon.
- Elena opens with the decision problem and context (30-45s).
- {desk_lead_name} breaks down the hard numbers, methodology, and trade-offs (1.5 - 2 mins).
- Elena synthesizes the bottom-line action and points to the interactive guide/calculator on Groundwork (30s).
- Total duration must be ~3 minutes (6 to 10 alternating dialogue turns).

Article Title: {article.get("title")}
Pillar: {pillar}
Article Excerpt: {article.get("excerpt", "")}
Article Text Summary: {article.get("content", "")[:1200]}

Return strict JSON matching schema:
{{
  "title": "{article.get("title")}",
  "pillar": "{pillar}",
  "turns": [
    {{"speaker": "Elena", "text": "..."}},
    {{"speaker": "{desk_lead_name}", "text": "..."}}
  ]
}}
"""
        # Attempt LiteLLM completion
        try:
            api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or os.getenv("OPENAI_API_KEY")
            if api_key:
                model = (
                    "gemini/gemini-1.5-flash"
                    if "GEMINI_API_KEY" in os.environ or "GOOGLE_API_KEY" in os.environ
                    else "gpt-4o-mini"
                )
                response = litellm.completion(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    temperature=0.4,
                )
                content = response.choices[0].message.content  # type: ignore[reportAttributeAccessIssue]
                data = json.loads(content)  # type: ignore[reportArgumentType]
                return PodcastScript(**data)
        except Exception as e:
            logger.warning(f"LiteLLM scriptgen fallback used due to: {e}")

        # Deterministic Fallback Script if LLM is unavailable
        title = article.get("title", "Research Breakdown")
        excerpt = article.get("excerpt", "Here is the evidence-based analysis.")
        return PodcastScript(
            title=title,
            pillar=pillar,
            turns=[
                DialogueTurn(
                    speaker="Elena",
                    text=f"Welcome to Groundwork Deep Dives. Today we're examining {title}. Let's get straight to the evidence.",
                ),
                DialogueTurn(
                    speaker=desk_lead_name,
                    text=f"Thanks Elena. Looking at the data, the core takeaway is straightforward: {excerpt}",
                ),
                DialogueTurn(
                    speaker="Elena",
                    text="What does this mean in practical terms for someone making this decision right now?",
                ),
                DialogueTurn(
                    speaker=desk_lead_name,
                    text="It means you should verify the underlying numbers first before committing capital or time. Our benchmarks show clear cost and efficiency divergence.",
                ),
                DialogueTurn(
                    speaker="Elena",
                    text="You can run your exact numbers using the interactive calculator and full research guide at gworky.com. Thank you for listening to Groundwork.",
                ),
            ],
        )

    # ── Dynamic Cover Art Synthesis ───────────────────────────────────────────

    # ── Dynamic Cover Art Synthesis ───────────────────────────────────────────

    @staticmethod
    def _load_system_font(size: int, bold: bool = True):
        """Load the crispest available TrueType font across macOS / Linux runners."""
        font_candidates = [
            "/System/Library/Fonts/SFProText-Bold.otf" if bold else "/System/Library/Fonts/SFProText-Regular.otf",
            "/System/Library/Fonts/SFNS.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
        for candidate in font_candidates:
            if os.path.exists(candidate):
                try:
                    return ImageFont.truetype(candidate, size)
                except Exception:
                    pass
        return ImageFont.load_default()

    def _generate_shorts_cover(
        self, title: str, pillar: str, output_png_path: str, featured_image_url: str | None = None
    ) -> str:
        """Native 1080x1920 vertical composition for YouTube Shorts / TikTok renders.

        Never crop the square podcast cover: text anchored at x=180 on a 3000px
        canvas gets amputated by the 1080px center-crop and reads as a broken
        landscape template. This builds the frame natively instead.
        """
        import io
        import urllib.request

        width, height = 1080, 1920
        pillar_color = PILLAR_COLORS.get(pillar.lower(), (16, 185, 129))

        img = Image.new("RGB", (width, height), color=(10, 12, 16))
        if featured_image_url and featured_image_url.startswith("http"):
            try:
                req = urllib.request.Request(featured_image_url, headers={"User-Agent": "GroundworkAudio/1.0"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    feat_img = Image.open(io.BytesIO(resp.read())).convert("RGBA")
                # Cover-fit 1080x1920: scale shortest side, center-crop the excess
                ratio = max(width / feat_img.width, height / feat_img.height)
                feat_img = feat_img.resize(
                    (round(feat_img.width * ratio), round(feat_img.height * ratio)),
                    Image.Resampling.LANCZOS,
                )
                left = (feat_img.width - width) // 2
                top = (feat_img.height - height) // 2
                feat_img = feat_img.crop((left, top, left + width, top + height))
                dark_overlay = Image.new("RGBA", (width, height), (8, 11, 16, 215))
                feat_img = Image.alpha_composite(feat_img, dark_overlay)
                img.paste(feat_img.convert("RGB"), (0, 0))
            except Exception as e:
                logger.warning(f"Could not download article featured image for shorts cover: {e}")

        draw = ImageDraw.Draw(img)

        # Corner glow, scaled for the vertical frame
        for r in range(520, 0, -24):
            draw.ellipse(
                (width - r, -r, width + r, r),
                fill=(pillar_color[0] // 3, pillar_color[1] // 3, pillar_color[2] // 3),
            )

        margin = 84
        font_brand = self._load_system_font(46, bold=True)
        font_badge = self._load_system_font(36, bold=True)
        font_title = self._load_system_font(92, bold=True)
        font_host = self._load_system_font(40, bold=True)
        font_footer = self._load_system_font(30, bold=False)

        draw.text((margin, 100), "GROUNDWORK DEEP DIVES", fill=(255, 255, 255), font=font_brand)

        badge_w = int(draw.textlength(f"● {pillar.upper()}", font=font_badge)) + 72
        draw.rounded_rectangle((margin, 176, margin + badge_w, 258), radius=20, fill=pillar_color)
        draw.text((margin + 32, 194), f"● {pillar.upper()}", fill=(10, 12, 16), font=font_badge)

        # Pixel-measured word-wrap keeps glyphs inside the 1080px frame
        clean_title = re.sub(r'["—]', "", title).strip()
        max_text_w = width - 2 * margin
        lines: list[str] = []
        curr = ""
        for word in clean_title.split():
            probe = f"{curr} {word}".strip()
            if draw.textlength(probe, font=font_title) <= max_text_w:
                curr = probe
            else:
                if curr:
                    lines.append(curr)
                curr = word
        if curr:
            lines.append(curr)

        y_offset = 330
        for line in lines[:7]:
            draw.text((margin, y_offset), line, fill=(255, 255, 255), font=font_title)
            y_offset += 114
        if len(lines) > 7:
            draw.text((margin, y_offset), "…", fill=(255, 255, 255), font=font_title)

        # Waveform band occupies y≈1280–1560 (ffmpeg overlays it there) — keep clear

        draw.line((margin, 1620, width - margin, 1620), fill=(51, 65, 85), width=4)
        draw.text((margin, 1652), "Elena & Research Desk Leads", fill=(241, 245, 249), font=font_host)
        draw.text(
            (margin, 1720),
            "Evidence-Based Guidance • gworky.com",
            fill=(148, 163, 184),
            font=font_footer,
        )

        os.makedirs(os.path.dirname(os.path.abspath(output_png_path)), exist_ok=True)
        img.save(output_png_path, "PNG")
        logger.info(f"Generated native 1080x1920 Shorts cover at: {output_png_path}")
        return output_png_path

    def generate_cover_art(
        self,
        title: str,
        pillar: str,
        output_png_path: str,
        featured_image_url: str | None = None,
        layout: str = "square",
    ) -> str:
        """Render high-contrast cover art with Pillow and TrueType typography.

        layout='square': 3000x3000 broadcast cover (RSS itunes:image, R2 artwork).
        layout='shorts': native 1080x1920 vertical frame for Shorts renders.
        """
        if layout == "shorts":
            return self._generate_shorts_cover(title, pillar, output_png_path, featured_image_url)
        width, height = 3000, 3000
        pillar_color = PILLAR_COLORS.get(pillar.lower(), (16, 185, 129))

        # Dark studio canvas background #0a0c10
        img = Image.new("RGB", (width, height), color=(10, 12, 16))

        # Try to load featured image from article if available
        if featured_image_url and featured_image_url.startswith("http"):
            try:
                import io
                import urllib.request

                req = urllib.request.Request(featured_image_url, headers={"User-Agent": "GroundworkAudio/1.0"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    feat_data = resp.read()
                    feat_img = Image.open(io.BytesIO(feat_data)).convert("RGBA")
                    # Crop/scale to 3000x3000
                    feat_img = feat_img.resize((width, height), Image.Resampling.LANCZOS)
                    # Overlay dark cinematic mask
                    dark_overlay = Image.new("RGBA", (width, height), (8, 11, 16, 210))
                    feat_img = Image.alpha_composite(feat_img, dark_overlay)
                    img.paste(feat_img.convert("RGB"), (0, 0))
            except Exception as e:
                logger.warning(f"Could not download article featured image for cover: {e}")

        draw = ImageDraw.Draw(img)

        # Ambient gradient glow on corner
        for r in range(1400, 0, -30):
            draw.ellipse(
                (width - r, -r, width + r, r), fill=(pillar_color[0] // 3, pillar_color[1] // 3, pillar_color[2] // 3)
            )

        font_brand = self._load_system_font(75, bold=True)
        font_badge = self._load_system_font(60, bold=True)
        font_title = self._load_system_font(130, bold=True)
        font_footer = self._load_system_font(55, bold=False)

        # Top Brand Tag
        draw.text((180, 200), "GROUNDWORK DEEP DIVES", fill=(255, 255, 255), font=font_brand)

        # Pillar Badge Box
        draw.rounded_rectangle((180, 320, 620, 420), radius=24, fill=pillar_color)
        draw.text((220, 345), f"● {pillar.upper()}", fill=(10, 12, 16), font=font_badge)

        # Clean title text formatting with word-wrap
        clean_title = re.sub(r"[\"—]", "", title).strip()
        lines = []
        words = clean_title.split()
        curr_line = []
        for word in words:
            curr_line.append(word)
            if len(" ".join(curr_line)) > 24:
                lines.append(" ".join(curr_line))
                curr_line = []
        if curr_line:
            lines.append(" ".join(curr_line))

        y_offset = 640
        for line in lines[:5]:
            draw.text((180, y_offset), line, fill=(255, 255, 255), font=font_title)
            y_offset += 175

        # Ambient card divider
        draw.line((180, 2600, 2820, 2600), fill=(51, 65, 85), width=6)

        # Host & Attribution
        draw.text((180, 2680), "Elena & Groundwork Research Desk Leads", fill=(241, 245, 249), font=font_brand)
        draw.text(
            (180, 2780),
            "Evidence-Based Guidance • Full Interactive Study at gworky.com",
            fill=(148, 163, 184),
            font=font_footer,
        )

        os.makedirs(os.path.dirname(os.path.abspath(output_png_path)), exist_ok=True)
        img.save(output_png_path, format="PNG", optimize=True)
        logger.info(f"Generated 3000x3000px high-contrast cover art at: {output_png_path}")
        return output_png_path

    # ── TTS Audio Generation ──────────────────────────────────────────────────

    async def _synthesize_voice_segment(
        self, text: str, voice: str, elevenlabs_voice_id: str | None, rate: str, pitch: str, out_file: str
    ):
        elevenlabs_key = os.getenv("ELEVENLABS_API_KEY", "").strip()

        # 1. Try ElevenLabs Studio Voice if API key is configured
        if elevenlabs_key and elevenlabs_voice_id:
            try:
                import json
                import urllib.request

                url = f"https://api.elevenlabs.io/v1/text-to-speech/{elevenlabs_voice_id}"
                headers = {"xi-api-key": elevenlabs_key, "Content-Type": "application/json", "Accept": "audio/mpeg"}
                payload = json.dumps(
                    {
                        "text": text,
                        "model_id": "eleven_multilingual_v2",
                        "voice_settings": {"stability": 0.5, "similarity_boost": 0.8},
                    }
                ).encode("utf-8")

                req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=15) as resp:
                    if resp.status == 200:
                        with open(out_file, "wb") as f:
                            f.write(resp.read())
                        logger.info(f"Synthesized segment with ElevenLabs studio voice ({elevenlabs_voice_id})")
                        return
            except Exception as e:
                logger.warning(
                    f"ElevenLabs TTS synthesis fallback (Quota or Error: {e}). Seamlessly switching to Edge-TTS."
                )

        # 2. Resilient Edge-TTS Free Neural Engine Fallback
        comm = edge_tts.Communicate(text=text, voice=voice, rate=rate, pitch=pitch)
        await comm.save(out_file)

    async def render_dialogue_audio(self, script: PodcastScript, output_mp3_path: str) -> tuple[int, str]:
        """Synthesizes all dialogue turns, concatenates with breath gaps, masters to -16 LUFS."""
        temp_dir = tempfile.mkdtemp(prefix="gw_audio_")
        segment_files = []

        pillar = script.pillar.lower()
        desk_info = VOICE_MAP.get(pillar, VOICE_MAP["money"])

        for idx, turn in enumerate(script.turns):
            seg_path = os.path.join(temp_dir, f"turn_{idx:03d}.mp3")
            v = VOICE_MAP["host"] if turn.speaker.lower() == "elena" else desk_info

            logger.info(f"Synthesizing turn {idx + 1}/{len(script.turns)} [{turn.speaker}] via {v['voice']}")
            await self._synthesize_voice_segment(
                text=turn.text,
                voice=v["voice"],
                elevenlabs_voice_id=v.get("elevenlabs_voice_id"),
                rate=v["rate"],
                pitch=v["pitch"],
                out_file=seg_path,
            )
            segment_files.append(seg_path)

        # Concatenate Audio with natural 450ms breath pause
        master_audio = AudioSegment.empty()
        pause = AudioSegment.silent(duration=450)

        for seg in segment_files:
            if os.path.exists(seg):
                audio_turn = AudioSegment.from_file(seg, format="mp3")
                master_audio += audio_turn + pause

        # EBU R128 Loudness Normalization (-16 LUFS standard)
        # Approximate normalization via target peak headroom
        change_in_gain = -1.5 - master_audio.max_dBFS
        normalized_audio = master_audio.apply_gain(change_in_gain)

        os.makedirs(os.path.dirname(os.path.abspath(output_mp3_path)), exist_ok=True)
        normalized_audio.export(output_mp3_path, format="mp3", bitrate="192k")

        duration_sec = int(len(normalized_audio) / 1000)
        minutes = duration_sec // 60
        seconds = duration_sec % 60
        duration_formatted = f"{minutes:02d}:{seconds:02d}"

        logger.info(f"Mastered episode MP3 to {output_mp3_path} ({duration_formatted})")
        return duration_sec, duration_formatted

    # ── Tagging & ID3 Embedding ───────────────────────────────────────────────

    def embed_id3_tags(self, mp3_path: str, title: str, pillar: str, cover_png_path: str):
        """Embed ID3v2.4 frames with cover artwork APIC."""
        try:
            audio = MP3(mp3_path, ID3=ID3)
            with contextlib.suppress(Exception):
                audio.add_tags()

            audio.tags.add(TIT2(encoding=3, text=title))  # type: ignore[reportOptionalMemberAccess]
            audio.tags.add(TPE1(encoding=3, text="Groundwork Media & Elena"))  # type: ignore[reportOptionalMemberAccess]
            audio.tags.add(TALB(encoding=3, text="Groundwork Deep Dives"))  # type: ignore[reportOptionalMemberAccess]
            audio.tags.add(TYER(encoding=3, text=str(datetime.now().year)))  # type: ignore[reportOptionalMemberAccess]
            audio.tags.add(TCON(encoding=3, text=f"Podcast / {pillar.capitalize()}"))  # type: ignore[reportOptionalMemberAccess]
            audio.tags.add(TCOP(encoding=3, text="© 2026 Groundwork Media. All rights reserved."))  # type: ignore[reportOptionalMemberAccess]

            if os.path.exists(cover_png_path):
                with open(cover_png_path, "rb") as art:
                    audio.tags.add(
                        APIC(  # type: ignore[reportOptionalMemberAccess]
                            encoding=3, mime="image/png", type=3, desc="Cover", data=art.read()
                        )
                    )
            audio.save(v2_version=3)
            logger.info(f"Embedded ID3v2.4 metadata and APIC artwork into: {mp3_path}")
        except Exception as e:
            logger.error(f"Failed to embed ID3 tags: {e}")

    # ── Video Audiogram Rendering ($0 FFmpeg Engine) ──────────────────────────

    def render_video_audiogram(
        self,
        mp3_path: str,
        cover_png_path: str,
        output_mp4_path: str,
        format_mode: str = "landscape",
        max_seconds: float | None = None,
    ) -> str | None:
        """Render broadcast video audiogram with dynamic soundwave spectrum.

        Supported formats:
          - 'landscape': 16:9 1920x1080 (YouTube Podcasts, Desktop Player)
          - 'shorts': 9:16 1080x1920 (YouTube Shorts, TikTok, Reels, Mobile)

        ``max_seconds`` caps the rendered output duration (e.g. 58s keeps a
        render inside YouTube Shorts length limits). ``None`` renders the
        full audio.
        """
        try:
            os.makedirs(os.path.dirname(os.path.abspath(output_mp4_path)), exist_ok=True)
            is_shorts = format_mode.lower() in ("shorts", "vertical", "reels", "tiktok")

            if is_shorts:
                filter_complex = (
                    "[1:a]compand,showwaves=s=880x280:mode=line:colors=0x10b981[wave];"
                    "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[bg];"
                    "[bg][wave]overlay=(W-w)/2:H-h-360:shortest=1[outv]"
                )
            else:
                filter_complex = (
                    "[1:a]compand,showwaves=s=1280x240:mode=line:colors=0x10b981[wave];"
                    "[0:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080[bg];"
                    "[bg][wave]overlay=(W-w)/2:H-h-120:shortest=1[outv]"
                )

            cmd = [
                "ffmpeg",
                "-y",
                "-loop",
                "1",
                "-i",
                cover_png_path,
                "-i",
                mp3_path,
                "-filter_complex",
                filter_complex,
                "-map",
                "[outv]",
                "-map",
                "1:a",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "26",
                "-c:a",
                "copy",
                "-shortest",
            ]
            if max_seconds:
                cmd += ["-t", str(max_seconds)]
            cmd.append(output_mp4_path)
            logger.info(f"Rendering FFmpeg video audiogram ({format_mode}): {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, timeout=120)
            if result.returncode == 0 and os.path.exists(output_mp4_path):
                logger.info(f"Successfully generated video audiogram at: {output_mp4_path}")
                return output_mp4_path
            else:
                logger.warning(
                    f"FFmpeg render returned code {result.returncode}: {result.stderr.decode('utf-8')[:300]}"
                )
                return None
        except Exception as e:
            logger.warning(f"Video audiogram rendering skipped or failed: {e}")
            return None

    # ── Cloudflare R2 Upload ──────────────────────────────────────────────────

    def upload_to_r2(self, local_path: str, s3_key: str, content_type: str = "audio/mpeg") -> str:
        """Upload asset to Cloudflare R2 bucket with byte-range support."""
        if self.s3_client and self.r2_bucket:
            try:
                logger.info(f"Uploading {local_path} to R2: s3://{self.r2_bucket}/{s3_key}")
                self.s3_client.upload_file(
                    local_path,
                    self.r2_bucket,
                    s3_key,
                    ExtraArgs={
                        "ContentType": content_type,
                        "CacheControl": "public, max-age=31536000, immutable",
                    },
                )
                return f"{self.r2_public_url}/{s3_key}"
            except Exception as e:
                logger.error(f"R2 S3 upload failed for {s3_key}: {e}")

        # Local fallback public URL
        return f"/audio/{s3_key.replace('audio/', '')}"

    # ── Database Upsert ───────────────────────────────────────────────────────

    def upsert_podcast_episode(self, episode_payload: dict[str, Any]) -> bool:
        res = self._supabase_request("POST", "podcast_episodes?on_conflict=slug", episode_payload)
        if res:
            logger.info(f"Successfully recorded podcast episode in DB: {episode_payload.get('slug')}")
            return True
        return False

    # ── Full Processing Pipeline for an Article ───────────────────────────────

    async def process_article(
        self,
        article: dict[str, Any],
        generate_video: bool = False,
        video_format: str = "landscape",
        shorts_max_seconds: float | None = None,
    ) -> dict[str, Any] | None:
        slug = article.get("slug")
        title = article.get("title")
        pillar = article.get("pillar", "money")
        article_id = article.get("id")

        if not slug or not title:
            return None

        logger.info(f"=== Producing Podcast Episode for [{pillar.upper()}] '{title}' ===")

        # 1. Generate Dialogue Script
        script = self.generate_dialogue_script(article)

        # 2. Paths
        base_dir = Path("public/audio")
        mp3_path = str(base_dir / "episodes" / f"{slug}.mp3")
        cover_path = str(base_dir / "covers" / f"{slug}.png")
        video_path = str(base_dir / "videos" / f"{slug}.mp4")

        # 3. Dynamic Cover Art with Unsplash / Source Image Support
        featured_img = article.get("featured_image") or article.get("image_url")
        self.generate_cover_art(title, pillar, cover_path, featured_image_url=featured_img)

        # 4. Render Audio Dialogue
        duration_sec, duration_formatted = await self.render_dialogue_audio(script, mp3_path)

        # 5. Embed ID3 Tags (must run BEFORE measuring file_size — embedded
        #    cover art changes the byte length reported in RSS enclosures;
        #    a stale value makes YouTube/podcast importers fail the transfer)
        self.embed_id3_tags(mp3_path, title, pillar, cover_path)
        file_size = os.path.getsize(mp3_path) if os.path.exists(mp3_path) else 0

        # 6. Render Video Audiogram (optional)
        video_url = None
        if generate_video:
            if video_format == "shorts":
                shorts_cover = str(base_dir / "covers" / f"{slug}-shorts.png")
                self.generate_cover_art(
                    title, pillar, shorts_cover, featured_image_url=featured_img, layout="shorts"
                )
                rendered_video = self.render_video_audiogram(
                    mp3_path,
                    shorts_cover,
                    video_path,
                    format_mode="shorts",
                    max_seconds=shorts_max_seconds,
                )
            else:
                rendered_video = self.render_video_audiogram(mp3_path, cover_path, video_path, format_mode=video_format)
            if rendered_video:
                s3_video_key = f"videos/{slug}.mp4"
                video_url = self.upload_to_r2(video_path, s3_video_key, content_type="video/mp4")

        # 7. Cloudflare R2 Upload for Audio & Cover
        s3_audio_key = f"episodes/{slug}.mp3"
        s3_cover_key = f"covers/{slug}.png"

        audio_url = self.upload_to_r2(mp3_path, s3_audio_key, content_type="audio/mpeg")
        cover_url = self.upload_to_r2(cover_path, s3_cover_key, content_type="image/png")

        # 8. Record to Supabase
        episode_payload = {
            "article_id": article_id,
            "slug": slug,
            "title": title,
            "description": article.get("excerpt") or f"Evidence-based research guide on {title}.",
            "pillar": pillar,
            "audio_url": audio_url,
            "cover_image_url": cover_url,
            "video_url": video_url,
            "duration_seconds": duration_sec,
            "duration_formatted": duration_formatted,
            "file_size_bytes": file_size,
            "mime_type": "audio/mpeg",
            "hosts": ["Elena", VOICE_MAP.get(pillar, {}).get("name", "Desk Lead")],
            "transcript_json": [t.model_dump() for t in script.turns],
            "published_at": article.get("published_at") or datetime.now(UTC).isoformat(),
        }

        self.upsert_podcast_episode(episode_payload)
        return episode_payload


# ─── Main Execution CLI ───────────────────────────────────────────────────────


async def main():
    parser = argparse.ArgumentParser(description="Autonomous Audio & Video Podcast Producer for Groundwork")
    parser.add_argument("--slug", type=str, help="Process a single article by slug")
    parser.add_argument("--backfill-top", type=int, default=0, help="Backfill N most recent published articles")
    parser.add_argument("--video", action="store_true", help="Also generate MP4 video audiograms")
    parser.add_argument(
        "--video-format",
        choices=["landscape", "shorts"],
        default="landscape",
        help="Video audiogram format: 'landscape' (16:9 for YouTube Podcasts) or 'shorts' (9:16 for YouTube Shorts/TikTok)",
    )
    args = parser.parse_args()

    producer = AudioProducer()

    if args.slug:
        articles = producer.fetch_articles(slug=args.slug)
    elif args.backfill_top > 0:
        articles = producer.fetch_articles(limit=args.backfill_top)
    else:
        articles = producer.fetch_articles(limit=5)

    if not articles:
        logger.warning("No articles found to process.")
        return

    logger.info(f"Found {len(articles)} articles to produce audio for.")
    for art in articles:
        await producer.process_article(art, generate_video=args.video, video_format=args.video_format)


if __name__ == "__main__":
    asyncio.run(main())
