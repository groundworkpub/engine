"""Groundwork Open Guides & Research Syncer.

Human-Centric Editorial Architecture:
  - Clean, legible typography and generous whitespace.
  - Zero internal SEO jargon, zero fourth-wall leaks, zero synthetic persona framing.
  - Natural category navigation: Money, Health, Home, Tech, Life, Digests.
  - Standard, valid Schema.org Article JSON-LD, sitemap, and RSS feed.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agents.authority_injector import _load_env_local, get_supabase_client
from agents.authority_linker import FLAGSHIP_ARTICLES, GROUNDWORK_TOOLS, PILLAR_HUBS, match_groundwork_resource
from agents.feed_generator import generate_rss_feed
from agents.news_harvester import NewsItem, harvest_all_sources
from agents.og_generator import generate_og_svg

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("github_pages_syncer")

SITE_URL = "https://gworky.com"
GH_PAGES_URL = "https://groundworkpub.github.io"
REPO_NAME = "groundworkpub/groundworkpub.github.io"
GSC_VERIFICATION_META = '<meta name="google-site-verification" content="XmlqHCUNSmjcRtPkLXSDciZNvtWivVpmTT4B0nfN0wg" />'

PILLAR_DISPLAY_NAMES = {
    "money": "Money & Finance",
    "body": "Health & Body",
    "home": "Home & Energy",
    "tech": "Technology",
    "life": "Life & Career",
    "digest": "Research Digests",
}


def escape_html(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def compute_reading_time(text: str) -> tuple[int, int]:
    words = len(re.findall(r"\w+", text))
    minutes = max(1, round(words / 200))
    return words, minutes


def get_nav_header_html(active_pillar: str = "all", root_prefix: str = ".") -> str:
    p_links = []
    for p, label in [
        ("money", "Money"),
        ("body", "Health"),
        ("home", "Home"),
        ("tech", "Tech"),
        ("life", "Life"),
        ("digest", "Digests"),
    ]:
        active_cls = "nav-link active" if p == active_pillar else "nav-link"
        p_links.append(f'<a href="{root_prefix}/{p}/" class="{active_cls}">{label}</a>')
    
    links_html = "\n".join(p_links)

    return f"""
  <header class="site-header">
    <div class="header-inner">
      <div class="header-brand">
        <a href="{root_prefix}/" class="brand-logo">
          <span class="logo-mark">✦</span>
          <strong>Groundwork</strong>
        </a>
      </div>
      <nav class="header-nav" aria-label="Main Navigation">
        <a href="{root_prefix}/" class="nav-link {'active' if active_pillar == 'all' else ''}">All</a>
        {links_html}
        <a href="{SITE_URL}" target="_blank" rel="noopener" class="nav-link gworky-link">gworky.com ↗</a>
      </nav>
    </div>
    <div id="reading-progress" class="reading-progress-bar"></div>
  </header>
