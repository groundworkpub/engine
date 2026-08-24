"""YouTube video uploader via OAuth refresh token (no service account needed).

Uses the resumable upload endpoint of YouTube Data API v3. Credentials come
from the environment:

    YOUTUBE_REFRESH_TOKEN      - long-lived token from one-time consent
    YOUTUBE_OAUTH_CLIENT_ID    - OAuth desktop client id
    YOUTUBE_OAUTH_CLIENT_SECRET- OAuth desktop client secret
    YOUTUBE_CHANNEL_ID         - target channel (for pre-flight verification)

CLI:
    python agents/youtube_uploader.py --video file.mp4 --title "..." \
        --description "..." --privacy private [--tags a,b] [--category 27]
"""

from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger("youtube_uploader")

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true"

DEFAULT_CATEGORY = "27"  # Education


class YouTubeUploadError(RuntimeError):
    """Raised when the upload or auth flow fails."""


def _load_env_local() -> None:
    env_file = Path(__file__).resolve().parent.parent / ".env.local"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def get_access_token(refresh_token: str | None = None) -> str:
    """Exchange the long-lived refresh token for a fresh access token."""
    rt = refresh_token or os.getenv("YOUTUBE_REFRESH_TOKEN")
    client_id = os.getenv("YOUTUBE_OAUTH_CLIENT_ID")
    client_secret = os.getenv("YOUTUBE_OAUTH_CLIENT_SECRET")
    if not (rt and client_id and client_secret):
        raise YouTubeUploadError(
            "Missing YOUTUBE_REFRESH_TOKEN / YOUTUBE_OAUTH_CLIENT_ID / YOUTUBE_OAUTH_CLIENT_SECRET"
        )
    body = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": rt,
            "grant_type": "refresh_token",
        }
    ).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise YouTubeUploadError(f"Token refresh failed: {exc.code} {exc.read().decode()[:200]}") from exc
    return str(data["access_token"])


def verify_channel(access_token: str, expected_channel_id: str | None = None) -> dict[str, Any]:
    """Confirm the token can see the target channel before uploading."""
    req = urllib.request.Request(CHANNELS_URL, headers={"Authorization": f"Bearer {access_token}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        items = json.loads(resp.read()).get("items", [])
    if not items:
        raise YouTubeUploadError("OAuth token cannot see any channel")
    channel = items[0]
    cid = channel["id"]
    if expected_channel_id and cid != expected_channel_id:
        raise YouTubeUploadError(f"Channel mismatch: got {cid}, expected {expected_channel_id}")
    logger.info("Channel verified: %s (%s)", channel["snippet"]["title"], cid)
    return {"id": cid, "title": channel["snippet"]["title"]}


def build_video_metadata(
    title: str,
    description: str,
    tags: list[str] | None = None,
    privacy_status: str = "private",
    category_id: str = DEFAULT_CATEGORY,
) -> dict[str, Any]:
    """Pure function so callers/tests can inspect exactly what gets sent."""
    return {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": (tags or [])[:30],
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }


def upload_video(
    video_path: str | Path,
    title: str,
    description: str,
    tags: list[str] | None = None,
    privacy_status: str = "private",
    category_id: str = DEFAULT_CATEGORY,
    access_token: str | None = None,
) -> dict[str, Any]:
    """Resumable single-request upload of ``video_path``. Returns the API resource."""
    path = Path(video_path)
    if not path.exists():
        raise YouTubeUploadError(f"Video file not found: {path}")

    token = access_token or get_access_token()
    expected = os.getenv("YOUTUBE_CHANNEL_ID")
    if expected:
        verify_channel(token, expected)

    meta = build_video_metadata(title, description, tags, privacy_status, category_id)
    mime = mimetypes.guess_type(str(path))[0] or "video/mp4"
    size = path.stat().st_size

    init_req = urllib.request.Request(
        UPLOAD_URL,
        data=json.dumps(meta).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(size),
            "X-Upload-Content-Type": mime,
        },
    )
    try:
        with urllib.request.urlopen(init_req, timeout=60) as resp:
            session_url = resp.headers.get("Location")
    except urllib.error.HTTPError as exc:
        raise YouTubeUploadError(f"Upload init failed: {exc.code} {exc.read().decode()[:300]}") from exc
    if not session_url:
        raise YouTubeUploadError("Resumable session URL missing from response")

    put_req = urllib.request.Request(
        session_url,
        data=path.read_bytes(),
        method="PUT",
        headers={"Content-Type": mime, "Content-Length": str(size)},
    )
    try:
        with urllib.request.urlopen(put_req, timeout=1800) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise YouTubeUploadError(f"Upload failed: {exc.code} {exc.read().decode()[:300]}") from exc

    logger.info("Uploaded: %s -> https://youtu.be/%s", title, result["id"])
    return result


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Upload a video to YouTube")
    parser.add_argument("--video", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--description", required=True)
    parser.add_argument("--tags", default="")
    parser.add_argument("--privacy", default="private", choices=["public", "unlisted", "private"])
    parser.add_argument("--category", default=DEFAULT_CATEGORY)
    args = parser.parse_args()

    _load_env_local()
    try:
        result = upload_video(
            args.video,
            args.title,
            args.description,
            tags=[t.strip() for t in args.tags.split(",") if t.strip()],
            privacy_status=args.privacy,
            category_id=args.category,
        )
    except YouTubeUploadError as exc:
        logger.error("%s", exc)
        return 1
    print(json.dumps({"id": result["id"], "url": f"https://youtu.be/{result['id']}"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
