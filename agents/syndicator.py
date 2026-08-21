"""Groundwork Syndicator (Tier 2) — canonical buffer syndication.

Publishes research executive briefs to Medium / Dev.to / Hashnode with a
strict ``rel=canonical`` (``canonical_url`` / ``originalArticleURL``) pointing
back to https://gworky.com/article/[slug], so the syndicated copy never
competes with the canonical article.

Human-gated by default: posts are created as *drafts* unless ``--publish-live``
is passed. Every post is recorded in the ``syndications`` table for auditability.

Run:  python agents/syndicator.py [--dry-run] [--platform medium devto hashnode]
"""

import argparse
import logging
import os
from datetime import UTC, datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SITE_URL = "https://gworky.com"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)

SUPPORTED_PLATFORMS = ("devto", "wordpress", "medium", "hashnode")

# Hashnode API is GraphQL; build the mutation once.
_HASHNODE_CREATE_POST = """
mutation CreatePost($input: CreatePostInput!, $publicationId: ObjectId!) {
  createPost(input: $input, publicationId: $publicationId) {
    post {
      id
      url
    }
  }
}
"""


def _supabase() -> Any:
    from supabase import create_client  # lazy: keeps module importable offline

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are required")
    return create_client(url, key)


def _fetch_published_articles(supabase: Any, min_words: int, limit: int) -> list[dict[str, Any]]:
    query = supabase.table("articles").select("id,slug,title,excerpt,takeaway,pillar,word_count,status")
    result = (
        query.eq("status", "published")
        .gte("word_count", min_words)
        .order("published_at", desc=True)
        .limit(limit)
        .execute()
    )
    return [dict(row) for row in (result.data or [])]


def _syndicated_pairs(supabase: Any) -> set[tuple[str, str]]:
    """(article_id, platform) pairs already recorded."""
    result = supabase.table("syndications").select("article_id,platform").execute()
    return {(row["article_id"], row["platform"]) for row in (result.data or [])}


def _build_brief(article: dict[str, Any]) -> str:
    """Deterministic executive brief — no LLM spend. Canonical-linked."""
    canonical = f"{SITE_URL}/article/{article['slug']}"
    title = article.get("title") or "Groundwork research"
    excerpt = (article.get("excerpt") or "").strip()
    takeaway = (article.get("takeaway") or "").strip()

    lines: list[str] = [f"# {title}", ""]
    if excerpt:
        lines += [excerpt, ""]
    if takeaway:
        lines += ["## The takeaway", "", takeaway, ""]
    lines += [
        "## Read the full analysis",
        "",
        f"The complete, evidence-based breakdown is published on Groundwork: [{canonical}]({canonical})",
        "",
        "---",
        "",
        f"*This research brief was first published by [Groundwork]({SITE_URL}) and "
        f"is republished with canonical attribution to the original analysis.*",
        "",
    ]
    return "\n".join(lines)


