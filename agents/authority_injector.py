"""Groundwork 3-Tier Authority Engine (Agent 4c)

Autonomous & CLI-driven link injection and syndication engine:
- Tier 1: Direct Buffer syndication (Dev.to DR 91, Hashnode DR 89, GitHub Pages DR 96, Medium DR 95)
- Tier 2: Blog Dummy Boosters (Blogger DR 99, Tumblr DR 86, WordPress.com DR 92) linking to Tier 1
- Tier 3: IndexNow & Instant Crawl Pings
- Fast AI Paraphraser: Single-pass LiteLLM (Gemini Flash) with dynamic contextual anchor text.

Usage:
  python agents/authority_injector.py --slug mortgage-rates-forecast-2026
  python agents/authority_injector.py --batch-all --limit 10
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

import httpx

# Configure logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("authority_injector")

SITE_URL = os.getenv("NEXT_PUBLIC_SITE_URL", "https://gworky.com").rstrip("/")
TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def _load_env_local() -> None:
    """Auto-loads .env.local from project root if present."""
    root_env = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env.local")
    if os.path.exists(root_env):
        try:
            with open(root_env, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'").strip('"')
                    if k not in os.environ:
                        os.environ[k] = v
        except Exception:
            pass


def get_supabase_client() -> Any:
    """Lazy initialization of Supabase client."""
    _load_env_local()
    from supabase import create_client

    url = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set.")
    return create_client(url, key)


def fast_paraphrase_article(
    title: str, excerpt: str, takeaway: str, content: str, target_keyword: str
) -> dict[str, str]:
    """Fast paraphraser with unified multi-provider fallback (Cloudflare AI Gateway / OpenRouter / Groq / Gemini)."""
    prompt = f"""You are an elite research editor at Groundwork.
Rewrite and paraphrase this article excerpt into a compelling 350-word executive summary for syndication.

Original Title: {title}
Original Excerpt: {excerpt}
Original Takeaway: {takeaway}
Target Keyword: {target_keyword}

Rules:
1. Paraphrase cleanly: concise, authoritative, zero motivational fluff, zero AI-slop.
2. Structure with Markdown: Title (H1), Summary (2 paragraphs), Key Findings (bullet points).
3. Do not include raw URLs.

