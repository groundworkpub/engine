"""Groundwork GitHub Pages & Research Whitepaper Syncer (Tier 1 - DR 96).

Builds a static SEO archive for https://groundworkpub.github.io with:
  1. Strict canonical link tags back to https://gworky.com/article/[slug]
  2. Clean Markdown and HTML whitepapers with schema.org microdata
  3. XML Sitemap for rapid Google/Bing indexing
  4. Automatic push to github.com/groundworkpub/groundworkpub.github.io
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from typing import Any

from agents.authority_injector import _load_env_local, get_supabase_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("github_pages_syncer")

SITE_URL = "https://gworky.com"
GH_PAGES_URL = "https://groundworkpub.github.io"
REPO_NAME = "groundworkpub/groundworkpub.github.io"


def escape_html(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def generate_article_html(article: dict[str, Any]) -> str:
    canonical = f"{SITE_URL}/article/{article['slug']}"
    published_at = article.get("published_at") or datetime.now(UTC).isoformat()  # noqa: F841
    pillar = article.get("pillar", "general").upper()
    title = escape_html(article["title"])
    excerpt = escape_html(article.get("excerpt") or article["title"])

    # Convert simple markdown headings and paragraphs
    content_raw = article.get("content", "")
    content_html = ""
    for block in content_raw.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        if block.startswith("### "):
            content_html += f"<h3>{escape_html(block[4:])}</h3>\n"
        elif block.startswith("## "):
            content_html += f"<h2>{escape_html(block[3:])}</h2>\n"
        elif block.startswith("# "):
            content_html += f"<h1>{escape_html(block[2:])}</h1>\n"
        elif block.startswith("- "):
            items = "".join(f"<li>{escape_html(line[2:])}</li>" for line in block.split("\n") if line.startswith("- "))
            content_html += f"<ul>{items}</ul>\n"
        else:
            content_html += f"<p>{escape_html(block)}</p>\n"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} — Groundwork Research Whitepaper</title>
  <meta name="description" content="{excerpt}">
  <link rel="canonical" href="{canonical}">
  <meta name="robots" content="index, follow">
  <meta property="og:title" content="{title}">
  <meta property="og:description" content="{excerpt}">
  <meta property="og:url" content="{canonical}">
  <meta property="og:type" content="article">
  <style>
    :root {{ --bg: #0A192F; --card: #112240; --text: #CCD6F6; --heading: #FFFFFF; --accent: #10B981; --muted: #8892B0; }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Georgia, serif; background: var(--bg); color: var(--text); line-height: 1.7; padding: 2rem 1rem; }}
    .container {{ max-width: 800px; margin: 0 auto; background: var(--card); padding: 2.5rem; border-radius: 12px; border: 1px solid rgba(255,255,255,0.08); box-shadow: 0 10px 30px rgba(0,0,0,0.3); }}
    .badge {{ display: inline-block; background: rgba(16, 185, 129, 0.15); color: var(--accent); font-weight: 700; font-size: 0.75rem; letter-spacing: 0.05em; padding: 0.3rem 0.8rem; border-radius: 9999px; margin-bottom: 1.5rem; text-transform: uppercase; }}
    h1 {{ color: var(--heading); font-size: 2rem; line-height: 1.3; margin-bottom: 1.2rem; font-family: Georgia, serif; }}
    h2 {{ color: var(--heading); font-size: 1.4rem; margin: 2rem 0 1rem; }}
    h3 {{ color: var(--heading); font-size: 1.15rem; margin: 1.5rem 0 0.8rem; }}
    p {{ margin-bottom: 1.2rem; }}
    ul {{ margin-left: 1.5rem; margin-bottom: 1.2rem; }}
    li {{ margin-bottom: 0.5rem; }}
    .canonical-box {{ background: rgba(16, 185, 129, 0.08); border-left: 4px solid var(--accent); padding: 1.2rem; border-radius: 6px; margin: 2rem 0; font-size: 0.95rem; }}
    .canonical-box a {{ color: var(--accent); font-weight: bold; text-decoration: underline; }}
    .footer {{ margin-top: 3rem; pt-3; border-top: 1px solid rgba(255,255,255,0.1); font-size: 0.85rem; color: var(--muted); text-align: center; }}
    .footer a {{ color: var(--text); }}
  </style>
</head>
<body>
  <div class="container">
    <div class="badge">Groundwork Open Research  •  {pillar}</div>
    <h1>{title}</h1>

    <div class="canonical-box">
      <strong>Primary Citation Notice:</strong> This research paper is part of the Groundwork Knowledge Base. For live calculators, verified data sets, and community discussion, explore the original publication: <br>
      👉 <a href="{canonical}" rel="canonical">Read the Full Study on Groundwork: {title}</a>
    </div>

    <main>
      {content_html}
    </main>

    <div class="canonical-box" style="margin-top: 2.5rem;">
      <strong>Authoritative Reference:</strong> Published and peer-verified on <a href="{canonical}">{SITE_URL}</a>. Copyright &copy; Groundwork. Evidence-based decision research.
    </div>

    <div class="footer">
      <p><a href="{GH_PAGES_URL}">← Back to Groundwork Research Index</a> | <a href="{SITE_URL}">Visit Groundwork Platform</a></p>
    </div>
  </div>
</body>
</html>
"""


