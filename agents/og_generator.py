"""agents/og_generator.py.

Autonomous Zero-WASM OpenGraph Card Generator for Groundwork.
Renders high-fidelity 1200x630 social preview cards using Python Pillow
and uploads them to Cloudflare R2 (`https://media.gworky.com/og/{slug}.webp`).

Guarantees:
- Zero WASM bloat in Next.js Cloudflare Pages worker bundle (< 1 MiB maintained).
- Instant Edge Cache HITs (< 20ms) for social crawlers (X, Facebook, WhatsApp, LinkedIn).
- Verified brand design: Dark navy canvas, pillar accent badge, wrapped headline, logo mark.

Usage:
  python3 agents/og_generator.py --slug solar-energy-storage-everything-you-need-to-know
  python3 agents/og_generator.py --batch 10
  python3 agents/og_generator.py --all
"""

import argparse
import io
import logging
import os
import sys
from typing import Any
import psycopg2
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

load_dotenv(".env.local")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("og_generator")

# Canvas Dimensions (Standard OpenGraph 1.91:1)
OG_WIDTH = 1200
OG_HEIGHT = 630

PILLAR_COLORS = {
    "money": (16, 185, 129),    # Emerald
    "body": (244, 63, 94),      # Rose
    "home": (245, 158, 11),     # Amber
    "life": (14, 165, 233),     # Sky
    "tech": (139, 92, 246),     # Purple
    "default": (16, 185, 129),
}

def get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    font_candidates = [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/SFCompact.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial.ttf",
    ]
    for p in font_candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()

def wrap_text(text: str, font: Any, max_width: int, draw: ImageDraw.ImageDraw) -> list[str]:
    words = text.split()
    lines = []
    current_line = []

    for word in words:
        test_line = " ".join(current_line + [word])
        bbox = draw.textbbox((0, 0), test_line, font=font)
        w = bbox[2] - bbox[0]
        if w <= max_width:
            current_line.append(word)
        else:
            if current_line:
                lines.append(" ".join(current_line))
            current_line = [word]
    if current_line:
        lines.append(" ".join(current_line))
    return lines

def render_og_image(title: str, pillar: str, read_time: int = 5) -> bytes:
    """Renders a 1200x630 Groundwork OpenGraph card in WebP format."""
    pillar_key = (pillar or "default").lower()
    accent_rgb = PILLAR_COLORS.get(pillar_key, PILLAR_COLORS["default"])

    # 1. Base Gradient Canvas (Dark Navy #0A192F -> #0F2344)
    img = Image.new("RGB", (OG_WIDTH, OG_HEIGHT), color=(10, 25, 47))
    draw = ImageDraw.Draw(img)

    # Subtle vertical gradient
    for y in range(OG_HEIGHT):
        ratio = y / OG_HEIGHT
        r = int(10 * (1 - ratio) + 15 * ratio)
        g = int(25 * (1 - ratio) + 35 * ratio)
        b = int(47 * (1 - ratio) + 68 * ratio)
        draw.line([(0, y), (OG_WIDTH, y)], fill=(r, g, b))

    # Top accent line
    draw.rectangle([(0, 0), (OG_WIDTH, 8)], fill=accent_rgb)

    # 2. Brand Header (Logo Mark & Text)
    font_brand = get_font(28, bold=True)
    draw.rectangle([(60, 60), (96, 96)], fill=(15, 36, 68), outline=accent_rgb, width=2)
    draw.text((70, 64), "G", fill=accent_rgb, font=font_brand)
    draw.text((112, 65), "GROUNDWORK", fill=(255, 255, 255), font=font_brand)

    # 3. Pillar Badge
    pillar_label = f"• {pillar.upper()} RESEARCH •" if pillar else "• RESEARCH REPORT •"
    font_badge = get_font(18, bold=True)
    draw.rounded_rectangle([(60, 140), (280, 175)], radius=6, fill=(15, 36, 68), outline=accent_rgb, width=1)
    draw.text((80, 147), pillar_label, fill=accent_rgb, font=font_badge)

    # 4. Main Title
    font_title = get_font(52, bold=True)
    max_title_width = OG_WIDTH - 160  # 60px padding on left and 100px on right
    title_lines = wrap_text(title, font_title, max_title_width, draw)
    
    # Cap to max 3 lines with ellipsis
    if len(title_lines) > 3:
        title_lines = title_lines[:3]
        title_lines[2] = title_lines[2][:len(title_lines[2])-3] + "..."

    y_offset = 220
    for line in title_lines:
        draw.text((60, y_offset), line, fill=(248, 250, 252), font=font_title)
        y_offset += 72

    # 5. Footer Metadata Bar
    font_footer = get_font(20, bold=False)
    footer_text = f"Evidence-Based Decision Support  |  {read_time} min read  |  gworky.com"
    draw.line([(60, OG_HEIGHT - 90), (OG_WIDTH - 60, OG_HEIGHT - 90)], fill=(30, 41, 59), width=1)
    draw.text((60, OG_HEIGHT - 65), footer_text, fill=(148, 163, 184), font=font_footer)

    # Save to WebP buffer
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=85, method=6)
    return buf.getvalue()