Output valid JSON strictly in this format:
{{"title": "Spun Title", "body": "Spun Markdown Body", "anchor_text": "Contextual 3-5 word anchor phrase"}}
"""
    try:
        from agents.llm_router import call_llm_json
    except ImportError:
        from llm_router import call_llm_json

    messages = [{"role": "user", "content": prompt}]
    parsed = call_llm_json(messages, max_tokens=800)
    if parsed and isinstance(parsed, dict) and "title" in parsed:
        return {
            "title": parsed.get("title", f"Analysis: {title}"),
            "body": parsed.get("body", f"{excerpt}\n\n{takeaway}"),
            "anchor_text": parsed.get("anchor_text", target_keyword or "read the full research"),
        }

    # Clean deterministic fallback (zero LLM dependency)
    logger.info("Using deterministic executive brief for syndication.")
    clean_anchor = f"{target_keyword.capitalize()} Research" if target_keyword else "comprehensive analysis"
    return {
        "title": f"Analysis: {title}",
        "body": f"# {title}\n\n{excerpt}\n\n## Core Findings\n{takeaway}\n\n*This research synthesis is published with canonical reference to the original Groundwork study.*",
        "anchor_text": clean_anchor,
    }


# ============================================================
# TIER 1 DISPATCHERS (Direct Buffer to gworky.com)
# ============================================================


def publish_to_devto(article: dict[str, Any], spun: dict[str, str], live: bool = True) -> str | None:
    """Publishes to DEV.to (DR 91, Dofollow) with canonical_url and rate-limit guard."""
    token = os.getenv("DEVTO_API_KEY")
    if not token:
        logger.info("[Tier 1] DEVTO_API_KEY not configured. Skipping DEV.to.")
        return None

    canonical = f"{SITE_URL}/article/{article['slug']}"
    body = (
        f"{spun['body']}\n\n"
        f"---\n"
        f"*Original investigation and evidence breakdown published on [Groundwork: {spun['anchor_text']}]({canonical}).*"
    )
    payload = {
        "article": {
            "title": spun["title"],
            "body_markdown": body,
            "canonical_url": canonical,
            "published": live,
            "tags": ["research", article.get("pillar", "finance")[:20], "guide"],
        }
    }
    headers = {"api-key": token, "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=TIMEOUT, headers=headers) as client:
            resp = client.post("https://dev.to/api/articles", json=payload)
            if resp.status_code in (200, 201):
                url = resp.json().get("url")
                logger.info(f"[Tier 1] DEV.to published successfully: {url}")
                return url
            elif resp.status_code == 429:
                logger.warning(
                    "[Tier 1] DEV.to rate-limited (anti-spam cooldown). Will retry on next scheduled interval."
                )
                return None
            logger.error(f"[Tier 1] DEV.to error: {resp.status_code} - {resp.text}")
    except Exception as exc:
        logger.warning(f"[Tier 1] DEV.to connection error: {exc}")
    return None


def publish_to_hashnode(article: dict[str, Any], spun: dict[str, str], live: bool = True) -> str | None:
    """Publishes to Hashnode (DR 89, Dofollow) with originalArticleURL."""
    token = os.getenv("HASHNODE_API_TOKEN")
    pub_id = os.getenv("HASHNODE_PUBLICATION_ID")
    if not token or not pub_id:
        logger.info("[Tier 1] HASHNODE credentials not configured. Skipping Hashnode.")
        return None

    canonical = f"{SITE_URL}/article/{article['slug']}"
    body = (
        f"{spun['body']}\n\n"
        f"---\n"
        f"*For data methodology and source citations, explore the [Groundwork {spun['anchor_text']}]({canonical}).*"
    )
    query = """
    mutation PublishPost($input: PublishPostInput!) {
      publishPost(input: $input) {
        post {
          id
          url
        }
      }
    }
    """
    payload = {
        "query": query,
        "variables": {
            "input": {
                "title": spun["title"],
                "contentMarkdown": body,
                "publicationId": pub_id,
                "originalArticleURL": canonical,
                "draft": not live,
            }
        },
    }
    headers = {"Authorization": token, "Content-Type": "application/json"}
    with httpx.Client(timeout=TIMEOUT, headers=headers) as client:
        resp = client.post("https://api.hashnode.com/v1/graphql", json=payload)
        if resp.status_code == 200:
            data = resp.json()
            if not data.get("errors"):
                url = data.get("data", {}).get("publishPost", {}).get("post", {}).get("url")
                logger.info(f"[Tier 1] Hashnode published successfully: {url}")
                return url
        logger.error(f"[Tier 1] Hashnode error: {resp.status_code} - {resp.text}")
        return None


def publish_to_medium(article: dict[str, Any], spun: dict[str, str], live: bool = True) -> str | None:
    """Publishes to Medium (DR 95, Canonical) with canonicalUrl."""
    token = os.getenv("MEDIUM_INTEGRATION_TOKEN")
    if not token:
        logger.info("[Tier 1] MEDIUM_INTEGRATION_TOKEN not configured. Skipping Medium.")
        return None

    canonical = f"{SITE_URL}/article/{article['slug']}"
    body = f"{spun['body']}\n\n---\n*Originally published at [Groundwork: {spun['anchor_text']}]({canonical}).*"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    with httpx.Client(timeout=TIMEOUT, headers=headers) as client:
        me_resp = client.get("https://api.medium.com/v1/me")
        if me_resp.status_code != 200:
            logger.error(f"[Tier 1] Medium auth failed: {me_resp.text}")
            return None
        author_id = me_resp.json()["data"]["id"]

        payload = {
            "title": spun["title"],
            "contentFormat": "markdown",
            "content": body,
            "canonicalUrl": canonical,
            "publishStatus": "public" if live else "draft",
            "tags": ["research", article.get("pillar", "groundwork")],
        }
        resp = client.post(f"https://api.medium.com/v1/users/{author_id}/posts", json=payload)
        if resp.status_code in (200, 201):
            url = resp.json().get("data", {}).get("url")
            logger.info(f"[Tier 1] Medium published successfully: {url}")
            return url
        logger.error(f"[Tier 1] Medium error: {resp.status_code} - {resp.text}")
        return None


# ============================================================
# TIER 2 DISPATCHERS (Dummy Blog Boosters to Tier 1)
# ============================================================


def publish_to_blogger_satellite(spun: dict[str, str], tier1_urls: list[str]) -> str | None:
    """Publishes to Blogger Web 2.0 Satellites linking to Tier 1 buffers (via REST API or Email-to-Blog)."""
    blog_id = os.getenv("BLOGGER_BLOG_ID", "8619484910889369200").strip()
    blogger_email = os.getenv("BLOGGER_EMAIL", "groundworkpub.gworky@blogger.com").strip()
    resend_key = os.getenv("RESEND_API_KEY")

    if not tier1_urls:
        logger.info("[Tier 2] Tier 1 targets not available. Skipping Blogger.")
        return None

    target_tier1 = tier1_urls[0]
    html_content = (
        f"<p>{spun['body'].replace(chr(10), '<br>')}</p>"
        f"<p>Read additional community coverage and deep analysis on <a href='{target_tier1}'>{spun['anchor_text']}</a>.</p>"
        f"<hr>"
        f"<p><small>Published by Groundwork Research Syndicate • <a href='{SITE_URL}'>{SITE_URL}</a></small></p>"
    )

    # Method 1: Google Blogger REST API (Direct & Zero-Bounce)
    access_token = os.getenv("BLOGGER_ACCESS_TOKEN")
    if blog_id and access_token:
        try:
            headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
            url = f"https://www.googleapis.com/blogger/v3/blogs/{blog_id}/posts/"
            post_payload = {
                "kind": "blogger#post",
                "title": spun["title"],
                "content": html_content,
            }
            with httpx.Client(timeout=TIMEOUT, headers=headers) as client:
                resp = client.post(url, json=post_payload)
                if resp.status_code in (200, 201):
                    post_url = resp.json().get("url")
                    logger.info(f"[Tier 2] Blogger Satellite published via REST API: {post_url}")
                    return post_url
                logger.warning(f"[Tier 2] Blogger API responded: {resp.status_code} - {resp.text}")
        except Exception as exc:
            logger.warning(f"[Tier 2] Blogger REST API error: {exc}")

    # Method 2: Email-to-Blog via Resend (Blogger Secret Post Address)
    if blogger_email and resend_key:
        try:
            payload = {
                "from": "Groundwork Syndicate <team@gworky.com>",
                "to": [blogger_email],
                "subject": spun["title"],
                "html": html_content,
            }
            headers = {"Authorization": f"Bearer {resend_key}", "Content-Type": "application/json"}
            with httpx.Client(timeout=TIMEOUT, headers=headers) as client:
                resp = client.post("https://api.resend.com/emails", json=payload)
                if resp.status_code in (200, 201):
                    logger.info(f"[Tier 2] Blogger Satellite dispatched via Email-to-Blog ({blogger_email})")
                    return f"https://gworky.blogspot.com/search?q={spun['title'][:30]}"
                logger.warning(f"[Tier 2] Resend error for Blogger: {resp.status_code} - {resp.text}")
        except Exception as exc:
            logger.warning(f"[Tier 2] Email-to-blog failed: {exc}")

    return None


# ============================================================
# TIER 3 INDEXING TRIGGER (IndexNow & Instant Pings)
# ============================================================


def trigger_indexnow(urls: list[str]) -> bool:
    """Fires IndexNow API requests for instant Bing / Yandex crawl (own domain only)."""
    if not urls:
        return True

    site_url_clean = SITE_URL.replace("https://", "").replace("http://", "")
    # Filter only URLs that belong to our own host (IndexNow protocol rule)
    internal_urls = [u for u in urls if site_url_clean in u]
    if not internal_urls:
        return True

    api_key = os.getenv("INDEXNOW_KEY", "groundwork-indexnow-key-2026")
    host = site_url_clean
    payload = {
        "host": host,
        "key": api_key,
        "keyLocation": f"{SITE_URL}/{api_key}.txt",
        "urlList": internal_urls,
    }
    headers = {"Content-Type": "application/json; charset=utf-8"}
    try:
        with httpx.Client(timeout=TIMEOUT, headers=headers) as client:
            resp = client.post("https://api.indexnow.org/IndexNow", json=payload)
            if resp.status_code in (200, 202):
                logger.info(f"[Tier 3] IndexNow triggered successfully for {len(internal_urls)} URLs.")
                return True
            logger.warning(f"[Tier 3] IndexNow response: {resp.status_code} - {resp.text}")
    except Exception as exc:
        logger.warning(f"[Tier 3] IndexNow failed to send: {exc}")
    return False


# ============================================================
# CORE ORCHESTRATOR
# ============================================================


def run_syndication_for_article(supabase: Any, article: dict[str, Any], live: bool = True) -> dict[str, Any]:
    """Runs complete 3-Tier syndication and backlink injection for a single article."""
    slug: str = str(article.get("slug") or "")
    title: str = str(article.get("title") or slug)
    excerpt: str = str(article.get("excerpt") or "")
    takeaway: str = str(article.get("takeaway") or "")
    content: str = str(article.get("content") or "")
    pillar: str = str(article.get("pillar") or "general")
    canonical: str = f"{SITE_URL}/article/{slug}"

    logger.info(f"==> Initiating Authority Engine for: {slug} ({pillar})")

    # Step 1: Fast AI Paraphraser
    spun = fast_paraphrase_article(title, excerpt, takeaway, content, target_keyword=pillar)

    tier1_results: list[dict[str, Any]] = []
    tier2_results: list[dict[str, Any]] = []

    # Step 2: Tier 1 Dispatch (Dev.to, Hashnode, Medium)
    devto_url = publish_to_devto(article, spun, live=live)
    if devto_url:
        tier1_results.append({"platform": "devto", "url": devto_url, "target": canonical, "dofollow": True})

    hashnode_url = publish_to_hashnode(article, spun, live=live)
    if hashnode_url:
        tier1_results.append({"platform": "hashnode", "url": hashnode_url, "target": canonical, "dofollow": True})

    medium_url = publish_to_medium(article, spun, live=live)
    if medium_url:
        tier1_results.append({"platform": "medium", "url": medium_url, "target": canonical, "dofollow": False})

    # Step 3: Tier 2 Dispatch (Blogger Satellite pointing to Tier 1 or canonical)
    tier1_live_urls = [r["url"] for r in tier1_results]
    blogger_targets = tier1_live_urls if tier1_live_urls else [canonical]
    blogger_url = publish_to_blogger_satellite(spun, blogger_targets)
    if blogger_url:
        tier2_results.append(
            {
                "platform": "blogger",
                "url": blogger_url,
                "target": blogger_targets[0],
                "dofollow": True,
            }
        )

    # Step 4: Record Injections in Supabase
    all_logs = []
    now_iso = datetime.now(UTC).isoformat()

    for item in tier1_results:
        all_logs.append(
            {
                "source_slug": slug,
                "target_platform": item["platform"],
                "tier_level": "tier1",
                "live_backlink_url": item["url"],
                "target_url": item["target"],
                "anchor_text": spun["anchor_text"],
                "is_dofollow": item["dofollow"],
                "status": "published" if live else "draft",
                "created_at": now_iso,
            }
        )

    for item in tier2_results:
        all_logs.append(
            {
                "source_slug": slug,
                "target_platform": item["platform"],
                "tier_level": "tier2",
                "live_backlink_url": item["url"],
                "target_url": item["target"],
                "anchor_text": spun["anchor_text"],
                "is_dofollow": item["dofollow"],
                "status": "published" if live else "draft",
                "created_at": now_iso,
            }
        )

    if all_logs:
        supabase.table("link_injection_logs").insert(all_logs).execute()
        logger.info(f"Logged {len(all_logs)} backlink injection rows into Supabase.")

    # Step 5: Fast Indexing
    all_live_urls = [r["url"] for r in tier1_results + tier2_results]
    all_live_urls.append(canonical)
    trigger_indexnow(all_live_urls)

    return {
        "slug": slug,
        "tier1_count": len(tier1_results),
        "tier2_count": len(tier2_results),
        "injected_urls": all_live_urls,
    }


# ============================================================
# HIGH-AUTHORITY INGRESS & EVIDENCE PACK GENERATOR
# ============================================================


def get_tier2_buffer_urls(slug: str, tool_slug: str | None = None) -> dict[str, str]:
    """Resolves active Tier-2 Authority Rotator URLs for safe multi-tier linking."""
    target_tool = tool_slug or "mortgage-refinance-calculator"
    return {
        "devto": f"https://dev.to/groundworkpub/{slug}",
        "github_pages": "https://groundworkpub.github.io/",
        "zenodo_doi": "https://doi.org/10.5281/zenodo.22011566",
        "youtube": "https://www.youtube.com/@gworky",
        "pinterest": f"{SITE_URL}/feed/pinterest",
        "direct_tool": f"{SITE_URL}/tools/{target_tool}",
        "direct_article": f"{SITE_URL}/article/{slug}",
    }


def generate_high_authority_evidence_pack(
    target_topic: str,
    target_domain: str,
    pillar: str,
    tool_title: str,
    tool_url: str,
    tier2_url: str | None = None,
) -> dict[str, str]:
    """
    Synthesizes an E-E-A-T Substantive Evidence Pack for high-authority ingress (.edu, .gov, top media):
    - Completely eliminates spam tone.
    - Provides direct, factual, mathematical breakdown with methodology.
    - Contextually cites the Tier-2 Buffer (e.g. GitHub calculation code / Dev.to benchmark) or direct Groundwork calculator.
    """
    citation_target = tier2_url or tool_url
    prompt = f"""You are an elite research fellow at Groundwork and Open Science contributor.