"""


def generate_article_html(
    article: dict[str, Any],
    sibling_articles: list[dict[str, Any]] | None = None,
    prev_article: dict[str, Any] | None = None,
    next_article: dict[str, Any] | None = None,
) -> str:
    canonical = f"{SITE_URL}/article/{article['slug']}"
    raw_pillar = article.get("pillar", "general").lower()
    pillar_label = PILLAR_DISPLAY_NAMES.get(raw_pillar, raw_pillar.capitalize())
    title = escape_html(article["title"])
    raw_title = article["title"]
    excerpt = escape_html(article.get("excerpt") or article["title"])
    slug = article["slug"]
    now_iso = datetime.now(UTC).strftime("%Y-%m-%d")
    og_image_url = f"{GH_PAGES_URL}/{slug}/og.svg"

    content_raw = article.get("content", "")
    word_count, read_time = compute_reading_time(f"{raw_title} {content_raw}")

    linked = match_groundwork_resource(article["title"], raw_pillar, excerpt, sibling_articles)

    toc_items = []
    content_html = ""
    heading_counter = 0
    for block in content_raw.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        if block.startswith("### "):
            heading_counter += 1
            h_id = f"heading-{heading_counter}"
            h_text = block[4:]
            toc_items.append((h_id, h_text, 3))
            content_html += f'<h3 id="{h_id}">{escape_html(h_text)}</h3>\n'
        elif block.startswith("## "):
            heading_counter += 1
            h_id = f"heading-{heading_counter}"
            h_text = block[3:]
            toc_items.append((h_id, h_text, 2))
            content_html += f'<h2 id="{h_id}">{escape_html(h_text)}</h2>\n'
        elif block.startswith("# "):
            content_html += f"<h1>{escape_html(block[2:])}</h1>\n"
        elif block.startswith("- "):
            items = "".join(f"<li>{escape_html(line[2:])}</li>" for line in block.split("\n") if line.startswith("- "))
            content_html += f"<ul>{items}</ul>\n"
        else:
            content_html += f"<p>{escape_html(block)}</p>\n"

    toc_html = ""
    if len(toc_items) >= 2:
        toc_lis = "".join(
            f'<li style="margin-left: {(level-2)*12}px; margin-bottom: 0.35rem;"><a href="#{hid}" style="color: #8892B0; text-decoration: none; font-size: 0.88rem;">{escape_html(htext)}</a></li>'
            for hid, htext, level in toc_items
        )
        toc_html = f"""
        <aside class="toc-card" aria-label="Table of Contents">
          <h4 style="color: #FFFFFF; font-size: 0.92rem; margin-bottom: 0.6rem; font-weight: 600;">
            In This Guide
          </h4>
          <ul style="list-style: none; padding-left: 0; margin-left: 0;">
            {toc_lis}
          </ul>
        </aside>
        """

    prev_html = (
        f'<a href="../{prev_article["slug"]}/" class="nav-card prev-card"><span class="nav-dir">← Previous</span><span class="nav-title">{escape_html(prev_article["title"])}</span></a>'
        if prev_article else '<div class="nav-card-empty"></div>'
    )
    next_html = (
        f'<a href="../{next_article["slug"]}/" class="nav-card next-card"><span class="nav-dir">Next →</span><span class="nav-title">{escape_html(next_article["title"])}</span></a>'
        if next_article else '<div class="nav-card-empty"></div>'
    )
    nav_cards_html = f'<nav class="study-pagination" aria-label="Pagination">{prev_html}{next_html}</nav>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  {GSC_VERIFICATION_META}
  <title>{title} — Groundwork</title>
  <meta name="description" content="{excerpt}">
  <link rel="canonical" href="{canonical}">
  <meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1">
  
  <meta property="og:title" content="{title}">
  <meta property="og:description" content="{excerpt}">
  <meta property="og:url" content="{canonical}">
  <meta property="og:type" content="article">
  <meta property="og:site_name" content="Groundwork">
  <meta property="og:image" content="{og_image_url}">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta property="article:published_time" content="{now_iso}">
  <meta property="article:section" content="{pillar_label}">
  
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{title}">
  <meta name="twitter:description" content="{excerpt}">
  <meta name="twitter:image" content="{og_image_url}">

  <!-- Standard Valid Schema.org Article -->
  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "Article",
    "headline": "{title}",
    "description": "{excerpt}",
    "image": "{og_image_url}",
    "url": "{canonical}",
    "datePublished": "{now_iso}",
    "dateModified": "{now_iso}",
    "wordCount": {word_count},
    "author": {{
      "@type": "Organization",
      "name": "Groundwork Editorial",
      "url": "{SITE_URL}"
    }},
    "publisher": {{
      "@type": "Organization",
      "name": "Groundwork",
      "url": "{SITE_URL}"
    }}
  }}
  </script>
  <style>
    :root {{
      --bg: #0A192F;
      --card: #112240;
      --card-alt: #162B4D;
      --text: #CCD6F6;
      --heading: #FFFFFF;
      --accent: #10B981;
      --muted: #8892B0;
      --border: rgba(255, 255, 255, 0.08);
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Georgia, serif; background: var(--bg); color: var(--text); line-height: 1.8; padding-top: 64px; }}
    
    .site-header {{ position: fixed; top: 0; left: 0; right: 0; background: rgba(10, 25, 47, 0.92); backdrop-filter: blur(10px); border-bottom: 1px solid var(--border); z-index: 1000; }}
    .header-inner {{ max-width: 1040px; margin: 0 auto; display: flex; justify-content: space-between; align-items: center; padding: 0.75rem 1.25rem; }}
    .brand-logo {{ color: #FFFFFF; text-decoration: none; font-size: 1.05rem; display: flex; align-items: center; gap: 6px; }}
    .logo-mark {{ color: var(--accent); }}
    
    .header-nav {{ display: flex; gap: 8px; align-items: center; }}
    .nav-link {{ color: var(--muted); text-decoration: none; font-size: 0.85rem; font-weight: 500; padding: 4px 10px; border-radius: 6px; transition: all 0.2s; }}
    .nav-link:hover, .nav-link.active {{ color: #FFFFFF; background: rgba(255, 255, 255, 0.06); }}
    .gworky-link {{ color: var(--accent) !important; border: 1px solid rgba(16, 185, 129, 0.25); }}
    .reading-progress-bar {{ height: 2px; background: var(--accent); width: 0%; transition: width 0.1s ease-out; }}
    
    .container {{ max-width: 820px; margin: 2rem auto; background: var(--card); padding: 2.5rem; border-radius: 12px; border: 1px solid var(--border); }}
    
    .breadcrumbs {{ font-size: 0.82rem; color: var(--muted); margin-bottom: 1.2rem; display: flex; gap: 6px; align-items: center; }}
    .breadcrumbs a {{ color: var(--muted); text-decoration: none; }}
    .breadcrumbs a:hover {{ color: var(--accent); }}
    
    .badge {{ display: inline-block; background: rgba(16, 185, 129, 0.1); color: var(--accent); font-weight: 600; font-size: 0.75rem; letter-spacing: 0.05em; padding: 0.25rem 0.75rem; border-radius: 4px; text-transform: uppercase; margin-bottom: 0.8rem; }}
    h1 {{ color: var(--heading); font-size: 2.15rem; line-height: 1.3; margin-bottom: 1rem; font-family: Georgia, serif; font-weight: normal; }}
    
    .article-meta {{ display: flex; gap: 16px; align-items: center; flex-wrap: wrap; font-size: 0.85rem; color: var(--muted); margin-bottom: 2rem; padding-bottom: 1rem; border-bottom: 1px solid var(--border); }}
    
    .takeaway-box {{ background: rgba(16, 185, 129, 0.05); border-left: 3px solid var(--accent); border-radius: 6px; padding: 1.25rem 1.5rem; margin-bottom: 2rem; }}
    .takeaway-box h4 {{ color: #FFFFFF; font-size: 0.9rem; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.4rem; font-weight: 600; }}
    .takeaway-box p {{ font-size: 1rem; color: var(--text); margin-bottom: 0; line-height: 1.6; }}

    .toc-card {{ background: var(--card-alt); border: 1px solid var(--border); border-radius: 6px; padding: 1.2rem; margin: 2rem 0; }}
    .toc-card a:hover {{ color: var(--accent); }}

    .article-prose h2 {{ color: var(--heading); font-size: 1.4rem; margin: 2.2rem 0 0.8rem; font-family: Georgia, serif; font-weight: normal; }}
    .article-prose h3 {{ color: var(--heading); font-size: 1.15rem; margin: 1.6rem 0 0.6rem; }}
    .article-prose p {{ margin-bottom: 1.2rem; font-size: 1.05rem; }}
    .article-prose ul {{ margin-left: 1.5rem; margin-bottom: 1.2rem; }}
    .article-prose li {{ margin-bottom: 0.4rem; font-size: 1.02rem; }}

    .study-pagination {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin: 3rem 0 1rem; }}
    .nav-card {{ background: var(--card-alt); border: 1px solid var(--border); padding: 1.2rem; border-radius: 8px; text-decoration: none; display: flex; flex-direction: column; gap: 4px; transition: all 0.2s; }}
    .nav-card:hover {{ border-color: var(--accent); }}
    .next-card {{ text-align: right; }}
    .nav-dir {{ font-size: 0.75rem; color: var(--accent); text-transform: uppercase; font-weight: 600; }}
    .nav-title {{ color: #FFFFFF; font-size: 0.9rem; font-weight: 500; line-height: 1.4; }}
    
    .footer {{ margin-top: 3rem; padding-top: 1.5rem; border-top: 1px solid var(--border); font-size: 0.82rem; color: var(--muted); text-align: center; }}
    .footer a {{ color: var(--text); text-decoration: none; }}
    .footer a:hover {{ color: var(--accent); }}

    @media (max-width: 640px) {{
      .container {{ padding: 1.5rem 1rem; }}
      h1 {{ font-size: 1.7rem; }}
      .header-nav {{ display: none; }}
      .study-pagination {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  {get_nav_header_html(active_pillar=raw_pillar, root_prefix="..")}

  <div class="container">
    <nav class="breadcrumbs" aria-label="Breadcrumb">
      <a href="../">All</a> <span>/</span>
      <a href="../{raw_pillar}/">{pillar_label}</a> <span>/</span>
      <span style="color: #CCD6F6;">{title[:35]}...</span>
    </nav>

    <div class="badge">{pillar_label}</div>
    <h1>{title}</h1>

    <div class="article-meta">
      <span>By <strong>Groundwork Editorial</strong></span>
      <span>•</span>
      <span>{read_time} min read</span>
      <span>•</span>
      <span>Updated {now_iso}</span>
    </div>

    <div class="takeaway-box">
      <h4>Key Takeaway</h4>
      <p>{excerpt}</p>
    </div>

    {toc_html}

    <main class="article-prose">
      {content_html}
    </main>

    <!-- Companion Tool Callout -->
    {linked.decision_matrix_html}

    <!-- Next / Previous Navigation -->
    {nav_cards_html}

    <footer class="footer">
      <p><a href="../">← All Guides</a> | <a href="../{raw_pillar}/">More in {pillar_label}</a> | <a href="{SITE_URL}">Groundwork</a></p>
    </footer>
  </div>

  <script>
    window.addEventListener('scroll', () => {{
      const winScroll = document.documentElement.scrollTop;
      const height = document.documentElement.scrollHeight - document.documentElement.clientHeight;
      const scrolled = (winScroll / height) * 100;
      const bar = document.getElementById('reading-progress');
      if (bar) bar.style.width = scrolled + '%';
    }});
  </script>
</body>
</html>
"""


