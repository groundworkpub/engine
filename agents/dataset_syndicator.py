#!/usr/bin/env python3
"""
Dataset Syndicator — Tier 2 buffer (Hugging Face DR 92, Kaggle DR 93, $0)
Uploads a public JSON/CSV dataset for a Groundwork calculator/pillar to Hugging Face.
Usage: HF_TOKEN=hf_xxx python -m agents.dataset_syndicator --slug solar-panel-cost --dry-run
Ref: wordpress_authority_link_injection_blueprint.md §4.2-4.3
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Auto-load .env.local if present
_env_path = Path(__file__).resolve().parent.parent / ".env.local"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

def get_supabase():
    url = os.getenv("NEXT_PUBLIC_SUPABASE_URL") or os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")
    if not url or not key:
        raise RuntimeError("Missing Supabase credentials")
    from supabase import create_client
    return create_client(url, key)

def build_dataset_for_articles(articles: list[dict], pillar: str) -> dict:
    # Minimal public dataset: list of articles with pillar, title, slug, excerpt
    return {
        "name": f"groundwork-{pillar}-2026",
        "description": f"Groundwork {pillar} research articles 2026 — open dataset for {pillar} pillar. Source: https://gworky.com/{pillar}",
        "records": [
            {"slug": a["slug"], "title": a["title"], "pillar": a.get("pillar"), "published_at": a.get("published_at"), "url": f"https://gworky.com/article/{a['slug']}"}
            for a in articles
        ],
    }

def upload_huggingface(dataset: dict, pillar: str, dry_run: bool = False) -> str | None:
    token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN")
    if not token and not dry_run:
        print("HF_TOKEN not set — skipping Hugging Face upload (dry-run only)", file=sys.stderr)
        return None
    # Clear S3 env that confuses HfApi (endpoint_url s3.hf.co)
    for k in ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_PROFILE", "HF_ENDPOINT"]:
        os.environ.pop(k, None)
    repo_id = f"elenagroundwork/groundwork-{pillar}-2026"
    if dry_run:
        print(f"[dry-run] would upload to huggingface.co/datasets/{repo_id} — {len(dataset['records'])} records")
        print(json.dumps(dataset, indent=2)[:400])
        return f"https://huggingface.co/datasets/{repo_id}"

    try:
        from huggingface_hub import HfApi
    except ImportError:
        print("huggingface_hub not installed — pip install huggingface_hub", file=sys.stderr)
        return None

    api = HfApi(token=token)
    api.create_repo(repo_id=repo_id, repo_type="dataset", exist_ok=True)
    # Write temp JSON
    tmp = Path(f"/tmp/{pillar}-dataset.json")
    tmp.write_text(json.dumps(dataset, indent=2))
    # README with YAML dataset card (HF Hub requires frontmatter)
    readme = f"""---
language:
- en
license: cc-by-4.0
pretty_name: "Groundwork {pillar.title()} 2026"
tags:
- groundwork
- {pillar}
- research
- open-dataset
task_categories:
- text-generation
---

# Groundwork {pillar.title()} 2026

Open dataset for Groundwork {pillar} pillar — {len(dataset['records'])} articles.

Source: https://gworky.com/{pillar}

See `data.json` for records.
"""
    tmp_readme = Path("/tmp/README.md")
    tmp_readme.write_text(readme)
    api.upload_file(path_or_fileobj=str(tmp), path_in_repo="data.json", repo_id=repo_id, repo_type="dataset")
    api.upload_file(path_or_fileobj=str(tmp_readme), path_in_repo="README.md", repo_id=repo_id, repo_type="dataset")
    url = f"https://huggingface.co/datasets/{repo_id}"
    print(f"[huggingface] {pillar} → {url}")
    return url

PILLARS = ["money", "body", "home", "life", "tech"]

def main():
    ap = argparse.ArgumentParser(description="Dataset syndicator Tier 2")
    ap.add_argument("--pillar", default="all", help="Pillar to export (money, body, home, life, tech, or all)")
    ap.add_argument("--all", action="store_true", help="Export all pillars")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    supa = get_supabase()
    target_pillars = PILLARS if (args.all or args.pillar.lower() == "all") else [args.pillar.lower()]

    for p in target_pillars:
        rows = supa.table("articles").select("slug,title,pillar,published_at").eq("status", "published").eq("pillar", p).order("published_at", desc=True).limit(args.limit).execute().data or []
        if not rows:
            print(f"[warning] No articles for pillar {p}")
            continue
        ds = build_dataset_for_articles(rows, p)
        upload_huggingface(ds, p, dry_run=args.dry_run)

if __name__ == "__main__":
    main()
