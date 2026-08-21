"""Smoke tests for the media pipeline (Agent 4) — no network or R2 required.

Run with:  pip install -r requirements.txt pytest && pytest agents/tests/
"""

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from PIL import Image

import media_uploader as mu

# ─── helpers ────────────────────────────────────────────────────────────────


def make_png(width: int, height: int) -> bytes:
    img = Image.new("RGB", (width, height), (200, 60, 60))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class FakeUploader:
    """Records put() calls; never touches real R2."""

    def __init__(self):
        self.calls = []

    def put(self, key: str, data: bytes, content_type: str) -> bool:
        self.calls.append((key, content_type))
        return True


# ─── Tier 1: source image ───────────────────────────────────────────────────


def test_tier1_uploads_webp_and_returns_public_url(monkeypatch):
    monkeypatch.setattr(mu, "fetch_bytes", lambda url: make_png(1600, 900))
    up = FakeUploader()
    result = mu.tier1_source(
        "https://example.com/source.png",
        "how-to-refinance-a-mortgage",
        up,
    )
    assert result.image_source == "self-hosted"
    assert result.image_url == f"{mu.MEDIA_BASE_URL}/{up.calls[0][0]}"
    assert up.calls[0][1] == "image/webp"


def test_tier1_rejects_small_image(monkeypatch):
    monkeypatch.setattr(mu, "fetch_bytes", lambda url: make_png(320, 240))
    up = FakeUploader()
    result = mu.tier1_source("https://example.com/small.png", "slug", up)
    assert result.image_url == ""
    assert up.calls == []


def test_tier1_reports_fetch_failure(monkeypatch):
    monkeypatch.setattr(mu, "fetch_bytes", lambda url: None)
    up = FakeUploader()
    result = mu.tier1_source("https://example.com/gone.png", "slug", up)
    assert result.image_url == ""
    assert result.errors
    assert up.calls == []


# ─── Tier 2: Unsplash (hotlink + ping + attribution) ────────────────────────


def test_tier2_requires_access_key(monkeypatch):
    monkeypatch.setattr(mu, "UNSPLASH_ACCESS_KEY", "")
    result = mu.tier2_unsplash("mortgage rates", "slug")
    assert result.image_url == ""
    assert "UNSPLASH_ACCESS_KEY" in result.errors[0]


def test_tier2_returns_hotlink_credit_and_pings(monkeypatch):
    monkeypatch.setattr(mu, "UNSPLASH_ACCESS_KEY", "demo-key")
    pings = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "results": [
                    {
                        "urls": {"regular": "https://images.unsplash.com/photo-abc?w=1080"},
                        "links": {
                            "download_location": "https://api.unsplash.com/photos/abc/download",
                            "html": "https://unsplash.com/photos/abc",
                        },
                        "user": {"name": "Jane Doe", "links": {"html": "https://unsplash.com/@janedoe"}},
                    }
                ]
            }

    def fake_get(url, **kwargs):
        if "download" in url:
            pings.append((url, kwargs.get("params", {}).get("client_id")))
            return type("Dl", (), {"status_code": 204})()
        return FakeResponse()

    monkeypatch.setattr(mu.httpx, "get", fake_get)

    result = mu.tier2_unsplash("mortgage rates", "slug")

    assert result.image_source == "unsplash"
    assert result.image_url.startswith("https://images.unsplash.com/")
    assert result.image_credit is not None
    assert result.image_credit["photographer"] == "Jane Doe"
    assert result.image_credit["utm_source"] == "gworky"
    # Mandatory download ping fired with client_id (Unsplash API Guidelines #2)
    assert len(pings) == 1
    assert pings[0][1] == "demo-key"


# ─── process_image orchestration ────────────────────────────────────────────


def test_process_image_falls_through_tiers(monkeypatch):
    up = FakeUploader()
    monkeypatch.setattr(mu, "tier1_source", lambda *a, **k: mu.MediaResult(errors=["tier1 failed"]))
    monkeypatch.setattr(
        mu,
        "tier2_unsplash",
        lambda *a, **k: mu.MediaResult(
            image_url="https://images.unsplash.com/hotlink",
            image_source="unsplash",
            image_credit={"photographer": "Jane Doe"},
        ),
    )
    result = mu.process_image("https://src.test/og.jpg", "A title", "slug", uploader=up)
    assert result.image_source == "unsplash"
    assert result.image_url == "https://images.unsplash.com/hotlink"


def test_process_image_tier1_wins_when_available(monkeypatch):
    up = FakeUploader()

    def fake_tier1(url, slug, uploader):
        return mu.MediaResult(image_url="https://media.gworky.com/2026/08/slug.webp", image_source="self-hosted")

    monkeypatch.setattr(mu, "tier1_source", fake_tier1)
    monkeypatch.setattr(mu, "tier2_unsplash", lambda *a, **k: pytest.fail("tier2 must not run when tier1 wins"))
    result = mu.process_image("https://src.test/og.jpg", "A title", "slug", uploader=up)
    assert result.image_source == "self-hosted"


def test_process_image_without_r2_env_returns_error():
    monkeypatch = pytest.MonkeyPatch()
    for var in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(var, raising=False)
    result = mu.process_image(None, "A title", "slug")
    assert result.image_url == ""
    assert result.errors == ["R2 unavailable"]
    monkeypatch.undo()


# ─── helpers ────────────────────────────────────────────────────────────────


def test_object_key_sanitizes_slug():
    key = mu._object_key("How To Refinance A Mortgage! 2026")
    assert key.startswith("2026/")
    assert key.endswith("how-to-refinance-a-mortgage-2026.webp")
    assert " " not in key


def test_crop_cover_produces_1200x675_webp():
    data = mu._crop_cover(make_png(3000, 1500))
    assert data is not None
    with Image.open(io.BytesIO(data)) as img:
        assert img.format == "WEBP"
        assert (img.width, img.height) == (1200, 675)


# ─── backfill helpers ────────────────────────────────────────────────────────


def test_backfill_updates_only_reprocessable_rows(monkeypatch):
    import backfill_images as bi

    calls = []

    class Supabase:
        def table(self, name):
            return self

        def select(self, cols):
            return self

        def eq(self, k, v):
            return self

        def or_(self, expr):
            calls.append(expr)
            return self

        def limit(self, n):
            return type("R", (), {"execute": lambda self: type("E", (), {"data": []})()})()

    result = bi.fetch_candidates(Supabase(), limit=25)
    assert result == []
    assert "image_url.not.like" in calls[0]
    assert "image_source.is.null" in calls[0]