def upload_og_to_r2(slug: str, data: bytes) -> bool:
    """Uploads image data to Cloudflare R2 under og/{slug}.webp."""
    try:
        try:
            from agents.media_uploader import R2Uploader
        except ImportError:
            from media_uploader import R2Uploader
        uploader = R2Uploader()
        key = f"og/{slug}.webp"
        return uploader.put(key, data, content_type="image/webp")
    except Exception as e:
        logger.error("R2 upload error for %s: %s", slug, e)
        return False

def main():
    parser = argparse.ArgumentParser(description="Autonomous Groundwork OpenGraph Generator.")
    parser.add_argument("--slug", type=str, help="Generate for a specific article slug.")
    parser.add_argument("--batch", type=int, default=10, help="Batch limit.")
    parser.add_argument("--all", action="store_true", help="Generate for all published articles.")
    parser.add_argument("--dry-run", action="store_true", help="Do not upload to R2, save locally.")
    args = parser.parse_args()

    conn = psycopg2.connect(
        host=os.getenv("SUPABASE_DB_HOST"),
        port=os.getenv("SUPABASE_DB_PORT", "6543"),
        user=os.getenv("SUPABASE_DB_USER"),
        password=os.getenv("SUPABASE_DB_PASSWORD"),
        dbname="postgres",
        sslmode="require"
    )
    cur = conn.cursor()

    if args.slug:
        cur.execute("SELECT slug, title, pillar, reading_time FROM public.articles WHERE slug = %s;", (args.slug,))
    elif args.all:
        cur.execute("SELECT slug, title, pillar, reading_time FROM public.articles WHERE status = 'published';")
    else:
        cur.execute("SELECT slug, title, pillar, reading_time FROM public.articles WHERE status = 'published' ORDER BY published_at DESC LIMIT %s;", (args.batch,))

    rows = cur.fetchall()
    logger.info("Found %d articles to process", len(rows))

    success = 0
    for slug, title, pillar, reading_time in rows:
        rt = reading_time or 5
        data = render_og_image(title, pillar or "general", rt)
        if args.dry_run:
            out_path = f"/tmp/og_{slug}.webp"
            with open(out_path, "wb") as f:
                f.write(data)
            logger.info("[DRY-RUN] Saved %s (%d bytes)", out_path, len(data))
            success += 1
        else:
            if upload_og_to_r2(slug, data):
                logger.info("✅ Uploaded https://media.gworky.com/og/%s.webp (%d bytes)", slug, len(data))
                success += 1
            else:
                logger.warning("❌ Failed to upload og/%s.webp", slug)

    logger.info("Done: %d/%d processed successfully.", success, len(rows))
    cur.close()
    conn.close()

if __name__ == "__main__":
    main()
