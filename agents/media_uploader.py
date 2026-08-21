"""Agent 4: Visual media processor for Groundwork.

Self-hosts article images on Cloudflare R2 behind a custom domain
(``media.gworky.com``) using a 4-tier sourcing pipeline:

    Tier 1  Polish source image (OG/feed >= 600px)  -> upload to R2
    Tier 2  Unsplash API stock (hotlink + ping + attribution)
    Tier 3  Dynamic OG banner (placeholder canvas)  -> upload to R2
    Tier 4  Pollinations AI visual                   -> upload to R2

Unsplash compliance (per https://help.unsplash.com/en/articles/2511245):
  * Images MUST be served via hotlinked ``photo.urls`` — never re-hosted.
  * A download ping MUST be sent to ``photo.links.download_location`` the
    moment an image is selected for use.
  * The frontend MUST display photographer attribution with utm params.

Tier 2 therefore returns an external hotlink (plus attribution metadata) and
is not stored in R2; tiers 1/3/4 are processed (WebP 82%, 1200x675, subtle
"gworky.com" credit overlay) and uploaded to R2.

Output for the Scribe::

    {
      "image_url": "https://media.gworky.com/yyyy/mm/slug.webp",
      "image_source": "self-hosted" | "unsplash",
      "image_credit": { ... } | None,   # Tier 2 attribution payload
    }
"""

from __future__ import annotations

import io
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)

MEDIA_BASE_URL = os.environ.get("R2_PUBLIC_BASE_URL", "https://media.gworky.com")
R2_BUCKET = os.environ.get("R2_BUCKET", "gworky-media-us")
R2_ENDPOINT = os.environ.get("R2_ENDPOINT", "").rstrip("/")

UNSPLASH_ACCESS_KEY = os.environ.get("UNSPLASH_ACCESS_KEY", "")
UNSPLASH_APP_NAME = "gworky"
UNSPLASH_PAGE = 1
UNSPLASH_PER_PAGE = 5

PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "")
PIXABAY_API_KEY = os.environ.get("PIXABAY_API_KEY", "")

MIN_SOURCE_WIDTH = 600
TARGET_WIDTH = 1200
TARGET_HEIGHT = 675
WEBP_QUALITY = 82

TIMEOUT = httpx.Timeout(30.0, connect=10.0)


@dataclass
class MediaResult:
    """Result of the 4-tier visual sourcing pipeline."""

    image_url: str = ""
    image_source: str = "none"  # self-hosted | unsplash | none
    image_credit: dict[str, str] | None = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "image_url": self.image_url,
            "image_source": self.image_source,
            "image_credit": self.image_credit,
        }


class Uploader(Protocol):
    """Structural contract for anything that can `put` an object to storage."""

    def put(self, key: str, data: bytes, content_type: str) -> bool: ...


