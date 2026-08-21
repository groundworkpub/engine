"""Groundwork WordPress Satellite Publisher (Agent 4d)

Publishes gworky.com articles to emailforums.biz via WP REST API.
Syncs published articles from Supabase, transforms them for WordPress,
sets canonical → gworky.com, pings Google sitemap, and requests GSC indexing.

Usage:
    python agents/wp_publisher.py --limit 10
    python agents/wp_publisher.py --slug mortgage-rates-forecast-2026
    python agents/wp_publisher.py --sync-all --dry-run
    python agents/wp_publisher.py --status
    python agents/wp_publisher.py --gsc-ping "https://emailforums.biz/slug/"
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import os
import re
import sys
import time
import urllib.parse
from datetime import UTC, datetime
from typing import Any

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("wp_publisher")

SITE_URL = os.getenv("NEXT_PUBLIC_SITE_URL", "https://gworky.com").rstrip("/")
WP_URL = "https://emailforums.biz"
WP_API = f"{WP_URL}/wp-json/wp/v2"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)

CATEGORY_MAP = {
    "money": 2,
    "body": 3,
    "home": 4,
    "life": 5,
    "tech": 6,
}


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


def get_supabase():
    from supabase import create_client

    url = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY required")
    return create_client(url, key)


def get_wp_client() -> httpx.Client:
    username = "gworky"
    password = "CEN6 D02Q 32Ci RUe6 QJLf JNTc"
    return httpx.Client(
        base_url=WP_API,
        auth=(username, password),
        timeout=TIMEOUT,
        headers={"User-Agent": "Groundwork-Publisher/1.0"},
    )


def compute_content_hash(content: str) -> str:
    cleaned = re.sub(r"<[^>]+>", "", content)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return hashlib.md5(cleaned.encode()).hexdigest()


def _format_inline(text: str, base_url: str) -> str:
    """Format bold, italic, and links in inline text."""
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)

    def _link_sub(match):
        label, url = match.group(1), match.group(2)
        if url.startswith("/"):
            url = f"{base_url.rstrip('/')}{url}"
        return f'<a href="{url}" target="_blank" rel="noopener">{label}</a>'

    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', _link_sub, text)
    return text


def markdown_to_html(md: str, base_url: str = SITE_URL) -> str:
    """Convert Markdown to clean, semantic HTML with full absolute URLs for WordPress."""
    if not md:
        return ""
    base = base_url.rstrip("/")

    # 1. Absolutify relative markdown links: [Text](/path) -> [Text](https://gworky.com/path)
    md = re.sub(r'\]\(/([^/][^)]*)\)', rf']({base}/\1)', md)
    # 2. Absolutify HTML links & images if present
    md = re.sub(r'href="/([^/][^"]*)"', rf'href="{base}/\1"', md)
    md = re.sub(r'src="/([^/][^"]*)"', rf'src="{base}/\1"', md)

    # 3. Parse block-level elements
    blocks = md.split("\n\n")
    html_blocks = []

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        if block.startswith("#### "):
            html_blocks.append(f"<h4>{_format_inline(block[5:].strip(), base)}</h4>")
        elif block.startswith("### "):
            html_blocks.append(f"<h3>{_format_inline(block[4:].strip(), base)}</h3>")
        elif block.startswith("## "):
            html_blocks.append(f"<h2>{_format_inline(block[3:].strip(), base)}</h2>")
        elif block.startswith("# "):
            html_blocks.append(f"<h1>{_format_inline(block[2:].strip(), base)}</h1>")
        elif block.startswith("- ") or block.startswith("* "):
            items = []
            for line in block.split("\n"):
                line_s = line.strip()
                if line_s.startswith("- ") or line_s.startswith("* "):
                    items.append(f"<li>{_format_inline(line_s[2:].strip(), base)}</li>")
            html_blocks.append("<ul>\n" + "\n".join(items) + "\n</ul>")
        elif re.match(r'^\d+\.\s', block):
            items = []
            for line in block.split("\n"):
                line_s = line.strip()
                m = re.match(r'^\d+\.\s*(.+)', line_s)
                if m:
                    items.append(f"<li>{_format_inline(m.group(1).strip(), base)}</li>")
            html_blocks.append("<ol>\n" + "\n".join(items) + "\n</ol>")
        elif block.startswith("> "):
            quote_text = "\n".join(l.lstrip("> ").strip() for l in block.split("\n"))
            html_blocks.append(f"<blockquote><p>{_format_inline(quote_text, base)}</p></blockquote>")
        else:
            para_lines = [_format_inline(l.strip(), base) for l in block.split("\n") if l.strip()]
            html_blocks.append(f"<p>{'<br />'.join(para_lines)}</p>")

    return "\n\n".join(html_blocks)


def transform_article_for_wp(article: dict[str, Any]) -> dict[str, Any]:
    """Transform gworky.com article for emailforums.biz WordPress."""
    raw_content = article.get("content", "")
    excerpt = article.get("excerpt", "")
    title = article.get("title", "")
    pillar = article.get("pillar", "tech")
    slug = article.get("slug", "")

    # Convert markdown to clean semantic HTML with absolute URLs
    html_content = markdown_to_html(raw_content, SITE_URL)

    cross_link_html = (
        f'<p style="margin-top:2em;padding:1em;background:#f8f9fa;border-left:4px solid #2563eb;">'
        f'<strong>Originally published on <a href="{SITE_URL}/article/{slug}" target="_blank" rel="noopener">'
        f'Groundwork</a></strong> — '
        f'evidence-based guides for smarter money, health, career, and living decisions.</p>'
    )
    content = html_content.rstrip() + "\n\n" + cross_link_html

    if excerpt and len(excerpt) > 155:
        excerpt = excerpt[:152].rsplit(" ", 1)[0] + "..."

    category_id = CATEGORY_MAP.get(pillar, 6)

    wp_data = {
        "title": title,
        "content": content,
        "excerpt": excerpt,
        "status": "publish",
        "categories": [category_id],
        "slug": slug,
        "comment_status": "open",
        "ping_status": "open",
    }

    if article.get("schema_type"):
        wp_data["meta"] = {
            "rank_math_focus_keyword": article.get("sub_topic", ""),
        }

    return wp_data


def fetch_unsynced_articles(supabase, limit: int = 10) -> list[dict]:
    """Fetch published articles from Supabase not yet synced."""
    published = (
        supabase.table("articles")
        .select("id,slug,title,content,excerpt,pillar,sub_topic,schema_type,published_at,source_url")
        .eq("status", "published")
        .order("published_at", desc=True)
        .limit(limit * 3)
        .execute()
    )
    articles = published.data or []

    wp = get_wp_client()
    existing_slugs = set()
    offset = 0
    while True:
        resp = wp.get("/posts", params={"per_page": 100, "offset": offset, "_fields": "slug"})
        if resp.status_code != 200:
            break
        batch = resp.json()
        if not batch:
            break
        existing_slugs.update(p["slug"] for p in batch)
        if len(batch) < 100:
            break
        offset += 100

    unsynced = [a for a in articles if a["slug"] not in existing_slugs]
    logger.info(f"Found {len(unsynced)} unsynced articles (of {len(articles)} published)")
    return unsynced[:limit]


def set_canonical_via_yoast(wp: httpx.Client, post_id: int, canonical_url: str) -> bool:
    """Set Yoast canonical URL for a post via Yoast REST API extension."""
    resp = wp.post(
        f"/yoast/v1/posts/{post_id}/canonical",
        json={"canonical": canonical_url},
    )
    if resp.status_code in (200, 201, 204):
        return True
    resp2 = wp.post(
        f"/yoast/v1/meta/{post_id}",
        json={"meta": {"_yoast_wpseo_canonical": canonical_url}},
    )
    return resp2.status_code in (200, 201, 204)


def publish_article(wp: httpx.Client, article: dict[str, Any], dry_run: bool = False) -> dict:
    wp_data = transform_article_for_wp(article)
    slug = article["slug"]

    if dry_run:
        logger.info(f"[DRY RUN] Would publish: {slug}")
        return {"slug": slug, "status": "dry_run"}

    existing = wp.get("/posts", params={"slug": slug, "_fields": "id"})
    if existing.status_code == 200 and existing.json():
        post_id = existing.json()[0]["id"]
        resp = wp.post(f"/posts/{post_id}", json=wp_data)
        action = "updated"
    else:
        resp = wp.post("/posts", json=wp_data)
        action = "created"

    if resp.status_code in (200, 201):
        result = resp.json()
        post_id = result.get("id")
        canonical_url = f"{SITE_URL}/article/{slug}"

        set_canonical_via_yoast(wp, post_id, canonical_url)

        request_gsc_indexing([result.get("link", "")])
        submit_indexnow([result.get("link", "")])

        logger.info(f"✅ {action}: {slug} → {result.get('link', 'N/A')} (id={post_id})")
        return {"slug": slug, "status": action, "wp_id": post_id, "url": result.get("link")}
    else:
        logger.error(f"❌ Failed: {slug} — {resp.status_code}: {resp.text[:200]}")
        return {"slug": slug, "status": "error", "code": resp.status_code, "error": resp.text[:200]}


def ping_google_sitemap(sitemap_url: str) -> bool:
    """Google deprecated /ping?sitemap= in 2023. Use GSC Indexing API instead (see request_gsc_indexing)."""
    logger.debug("Google sitemap ping deprecated — using GSC Indexing API")
    return False


def submit_indexnow(urls: list[str]) -> dict:
    """Submit URLs to IndexNow API (Bing, Yandex, Naver, etc.)."""
    try:
        api_key = os.getenv("INDEXNOW_API_KEY", "d1c63d16e77e4e60b8f6e6c47e1e9f4d")
        key_location = f"https://emailforums.biz/{api_key}.txt"

        resp = httpx.post(
            "https://api.indexnow.org/indexnow",
            json={
                "host": "emailforums.biz",
                "key": api_key,
                "keyLocation": key_location,
                "urlList": urls,
            },
            headers={"Content-Type": "application/json"},
            timeout=10.0,
        )
        if resp.status_code == 200:
            logger.info(f"📢 IndexNow submitted: {len(urls)} URL(s)")
            return {"status": "ok", "submitted": len(urls)}
        logger.warning(f"⚠️ IndexNow failed: {resp.status_code}")
        return {"status": "error", "code": resp.status_code}
    except Exception as e:
        logger.warning(f"⚠️ IndexNow error: {e}")
        return {"status": "error", "error": str(e)}


def get_gsc_access_token() -> str | None:
    """Get OAuth2 access token for GSC Indexing API using service account."""
    try:
        import jwt as pyjwt

        b64_key = os.getenv("GSC_SERVICE_ACCOUNT_JSON_B64")
        if not b64_key:
            root_env = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env.local")
            if os.path.exists(root_env):
                with open(root_env) as f:
                    for line in f:
                        if line.startswith("GSC_SERVICE_ACCOUNT_JSON_B64="):
                            b64_key = line.strip().split("=", 1)[1]
                            break
        if not b64_key:
            return None

        creds = json.loads(base64.b64decode(b64_key))
        now = time.time()
        token = pyjwt.encode({
            "iss": creds["client_email"],
            "scope": "https://www.googleapis.com/auth/indexing",
            "aud": "https://oauth2.googleapis.com/token",
            "iat": int(now),
            "exp": int(now) + 3600,
        }, creds["private_key"], algorithm="RS256")

        resp = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": token},
            timeout=10.0,
        )
        return resp.json().get("access_token")
    except Exception as e:
        logger.warning(f"⚠️ GSC token error: {e}")
        return None


def request_gsc_indexing(urls: list[str]) -> dict:
    """Request indexing for URLs via GSC Indexing API."""
    token = get_gsc_access_token()
    if not token:
        return {"error": "no_token"}

    results = {"success": 0, "errors": 0}
    for url in urls:
        try:
            resp = httpx.post(
                "https://indexing.googleapis.com/v3/urlNotifications:publish",
                json={"url": url, "type": "URL_UPDATED"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=10.0,
            )
            if resp.status_code == 200:
                results["success"] += 1
                logger.info(f"🔍 GSC indexing requested: {url}")
            else:
                results["errors"] += 1
                logger.warning(f"⚠️ GSC indexing failed for {url}: {resp.status_code}")
        except Exception as e:
            results["errors"] += 1
            logger.warning(f"⚠️ GSC indexing error for {url}: {e}")

    return results


def show_status() -> None:
    wp = get_wp_client()
    supabase = get_supabase()

    published = supabase.table("articles").select("id", count="exact").eq("status", "published").execute()
    total_gworky = published.count or 0

    total_wp = 0
    offset = 0
    while True:
        resp = wp.get("/posts", params={"per_page": 100, "offset": offset, "_fields": "id"})
        if resp.status_code != 200:
            break
        batch = resp.json()
        total_wp += len(batch)
        if len(batch) < 100:
            break
        offset += 100

    unsynced = fetch_unsynced_articles(supabase, limit=999)

    print(f"\n📊 WP Publisher Status")
    print(f"{'─' * 40}")
    print(f"  gworky.com published:  {total_gworky}")
    print(f"  emailforums.biz posts: {total_wp}")
    print(f"  Unsynced:              {len(unsynced)}")
    print(f"  Site:                  {WP_URL}")
    print()


def add_internal_links(dry_run: bool = False, limit: int = 50) -> dict:
    """Add contextual internal cross-links within emailforums.biz posts by category.

    Strategy:
    1. Fetch all posts with their categories
    2. Group posts by category
    3. For each post, find 2-3 related posts in the same category
    4. Add contextual anchor links in the content (before </p> or </h2> tags)
    5. Update via WP REST API
    """
    wp = get_wp_client()

    # 1. Fetch all posts with categories
    all_posts = []
    offset = 0
    while True:
        resp = wp.get("/posts", params={
            "per_page": 100,
            "offset": offset,
            "_fields": "id,slug,title,content,categories,link",
        })
        if resp.status_code != 200:
            break
        batch = resp.json()
        if not batch:
            break
        all_posts.extend(batch)
        if len(batch) < 100:
            break
        offset += 100

    logger.info(f"Fetched {len(all_posts)} posts for internal linking")

    # 2. Group by category
    cat_posts: dict[int, list[dict]] = {}
    for post in all_posts:
        for cat_id in post.get("categories", []):
            cat_posts.setdefault(cat_id, []).append(post)

    # 3. Build link map: for each post, find 2-3 siblings in same category
    updates = []
    for post in all_posts:
        post_cats = post.get("categories", [])
        if not post_cats:
            continue

        # Pick the category with most siblings
        best_cat = max(post_cats, key=lambda c: len(cat_posts.get(c, [])))
        siblings = [s for s in cat_posts.get(best_cat, []) if s["id"] != post["id"]]

        if not siblings:
            continue

        # Pick 2-3 random siblings (different from current post)
        import random
        random.seed(post["id"])  # Deterministic per post
        link_targets = random.sample(siblings, min(3, len(siblings)))

        # Check if post already has internal links (avoid duplicates)
        content = post.get("content", {})
        if isinstance(content, dict):
            content = content.get("rendered", "")
        existing_links = content.count(f"{WP_URL}/")
        if existing_links >= 3:
            continue

        # 4. Add contextual anchor links
        h2_pattern = re.compile(r"</h2>", re.IGNORECASE)
        matches = list(h2_pattern.finditer(content))

        # Build related links HTML
        related_html = '<div class="internal-links" style="margin:1.5em 0;padding:1em;background:#f8f9fa;border-left:3px solid #0073aa;border-radius:4px;">'
        related_html += '<p style="font-weight:600;margin:0 0 0.5em;color:#333;">Related reads:</p><ul style="margin:0;padding-left:1.2em;">'
        for target in link_targets:
            title = target.get("title", {})
            if isinstance(title, dict):
                title = title.get("rendered", "Untitled")
            title = re.sub(r"<[^>]+>", "", str(title))
            related_html += f'<li><a href="{target["link"]}" target="_blank" rel="noopener">{title}</a></li>'
        related_html += "</ul></div>"

        if matches:
            # Insert after the second H2 or before the last H2
            insert_pos = matches[min(1, len(matches) - 1)].end()
            new_content = content[:insert_pos] + related_html + content[insert_pos:]
        elif "</p>" in content:
            # No H2 — insert after the last </p>
            last_p = content.rfind("</p>")
            if last_p > 0:
                insert_pos = last_p + 4
                new_content = content[:insert_pos] + related_html + content[insert_pos:]
            else:
                new_content = content + related_html
        else:
            new_content = content + related_html

        updates.append({
            "id": post["id"],
            "slug": post["slug"],
            "content": new_content,
            "link": post["link"],
        })

        if len(updates) >= limit:
            break

    logger.info(f"Prepared {len(updates)} posts for internal linking")

    if dry_run:
        for u in updates:
            logger.info(f"[DRY RUN] Would add links to: {u['slug']}")
        return {"updated": 0, "dry_run": len(updates)}

    # 5. Update posts via WP REST API
    success = 0
    errors = 0
    for u in updates:
        resp = wp.post(f"/posts/{u['id']}", json={"content": u["content"]})
        if resp.status_code == 200:
            success += 1
            logger.info(f"🔗 Internal links added: {u['slug']}")
        else:
            errors += 1
            logger.warning(f"⚠️ Failed to add links to {u['slug']}: {resp.status_code}")

    return {"updated": success, "errors": errors}


def publish_expired_routes(supabase: Any, wp: httpx.Client, limit: int = 10, dry_run: bool = False) -> list[dict]:
    """Publish modernized expired domain routes (status=AI_REWRITING) to WordPress satellite."""
    res = supabase.table("expired_routes").select("*").eq("status", "AI_REWRITING").limit(limit).execute()
    routes = res.data or []
    logger.info(f"Found {len(routes)} expired routes ready for WordPress publishing.")

    results = []
    for r in routes:
        title = r.get("historical_title") or "Archived Research Topic"
        content = r.get("historical_content") or ""
        pillar = r.get("target_pillar") or "money"
        cat_id = CATEGORY_MAP.get(pillar, 2)
        html_content = markdown_to_html(content)

        if dry_run:
            logger.info(f"[DRY-RUN] Would publish expired route: {title}")
            results.append({"id": r["id"], "title": title, "status": "dry_run"})
            continue

        try:
            payload = {
                "title": title,
                "content": html_content,
                "status": "publish",
                "categories": [cat_id],
            }
            resp = wp.post("/posts", json=payload)
            if resp.status_code in (200, 201):
                post_data = resp.json()
                supabase.table("expired_routes").update({
                    "status": "WP_PUBLISHED",
                    "http_status_code": 200,
                }).eq("id", r["id"]).execute()
                logger.info(f"✅ Published expired route: {title} -> {post_data.get('link')}")
                results.append({"id": r["id"], "title": title, "status": "published", "link": post_data.get("link")})
            else:
                logger.error(f"Failed to publish route {r['id']}: {resp.status_code}")
                results.append({"id": r["id"], "title": title, "status": "error", "code": resp.status_code})
        except Exception as e:
            logger.error(f"Exception publishing route {r['id']}: {e}")
            results.append({"id": r["id"], "title": title, "status": "error", "error": str(e)})

    return results


def main():
    _load_env_local()

    parser = argparse.ArgumentParser(description="Groundwork WP Satellite Publisher")
    parser.add_argument("--limit", type=int, default=10, help="Max articles to sync")
    parser.add_argument("--slug", type=str, help="Publish a specific article by slug")
    parser.add_argument("--sync-all", action="store_true", help="Sync all unsynced articles")
    parser.add_argument("--sync-expired", action="store_true", help="Sync AI_REWRITING expired routes")
    parser.add_argument("--dry-run", action="store_true", help="Preview without publishing")
    parser.add_argument("--status", action="store_true", help="Show sync status")
    parser.add_argument("--gsc-ping", type=str, help="Ping GSC Indexing API for a URL")
    parser.add_argument("--internal-links", action="store_true", help="Add internal cross-links within posts")
    parser.add_argument("--internal-links-limit", type=int, default=50, help="Max posts to add links to")
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    supabase = get_supabase()
    wp = get_wp_client()

    if args.gsc_ping:
        urls = [u.strip() for u in args.gsc_ping.split(",")]
        result = request_gsc_indexing(urls)
        submit_indexnow(urls)
        print(json.dumps(result, indent=2))
        return

    if args.internal_links:
        result = add_internal_links(dry_run=args.dry_run, limit=args.internal_links_limit)
        print(json.dumps(result, indent=2))
        return

    if args.slug:
        res = supabase.table("articles").select("*").eq("slug", args.slug).maybe_single().execute()
        if not res.data:
            logger.error(f"Article not found: {args.slug}")
            sys.exit(1)
        result = publish_article(wp, res.data, dry_run=args.dry_run)
        print(json.dumps(result, indent=2))
        return

    limit = 999 if args.sync_all else args.limit
    articles = fetch_unsynced_articles(supabase, limit=limit)

    if not articles:
        logger.info("All articles synced — nothing to do")
        return

    results = []
    for article in articles:
        result = publish_article(wp, article, dry_run=args.dry_run)
        results.append(result)

    created = sum(1 for r in results if r["status"] == "created")
    updated = sum(1 for r in results if r["status"] == "updated")
    errors = sum(1 for r in results if r["status"] == "error")

    print(f"\n{'─' * 40}")
    print(f"  Created: {created} | Updated: {updated} | Errors: {errors}")
    print(f"{'─' * 40}")


if __name__ == "__main__":
    main()