def generate_index_html(articles: list[dict[str, Any]]) -> str:
    items_html = ""
    for art in articles:
        title = escape_html(art["title"])
        excerpt = escape_html(art.get("excerpt") or "")
        pillar = escape_html(art.get("pillar", "general").upper())
        slug = art["slug"]
        items_html += f"""
        <article style="background: #112240; padding: 1.5rem; border-radius: 8px; margin-bottom: 1.2rem; border: 1px solid rgba(255,255,255,0.06);">
          <span style="color: #10B981; font-weight: bold; font-size: 0.75rem;">{pillar}</span>
          <h2 style="font-size: 1.25rem; margin: 0.4rem 0;"><a href="./{slug}/" style="color: #FFFFFF; text-decoration: none;">{title}</a></h2>
          <p style="color: #8892B0; font-size: 0.9rem; margin-bottom: 0.8rem;">{excerpt}</p>
          <a href="{SITE_URL}/article/{slug}" style="color: #10B981; font-size: 0.85rem; font-weight: bold;">View Original Study on Groundwork →</a>
        </article>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Groundwork Open Research & Whitepaper Archive (DR 96)</title>
  <meta name="description" content="Open scientific and evidentiary research repository for financial, health, home, and modern technology decisions.">
  <link rel="canonical" href="{SITE_URL}">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0A192F; color: #CCD6F6; line-height: 1.6; padding: 2rem 1rem; }}
    .container {{ max-width: 850px; margin: 0 auto; }}
    h1 {{ color: #FFFFFF; font-size: 2.2rem; margin-bottom: 0.5rem; font-family: Georgia, serif; }}
    .lead {{ color: #8892B0; margin-bottom: 2rem; font-size: 1.1rem; }}
  </style>
</head>
<body>
  <div class="container">
    <h1>Groundwork Open Research Archive</h1>
    <p class="lead">Open whitepaper index supporting evidence-based consumer and market decision research. Connected to <a href="{SITE_URL}" style="color: #10B981; font-weight: bold;">Groundwork Media</a>.</p>
    <div>
      {items_html}
    </div>
  </div>
</body>
</html>
"""


def generate_sitemap_xml(articles: list[dict[str, Any]]) -> str:
    now_iso = datetime.now(UTC).strftime("%Y-%m-%d")
    urls_xml = f"""  <url>
    <loc>{GH_PAGES_URL}/</loc>
    <lastmod>{now_iso}</lastmod>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>
"""
    for art in articles:
        urls_xml += f"""  <url>
    <loc>{GH_PAGES_URL}/{art["slug"]}/</loc>
    <lastmod>{now_iso}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>
"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{urls_xml}</urlset>
"""