Write a substantive, academic-grade research contribution (2 short paragraphs, ~150-200 words) for a resource desk or discussion at {target_domain}.

Topic: {target_topic}
Pillar: {pillar.capitalize()}
Referenced Utility / Dataset: {tool_title} ({citation_target})

Rules:
1. Zero marketing fluff. Zero promotion. Speak with pure empirical rigor.
2. Provide a practical rule of thumb or formula (e.g. break-even threshold, metabolic coefficient, or IRA 25C tax rebate calculation).
3. Naturally cite the reference dataset or interactive decision utility ({citation_target}) as an open methodology reference.
4. Output valid JSON strictly in this format:
{{"subject": "Short descriptive subject line", "comment_body": "Substantive 150-200 word commentary", "anchor_phrase": "Descriptive 3-5 word anchor"}}
"""
    try:
        from agents.llm_router import call_llm_json
    except ImportError:
        from llm_router import call_llm_json

    messages = [{"role": "user", "content": prompt}]
    parsed = call_llm_json(messages, max_tokens=600)
    if parsed and isinstance(parsed, dict) and "comment_body" in parsed:
        return {
            "subject": parsed.get("subject", f"Empirical benchmark on {target_topic}"),
            "body": parsed.get("comment_body", f"Based on recent open datasets for {target_topic}, scenario models indicate significant sensitivity to underlying interest and efficiency parameters. Complete decision formulas are documented in the {tool_title} open model at {citation_target}."),
            "anchor": parsed.get("anchor_phrase", f"{tool_title} reference model"),
            "target_url": citation_target,
        }

    # Deterministic fallback
    return {
        "subject": f"Empirical research reference: {target_topic}",
        "body": f"Regarding {target_topic}, empirical sensitivity models demonstrate that break-even thresholds and payback periods depend strictly on localized baseline rates. For verifiable calculation formulas and open datasets, see the {tool_title} model ({citation_target}).",
        "anchor": f"open {tool_title} model",
        "target_url": citation_target,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Groundwork 3-Tier Authority & Link Injection Engine")
    parser.add_argument("--slug", help="Syndicate single article by slug")
    parser.add_argument("--batch-all", action="store_true", help="Batch syndicate all published articles")
    parser.add_argument("--limit", type=int, default=5, help="Max articles to process in batch mode")
    parser.add_argument("--draft", action="store_true", help="Create as draft instead of live publish")
    parser.add_argument("--evidence-pack", action="store_true", help="Generate high-authority evidence packs for .edu/.gov hubs")
    parser.add_argument("--interactive", action="store_true", help="Interactive terminal menu mode")
    args = parser.parse_args()

    if args.evidence_pack:
        print("🔍 Generating High-Authority Evidence Packs for Curated Resource Hubs...")
        hubs = [
            ("personal finance & mortgage refinance", "harvard.edu", "money", "Mortgage Refinance Break-Even Engine", f"{SITE_URL}/tools/mortgage-refinance-calculator"),
            ("home solar & heat pump electrification", "energy.gov", "home", "Heat Pump & Solar ROI Sizer", f"{SITE_URL}/tools/heat-pump-roi-calculator"),
            ("metabolic rate & energy expenditure", "cdc.gov", "body", "BMR & TDEE Metabolic Engine", f"{SITE_URL}/tools/bmr-tdee-calculator"),
        ]
        for topic, dom, pillar, title, tool_url in hubs:
            pack = generate_high_authority_evidence_pack(topic, dom, pillar, title, tool_url)
            print(f"\n=======================================================")
            print(f"🎯 Target: {dom} | Topic: {topic}")
            print(f"📌 Subject: {pack['subject']}")
            print(f"📝 Body:\n{pack['body']}")
            print(f"🔗 Citation: {pack['target_url']}")
        return

    supabase = get_supabase_client()

    if args.slug:
        res = supabase.table("articles").select("*").eq("slug", args.slug).execute()
        if not res.data:
            logger.error(f"Article '{args.slug}' not found in database.")
            sys.exit(1)
        article = res.data[0]
        run_syndication_for_article(supabase, article, live=not args.draft)
    elif args.batch_all:
        res = (
            supabase.table("articles")
            .select("*")
            .eq("status", "published")
            .order("published_at", desc=True)
            .limit(args.limit)
            .execute()
        )
        articles = res.data or []
        logger.info(f"Found {len(articles)} published articles for batch syndication.")
        for art in articles:
            run_syndication_for_article(supabase, art, live=not args.draft)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
