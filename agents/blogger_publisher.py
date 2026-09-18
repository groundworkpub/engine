"""Groundwork Blogger API v3 Autonomous Publisher

Publishes synthesized buffer articles with embedded sticky Groundwork
utility widgets to Blogspot properties via Google Blogger API v3 ($0 USD).

Features:
1. Multi-Node Fleet Support: Reads configurations directly from config/buffer_nodes.json.
2. Google Service Account authentication (zero token expiration / zero browser dependency).
3. Graceful Local Staging: If blog_id is pending, stages HTML and reports readiness without error.
4. Live backlink logging to Supabase public.link_injection_logs (tier_level: "tier2").
5. Pure observational Telegram telemetry (@gwelena_bot).

Usage:
    python agents/blogger_publisher.py --dry-run
    python agents/blogger_publisher.py --pillar money
    python agents/blogger_publisher.py --blog-id "1234567890" --html-path "scratch/buffer_posts/post.html" --pillar money
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx


# Load environment
def _load_env_local() -> None:
    root_env = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env.local")
    if os.path.exists(root_env):
        with open(root_env, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip("'").strip('"')
                if k not in os.environ:
                    os.environ[k] = v

_load_env_local()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("blogger_publisher")

BLOGGER_SCOPES = ["https://www.googleapis.com/auth/blogger"]
CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "buffer_nodes.json")


def load_fleet_config() -> dict[str, Any]:
    """Loads the 5-pillar buffer fleet configuration."""
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load {CONFIG_PATH}: {e}")
    return {}


def get_service_account_credentials() -> dict[str, Any] | None:
    """Loads Google Service Account JSON from environment or file."""
    raw_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw_json:
        try:
            return json.loads(raw_json)
        except Exception as e:
            logger.warning(f"Failed to parse GOOGLE_SERVICE_ACCOUNT_JSON env var: {e}")

    file_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if file_path and os.path.exists(file_path):
        try:
            with open(file_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load service account file from {file_path}: {e}")

    root_dir = os.path.dirname(os.path.dirname(__file__))
    candidates = [
        os.path.join(root_dir, "service_account.json"),
        os.path.join(root_dir, "google_credentials.json"),
        os.path.join(root_dir, "credentials", "service_account.json"),
    ]
    for c in candidates:
        if os.path.exists(c):
            try:
                with open(c, encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.debug(f"Failed to load {c}: {e}")

    return None


def get_blogger_client(credentials_info: dict[str, Any]) -> Any:
    """Builds the authorized Blogger API v3 service resource."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds = service_account.Credentials.from_service_account_info(
        credentials_info,
        scopes=BLOGGER_SCOPES,
    )
    return build("blogger", "v3", credentials=creds)