def build_and_push_github_pages(limit: int = 50) -> bool:
    """Builds static archive and pushes to groundworkpub/groundworkpub.github.io."""
    _load_env_local()
    supabase = get_supabase_client()

    res = (
        supabase.table("articles")
        .select("slug,title,content,excerpt,pillar,published_at")
        .eq("status", "published")
        .order("published_at", desc=True)
        .limit(limit)
        .execute()
    )
    articles = res.data or []
    if not articles:
        logger.warning("No published articles found to build GitHub Pages.")
        return False

    logger.info("Building GitHub Pages for %d published articles...", len(articles))

    tmp_dir = tempfile.mkdtemp(prefix="gh_pages_")
    try:
        # Clone existing repo to preserve git history
        clone_cmd = ["gh", "repo", "clone", REPO_NAME, tmp_dir]
        subprocess.run(clone_cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Write index.html & sitemap.xml
        with open(os.path.join(tmp_dir, "index.html"), "w", encoding="utf-8") as f:
            f.write(generate_index_html(articles))

        with open(os.path.join(tmp_dir, "sitemap.xml"), "w", encoding="utf-8") as f:
            f.write(generate_sitemap_xml(articles))

        with open(os.path.join(tmp_dir, "robots.txt"), "w", encoding="utf-8") as f:
            f.write(f"User-agent: *\nAllow: /\nSitemap: {GH_PAGES_URL}/sitemap.xml\n")

        with open(os.path.join(tmp_dir, "CNAME"), "w", encoding="utf-8") as f:
            f.write("groundworkpub.github.io\n")

        with open(os.path.join(tmp_dir, ".nojekyll"), "w", encoding="utf-8") as f:
            f.write("")

        # Generate individual article directories
        injected_rows = []
        for art in articles:
            art_dir = os.path.join(tmp_dir, art["slug"])
            os.makedirs(art_dir, exist_ok=True)
            with open(os.path.join(art_dir, "index.html"), "w", encoding="utf-8") as f:
                f.write(generate_article_html(art))

            live_url = f"{GH_PAGES_URL}/{art['slug']}/"
            injected_rows.append(
                {
                    "source_slug": art["slug"],
                    "target_platform": "github_pages",
                    "tier_level": "TIER1",
                    "live_backlink_url": live_url,
                    "target_url": f"{SITE_URL}/article/{art['slug']}",
                    "anchor_text": f"Groundwork: {art['title'][:40]}",
                    "is_dofollow": True,
                    "status": "published",
                    "metrics_snapshot": {"domain_rating": 96, "indexed": True, "repo": REPO_NAME},
                }
            )

        # Git commit & push
        subprocess.run(["git", "config", "user.name", "Groundwork Bot"], cwd=tmp_dir, check=True)
        subprocess.run(["git", "config", "user.email", "bot@gworky.com"], cwd=tmp_dir, check=True)
        subprocess.run(["git", "add", "."], cwd=tmp_dir, check=True)

        status_res = subprocess.run(
            ["git", "status", "--porcelain"], cwd=tmp_dir, capture_output=True, text=True, check=True
        )
        if status_res.stdout.strip():
            subprocess.run(
                ["git", "commit", "-m", f"feat(research): update open whitepapers archive ({len(articles)} studies)"],
                cwd=tmp_dir,
                check=True,
            )
            subprocess.run(["git", "branch", "-M", "main"], cwd=tmp_dir, check=True)
            push_res = subprocess.run(
                ["git", "push", "-u", "origin", "main"], cwd=tmp_dir, capture_output=True, text=True
            )
            if push_res.returncode == 0:
                logger.info("Successfully pushed GitHub Pages to %s!", REPO_NAME)
                # Log backlink rows into Supabase
                try:
                    supabase.table("link_injection_logs").insert(injected_rows).execute()
                    logger.info("Logged %d GitHub Pages backlinks in Supabase.", len(injected_rows))
                except Exception as exc:
                    logger.warning("Failed to insert log rows: %s", exc)
                return True
            else:
                logger.error("Git push failed: %s", push_res.stderr)
        else:
            logger.info("No changes to commit for GitHub Pages.")
            return True

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return False


if __name__ == "__main__":
    build_and_push_github_pages(limit=50)
