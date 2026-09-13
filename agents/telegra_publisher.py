#!/usr/bin/env python3
"""
Telegra.ph publisher — Tier 2 buffer (DA 93, $0, 150ms, no auth)
Creates a Telegra.ph page for a Groundwork article/calculator and returns its URL.
Usage: python -m agents.telegra_publisher --slug best-ai-note-taking-app-2026 --dry-run
Ref: wordpress_authority_link_injection_blueprint.md §4.1, Tier 2
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import httpx

TELEGRAPH_CREATE = "https://api.telegra.ph/createPage"
TELEGRAPH_ACCOUNT = "https://api.telegra.ph/createAccount"

def get_supabase():
    url = os.getenv("NEXT_PUBLIC_SUPABASE_URL") or os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")
    if not url or not key:
        raise RuntimeError("Missing Supabase credentials")
    from supabase import create_client
    return create_client(url, key)

def ensure_telegraph_token() -> str:
    # Reuse or create account; token can be cached in .env.local as TELEGRAPH_ACCESS_TOKEN
    tok = os.getenv("TELEGRAPH_ACCESS_TOKEN")
    if tok:
        return tok
    # Create ephemeral account
    r = httpx.post(TELEGRAPH_ACCOUNT, data={
        "short_name": "Groundwork",
        "author_name": "Groundwork Research Syndicate",
        "author_url": "https://gworky.com",
    }, timeout=15)
    j = r.json()
    if not j.get("ok"):
        raise RuntimeError(f"Telegraph createAccount failed: {j}")
    return j["result"]["access_token"]

def build_content_nodes(article: dict) -> list[dict]:
    # Minimal AST: intro + link to Tier 1 (gworky.com) + link to Tier 2 (github.io)
    title = article["title"]
    excerpt = article.get("excerpt") or ""
    slug = article["slug"]
    gworky_url = f"https://gworky.com/article/{slug}"
    github_url = f"https://groundworkpub.github.io/article/{slug}"
    return [
        {"tag": "p", "children": [excerpt]},
        {"tag": "p", "children": ["Read the full research and interactive calculator on ", {"tag": "a", "attrs": {"href": gworky_url}, "children": ["Groundwork"]}, " and the open research portal ", {"tag": "a", "attrs": {"href": github_url}, "children": ["Groundwork Research Portal"]}, "."]},
        {"tag": "p", "children": [f"Pillar: {article.get('pillar','tech')} • Published: {article.get('published_at','')[:10]}"]},
        {"tag": "blockquote", "children": [title]},
    ]

def publish_telegraph(article: dict, dry_run: bool = False) -> str | None:
    token = ensure_telegraph_token()
    content = build_content_nodes(article)
    payload = {
        "access_token": token,
        "title": article["title"][:120],
        "author_name": "Groundwork Research Syndicate",
        "author_url": "https://gworky.com",
        "content": json.dumps(content),
        "return_content": False,
    }
    if dry_run:
        print(f"[dry-run] would POST {TELEGRAPH_CREATE} title={payload['title'][:40]} nodes={len(content)}")
        return f"https://telegra.ph/dry-run-{article['slug']}"
    r = httpx.post(TELEGRAPH_CREATE, data=payload, timeout=15)
    j = r.json()
    if not j.get("ok"):
        raise RuntimeError(f"Telegraph createPage failed: {j}")
    url = j["result"]["url"]
    print(f"[telegra] {article['slug']} → {url}")
    return url

def main():
    ap = argparse.ArgumentParser(description="Telegra.ph Tier 2 publisher")
    ap.add_argument("--slug", help="Article slug to publish")
    ap.add_argument("--limit", type=int, default=5, help="Batch limit (recent published)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    supa = get_supabase()
    if args.slug:
        rows = supa.table("articles").select("slug,title,excerpt,pillar,published_at").eq("slug", args.slug).limit(1).execute().data or []
    else:
        rows = supa.table("articles").select("slug,title,excerpt,pillar,published_at").eq("status", "published").order("published_at", desc=True).limit(args.limit).execute().data or []

    if not rows:
        print("No articles found")
        sys.exit(1)

    for art in rows:
        try:
            publish_telegraph(art, dry_run=args.dry_run)
        except Exception as e:
            print(f"Failed {art['slug']}: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()