def generate_news_digest_html(
    item: NewsItem,
    sibling_articles: list[dict[str, Any]] | None = None,
    prev_digest: NewsItem | None = None,
    next_digest: NewsItem | None = None,
) -> str:
    raw_pillar = item.pillar.lower()
    pillar_label = PILLAR_DISPLAY_NAMES.get(raw_pillar, raw_pillar.capitalize())
    title = escape_html(item.title)
    desc = escape_html(item.description)
    source = escape_html(item.source)
    slug = item.slug
    now_iso = datetime.now(UTC).strftime("%Y-%m-%d")
    og_image_url = f"{GH_PAGES_URL}/digest/{slug}/og.svg"

    linked = match_groundwork_resource(item.title, raw_pillar, item.description, sibling_articles)

    prev_html = (
        f'<a href="../{prev_digest.slug}/" class="nav-card prev-card"><span class="nav-dir">← Previous</span><span class="nav-title">{escape_html(prev_digest.title)}</span></a>'
        if prev_digest else '<div class="nav-card-empty"></div>'
    )
    next_html = (
        f'<a href="../{next_digest.slug}/" class="nav-card next-card"><span class="nav-dir">Next →</span><span class="nav-title">{escape_html(next_digest.title)}</span></a>'
        if next_digest else '<div class="nav-card-empty"></div>'
    )
    nav_cards_html = f'<nav class="study-pagination" aria-label="Pagination">{prev_html}{next_html}</nav>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  {GSC_VERIFICATION_META}
  <title>{title} — Groundwork</title>
  <meta name="description" content="{desc}">
  <link rel="canonical" href="{GH_PAGES_URL}/digest/{slug}/">
  <meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1">
  
  <meta property="og:title" content="{title}">
  <meta property="og:description" content="{desc}">
  <meta property="og:type" content="article">
  <meta property="og:site_name" content="Groundwork">
  <meta property="og:image" content="{og_image_url}">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta property="article:published_time" content="{now_iso}">

  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{title}">
  <meta name="twitter:description" content="{desc}">
  <meta name="twitter:image" content="{og_image_url}">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "NewsArticle",
    "headline": "{title}",
    "description": "{desc}",
    "image": "{og_image_url}",
    "datePublished": "{now_iso}",
    "publisher": {{
      "@type": "Organization",
      "name": "Groundwork",
      "url": "{SITE_URL}"
    }}
  }}
  </script>
  <style>
    :root {{
      --bg: #0A192F;
      --card: #112240;
      --card-alt: #162B4D;
      --text: #CCD6F6;
      --heading: #FFFFFF;
      --accent: #10B981;
      --muted: #8892B0;
      --border: rgba(255, 255, 255, 0.08);
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Georgia, serif; background: var(--bg); color: var(--text); line-height: 1.8; padding-top: 64px; }}
    
    .site-header {{ position: fixed; top: 0; left: 0; right: 0; background: rgba(10, 25, 47, 0.92); backdrop-filter: blur(10px); border-bottom: 1px solid var(--border); z-index: 1000; }}
    .header-inner {{ max-width: 1040px; margin: 0 auto; display: flex; justify-content: space-between; align-items: center; padding: 0.75rem 1.25rem; }}
    .brand-logo {{ color: #FFFFFF; text-decoration: none; font-size: 1.05rem; display: flex; align-items: center; gap: 6px; }}
    .logo-mark {{ color: var(--accent); }}
    
    .header-nav {{ display: flex; gap: 8px; align-items: center; }}
    .nav-link {{ color: var(--muted); text-decoration: none; font-size: 0.85rem; font-weight: 500; padding: 4px 10px; border-radius: 6px; transition: all 0.2s; }}
    .nav-link:hover, .nav-link.active {{ color: #FFFFFF; background: rgba(255, 255, 255, 0.06); }}
    .gworky-link {{ color: var(--accent) !important; border: 1px solid rgba(16, 185, 129, 0.25); }}
    
    .container {{ max-width: 820px; margin: 2rem auto; background: var(--card); padding: 2.5rem; border-radius: 12px; border: 1px solid var(--border); }}
    .breadcrumbs {{ font-size: 0.82rem; color: var(--muted); margin-bottom: 1.2rem; display: flex; gap: 6px; align-items: center; }}
    .breadcrumbs a {{ color: var(--muted); text-decoration: none; }}
    .breadcrumbs a:hover {{ color: var(--accent); }}
    
    .badge {{ display: inline-block; background: rgba(16, 185, 129, 0.1); color: var(--accent); font-weight: 600; font-size: 0.75rem; letter-spacing: 0.05em; padding: 0.25rem 0.75rem; border-radius: 4px; text-transform: uppercase; margin-bottom: 0.8rem; }}
    h1 {{ color: var(--heading); font-size: 1.95rem; line-height: 1.35; margin-bottom: 0.8rem; font-family: Georgia, serif; font-weight: normal; }}
    .source-meta {{ color: var(--muted); font-size: 0.85rem; margin-bottom: 1.8rem; padding-bottom: 0.8rem; border-bottom: 1px solid var(--border); }}
    
    .synthesis-card {{ background: var(--card-alt); border: 1px solid var(--border); padding: 1.5rem; border-radius: 8px; margin-bottom: 2rem; font-size: 1.05rem; }}
    
    .study-pagination {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin: 3rem 0 1rem; }}
    .nav-card {{ background: var(--card-alt); border: 1px solid var(--border); padding: 1.2rem; border-radius: 8px; text-decoration: none; display: flex; flex-direction: column; gap: 4px; transition: all 0.2s; }}
    .nav-card:hover {{ border-color: var(--accent); }}
    .next-card {{ text-align: right; }}
    .nav-dir {{ font-size: 0.75rem; color: var(--accent); text-transform: uppercase; font-weight: 600; }}
    .nav-title {{ color: #FFFFFF; font-size: 0.9rem; font-weight: 500; line-height: 1.4; }}
    
    .footer {{ margin-top: 3rem; padding-top: 1.5rem; border-top: 1px solid var(--border); font-size: 0.82rem; color: var(--muted); text-align: center; }}
    .footer a {{ color: var(--text); text-decoration: none; }}
    .footer a:hover {{ color: var(--accent); }}
  </style>
</head>
<body>
  {get_nav_header_html(active_pillar="digest", root_prefix="../..")}

  <div class="container">
    <nav class="breadcrumbs" aria-label="Breadcrumb">
      <a href="../../">All</a> <span>/</span>
      <a href="../">Digests</a> <span>/</span>
      <span style="color: #CCD6F6;">{title[:35]}...</span>
    </nav>

    <div class="badge">{pillar_label} Digest</div>
    <h1>{title}</h1>
    <div class="source-meta">Reported via {source} • {now_iso}</div>

    <div class="synthesis-card">
      <p>{desc}</p>
    </div>

    <!-- Companion Tool Callout -->
    {linked.decision_matrix_html}

    {nav_cards_html}

    <footer class="footer">
      <p><a href="../../">← All Guides</a> | <a href="../">All Digests</a> | <a href="{SITE_URL}">Groundwork</a></p>
    </footer>
  </div>
</body>
</html>
"""


def generate_index_html(
    articles: list[dict[str, Any]],
    news_digests: list[NewsItem],
    filter_pillar: str = "all",
    root_prefix: str = ".",
) -> str:
    filtered_articles = (
        articles if filter_pillar == "all"
        else [a for a in articles if a.get("pillar", "").lower() == filter_pillar.lower()]
    )
    filtered_digests = (
        news_digests if filter_pillar in ("all", "digest")
        else [d for d in news_digests if d.pillar.lower() == filter_pillar.lower()]
    )

    # Clean Domain Directory Grid
    hubs_grid_html = ""
    for p_key, hub in PILLAR_HUBS.items():
        flagships = FLAGSHIP_ARTICLES.get(p_key, [])
        flagship_title = flagships[0]["title"] if flagships else f"{hub['title']} Guide"
        flagship_url = flagships[0]["url"] if flagships else hub["url"]

        tools = GROUNDWORK_TOOLS.get(p_key, [])
        tool_title = tools[0]["title"] if tools else "Calculator"
        tool_url = tools[0]["url"] if tools else f"{SITE_URL}/tools"

        hubs_grid_html += f"""
        <div style="background: #112240; border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; padding: 1.25rem; display: flex; flex-direction: column; justify-content: space-between;">
          <div>
            <span style="color: #10B981; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; font-weight: 600;">{p_key.upper()}</span>
            <h3 style="font-size: 1.05rem; margin: 0.25rem 0 0.4rem; font-weight: 600;"><a href="{root_prefix}/{p_key}/" style="color: #FFFFFF; text-decoration: none;">{hub['title']}</a></h3>
            <p style="color: #8892B0; font-size: 0.85rem; margin-bottom: 0.8rem; line-height: 1.5;">{hub['headline']}</p>
          </div>
          <div style="border-top: 1px solid rgba(255,255,255,0.06); padding-top: 0.6rem; font-size: 0.8rem; display: flex; flex-direction: column; gap: 4px;">
            <a href="{flagship_url}" style="color: #CCD6F6; text-decoration: none;">• Guide: {flagship_title[:38]}... &rarr;</a>
            <a href="{tool_url}" style="color: #10B981; text-decoration: none;">• Calculator: {tool_title[:38]}... &rarr;</a>
          </div>
        </div>
        """

    all_cards_html = ""
    for dig in (news_digests if filter_pillar == "all" else filtered_digests):
        title = escape_html(dig.title)
        desc = escape_html(dig.description)
        pillar = escape_html(dig.pillar.lower())
        source = escape_html(dig.source)
        slug = dig.slug
        target_href = f"{root_prefix}/digest/{slug}/"
        all_cards_html += f"""
        <article class="study-card" data-pillar="{pillar}" data-type="digest" data-search="{title.lower()} {desc.lower()} {pillar}">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.3rem;">
            <span class="card-pill pill-digest">DIGEST • {pillar.upper()}</span>
            <span style="color: #8892B0; font-size: 0.78rem;">{source}</span>
          </div>
          <h3 style="font-size: 1.15rem; margin: 0.2rem 0 0.4rem; line-height: 1.4; font-weight: 500;"><a href="{target_href}" style="color: #FFFFFF; text-decoration: none;">{title}</a></h3>
          <p style="color: #8892B0; font-size: 0.9rem; margin-bottom: 0.5rem; line-height: 1.5;">{desc}</p>
          <a href="{target_href}" style="color: #10B981; font-size: 0.82rem; font-weight: 500; text-decoration: none;">Read Digest &rarr;</a>
        </article>
        """

    for art in (articles if filter_pillar == "all" else filtered_articles):
        title = escape_html(art["title"])
        excerpt = escape_html(art.get("excerpt") or "")
        pillar = escape_html(art.get("pillar", "general").lower())
        slug = art["slug"]
        target_href = f"{root_prefix}/{slug}/"
        all_cards_html += f"""
        <article class="study-card" data-pillar="{pillar}" data-type="study" data-search="{title.lower()} {excerpt.lower()} {pillar}">
          <div style="margin-bottom: 0.3rem;">
            <span class="card-pill pill-study">{pillar.upper()}</span>
          </div>
          <h3 style="font-size: 1.15rem; margin: 0.2rem 0 0.4rem; line-height: 1.4; font-weight: 500;"><a href="{target_href}" style="color: #FFFFFF; text-decoration: none;">{title}</a></h3>
          <p style="color: #8892B0; font-size: 0.9rem; margin-bottom: 0.5rem; line-height: 1.5;">{excerpt}</p>
          <a href="{SITE_URL}/article/{slug}" style="color: #10B981; font-size: 0.82rem; font-weight: 500; text-decoration: none;">Read on Groundwork &rarr;</a>
        </article>
        """

    page_heading = (
        "Practical Guides & Research" if filter_pillar == "all"
        else f"{PILLAR_DISPLAY_NAMES.get(filter_pillar, filter_pillar.capitalize())}"
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  {GSC_VERIFICATION_META}
  <title>{page_heading} — Groundwork</title>
  <meta name="description" content="Clear, evidence-backed guides and decision utilities for personal finance, health, home systems, and technology.">
  <link rel="canonical" href="{SITE_URL if filter_pillar == 'all' else f'{SITE_URL}/{filter_pillar}'}">
  <link rel="alternate" type="application/rss+xml" title="Groundwork RSS Feed" href="{GH_PAGES_URL}/feed.xml" />
  <meta name="robots" content="index, follow, max-image-preview:large">
  
  <meta property="og:title" content="{page_heading} — Groundwork">
  <meta property="og:description" content="Clear, evidence-backed guides and decision calculators.">
  <meta property="og:image" content="{GH_PAGES_URL}/og.svg">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  
  <style>
    :root {{
      --bg: #0A192F;
      --card: #112240;
      --card-alt: #162B4D;
      --text: #CCD6F6;
      --heading: #FFFFFF;
      --accent: #10B981;
      --muted: #8892B0;
      --border: rgba(255, 255, 255, 0.08);
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Georgia, serif; background: var(--bg); color: var(--text); line-height: 1.7; padding-top: 64px; }}
    
    .site-header {{ position: fixed; top: 0; left: 0; right: 0; background: rgba(10, 25, 47, 0.92); backdrop-filter: blur(10px); border-bottom: 1px solid var(--border); z-index: 1000; }}
    .header-inner {{ max-width: 1040px; margin: 0 auto; display: flex; justify-content: space-between; align-items: center; padding: 0.75rem 1.25rem; }}
    .brand-logo {{ color: #FFFFFF; text-decoration: none; font-size: 1.05rem; display: flex; align-items: center; gap: 6px; }}
    .logo-mark {{ color: var(--accent); }}
    
    .header-nav {{ display: flex; gap: 8px; align-items: center; }}
    .nav-link {{ color: var(--muted); text-decoration: none; font-size: 0.85rem; font-weight: 500; padding: 4px 10px; border-radius: 6px; transition: all 0.2s; }}
    .nav-link:hover, .nav-link.active {{ color: #FFFFFF; background: rgba(255, 255, 255, 0.06); }}
    .gworky-link {{ color: var(--accent) !important; border: 1px solid rgba(16, 185, 129, 0.25); }}
    
    .container {{ max-width: 960px; margin: 2rem auto; padding: 0 1.25rem; }}
    .top-header {{ margin-bottom: 2rem; }}
    h1 {{ color: var(--heading); font-size: 2.2rem; margin-bottom: 0.5rem; font-family: Georgia, serif; font-weight: normal; }}
    .lead {{ color: var(--muted); font-size: 1.05rem; max-width: 680px; line-height: 1.6; margin-bottom: 1rem; }}
    
    /* Control Toolbar */
    .toolbar {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 1.2rem; margin-bottom: 2rem; }}
    .search-input {{ width: 100%; background: #0A192F; border: 1px solid var(--border); border-radius: 6px; padding: 0.75rem 1rem; color: #FFFFFF; font-size: 0.95rem; outline: none; margin-bottom: 0.8rem; transition: border-color 0.2s; }}
    .search-input:focus {{ border-color: var(--accent); }}
    .filter-pills {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    .filter-pill {{ background: #0A192F; color: var(--muted); border: 1px solid var(--border); border-radius: 6px; padding: 0.35rem 0.85rem; font-size: 0.82rem; font-weight: 500; cursor: pointer; transition: all 0.2s; text-decoration: none; }}
    .filter-pill:hover {{ color: var(--heading); border-color: var(--accent); }}
    .filter-pill.active {{ background: var(--accent); color: #0A192F; border-color: var(--accent); }}
    
    /* Cards */
    .study-card {{ background: var(--card); padding: 1.25rem 1.5rem; border-radius: 8px; margin-bottom: 1rem; border: 1px solid var(--border); transition: border-color 0.2s; }}
    .study-card:hover {{ border-color: rgba(16, 185, 129, 0.35); }}
    .card-pill {{ font-size: 0.72rem; font-weight: 600; letter-spacing: 0.05em; padding: 2px 6px; border-radius: 4px; display: inline-block; }}
    .pill-study {{ background: rgba(255, 255, 255, 0.06); color: var(--accent); }}
    .pill-digest {{ background: rgba(16, 185, 129, 0.1); color: var(--accent); }}
    
    footer {{ margin-top: 4rem; padding: 2rem 0; border-top: 1px solid var(--border); text-align: center; color: var(--muted); font-size: 0.85rem; }}
    footer a {{ color: var(--accent); text-decoration: none; }}

    @media (max-width: 640px) {{
      .header-nav {{ display: none; }}
    }}
  </style>
</head>
<body>
  {get_nav_header_html(active_pillar=filter_pillar, root_prefix=root_prefix)}

  <div class="container">
    <header class="top-header">
      <h1>{page_heading}</h1>
      <p class="lead">Clear, evidence-backed guides and decision calculators for personal finance, health, home infrastructure, and technology.</p>
    </header>

    {'' if filter_pillar != 'all' else f'''
    <div style="margin-bottom: 2.5rem;">
      <h2 style="font-size: 1.15rem; color: #FFFFFF; margin-bottom: 0.8rem; font-weight: 600;">Topics</h2>
      <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 1rem;">
        {hubs_grid_html}
      </div>
    </div>
    '''}

    <div class="toolbar">
      <input type="text" id="search-box" class="search-input" placeholder="Search guides and calculators... (Press '/' to focus)" oninput="filterCards()">
      <div class="filter-pills">
        <button class="filter-pill {'active' if filter_pillar == 'all' else ''}" onclick="setFilter('all', this)">All</button>
        <button class="filter-pill {'active' if filter_pillar == 'money' else ''}" onclick="setFilter('money', this)">Money</button>
        <button class="filter-pill {'active' if filter_pillar == 'body' else ''}" onclick="setFilter('body', this)">Health</button>
        <button class="filter-pill {'active' if filter_pillar == 'home' else ''}" onclick="setFilter('home', this)">Home</button>
        <button class="filter-pill {'active' if filter_pillar == 'tech' else ''}" onclick="setFilter('tech', this)">Tech</button>
        <button class="filter-pill {'active' if filter_pillar == 'digest' else ''}" onclick="setFilter('digest', this)">Digests</button>
      </div>
    </div>

    <div id="cards-container">
      {all_cards_html}
    </div>

    <footer>
      <p>Groundwork Media • <a href="{SITE_URL}">gworky.com</a> | <a href="{root_prefix}/sitemap.html">Directory Index</a></p>
    </footer>
  </div>

  <script>
    let currentPillar = '{filter_pillar}';

    document.addEventListener('keydown', (e) => {{
      if (e.key === '/' && document.activeElement !== document.getElementById('search-box')) {{
        e.preventDefault();
        document.getElementById('search-box').focus();
      }}
    }});

    function setFilter(pillar, btn) {{
      currentPillar = pillar;
      document.querySelectorAll('.filter-pill').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      filterCards();
    }}

    function filterCards() {{
      const query = document.getElementById('search-box').value.toLowerCase().trim();
      const cards = document.querySelectorAll('.study-card');

      cards.forEach(card => {{
        const searchData = card.getAttribute('data-search') || '';
        const cardPillar = card.getAttribute('data-pillar') || '';
        const cardType = card.getAttribute('data-type') || '';

        const matchesQuery = query === '' || searchData.includes(query);
        const matchesPillar = currentPillar === 'all' 
          || (currentPillar === 'digest' && cardType === 'digest')
          || cardPillar === currentPillar;

        if (matchesQuery && matchesPillar) {{
          card.style.display = 'block';
        }} else {{
          card.style.display = 'none';
        }}
      }});
    }}
  </script>
</body>
</html>
"""


def generate_html_sitemap(articles: list[dict[str, Any]], news_digests: list[NewsItem]) -> str:
    p_sections = {}
    for p, label in [
        ("money", "Money & Finance"),
        ("body", "Health & Body"),
        ("home", "Home & Energy"),
        ("tech", "Technology"),
        ("life", "Life & Career"),
    ]:
        p_articles = [a for a in articles if a.get("pillar", "").lower() == p]
        lis = "".join(f'<li><a href="./{a["slug"]}/">{escape_html(a["title"])}</a></li>' for a in p_articles)
        p_sections[p] = f"""
        <section style="margin-bottom: 2rem;">
          <h2 style="color: #FFFFFF; font-size: 1.15rem; border-bottom: 1px solid rgba(255,255,255,0.08); padding-bottom: 0.4rem; margin-bottom: 0.8rem; font-weight: 600;">
            {label} ({len(p_articles)})
          </h2>
          <ul style="column-count: 2; column-gap: 2rem; list-style-position: inside; line-height: 1.8; font-size: 0.9rem;">
            {lis}
          </ul>
        </section>
        """

    digest_lis = "".join(f'<li><a href="./digest/{d.slug}/">{escape_html(d.title)}</a></li>' for d in news_digests)
    digest_section = f"""
    <section style="margin-bottom: 2rem;">
      <h2 style="color: #FFFFFF; font-size: 1.15rem; border-bottom: 1px solid rgba(255,255,255,0.08); padding-bottom: 0.4rem; margin-bottom: 0.8rem; font-weight: 600;">
        Digests ({len(news_digests)})
      </h2>
      <ul style="column-count: 2; column-gap: 2rem; list-style-position: inside; line-height: 1.8; font-size: 0.9rem;">
        {digest_lis}
      </ul>
    </section>
    """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  {GSC_VERIFICATION_META}
  <title>Groundwork Directory</title>
  <meta name="description" content="Complete directory of guides, tools, and digests.">
  <link rel="canonical" href="{GH_PAGES_URL}/sitemap.html">
  <meta name="robots" content="index, follow">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0A192F; color: #CCD6F6; line-height: 1.7; padding: 2rem 1.25rem; }}
    .container {{ max-width: 960px; margin: 0 auto; background: #112240; padding: 2.5rem; border-radius: 10px; border: 1px solid rgba(255,255,255,0.08); }}
    h1 {{ color: #FFFFFF; font-size: 2rem; margin-bottom: 0.4rem; font-family: Georgia, serif; font-weight: normal; }}
    a {{ color: #10B981; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    @media (max-width: 768px) {{ ul {{ column-count: 1 !important; }} }}
  </style>
</head>
<body>
  <div class="container">
    <h1>Groundwork Directory</h1>
    <p style="color: #8892B0; margin-bottom: 2rem; font-size: 0.95rem;">Directory of published guides, calculators, and briefs from <a href="{SITE_URL}">Groundwork</a>.</p>
    
    {''.join(p_sections.values())}
    {digest_section}

    <footer style="margin-top: 3rem; padding-top: 1rem; border-top: 1px solid rgba(255,255,255,0.08); text-align: center; font-size: 0.85rem; color: #8892B0;">
      <a href="./">← Return Home</a> | <a href="{SITE_URL}">Visit Groundwork Platform</a>
    </footer>
  </div>
</body>
</html>
"""


def generate_llms_txt(articles: list[dict[str, Any]], news_digests: list[NewsItem]) -> str:
    header = f"""# Groundwork Knowledge Base

> Evidence-based guides, decision calculators, and comparisons published by Groundwork ({SITE_URL}).

## Documentation & Links
- Main Platform: {SITE_URL}
- Knowledge Directory: {GH_PAGES_URL}
- RSS Feed: {GH_PAGES_URL}/feed.xml

## Pillars
- Money: {SITE_URL}/money
- Health: {SITE_URL}/body
- Home: {SITE_URL}/home
- Tech: {SITE_URL}/tech
- Life: {SITE_URL}/life

## Featured Guides ({len(articles)} Total)
"""
    items = []
    for art in articles[:40]:
        items.append(f"- [{art['title']}]({GH_PAGES_URL}/{art['slug']}/): {art.get('excerpt', '')[:120]}...")

    return header + "\n".join(items)


def generate_sitemap_xml(articles: list[dict[str, Any]], news_digests: list[NewsItem]) -> str:
    now_iso = datetime.now(UTC).strftime("%Y-%m-%d")
    urls_xml = f"""  <url>
    <loc>{GH_PAGES_URL}/</loc>
    <lastmod>{now_iso}</lastmod>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>{GH_PAGES_URL}/sitemap.html</loc>
    <lastmod>{now_iso}</lastmod>
    <changefreq>daily</changefreq>
    <priority>0.9</priority>
  </url>
"""
    for p in ["money", "body", "home", "tech", "life", "digest"]:
        urls_xml += f"""  <url>
    <loc>{GH_PAGES_URL}/{p}/</loc>
    <lastmod>{now_iso}</lastmod>
    <changefreq>daily</changefreq>
    <priority>0.9</priority>
  </url>
"""

    for dig in news_digests:
        urls_xml += f"""  <url>
    <loc>{GH_PAGES_URL}/digest/{dig.slug}/</loc>
    <lastmod>{now_iso}</lastmod>
    <changefreq>daily</changefreq>
    <priority>0.85</priority>
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


def generate_readme_markdown(total_studies: int, total_digests: int, version: str) -> str:
    return f"""# Groundwork Knowledge Archive

[![GitHub Pages](https://img.shields.io/badge/GitHub_Pages-Live-emerald?style=flat-square&logo=github)]({GH_PAGES_URL})
[![RSS Feed](https://img.shields.io/badge/RSS_2.0-Active-orange?style=flat-square)]({GH_PAGES_URL}/feed.xml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

> Practical, evidence-backed guides, decision calculators, and digests for adults aged 35–48 in the US, UK, and Australia.

---

- **Published Guides:** `{total_studies}`
- **Digests:** `{total_digests}`
- **Primary Platform:** [{SITE_URL}]({SITE_URL})
- **Directory Sitemap:** [{GH_PAGES_URL}/sitemap.html]({GH_PAGES_URL}/sitemap.html)

Distributed under the MIT License.
"""


def get_next_release_tag(current_tags: list[str]) -> str:
    max_major, max_minor, max_patch = 1, 0, 0
    for tag in current_tags:
        m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)$", tag.strip())
        if m:
            maj, min_, pat = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if (maj, min_, pat) > (max_major, max_minor, max_patch):
                max_major, max_minor, max_patch = maj, min_, pat
    return f"v{max_major}.{max_minor + 1}.0"


def build_and_push_github_pages(
    limit: int = 1000,
    include_news: bool = True,
    create_release: bool = True,
    dry_run: bool = False,
) -> bool:
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

    news_digests: list[NewsItem] = []
    if include_news:
        try:
            logger.info("Harvesting multi-source Google News feeds...")
            news_digests = harvest_all_sources(max_per_pillar=4)
        except Exception as exc:
            logger.warning("News harvesting encountered error: %s (continuing with articles)", exc)

    logger.info("Building GitHub Pages for %d guides + %d open digests...", len(articles), len(news_digests))
    if dry_run:
        logger.info("DRY-RUN: Processed successfully, skipping git push & release creation.")
        return True

    tmp_dir = tempfile.mkdtemp(prefix="gh_pages_")
    try:
        clone_cmd = ["gh", "repo", "clone", REPO_NAME, tmp_dir]
        subprocess.run(clone_cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        tags_res = subprocess.run(
            ["git", "tag", "-l"], cwd=tmp_dir, capture_output=True, text=True, check=False
        )
        existing_tags = [t.strip() for t in tags_res.stdout.splitlines() if t.strip()]
        next_tag = get_next_release_tag(existing_tags)

        with open(os.path.join(tmp_dir, "index.html"), "w", encoding="utf-8") as f:
            f.write(generate_index_html(articles, news_digests, filter_pillar="all", root_prefix="."))

        with open(os.path.join(tmp_dir, "sitemap.html"), "w", encoding="utf-8") as f:
            f.write(generate_html_sitemap(articles, news_digests))

        with open(os.path.join(tmp_dir, "llms.txt"), "w", encoding="utf-8") as f:
            f.write(generate_llms_txt(articles, news_digests))

        with open(os.path.join(tmp_dir, "sitemap.xml"), "w", encoding="utf-8") as f:
            f.write(generate_sitemap_xml(articles, news_digests))

        with open(os.path.join(tmp_dir, "feed.xml"), "w", encoding="utf-8") as f:
            f.write(generate_rss_feed(articles, news_digests))

        with open(os.path.join(tmp_dir, "robots.txt"), "w", encoding="utf-8") as f:
            f.write(f"User-agent: *\nAllow: /\nSitemap: {GH_PAGES_URL}/sitemap.xml\n")

        with open(os.path.join(tmp_dir, "CNAME"), "w", encoding="utf-8") as f:
            f.write("groundworkpub.github.io\n")

        with open(os.path.join(tmp_dir, ".nojekyll"), "w", encoding="utf-8") as f:
            f.write("")

        with open(os.path.join(tmp_dir, "googleXmlqHCUNSmjcRtPkLXSDciZNvtWivVpmTT4B0nfN0wg.html"), "w", encoding="utf-8") as f:
            f.write("google-site-verification: googleXmlqHCUNSmjcRtPkLXSDciZNvtWivVpmTT4B0nfN0wg.html\n")

        with open(os.path.join(tmp_dir, "og.svg"), "w", encoding="utf-8") as f:
            f.write(generate_og_svg("Groundwork Practical Guides & Tools", "GUIDES"))

        with open(os.path.join(tmp_dir, "README.md"), "w", encoding="utf-8") as f:
            f.write(generate_readme_markdown(len(articles), len(news_digests), next_tag))

        for p in ["money", "body", "home", "tech", "life"]:
            p_dir = os.path.join(tmp_dir, p)
            os.makedirs(p_dir, exist_ok=True)
            with open(os.path.join(p_dir, "index.html"), "w", encoding="utf-8") as f:
                f.write(generate_index_html(articles, news_digests, filter_pillar=p, root_prefix=".."))

        injected_rows = []
        for i, art in enumerate(articles):
            art_dir = os.path.join(tmp_dir, art["slug"])
            os.makedirs(art_dir, exist_ok=True)
            
            prev_art = articles[i - 1] if i > 0 else None
            next_art = articles[i + 1] if i < len(articles) - 1 else None

            with open(os.path.join(art_dir, "index.html"), "w", encoding="utf-8") as f:
                f.write(
                    generate_article_html(
                        art,
                        sibling_articles=articles,
                        prev_article=prev_art,
                        next_article=next_art,
                    )
                )

            with open(os.path.join(art_dir, "og.svg"), "w", encoding="utf-8") as f:
                f.write(generate_og_svg(art["title"], art.get("pillar", "guide"), is_digest=False))

            linked = match_groundwork_resource(art["title"], art.get("pillar", "money"), art.get("excerpt") or "", sibling_articles=articles)
            live_url = f"{GH_PAGES_URL}/{art['slug']}/"
            injected_rows.append(
                {
                    "source_slug": art["slug"],
                    "target_platform": "github_pages_article",
                    "tier_level": "tier1",
                    "live_backlink_url": live_url,
                    "target_url": f"{SITE_URL}/article/{art['slug']}",
                    "anchor_text": f"Groundwork: {art['title'][:40]}",
                    "is_dofollow": True,
                    "status": "published",
                    "metrics_snapshot": {
                        "pillar_hub": linked.pillar_url,
                        "flagship_guide": linked.flagship_url,
                        "tool_url": linked.tool_url,
                    },
                }
            )

        digest_base_dir = os.path.join(tmp_dir, "digest")
        os.makedirs(digest_base_dir, exist_ok=True)
        with open(os.path.join(digest_base_dir, "index.html"), "w", encoding="utf-8") as f:
            f.write(generate_index_html(articles, news_digests, filter_pillar="digest", root_prefix=".."))

        for j, dig in enumerate(news_digests):
            dig_dir = os.path.join(digest_base_dir, dig.slug)
            os.makedirs(dig_dir, exist_ok=True)
            
            prev_dig = news_digests[j - 1] if j > 0 else None
            next_dig = news_digests[j + 1] if j < len(news_digests) - 1 else None

            with open(os.path.join(dig_dir, "index.html"), "w", encoding="utf-8") as f:
                f.write(
                    generate_news_digest_html(
                        dig,
                        sibling_articles=articles,
                        prev_digest=prev_dig,
                        next_digest=next_dig,
                    )
                )

            with open(os.path.join(dig_dir, "og.svg"), "w", encoding="utf-8") as f:
                f.write(generate_og_svg(dig.title, dig.pillar, is_digest=True))

            linked = match_groundwork_resource(dig.title, dig.pillar, dig.description, sibling_articles=articles)
            live_url = f"{GH_PAGES_URL}/digest/{dig.slug}/"
            injected_rows.append(
                {
                    "source_slug": f"digest-{dig.slug}",
                    "target_platform": "github_pages_digest",
                    "tier_level": "tier1",
                    "live_backlink_url": live_url,
                    "target_url": linked.pillar_url,
                    "anchor_text": f"Groundwork {linked.pillar_title}",
                    "is_dofollow": True,
                    "status": "published",
                    "metrics_snapshot": {
                        "pillar_hub": linked.pillar_url,
                        "flagship_guide": linked.flagship_url,
                        "tool_url": linked.tool_url,
                    },
                }
            )

        subprocess.run(["git", "config", "user.name", "Groundwork Bot"], cwd=tmp_dir, check=True)
        subprocess.run(["git", "config", "user.email", "bot@gworky.com"], cwd=tmp_dir, check=True)
        subprocess.run(["git", "add", "."], cwd=tmp_dir, check=True)

        status_res = subprocess.run(
            ["git", "status", "--porcelain"], cwd=tmp_dir, capture_output=True, text=True, check=True
        )
        if status_res.stdout.strip():
            commit_msg = f"refactor(editorial): streamline human-centric copy & clean schemas ({next_tag})"
            subprocess.run(["git", "commit", "-m", commit_msg], cwd=tmp_dir, check=True)
            subprocess.run(["git", "branch", "-M", "main"], cwd=tmp_dir, check=True)
            push_res = subprocess.run(
                ["git", "push", "-u", "origin", "main"], cwd=tmp_dir, capture_output=True, text=True
            )
            if push_res.returncode == 0:
                logger.info("Successfully pushed clean GitHub Pages to %s!", REPO_NAME)

                if create_release:
                    try:
                        logger.info("Creating GitHub Release %s...", next_tag)
                        release_title = f"Groundwork {next_tag}"
                        release_notes = (
                            f"Groundwork Practical Guides & Tools ({next_tag}):\n\n"
                            f"- Pure human-centric editorial layout and clean typography\n"
                            f"- Direct navigation across Money, Health, Home, Tech, Life\n"
                            f"- Valid, standard Schema.org Article JSON-LD\n"
                            f"- Primary Platform: {SITE_URL}"
                        )
                        rel_cmd = [
                            "gh", "release", "create", next_tag,
                            "--repo", REPO_NAME,
                            "--title", release_title,
                            "--notes", release_notes
                        ]
                        rel_res = subprocess.run(rel_cmd, cwd=tmp_dir, capture_output=True, text=True)
                        if rel_res.returncode == 0:
                            logger.info("✅ Successfully created GitHub Release %s!", next_tag)
                        else:
                            logger.warning("GitHub Release creation returned: %s", rel_res.stderr.strip())
                    except Exception as rel_err:
                        logger.warning("Failed to create GitHub release: %s", rel_err)

                try:
                    chunk_size = 100
                    for i in range(0, len(injected_rows), chunk_size):
                        chunk = injected_rows[i:i + chunk_size]
                        supabase.table("link_injection_logs").upsert(chunk).execute()
                    logger.info("Logged %d Tier-1 backlinks in Supabase.", len(injected_rows))
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
    import argparse
    parser = argparse.ArgumentParser(description="Groundwork GitHub Pages Syncer")
    parser.add_argument("--limit", type=int, default=1000, help="Maximum articles to sync (default: 1000)")
    parser.add_argument("--no-news", action="store_true", help="Skip harvesting external news feeds")
    parser.add_argument("--no-release", action="store_true", help="Skip creating GitHub release")
    parser.add_argument("--dry-run", action="store_true", help="Dry run without push or release")
    args = parser.parse_args()

    build_and_push_github_pages(
        limit=args.limit,
        include_news=not args.no_news,
        create_release=not args.no_release,
        dry_run=args.dry_run,
    )