def _publish_medium(article: dict[str, Any], brief: str, token: str, live: bool) -> str:
    """Create a Medium draft (or live post) with canonicalUrl. Returns URL."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    with httpx.Client(timeout=TIMEOUT, headers=headers) as client:
        me = client.get("https://api.medium.com/v1/me")
        me.raise_for_status()
        author_id = me.json()["data"]["id"]
        payload = {
            "title": article["title"],
            "contentFormat": "markdown",
            "content": brief,
            "canonicalUrl": f"{SITE_URL}/article/{article['slug']}",
            "publishStatus": "public" if live else "draft",
            "tags": ["research", article.get("pillar", "groundwork")],
        }
        resp = client.post(f"https://api.medium.com/v1/users/{author_id}/posts", json=payload)
        resp.raise_for_status()
        return resp.json()["data"]["url"]


def _publish_devto(article: dict[str, Any], brief: str, token: str, live: bool) -> str:
    """Create a Dev.to draft (or live article) with canonical_url. Returns URL."""
    headers = {"api-key": token, "Content-Type": "application/json"}
    payload = {
        "article": {
            "title": article["title"],
            "body_markdown": brief,
            "canonical_url": f"{SITE_URL}/article/{article['slug']}",
            "published": live,
            "tags": ["research", article.get("pillar", "groundwork")],
        }
    }
    with httpx.Client(timeout=TIMEOUT, headers=headers) as client:
        resp = client.post("https://dev.to/api/articles", json=payload)
        resp.raise_for_status()
        return resp.json()["url"]


def _publish_hashnode(
    article: dict[str, Any],
    brief: str,
    token: str,
    publication_id: str,
    live: bool,
) -> str:
    """Create a Hashnode post with originalArticleURL (canonical). Returns URL."""
    headers = {"Authorization": token, "Content-Type": "application/json"}
    payload = {
        "query": _HASHNODE_CREATE_POST,
        "variables": {
            "publicationId": publication_id,
            "input": {
                "title": article["title"],
                "contentMarkdown": brief,
                "originalArticleURL": f"{SITE_URL}/article/{article['slug']}",
                "draft": not live,
            },
        },
    }
    with httpx.Client(timeout=TIMEOUT, headers=headers) as client:
        resp = client.post("https://api.hashnode.com/v1/graphql", json=payload)
        resp.raise_for_status()
        data = resp.json()
        if data.get("errors"):
            raise RuntimeError(data["errors"][0].get("message", "unknown GraphQL error"))
        return data["data"]["createPost"]["post"]["url"]


def _record_syndication(
    supabase: Any,
    article_id: str,
    platform: str,
    external_url: str,
    live: bool,
    canonical_url: str,
    error_log: str | None = None,
) -> None:
    row = {
        "article_id": article_id,
        "platform": platform,
        "external_url": external_url,
        "canonical_url": canonical_url,
        "status": "published" if (live and not error_log) else ("draft" if not error_log else "failed"),
        "error_log": error_log,
        "syndicated_at": datetime.now(UTC).isoformat() if not error_log else None,
    }
    supabase.table("syndications").upsert(row, on_conflict="article_id,platform").execute()


def _log_run(supabase: Any, status: str, items_processed: int, items_published: int, error_log: str | None) -> None:
    try:
        supabase.table("pipeline_runs").insert(
            {
                "agent": "syndicator",
                "status": status,
                "items_processed": items_processed,
                "items_published": items_published,
                "error_log": error_log,
                "run_at": datetime.now(UTC).isoformat(),
            }
        ).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to write pipeline_runs: %s", exc)


def run_syndicator(
    supabase: Any,
    platforms: list[str],
    min_words: int,
    limit: int,
    live: bool,
    dry_run: bool = False,
) -> tuple[int, int]:
    """Publish executive briefs for unsyndicated published articles.

    Returns (articles_considered, posts_created). Skips articles already
    recorded for the requested platform(s).
    """
    articles = _fetch_published_articles(supabase, min_words, limit) if supabase is not None else []
    pairs = _syndicated_pairs(supabase) if supabase is not None else set()

    created = 0
    for article in articles:
        brief = _build_brief(article)
        for platform in platforms:
            if (article["id"], platform) in pairs:
                logger.info("skip %s → %s (already syndicated)", article["slug"], platform)
                continue
            if dry_run:
                logger.info("[dry-run] would publish %s → %s", article["slug"], platform)
                created += 1
                continue
            try:
                external_url = _publish_to_platform(article, brief, platform, live)
                _record_syndication(
                    supabase,
                    article["id"],
                    platform,
                    external_url,
                    live,
                    canonical_url=f"{SITE_URL}/article/{article['slug']}",
                )
                created += 1
                logger.info("published %s → %s (%s)", article["slug"], platform, external_url)
            except Exception as exc:  # noqa: BLE001
                logger.warning("failed %s → %s: %s", article["slug"], platform, exc)
                if not dry_run:
                    try:
                        _record_syndication(
                            supabase,
                            article["id"],
                            platform,
                            "",
                            live,
                            canonical_url=f"{SITE_URL}/article/{article['slug']}",
                            error_log=str(exc)[:500],
                        )
                    except Exception:  # noqa: BLE001
                        logger.warning("could not record failure for %s", article["slug"])

    logger.info("syndicator: considered=%s created=%s", len(articles), created)
    return len(articles), created


def _publish_wordpress(article: dict[str, Any], brief: str, live: bool) -> str:
    """Create a WordPress post with canonical link using WordPress REST API."""
    wp_url = (os.getenv("WORDPRESS_URL") or "https://emailforums.biz").rstrip("/")
    wp_user = os.getenv("WORDPRESS_USERNAME") or ""
    wp_app_pwd = os.getenv("WORDPRESS_APPLICATION_PASSWORD") or ""

    if not wp_user or not wp_app_pwd:
        raise RuntimeError("WORDPRESS_USERNAME / WORDPRESS_APPLICATION_PASSWORD are not configured")

    endpoint = f"{wp_url}/wp-json/wp/v2/posts"
    # Format markdown body to HTML with canonical footer
    html_content = f"{brief.replace(chr(10), '<br/>')}<p><em>Canonical source: <a href='{SITE_URL}/article/{article['slug']}'>{SITE_URL}/article/{article['slug']}</a></em></p>"
    payload = {
        "title": article["title"],
        "content": html_content,
        "status": "publish" if live else "draft",
        "slug": f"research-{article['slug']}",
    }
    import base64

    auth_header = base64.b64encode(f"{wp_user}:{wp_app_pwd}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=TIMEOUT, headers=headers) as client:
        resp = client.post(endpoint, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data.get("link", f"{wp_url}/?p={data.get('id')}")


def _publish_to_platform(article: dict[str, Any], brief: str, platform: str, live: bool) -> str:
    """Dispatch to the platform publisher using configured API tokens."""
    if platform == "wordpress":
        return _publish_wordpress(article, brief, live)
    if platform == "devto":
        token = os.getenv("DEVTO_API_KEY")
        if not token:
            raise RuntimeError("DEVTO_API_KEY is not configured")
        return _publish_devto(article, brief, token, live)
    if platform == "medium":
        token = os.getenv("MEDIUM_API_TOKEN")
        if not token:
            raise RuntimeError("MEDIUM_API_TOKEN is not configured")
        return _publish_medium(article, brief, token, live)
    if platform == "hashnode":
        token = os.getenv("HASHNODE_API_TOKEN")
        publication_id = os.getenv("HASHNODE_PUBLICATION_ID")
        if not token or not publication_id:
            raise RuntimeError("HASHNODE_API_TOKEN / HASHNODE_PUBLICATION_ID are not configured")
        return _publish_hashnode(article, brief, token, publication_id, live)
    raise ValueError(f"unsupported platform: {platform}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Groundwork Syndicator — canonical buffer syndication (Medium/Dev.to/Hashnode)"
    )
    parser.add_argument(
        "--platform",
        nargs="*",
        default=list(SUPPORTED_PLATFORMS),
        choices=list(SUPPORTED_PLATFORMS),
        help="Platforms to syndicate to (default: all three)",
    )
    parser.add_argument("--min-words", type=int, default=800, help="Min article word_count to syndicate")
    parser.add_argument("--limit", type=int, default=10, help="Max articles per run (default 10)")
    parser.add_argument("--publish-live", action="store_true", help="Publish live instead of drafts")
    parser.add_argument("--dry-run", action="store_true", help="Preview what would be syndicated")
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()

    try:
        supabase = _supabase()
    except RuntimeError as exc:
        if args.dry_run:
            logger.info("[dry-run] skipping DB: %s", exc)
            supabase = None
        else:
            logger.error("DB unavailable: %s", exc)
            return 1

    try:
        processed, created = run_syndicator(
            supabase,
            args.platform,
            args.min_words,
            args.limit,
            args.publish_live,
            dry_run=args.dry_run,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("syndicator failed: %s", exc)
        if supabase is not None and not args.dry_run:
            _log_run(supabase, "error", 0, 0, str(exc)[:500])
        return 1

    if supabase is not None and not args.dry_run:
        status = "success" if created > 0 or processed == 0 else "partial"
        _log_run(supabase, status, processed, created, None)
    if args.dry_run:
        print(f"[dry-run] would syndicate {created} post(s) from {processed} article(s)")
        return 0
    logger.info("done: considered=%s created=%s", processed, created)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