class R2Uploader:
    """Minimal S3-compatible uploader for Cloudflare R2 (no boto3)."""

    def __init__(self) -> None:
        account_id = os.environ.get("R2_ACCOUNT_ID", "")
        access_key = os.environ.get("R2_ACCESS_KEY_ID", "")
        secret_key = os.environ.get("R2_SECRET_ACCESS_KEY", "")
        if not (account_id and access_key and secret_key):
            raise RuntimeError("R2 credentials missing (R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY)")
        self.bucket = R2_BUCKET
        self.host = f"{account_id}.r2.cloudflarestorage.com"
        self.access_key = access_key
        self.secret_key = secret_key

    def _sign(self, method: str, path: str, content_sha256: str) -> str:
        import hashlib
        import hmac
        from datetime import datetime

        now = datetime.now(UTC)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        region = "auto"
        service = "s3"
        payload_hash = content_sha256

        def sign(key: bytes, msg: str) -> bytes:
            return hmac.new(key, msg.encode(), hashlib.sha256).digest()

        k_date = sign(f"AWS4{self.secret_key}".encode(), date_stamp)
        k_region = sign(k_date, region)
        k_service = sign(k_region, service)
        k_signing = sign(k_service, "aws4_request")

        canonical_uri = path if path.startswith("/") else f"/{path}"
        canonical_query = ""
        canonical_headers = f"host:{self.host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n"
        signed_headers = "host;x-amz-content-sha256;x-amz-date"
        canonical_request = "\n".join(
            [method, canonical_uri, canonical_query, canonical_headers, signed_headers, payload_hash]
        )
        scope = f"{date_stamp}/{region}/{service}/aws4_request"
        string_to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, _sha256_hex(canonical_request.encode())])
        signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()

        return (
            f"AWS4-HMAC-SHA256 Credential={self.access_key}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )

    def put(self, key: str, data: bytes, content_type: str) -> bool:
        path = f"/{self.bucket}/{key}"
        sha = _sha256_hex(data)
        url = f"https://{self.host}{path}"
        auth = self._sign("PUT", path, sha)
        try:
            resp = httpx.put(
                url,
                content=data,
                headers={
                    "Authorization": auth,
                    "x-amz-content-sha256": sha,
                    "x-amz-date": _amz_date(),
                    "Content-Type": content_type,
                },
                timeout=TIMEOUT,
            )
            if resp.status_code in (200, 201, 204):
                return True
            logger.error("R2 upload failed %s: %s %s", key, resp.status_code, resp.text[:300])
        except httpx.HTTPError as e:
            logger.exception("R2 upload error for %s: %s", key, e)
        return False


def _sha256_hex(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _amz_date() -> str:
    from datetime import datetime

    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _crop_cover(data: bytes, width: int = TARGET_WIDTH, height: int = TARGET_HEIGHT) -> bytes | None:
    """Resize to cover-crop 1200x675 and encode WebP 82%. Returns None on failure."""
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(data))
        img = img.convert("RGB")
        img.thumbnail((width * 2, height * 2))
        ratio = width / height
        w, h = img.size
        if w / h > ratio:
            new_w = int(h * ratio)
            left = (w - new_w) // 2
            img = img.crop((left, 0, left + new_w, h))
        else:
            new_h = int(w / ratio)
            top = (h - new_h) // 2
            img = img.crop((0, top, w, top + new_h))
        img = img.resize((width, height), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "WEBP", quality=WEBP_QUALITY, method=4)
        return buf.getvalue()
    except Exception:
        logger.exception("Image processing failed")
        return None


def _add_credit(data: bytes) -> bytes:
    """Overlay a subtle 'gworky.com' credit in the bottom corner."""
    try:
        from PIL import Image, ImageDraw, ImageFont

        img = Image.open(io.BytesIO(data)).convert("RGB")
        draw = ImageDraw.Draw(img)
        font = None
        for path in ("/System/Library/Fonts/Helvetica.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
            try:
                font = ImageFont.truetype(path, 18)
                break
            except OSError:
                continue
        text = "gworky.com"
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x, y = img.width - tw - 12, img.height - th - 8
        draw.rectangle((x - 4, y - 4, x + tw + 4, y + th + 4), fill=(0, 0, 0, 120))
        draw.text((x, y), text, fill=(255, 255, 255, 220), font=font)
        buf = io.BytesIO()
        img.save(buf, "WEBP", quality=WEBP_QUALITY, method=4)
        return buf.getvalue()
    except Exception:
        logger.exception("Credit overlay failed")
        return data


def fetch_bytes(url: str) -> bytes | None:
    try:
        resp = httpx.get(url, timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": "GroundworkBot/1.0"})
        if resp.status_code == 200:
            return resp.content
    except httpx.HTTPError as e:
        logger.warning("Fetch failed %s: %s", url, e)
    return None


def _is_dimension_ok(data: bytes, min_width: int = MIN_SOURCE_WIDTH) -> bool:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as img:
            return img.width >= min_width
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Tier 1: source image
# ---------------------------------------------------------------------------
def tier1_source(url: str, slug: str, uploader: Uploader) -> MediaResult:
    data = fetch_bytes(url)
    if data is None:
        return MediaResult(errors=[f"tier1 fetch failed: {url[:80]}"])
    if not _is_dimension_ok(data):
        return MediaResult(errors=[f"tier1 too small/undecodable: {url[:80]}"])
    processed = _crop_cover(data)
    if processed is None:
        return MediaResult(errors=[f"tier1 process failed: {url[:80]}"])
    key = _object_key(slug)
    if not uploader.put(key, processed, "image/webp"):
        return MediaResult(errors=[f"tier1 upload failed: {key}"])
    return MediaResult(image_url=f"{MEDIA_BASE_URL}/{key}", image_source="self-hosted")


# ---------------------------------------------------------------------------
# Tier 2: Unsplash stock (hotlink + download ping + attribution)
# ---------------------------------------------------------------------------
def tier2_unsplash(query: str, slug: str) -> MediaResult:
    if not UNSPLASH_ACCESS_KEY:
        return MediaResult(errors=["UNSPLASH_ACCESS_KEY not set"])
    try:
        resp = httpx.get(
            "https://api.unsplash.com/search/photos",
            params={
                "query": query,
                "client_id": UNSPLASH_ACCESS_KEY,
                "page": UNSPLASH_PAGE,
                "per_page": UNSPLASH_PER_PAGE,
                "orientation": "landscape",
            },
            timeout=TIMEOUT,
            headers={"User-Agent": "GroundworkBot/1.0"},
        )
        if resp.status_code != 200:
            return MediaResult(errors=[f"tier2 search HTTP {resp.status_code}"])
        results = resp.json().get("results", [])
        if not results:
            return MediaResult(errors=["tier2 no results"])
        photo = results[0]
        # Mandatory download ping (Unsplash API Guidelines #2).
        dl_url = photo.get("links", {}).get("download_location", "")
        if dl_url:
            try:
                httpx.get(dl_url, params={"client_id": UNSPLASH_ACCESS_KEY}, timeout=TIMEOUT)
            except httpx.HTTPError as e:
                logger.warning("Unsplash download ping failed: %s", e)
        user = photo.get("user", {})
        links = photo.get("links", {})
        return MediaResult(
            image_url=photo.get("urls", {}).get("regular", ""),
            image_source="unsplash",
            image_credit={
                "photographer": user.get("name", "Unsplash"),
                "photographer_url": user.get("links", {}).get("html", "https://unsplash.com"),
                "photo_url": links.get("html", ""),
                "unsplash_url": "https://unsplash.com",
                "utm_source": UNSPLASH_APP_NAME,
                "utm_medium": "referral",
            },
        )
    except httpx.HTTPError as e:
        return MediaResult(errors=[f"tier2 error: {e}"])

# ---------------------------------------------------------------------------
# Tier 2b: Pexels / Pixabay Fallback (Downloaded and uploaded to R2)
# ---------------------------------------------------------------------------
def tier2b_stock_fallback(query: str, slug: str, uploader: Uploader) -> MediaResult:
    # Try Pexels first
    if PEXELS_API_KEY:
        try:
            resp = httpx.get(
                "https://api.pexels.com/v1/search",
                params={"query": query, "per_page": 1},
                headers={"Authorization": PEXELS_API_KEY, "User-Agent": "GroundworkBot/1.0"},
                timeout=TIMEOUT,
            )
            if resp.status_code == 200:
                photos = resp.json().get("photos", [])
                if photos:
                    img_url = photos[0].get("src", {}).get("large2x") or photos[0].get("src", {}).get("original")
                    if img_url:
                        res = tier1_source(img_url, slug, uploader)
                        if res.image_url:
                            return res
        except httpx.HTTPError as e:
            logger.warning("Pexels fallback failed: %s", e)

    # Try Pixabay if Pexels fails or missing key
    if PIXABAY_API_KEY:
        try:
            resp = httpx.get(
                "https://pixabay.com/api/",
                params={"key": PIXABAY_API_KEY, "q": query, "image_type": "photo", "per_page": 3},
                headers={"User-Agent": "GroundworkBot/1.0"},
                timeout=TIMEOUT,
            )
            if resp.status_code == 200:
                hits = resp.json().get("hits", [])
                if hits:
                    img_url = hits[0].get("largeImageURL")
                    if img_url:
                        res = tier1_source(img_url, slug, uploader)
                        if res.image_url:
                            return res
        except httpx.HTTPError as e:
            logger.warning("Pixabay fallback failed: %s", e)

    return MediaResult(errors=["tier2b fallbacks failed"])


# ---------------------------------------------------------------------------
# Tier 3: Pollinations AI visual (free photorealistic editorial illustration)
# ---------------------------------------------------------------------------
def tier3_pollinations(title: str, pillar: str, slug: str, uploader: Uploader) -> MediaResult:
    import urllib.parse

    clean_title = title.replace('"', "").replace("'", "")[:100]
    prompt = urllib.parse.quote(
        f"editorial high quality photography, cinematic lighting, {pillar} concept, {clean_title}, modern, 8k, photorealistic"
    )
    url = f"https://image.pollinations.ai/prompt/{prompt}?width={TARGET_WIDTH}&height={TARGET_HEIGHT}&nologo=true"
    data = fetch_bytes(url)
    if data is None or len(data) < 2000:
        return MediaResult(errors=["tier3 pollinations fetch failed"])

    processed = _crop_cover(data)
    if processed is None:
        return MediaResult(errors=["tier3 pollinations process failed"])

    key = _object_key(slug)
    if not uploader.put(key, processed, "image/webp"):
        return MediaResult(errors=[f"tier3 upload failed: {key}"])
    return MediaResult(image_url=f"{MEDIA_BASE_URL}/{key}", image_source="self-hosted")


# ---------------------------------------------------------------------------
# Tier 4: Dynamic Editorial Banner (Premium typography card fallback)
# ---------------------------------------------------------------------------
def tier4_dynamic(title: str, pillar: str, slug: str, uploader: Uploader) -> MediaResult:
    try:
        from PIL import Image, ImageDraw, ImageFont

        # Pillar background gradient accent
        accent_colors = {
            "money": (16, 185, 129),  # Emerald
            "body": (14, 165, 233),  # Sky blue
            "home": (245, 158, 11),  # Amber
            "life": (168, 85, 247),  # Purple
            "tech": (99, 102, 241),  # Indigo
        }
        accent = accent_colors.get(pillar.lower(), (16, 185, 129))

        img = Image.new("RGB", (TARGET_WIDTH, TARGET_HEIGHT), (10, 25, 47))
        draw = ImageDraw.Draw(img)

        # Subtle dark top-right decorative accent
        for r in range(300, 0, -20):
            alpha = int((300 - r) / 300 * 40)
            draw.ellipse([TARGET_WIDTH - 200 - r, -100 - r, TARGET_WIDTH + 200 + r, 300 + r], outline=(*accent, alpha))

        font = ImageFont.load_default()
        label_font = font
        for path in (
            "/System/Library/Fonts/Helvetica.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/System/Library/Fonts/SFProText-Bold.otf",
        ):
            try:
                label_font = ImageFont.truetype(path, 24)
                font = ImageFont.truetype(path, 46)
                break
            except OSError:
                continue

        # Top Brand & Pillar Badge
        draw.rectangle([48, 48, 200, 92], fill=(20, 40, 75))
        draw.text((64, 58), f"GROUNDWORK  •  {pillar.upper()}", fill=accent, font=label_font)

        # Main Title (word wrap)
        words = title.split()
        lines = []
        cur_line = []
        for w in words:
            if len(" ".join(cur_line + [w])) <= 42:
                cur_line.append(w)
            else:
                lines.append(" ".join(cur_line))
                cur_line = [w]
        if cur_line:
            lines.append(" ".join(cur_line))
        lines = lines[:4]

        y = 170
        for line in lines:
            draw.text((48, y), line, fill=(255, 255, 255), font=font)
            y += 62

        # Bottom verification footer
        draw.line([(48, TARGET_HEIGHT - 80), (TARGET_WIDTH - 48, TARGET_HEIGHT - 80)], fill=(30, 58, 100), width=2)
        draw.text(
            (48, TARGET_HEIGHT - 60),
            "gworky.com  •  Evidence-Based Research & Deep Analysis",
            fill=(140, 160, 200),
            font=label_font,
        )

        buf = io.BytesIO()
        img.save(buf, "WEBP", quality=WEBP_QUALITY)
        data = buf.getvalue()
    except Exception:
        logger.exception("Tier 4 generation failed")
        return MediaResult(errors=["tier4 generation failed"])

    key = _object_key(slug)
    if not uploader.put(key, data, "image/webp"):
        return MediaResult(errors=[f"tier4 upload failed: {key}"])
    return MediaResult(image_url=f"{MEDIA_BASE_URL}/{key}", image_source="self-hosted")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def _object_key(slug: str) -> str:
    import re
    from datetime import datetime

    safe = re.sub(r"[^a-z0-9-]+", "-", slug.lower()).strip("-")[:80] or "article"
    ym = datetime.now(UTC).strftime("%Y/%m")
    return f"{ym}/{safe}.webp"


def process_image(
    source_url: str | None,
    title: str,
    slug: str,
    pillar: str = "money",
    uploader: Uploader | None = None,
) -> MediaResult:
    """Run the 4-tier pipeline; returns the best image result available."""
    if uploader is None:
        try:
            uploader = R2Uploader()
        except RuntimeError as e:
            logger.error("R2 unavailable: %s — no image produced", e)
            return MediaResult(errors=["R2 unavailable"])

    # Tier 1: source image from feed/OG.
    if source_url:
        result = tier1_source(source_url, slug, uploader)
        if result.image_url:
            return result
        logger.info("Tier 1 failed for %s: %s", slug, result.errors)

    # Tier 2: Unsplash stock keyword fallback.
    result = tier2_unsplash(f"{pillar} {title}", slug)
    if result.image_url:
        return result
    logger.info("Tier 2 failed for %s: %s", slug, result.errors)

    # Tier 2b: Pexels / Pixabay Fallback
    result = tier2b_stock_fallback(f"{pillar} {title}", slug, uploader)
    if result.image_url:
        return result
    logger.info("Tier 2b failed for %s: %s", slug, result.errors)

    # Tier 3: Pollinations AI photorealistic visual (rich imagery, free, high CTR).
    result = tier3_pollinations(title, pillar, slug, uploader)
    if result.image_url:
        return result
    logger.info("Tier 3 failed for %s: %s", slug, result.errors)

    # Tier 4: Dynamic SVG/Canvas typography banner (ultimate fallback).
    return tier4_dynamic(title, pillar, slug, uploader)