def publish_post_to_blogger(
    blog_id: str,
    title: str,
    content_html: str,
    labels: list[str] | None = None,
    is_draft: bool = False,
    credentials_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Inserts and publishes a post to a Google Blogger blog."""
    creds_data = credentials_info or get_service_account_credentials()
    if not creds_data:
        logger.warning("No Google Service Account credentials found. Operation cannot proceed.")
        return {"success": False, "error": "missing_google_service_account_credentials"}

    try:
        service = get_blogger_client(creds_data)
        body = {
            "kind": "blogger#post",
            "title": title,
            "content": content_html,
            "labels": labels or ["Groundwork Research", "Data Analysis"],
        }

        logger.info(f"Publishing post to Blog ID: {blog_id} (Title: '{title[:50]}...')")
        posts_resource = service.posts()
        request = posts_resource.insert(blogId=blog_id, body=body, isDraft=is_draft)
        response = request.execute()

        post_id = response.get("id")
        post_url = response.get("url")
        status = response.get("status", "LIVE")

        logger.info(f"Post published successfully! ID: {post_id}, URL: {post_url}")
        return {
            "success": True,
            "post_id": post_id,
            "post_url": post_url,
            "status": status,
            "blog_id": blog_id,
        }
    except Exception as e:
        logger.error(f"Failed to publish to Blogger API v3: {e}")
        return {"success": False, "error": str(e)}


def log_blogger_post_to_supabase(
    pillar: str,
    title: str,
    live_url: str,
    blog_id: str,
    post_id: str,
) -> bool:
    """Records published buffer post into Supabase link_injection_logs."""
    supabase_url = os.getenv("NEXT_PUBLIC_SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not supabase_key:
        return False

    now_iso = datetime.now(UTC).isoformat()
    record = {
        "source_slug": f"blogger-{pillar}-{post_id}",
        "target_platform": "blogger",
        "tier_level": "tier2",
        "live_backlink_url": live_url,
        "target_url": f"https://www.gworky.com/{pillar}",
        "anchor_text": f"Groundwork {pillar.capitalize()} Calculator",
        "is_dofollow": True,
        "status": "published",
        "metrics_snapshot": {
            "title": title,
            "pillar": pillar,
            "blog_id": blog_id,
            "remote_post_id": post_id,
            "published_at": now_iso,
        },
        "created_at": now_iso,
        "updated_at": now_iso,
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{supabase_url}/rest/v1/link_injection_logs",
                headers={
                    "apikey": supabase_key,
                    "Authorization": f"Bearer {supabase_key}",
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal",
                },
                json=record,
            )
            return resp.status_code in (200, 201)
    except Exception as e:
        logger.warning(f"Failed to log blogger post to Supabase: {e}")
        return False


def send_telegram_blogger_report(
    pillar: str,
    title: str,
    live_url: str,
    post_id: str,
) -> None:
    """Emits pure observational telemetry to Telegram when a buffer post is live."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_FOUNDER_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        return

    msg = (
        f"📢 <b>[BLOGGER PUBLISHER] BUFFER POST LIVE!</b>\n\n"
        f"• <b>Pillar:</b> <code>{pillar.upper()}</code>\n"
        f"• <b>Post Title:</b> <i>{title[:65]}...</i>\n"
        f"• <b>Live URL:</b> <a href=\"{live_url}\">{urlparse(live_url).netloc}</a>\n"
        f"• <b>Post ID:</b> <code>{post_id}</code>\n"
        f"• <b>Embedded Utility:</b> Groundwork Sticky Calculator Widget\n\n"
        f"<i>Tier-2 backlink active on Google Blogspot ecosystem. Recorded in Supabase.</i>"
    )

    telegram_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": True}

    try:
        with httpx.Client(timeout=8.0) as client:
            client.post(telegram_url, json=payload)
    except Exception as e:
        logger.warning(f"Telegram telemetry delivery notice: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Groundwork Blogger API v3 Autonomous Publisher")
    parser.add_argument("--blog-id", type=str, default=None, help="Google Blogger Blog ID")
    parser.add_argument("--html-path", type=str, default=None, help="Path to packaged HTML article file")
    parser.add_argument("--pillar", choices=["money", "body", "home", "life", "tech"], default="money")
    parser.add_argument("--dry-run", action="store_true", help="Simulate Blogger API publishing and inspect credentials")
    args = parser.parse_args()

    creds = get_service_account_credentials()
    fleet_cfg = load_fleet_config().get("nodes", {})
    pillar_node = fleet_cfg.get(args.pillar, {})
    resolved_blog_id = args.blog_id or pillar_node.get("blog_id")

    if args.dry_run:
        logger.info("Executing dry-run verification for Blogger API Publisher...")
        print(json.dumps({
            "dry_run": True,
            "pillar": args.pillar,
            "configured_nodes_found": list(fleet_cfg.keys()),
            "node_status": pillar_node.get("status", "unknown"),
            "resolved_blog_id": resolved_blog_id or "PENDING_REGISTRATION",
            "google_service_account_configured": creds is not None,
            "client_email": creds.get("client_email") if creds else "NOT_CONFIGURED",
            "project_id": creds.get("project_id") if creds else "NOT_CONFIGURED",
            "scopes": BLOGGER_SCOPES,
            "message": "When blog_id is set and Service Account email is added as Author, live publishing will execute seamlessly."
        }, indent=2))
        return

    # Check if blog_id is pending
    if not resolved_blog_id:
        logger.info(f"Pillar '{args.pillar}' node is currently in '{pillar_node.get('status', 'staging')}' mode without an active blog_id.")
        print(json.dumps({
            "success": False,
            "status": "staged_locally",
            "pillar": args.pillar,
            "message": f"Buffer node for {args.pillar} is in staging mode. HTML digests are staged in scratch/buffer_posts/. Add blog_id to config/buffer_nodes.json to enable live publishing."
        }, indent=2))
        return

    if not args.html_path or not os.path.exists(args.html_path):
        logger.error(f"HTML article path invalid: {args.html_path}")
        sys.exit(1)

    with open(args.html_path, encoding="utf-8") as f:
        html_content = f.read()

    title_match = re.search(r"<title>(.*?)</title>", html_content, re.I)
    title = title_match.group(1).strip() if title_match else f"Groundwork Research: {args.pillar.capitalize()} Intelligence"

    res = publish_post_to_blogger(
        blog_id=resolved_blog_id,
        title=title,
        content_html=html_content,
        labels=[f"Groundwork {args.pillar.capitalize()}", "Research Guide", "Decision Tools"],
        credentials_info=creds,
    )

    if res.get("success"):
        log_blogger_post_to_supabase(
            pillar=args.pillar,
            title=title,
            live_url=res.get("post_url", ""),
            blog_id=resolved_blog_id,
            post_id=res.get("post_id", ""),
        )
        send_telegram_blogger_report(
            pillar=args.pillar,
            title=title,
            live_url=res.get("post_url", ""),
            post_id=res.get("post_id", ""),
        )

    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
