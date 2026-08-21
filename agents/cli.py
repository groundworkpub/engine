"""Groundwork Master AI & SEO Command-Line Interface (CLI)

Provides terminal commands for operations across all 4 layers:
- `python -m agents.cli authority ...`
- `python -m agents.cli simulate ...`
- `python -m agents.cli distribution (social|audio|podcast|zenodo|webmention|fediverse|archive|all) ...`
- `python -m agents.cli egress (test|status) ...`
- `python -m agents.cli journey (run|list) ...`
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from typing import Any

import httpx

# Ensure project root and agents directory are in sys.path
_current_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_current_dir)
for _p in [_project_root, _current_dir]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from agents.authority_injector import TIMEOUT, get_supabase_client, run_syndication_for_article
except ImportError:
    from authority_injector import TIMEOUT, get_supabase_client, run_syndication_for_article


# ==============================================================================
# AUTHORITY & SIMULATION STATS
# ==============================================================================


def cmd_authority_stats(supabase: Any) -> None:
    """Displays real-time Authority Engine injection statistics."""
    print("=" * 60)
    print(" 🚀 GROUNDWORK 3-TIER AUTHORITY ENGINE STATS")
    print("=" * 60)

    # Fetch total targets
    targets_res = (
        supabase.table("authority_targets").select("domain,platform_name,tier_level,dr_rating,status").execute()
    )
    targets = targets_res.data or []
    print(f"\n[Registered Authority Targets ({len(targets)})]:")
    for t in targets:
        print(f"  • {t['platform_name']:<38} [{t['tier_level'].upper()}] DR: {t['dr_rating']:<3} Status: {t['status']}")

    # Fetch total logs
    logs_res = (
        supabase.table("link_injection_logs")
        .select("id,source_slug,target_platform,tier_level,live_backlink_url,status,created_at")
        .order("created_at", desc=True)
        .limit(10)
        .execute()
    )
    logs = logs_res.data or []
    print("\n[Recent Injected Backlinks (Last 10)]:")
    if not logs:
        print("  (No injected backlinks logged yet. Run a syndication job to start!)")
    for log in logs:
        print(
            f"  • [{log['tier_level'].upper()}] {log['source_slug']:<25} ➔ {log['target_platform']:<10} {log['live_backlink_url']}"
        )

    print("=" * 60)


def cmd_verify_links(supabase: Any) -> None:
    """Crawls all live backlink URLs to verify HTTP 200 OK status."""
    print("🔍 Checking live status of all injected backlinks...")
    logs_res = supabase.table("link_injection_logs").select("id,live_backlink_url,target_platform").execute()
    logs = logs_res.data or []
    if not logs:
        print("No backlinks to verify.")
        return

    verified_count = 0
    failed_count = 0

    with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
        for log in logs:
            url = log.get("live_backlink_url")
            if not url:
                continue
            try:
                resp = client.get(url)
                if resp.status_code == 200:
                    print(f"  ✅ [200 OK] {log['target_platform']:<12} {url}")
                    verified_count += 1
                else:
                    print(f"  ⚠️ [{resp.status_code}] {log['target_platform']:<12} {url}")
                    failed_count += 1
            except Exception as exc:
                print(f"  ❌ [ERR] {log['target_platform']:<12} {url} ({exc})")
                failed_count += 1

    print(f"\nSummary: {verified_count} Live / {failed_count} Issues.")


def cmd_simulation_stats(supabase: Any) -> None:
    """Displays real-time Synthetic Engagement & Traffic Simulation statistics."""
    print("=" * 60)
    print(" 🤖 GROUNDWORK ORGANIC ENGAGEMENT SIMULATOR STATS")
    print("=" * 60)

    try:
        res = supabase.rpc("get_synthetic_engagement_summary").execute()
        summary = res.data or {}
    except Exception as exc:
        print(f"Error fetching engagement summary: {exc}")
        summary = {}

    total = summary.get("total_sessions", 0)
    dwell = summary.get("avg_dwell_time_seconds", 0)
    scroll = summary.get("avg_scroll_depth_percent", 0)
    geo = summary.get("geo_breakdown", {})
    actions = summary.get("action_counts", {})

    print("\n[Summary Metrics]:")
    print(f"  • Total Sessions Completed : {total}")
    print(f"  • Average Dwell Time       : {dwell}s (Target: 90–240s)")
    print(f"  • Average Scroll Depth     : {scroll}%")

    print("\n[Tier-1 Geo Demographics]:")
    if geo:
        for k, v in geo.items():
            print(f"  • {k:<8} : {v} sessions")
    else:
        print("  (No geo records yet)")

    print("\n[User Actions Triggered]:")
    if actions:
        for act, cnt in actions.items():
            print(f"  • {act:<28} : {cnt} times")
    else:
        print("  (No actions recorded yet)")

    # Fetch recent session logs
    logs_res = (
        supabase.table("synthetic_engagement_logs")
        .select(
            "session_id,article_slug,target_platform,geo_region,dwell_time_seconds,scroll_depth_percent,status,created_at"
        )
        .order("created_at", desc=True)
        .limit(8)
        .execute()
    )
    logs = logs_res.data or []
    print("\n[Recent Simulated Sessions (Last 8)]:")
    if not logs:
        print("  (No sessions recorded yet. Run `python -m agents.cli simulate run` to start!)")
    for log in logs:
        print(
            f"  • [{log['geo_region']}] {log['target_platform']:<10} {log['dwell_time_seconds']:>3}s {log['scroll_depth_percent']:>2}% | {log['article_slug']}"
        )

    print("=" * 60)


# ==============================================================================
# INTERACTIVE MASTER CONTROL CENTER MENU (TUI)
# ==============================================================================


def run_interactive_menu(supabase: Any = None) -> None:
    """Master Interactive Control Center for Groundwork AI & Operations."""
    import subprocess
    import webbrowser

    while True:
        print("\n" + "═" * 70)
        print("  🏛️  GROUNDWORK MASTER CONTROL CENTER (LOCAL OPERATIONS)")
        print("═" * 70)
        print("  [1]  🌐 Open Localhost Web Dashboard (http://localhost:3000/dashboard)")
        print("  [2]  📰 Run Content Production Pipeline (Scouter → Critic → Scribe)")
        print("  [3]  🔍 SEO Observer & Google Indexing Inspection")
        print("  [4]  📢 Herald Social Media Publisher (Bluesky / Pinterest / X)")
        print("  [5]  🔗 3-Tier Backlink Syndication & Authority Engine")
        print("  [6]  🎙️ Audio Producer & Studio Voice Narration (ElevenLabs)")
        print("  [7]  ✉️ Envoy Digital PR & Journalist Pitching (HARO / Qwoted)")
        print("  [8]  🔍 Link Watch, Mentions & Toxic Backlink Audit")
        print("  [9]  🛡️ Egress Proxy Mesh & WAF Bypass Diagnostics")
        print("  [10] 🤖 Organic Engagement & Traffic Simulator (DataImpulse)")
        print("  [11] 📊 Opik Telemetry & Headroom Token Compression")
        print("  [12] 🧪 Full System Health & Quality Gate Check (Format + Lint + Tests)")
        print("  [13] 🧠 CodeGraph AST Intelligence & Symbol Explorer")
        print("  [0]  🚪 Exit Control Center")
        print("═" * 70)

        try:
            choice = input("Pilih menu [0-13]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n👋 Keluar dari Groundwork Control Center.\n")
            break

        if choice == "0":
            print("\n👋 Sampai jumpa! Groundwork Control Center ditutup.\n")
            break

        elif choice == "1":
            print("\n🌐 Membuka Localhost Web Dashboard...")
            url = "http://localhost:3000/dashboard"
            server_ready = False
            try:
                with httpx.Client(timeout=2.0) as client:
                    resp = client.get("http://localhost:3000/api/health")
                    if resp.status_code == 200:
                        print("  ✅ Next.js dev server aktif di port 3000.")
                        server_ready = True
            except Exception:
                pass

            if not server_ready:
                print("  ⚡ Menyambungkan & menyalakan Next.js dev server di background...")
                subprocess.Popen(
                    ["npm", "run", "dev"],
                    cwd=_project_root,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                time.sleep(3)

            webbrowser.open(url)
            print(f"  🚀 Dashboard dibuka di browser: {url}")

        elif choice == "2":
            print("\n📰 CONTENT PRODUCTION PIPELINE")
            print("  1. Full Pipeline (Scouter → Critic → Scribe)")
            print("  2. Dry Run (Scouter & Critic tanpa publish)")
            print("  3. Scribe Single Article Refinement")
            sub = input("  Pilih opsi [1-3, enter=batal]: ").strip()
            if sub == "1":
                subprocess.run([sys.executable, os.path.join(_current_dir, "pipeline.py")])
            elif sub == "2":
                subprocess.run([sys.executable, os.path.join(_current_dir, "pipeline.py"), "--dry-run"])
            elif sub == "3":
                slug = input("  Masukkan slug artikel: ").strip()
                if slug:
                    subprocess.run([sys.executable, os.path.join(_current_dir, "scribe.py"), "--slug", slug])

        elif choice == "3":
            print("\n🔍 SEO OBSERVER & GSC INSPECTION")
            print("  1. Inspect Single URL")
            print("  2. Batch Indexation Audit (Top Decaying Articles)")
            sub = input("  Pilih opsi [1-2, enter=batal]: ").strip()
            if sub == "1":
                url = input("  Masukkan URL/slug artikel: ").strip()
                if url:
                    subprocess.run([sys.executable, os.path.join(_current_dir, "seo_observer.py"), "--inspect-url", url])
            elif sub == "2":
                subprocess.run([sys.executable, os.path.join(_current_dir, "seo_observer.py"), "--batch-inspect", "--limit", "20"])

        elif choice == "4":
            print("\n📢 HERALD SOCIAL MEDIA AMPLIFIER")
            print("  1. Publish latest unshared articles to Bluesky / Pinterest")
            print("  2. Dry run preview social cards")
            sub = input("  Pilih opsi [1-2, enter=batal]: ").strip()
            if sub == "1":
                subprocess.run([sys.executable, os.path.join(_current_dir, "herald.py")])
            elif sub == "2":
                subprocess.run([sys.executable, os.path.join(_current_dir, "herald.py"), "--dry-run"])

        elif choice == "5":
            print("\n🔗 3-TIER AUTHORITY & BACKLINK ENGINE")
            print("  1. View Authority Target Stats")
            print("  2. Verify Live Injected Backlinks")
            print("  3. Run Syndication Batch (Dev.to / Blogger / IndexNow)")
            print("  4. Sync Whitepapers to GitHub Pages (DR 96)")
            sub = input("  Pilih opsi [1-4, enter=batal]: ").strip()
            if sub == "1" and supabase:
                cmd_authority_stats(supabase)
            elif sub == "2" and supabase:
                cmd_verify_links(supabase)
            elif sub == "3":
                subprocess.run([sys.executable, os.path.join(_current_dir, "syndicator.py"), "--limit", "5"])
            elif sub == "4":
                subprocess.run([sys.executable, os.path.join(_current_dir, "github_pages_syncer.py")])

        elif choice == "6":
            print("\n🎙️ AUDIO PRODUCER & PODCAST GENERATOR")
            print("  1. Generate Audio Narration for Top Articles")
            print("  2. Check Podcast RSS Feed Integrity")
            sub = input("  Pilih opsi [1-2, enter=batal]: ").strip()
            if sub == "1":
                subprocess.run([sys.executable, os.path.join(_current_dir, "audio_producer.py"), "--backfill-top", "3"])
            elif sub == "2":
                try:
                    with httpx.Client(timeout=5.0) as client:
                        resp = client.get("http://localhost:3000/podcast/feed.xml")
                        print(f"  Podcast Feed Status: HTTP {resp.status_code}")
                except Exception as e:
                    print(f"  Tidak dapat mengakses feed lokal: {e}")

        elif choice == "7":
            print("\n✉️ ENVOY DIGITAL PR & HARO PITCHING")
            print("  1. Ingest Media Queries & Draft Expert Commentary")
            print("  2. View Pending Outreach Drafts")
            sub = input("  Pilih opsi [1-2, enter=batal]: ").strip()
            if sub == "1":
                subprocess.run([sys.executable, os.path.join(_current_dir, "envoy.py")])
            elif sub == "2":
                subprocess.run([sys.executable, os.path.join(_current_dir, "link_drafts.py")])

        elif choice == "8":
            print("\n🔍 LINK WATCH & BACKLINK AUDIT")
            print("  1. Scan for New Unlinked Brand Mentions")
            print("  2. Run Toxic Backlink Scan (Link Audit)")
            sub = input("  Pilih opsi [1-2, enter=batal]: ").strip()
            if sub == "1":
                subprocess.run([sys.executable, os.path.join(_current_dir, "link_watch.py")])
            elif sub == "2":
                subprocess.run([sys.executable, os.path.join(_current_dir, "link_audit.py")])

        elif choice == "9":
            print("\n🛡️ EGRESS PROXY MESH & WAF DIAGNOSTICS")
            subprocess.run([sys.executable, os.path.join(_current_dir, "egress_selector.py"), "--test"])

        elif choice == "10":
            print("\n🤖 ORGANIC TRAFFIC & ENGAGEMENT SIMULATOR")
            print("  1. Run Background Simulation (Headless)")
            print("  2. Run Visual Simulation (Headed Chromium Window)")
            print("  3. View Real-time Telemetry Stats")
            sub = input("  Pilih opsi [1-3, enter=batal]: ").strip()
            if sub == "1":
                subprocess.run([sys.executable, os.path.join(_current_dir, "traffic_cli.py"), "--concurrency", "2"])
            elif sub == "2":
                subprocess.run([sys.executable, os.path.join(_current_dir, "traffic_cli.py"), "--headed", "--concurrency", "1"])
            elif sub == "3" and supabase:
                cmd_simulation_stats(supabase)

        elif choice == "11":
            print("\n📊 OPIK TELEMETRY & TOKEN COMPRESSION")
            try:
                from agents.eval_tracer import OpikTracer
                tracer = OpikTracer()
                stats = tracer.get_summary_stats()
                print(f"  • Total Spans: {stats['total_spans']} | Avg Latency: {stats['avg_latency_ms']}ms | Success: {stats['success_rate_pct']}%")
            except Exception as e:
                print(f"  Opik Tracer: {e}")

        elif choice == "12":
            print("\n🧪 RUNNING FULL SYSTEM QUALITY GATE...")
            subprocess.run(["npm", "run", "format:check"], cwd=_project_root)
            subprocess.run(["npm", "run", "lint"], cwd=_project_root)
            subprocess.run(["npx", "tsc", "--noEmit"], cwd=_project_root)
            subprocess.run(["npm", "test"], cwd=_project_root)

        elif choice == "13":
            print("\n🧠 CODEGRAPH AST INTELLIGENCE & SYMBOL EXPLORER")
            print("  1. Query Symbol / Function / Route / Interface")
            print("  2. Rebuild CodeGraph AST Index (.codegraph & .agents)")
            print("  3. View Graph Architecture Stats")
            sub = input("  Pilih opsi [1-3, enter=batal]: ").strip()
            if sub == "1":
                sym = input("  Masukkan nama symbol (misal: ArticleHomeItem, run_scouter): ").strip()
                if sym:
                    subprocess.run(["npx", "tsx", "scripts/codebase_graph.ts", "query", sym], cwd=_project_root)
            elif sub == "2":
                subprocess.run(["npx", "tsx", "scripts/codebase_graph.ts", "build"], cwd=_project_root)
            elif sub == "3":
                subprocess.run(["npx", "tsx", "scripts/codebase_graph.ts", "stats"], cwd=_project_root)

        try:
            input("\n[Tekan Enter untuk kembali ke menu utama]")
        except (KeyboardInterrupt, EOFError):
            break


# ==============================================================================
# MAIN CLI ENTRYPOINT
# ==============================================================================


def main() -> None:
    parser = argparse.ArgumentParser(description="Groundwork Unified AI, Distribution & Operations CLI")
    subparsers = parser.add_subparsers(dest="subcommand", help="Available modules")

    # 1. Authority Subcommand
    auth_parser = subparsers.add_parser("authority", help="Authority Engine & 3-Tier Syndication")
    auth_sub = auth_parser.add_subparsers(dest="action", help="Authority actions")

    synd_parser = auth_sub.add_parser("syndicate", help="Syndicate single article")
    synd_parser.add_argument("--slug", required=True, help="Slug of the article")
    synd_parser.add_argument("--draft", action="store_true", help="Post as draft")

    batch_parser = auth_sub.add_parser("batch-all", help="Batch syndicate all published articles")
    batch_parser.add_argument("--limit", type=int, default=5, help="Number of articles to process")
    batch_parser.add_argument("--draft", action="store_true", help="Post as draft")

    auth_sub.add_parser("stats", help="Show authority stats and recent injections")
    auth_sub.add_parser("verify-links", help="Verify live backlink status")

    gh_parser = auth_sub.add_parser("sync-github-pages", help="Build and push whitepapers to GitHub Pages (DR 96)")
    gh_parser.add_argument("--limit", type=int, default=50, help="Number of articles to sync")

    # 2. Simulate Subcommand (Fase OS)
    sim_parser = subparsers.add_parser("simulate", help="Autonomous Organic Engagement & Traffic Simulator")
    sim_sub = sim_parser.add_subparsers(dest="action", help="Simulator actions")

    sim_run_parser = sim_sub.add_parser("run", help="Run organic discovery & engagement simulation")
    sim_run_parser.add_argument("--limit", type=int, default=3, help="Number of article targets to simulate")
    sim_run_parser.add_argument("--slug", type=str, default=None, help="Specific article slug to target")
    sim_run_parser.add_argument("--headed", action="store_true", help="Open visible Chromium GUI window")
    sim_run_parser.add_argument("--concurrency", type=int, default=1, help="Concurrent worker sessions (1-10)")
    sim_run_parser.add_argument(
        "--channel",
        choices=["google_search", "youtube_gworky", "topic_silo", "all"],
        default="all",
        help="Referral source channel",
    )
    sim_run_parser.add_argument("--ai-brain", action="store_true", help="Enable LLM reasoning")
    sim_run_parser.add_argument("--dashboard", action="store_true", help="Open Localhost CMS dashboard")
    sim_run_parser.add_argument("--dry-run", action="store_true", help="Run without persisting DB logs")

    sim_sub.add_parser("stats", help="Show simulation telemetry statistics")

    # 3. Distribution Subcommand (Unified Distribution Hub)
    dist_parser = subparsers.add_parser("distribution", help="Federated Content & Authority Distribution")
    dist_sub = dist_parser.add_subparsers(dest="action", help="Distribution channels")

    # social (herald: bluesky + pinterest)
    social_p = dist_sub.add_parser("social", help="Post to Bluesky & Pinterest via Herald")
    social_p.add_argument("--slug", default=None, help="Target specific article slug")
    social_p.add_argument("--limit", type=int, default=3, help="Max articles to amplify")
    social_p.add_argument("--dry-run", action="store_true", help="Preview without posting")

    # audio
    audio_p = dist_sub.add_parser("audio", help="Generate TTS audio reader & video audiograms")
    audio_p.add_argument("--slug", default=None, help="Target specific article slug")
    audio_p.add_argument("--backfill-top", type=int, default=0, help="Backfill N most recent articles")
    audio_p.add_argument("--video", action="store_true", help="Generate MP4 video audiograms")
    audio_p.add_argument("--dry-run", action="store_true", help="Preview without generating")

    # video (YouTube Shorts 9:16 & Longform 16:9 Broadcaster)
    video_p = dist_sub.add_parser("video", help="Render YouTube Shorts 9:16 or Longform 16:9 MP4 with FFmpeg")
    video_p.add_argument("--slug", required=True, help="Target article slug")
    video_p.add_argument("--format", choices=["shorts", "landscape"], default="shorts", help="Video format (shorts=9:16, landscape=16:9)")
    video_p.add_argument("--out", default=None, help="Output MP4 destination path")

    # podcast
    pod_p = dist_sub.add_parser("podcast", help="Submit RSS podcast feeds to Podcast Index & WebSub")
    pod_p.add_argument("--dry-run", action="store_true", help="Preview without submitting")

    # zenodo
    zen_p = dist_sub.add_parser("zenodo", help="Deposit articles to CERN Zenodo for citable DOIs")
    zen_p.add_argument("--slug", default=None, help="Target article slug")
    zen_p.add_argument("--batch-all", action="store_true", help="Batch deposit all flagship articles")
    zen_p.add_argument("--limit", type=int, default=10, help="Max articles")
    zen_p.add_argument("--sandbox", action="store_true", help="Use Zenodo Sandbox environment")
    zen_p.add_argument("--dry-run", action="store_true", help="Preview without depositing")

    # webmention
    wm_p = dist_sub.add_parser("webmention", help="Send W3C Webmentions to cited external domains")
    wm_p.add_argument("--slug", default=None, help="Target article slug")
    wm_p.add_argument("--batch-all", action="store_true", help="Batch send for all recent articles")
    wm_p.add_argument("--limit", type=int, default=20, help="Max articles")
    wm_p.add_argument("--dry-run", action="store_true", help="Preview without sending")

    # fediverse
    fed_p = dist_sub.add_parser("fediverse", help="Publish research summaries to Mastodon/ActivityPub")
    fed_p.add_argument("--slug", default=None, help="Target article slug")
    fed_p.add_argument("--batch-all", action="store_true", help="Batch post recent articles")
    fed_p.add_argument("--limit", type=int, default=5, help="Max articles")
    fed_p.add_argument("--dry-run", action="store_true", help="Preview without posting")

    # archive
    arc_p = dist_sub.add_parser("archive", help="Permanently archive snapshots to Wayback Machine")
    arc_p.add_argument("--slug", default=None, help="Target article slug")
    arc_p.add_argument("--batch-all", action="store_true", help="Batch archive recent articles")
    arc_p.add_argument("--limit", type=int, default=20, help="Max articles")
    arc_p.add_argument("--dry-run", action="store_true", help="Preview without archiving")

    # all (orchestrate across all channels)
    all_p = dist_sub.add_parser("all", help="Distribute across ALL federated channels simultaneously")
    all_p.add_argument("--slug", default=None, help="Target specific article slug")
    all_p.add_argument("--batch-all", action="store_true", help="Process batch of recent articles")
    all_p.add_argument("--limit", type=int, default=3, help="Max articles")
    all_p.add_argument("--dry-run", action="store_true", help="Dry run all channels")

    # 4. Egress Subcommand
    egress_p = subparsers.add_parser("egress", help="Egress Mesh, Proxy Selector & Routing Diagnostics")
    egress_sub = egress_p.add_subparsers(dest="action", help="Egress actions")
    egress_sub.add_parser("test", help="Test and health-check all egress routes")
    egress_sub.add_parser("status", help="Display current egress status and active IP")

    # 5. Journey Subcommand
    jour_p = subparsers.add_parser("journey", help="Scenario-Driven User Journey QA & Synthetic Telemetry")
    jour_sub = jour_p.add_subparsers(dest="action", help="Journey actions")

    jour_run_p = jour_sub.add_parser("run", help="Run user journey scenario")
    jour_run_p.add_argument(
        "--scenario",
        default="research_article",
        help="Scenario name (research_article|youtube_referral|calculator_flow|newsletter_signup)",
    )
    jour_run_p.add_argument("--slug", default=None, help="Target article slug")
    jour_run_p.add_argument("--headed", action="store_true", help="Show browser window")
    jour_run_p.add_argument("--engine", default="chromium", choices=["chromium", "camoufox"], help="Browser engine")
    jour_run_p.add_argument("--dry-run", action="store_true", help="Preview steps without browser")

    # 6. Proxy Pool Subcommand (Zero-Cost Egress Hardening)
    pp_p = subparsers.add_parser("proxy-pool", help="Hardened Public Proxy Pool & Local Forward Adapter")
    pp_p.add_argument("--harvest", action="store_true", help="Harvest fresh proxies from public feeds")
    pp_p.add_argument("--validate", action="store_true", help="Validate candidate proxies in SQLite cache")
    pp_p.add_argument("--limit", type=int, default=100, help="Max candidate proxies to validate")
    pp_p.add_argument("--get", action="store_true", help="Print best active proxy URL")
    pp_p.add_argument("--stats", action="store_true", help="Show pool statistics")

    # 7. SEO MCP Intelligence Subcommand
    mcp_p = subparsers.add_parser("seo-mcp", help="Groundwork Full-Scope SEO Intelligence & Tri-Mode FastMCP Server")
    mcp_p.add_argument("--test", action="store_true", help="Test all 6 SEO tools")
    mcp_p.add_argument("--serve", action="store_true", help="Run local HTTP REST server")
    mcp_p.add_argument("--port", type=int, default=8080, help="Port for HTTP server")
    mcp_p.add_argument("--inspect", help="Inspect a specific URL")
    mcp_p.add_argument("--aeo", help="Score AEO/GEO for an article slug")
    mcp_p.add_argument("--paa", help="Extract PAA questions for a keyword")
    mcp_p.add_argument("--decay", action="store_true", help="List decaying articles")
    mcp_p.add_argument("--cannibalization", action="store_true", help="Detect keyword conflicts")

    # 8. Refine Article Subcommand (Closed-Loop Content Remediation)
    ref_p = subparsers.add_parser("refine-article", help="Trigger Scribe AI to remediate decaying article content")
    ref_p.add_argument("--slug", required=True, help="Slug of article to remediate")
    ref_p.add_argument("--queries", default="how to guide, review 2026", help="Comma-separated top search queries")
    ref_p.add_argument("--impressions", type=int, default=1200, help="GSC impression count")
    ref_p.add_argument("--ctr", type=float, default=0.015, help="Current click-through rate")

    # 9. Compress Subcommand (Headroom Token Compressor)
    comp_p = subparsers.add_parser("compress", help="Compress raw HTML or text snippets using Headroom token compressor")
    comp_p.add_argument("--sample", default=None, help="Sample raw HTML / text string")
    comp_p.add_argument("--file", default=None, help="Path to input text/HTML file")
    comp_p.add_argument("--max-chars", type=int, default=3000, help="Target max chars")

    # 10. Humanize Subcommand (Editorial Anti-AI-Slop Engine)
    hum_p = subparsers.add_parser("humanize", help="Sanitize AI slop words and compute sentence burstiness")
    hum_p.add_argument("--sample", default=None, help="Sample text string to humanize")
    hum_p.add_argument("--file", default=None, help="Path to input text file")

    # 11. Trace Subcommand (Opik-Compatible Local Telemetry)
    trc_p = subparsers.add_parser("trace", help="View Opik-compatible LLM tracing spans and evaluation metrics")
    trc_p.add_argument("--limit", type=int, default=10, help="Number of recent spans to show")
    trc_p.add_argument("--summary", action="store_true", help="Show aggregate performance summary")

    # 12. Optimize Prompt Subcommand
    opt_p = subparsers.add_parser("optimize-prompt", help="Analyze and evaluate prompt variations and rubric scoring")
    opt_p.add_argument("--pillar", default="tech", choices=["money", "body", "home", "life", "tech"], help="Content pillar")
    opt_p.add_argument("--eval-file", default=None, help="JSON file containing ScribeOutput payload to score")

    # 13. Satellite Subcommand (emailforums.biz & Multi-Tenant Sync)
    sat_p = subparsers.add_parser("satellite", help="WordPress Satellite & Guest Submission Operations (emailforums.biz)")
    sat_sub = sat_p.add_subparsers(dest="action", help="Satellite actions")
    sat_sync = sat_sub.add_parser("sync", help="Sync unsynced articles to WordPress satellite")
    sat_sync.add_argument("--limit", type=int, default=10, help="Max articles to sync")
    sat_sync.add_argument("--sync-expired", action="store_true", help="Sync AI-modernized expired routes")
    sat_sync.add_argument("--dry-run", action="store_true", help="Preview without publishing")

    sat_guest = sat_sub.add_parser("process-guests", help="Process pending guest submissions with AI co-citation")
    sat_guest.add_argument("--limit", type=int, default=5, help="Max guest submissions to process")
    sat_guest.add_argument("--dry-run", action="store_true", help="Preview without publishing")

    sat_roundup = sat_sub.add_parser("roundup", help="Generate and publish weekly industry research roundup")
    sat_roundup.add_argument("--pillar", default="money", choices=["money", "body", "home", "life", "tech"], help="Target pillar")
    sat_roundup.add_argument("--all-pillars", action="store_true", help="Generate for all 5 pillars")
    sat_roundup.add_argument("--dry-run", action="store_true", help="Preview without publishing")

    # 14. Expired Domain Subcommand (Tier-0 Authority Network)
    exp_p = subparsers.add_parser("expired", help="Expired Domain Authority Recovery & Edge Redirection Network")
    exp_sub = exp_p.add_subparsers(dest="action", help="Expired domain actions")
    exp_harv = exp_sub.add_parser("harvest", help="Harvest historical routes from Wayback CDX API")
    exp_harv.add_argument("--domain", required=True, help="Expired domain (e.g. emailforums.biz)")
    exp_harv.add_argument("--limit", type=int, default=30, help="Max routes to harvest")
    exp_harv.add_argument("--dry-run", action="store_true", help="Preview without saving")

    exp_rew = exp_sub.add_parser("rewrite", help="Modernize archived routes to 2026 with LiteLLM semantic bridge")
    exp_rew.add_argument("--limit", type=int, default=10, help="Max routes to rewrite")
    exp_rew.add_argument("--dry-run", action="store_true", help="Preview without saving")

    exp_idx = exp_sub.add_parser("index", help="Dispatch Google Indexing API & IndexNow for active routes")
    exp_idx.add_argument("--limit", type=int, default=30, help="Max URLs to index")
    exp_idx.add_argument("--url", default=None, help="Submit a single specific URL")
    exp_idx.add_argument("--dry-run", action="store_true", help="Preview without submitting")

    exp_sub.add_parser("status", help="Display overall Expired Domain & Satellite network status")

    parser.add_argument("-i", "--interactive", "--menu", action="store_true", help="Open interactive Master Control Center TUI")
    args = parser.parse_args()

    try:
        supabase = get_supabase_client()
    except Exception:
        supabase = None

    if not args.subcommand or getattr(args, "interactive", False):
        run_interactive_menu(supabase)
        return

    # ── Execute Subcommands ──────────────────────────────────────────

    if args.subcommand == "authority":
        if not supabase:
            print("❌ Supabase required for authority operations.")
            sys.exit(1)
        if args.action == "syndicate":
            res = supabase.table("articles").select("*").eq("slug", args.slug).execute()
            if not res.data:
                print(f"Article '{args.slug}' not found.")
                return
            run_syndication_for_article(supabase, res.data[0], live=not args.draft)
        elif args.action == "batch-all":
            res = (
                supabase.table("articles")
                .select("*")
                .eq("status", "published")
                .order("published_at", desc=True)
                .limit(args.limit)
                .execute()
            )
            articles = res.data or []
            print(f"Starting batch syndication for {len(articles)} articles...")
            for idx, art in enumerate(articles, 1):
                print(f"[{idx}/{len(articles)}] Processing {art['slug']}...")
                run_syndication_for_article(supabase, art, live=not args.draft)
                if idx < len(articles):
                    print("Waiting 15 seconds to respect API platform rate limits...")
                    time.sleep(15)
        elif args.action == "sync-github-pages":
            try:
                from agents.github_pages_syncer import build_and_push_github_pages
            except ImportError:
                from github_pages_syncer import build_and_push_github_pages
            build_and_push_github_pages(limit=args.limit)
        elif args.action == "stats":
            cmd_authority_stats(supabase)
        elif args.action == "verify-links":
            cmd_verify_links(supabase)
        else:
            auth_parser.print_help()

    elif args.subcommand == "simulate":
        if not supabase:
            print("❌ Supabase required for simulate operations.")
            sys.exit(1)
        if args.action == "run":
            try:
                from agents.traffic_cli import execute_batch_concurrency, fetch_targets
            except ImportError:
                from traffic_cli import execute_batch_concurrency, fetch_targets

            if args.dashboard:
                import webbrowser

                webbrowser.open("http://localhost:3000/dashboard/agents")

            targets = fetch_targets(supabase, limit=args.limit, specific_slug=args.slug)
            asyncio.run(
                execute_batch_concurrency(
                    targets=targets,
                    supabase=supabase,
                    concurrency=args.concurrency,
                    headed=args.headed,
                    use_ai=args.ai_brain,
                    channel=args.channel,
                    dry_run=args.dry_run,
                )
            )
        elif args.action == "stats":
            cmd_simulation_stats(supabase)
        else:
            sim_parser.print_help()

    elif args.subcommand == "distribution":
        if args.action == "social":
            try:
                from agents.herald import main as herald_main
            except ImportError:
                from herald import main as herald_main
            old_argv = sys.argv
            sys.argv = ["herald"]
            if args.dry_run:
                sys.argv.append("--dry-run")
            if args.slug:
                sys.argv.extend(["--slug", args.slug])
            if args.limit:
                sys.argv.extend(["--limit", str(args.limit)])
            try:
                herald_main()
            finally:
                sys.argv = old_argv

        elif args.action == "audio":
            try:
                from agents.audio_producer import main as audio_main
            except ImportError:
                from audio_producer import main as audio_main
            # Set argv for audio_producer
            sys.argv = ["audio_producer"]
            if args.slug:
                sys.argv.extend(["--slug", args.slug])
            if args.backfill_top:
                sys.argv.extend(["--backfill-top", str(args.backfill_top)])
            if args.video:
                sys.argv.append("--video")
            if args.dry_run:
                sys.argv.append("--dry-run")
            audio_main()

        elif args.action == "video":
            import urllib.parse
            try:
                from agents.broadcaster import VideoBroadcaster
            except ImportError:
                from broadcaster import VideoBroadcaster
            broadcaster = VideoBroadcaster()
            episode = broadcaster.fetch_episode(args.slug)
            if not episode:
                print(f"❌ Episode/Article '{args.slug}' not found.")
                return
            out_file = args.out or f"public/audio/videos/{args.slug}_{args.format}.mp4"
            os.makedirs(os.path.dirname(os.path.abspath(out_file)), exist_ok=True)
            is_shorts = args.format == "shorts"
            cover_url = (
                f"{broadcaster.site_url}/api/og?format=shorts&pillar={episode.get('pillar', 'money')}&title={urllib.parse.quote_plus(episode.get('title', ''))}"
                if is_shorts
                else f"{broadcaster.site_url}/api/og?format=youtube&pillar={episode.get('pillar', 'money')}&title={urllib.parse.quote_plus(episode.get('title', ''))}"
            )
            audio_url = episode.get("audio_url") or f"{broadcaster.site_url}/api/audio/{args.slug}.mp3"
            success = broadcaster.generate_video(
                audio_path_or_url=audio_url,
                cover_path_or_url=cover_url,
                output_mp4=out_file,
                format_mode=args.format,
            )
            if success:
                metadata = broadcaster.build_youtube_metadata(episode, is_shorts=is_shorts)
                print(f"✅ Video generated at {out_file}")
                print(f"YouTube Metadata [{args.format.upper()}]:\n{json.dumps(metadata, indent=2)}")

        elif args.action == "podcast":
            try:
                from agents.podcast_distributor import main as pod_main
            except ImportError:
                from podcast_distributor import main as pod_main
            pod_main()

        elif args.action == "zenodo":
            try:
                from agents.distribution_zenodo import ZenodoEngine
            except ImportError:
                from distribution_zenodo import ZenodoEngine
            engine = ZenodoEngine(sandbox=args.sandbox, dry_run=args.dry_run)
            if args.slug:
                if not supabase:
                    print("❌ Supabase required to fetch article.")
                    return
                res = supabase.table("articles").select("*").eq("slug", args.slug).maybe_single().execute()
                if res.data:
                    res_dep = engine.deposit_article(res.data)
                    print(f"Result: {res_dep}")
                else:
                    print(f"Article '{args.slug}' not found.")
            elif args.batch_all:
                if not supabase:
                    print("❌ Supabase required.")
                    return
                res = (
                    supabase.table("articles")
                    .select("*")
                    .eq("status", "published")
                    .eq("is_flagship", True)
                    .limit(args.limit)
                    .execute()
                )
                results = engine.batch_deposit(res.data or [], limit=args.limit)
                print(f"Batch Zenodo completed: {len(results)} items.")
            else:
                zen_p.print_help()

        elif args.action == "webmention":
            try:
                from agents.distribution_webmention import WebmentionSender
            except ImportError:
                from distribution_webmention import WebmentionSender
            sender = WebmentionSender(dry_run=args.dry_run)
            if args.slug:
                if not supabase:
                    print("❌ Supabase required.")
                    return
                res = supabase.table("articles").select("slug, content").eq("slug", args.slug).maybe_single().execute()
                if res.data:
                    results = sender.process_article(res.data)
                    print(f"Webmention result: {len(results)} targets processed.")
            elif args.batch_all:
                if not supabase:
                    print("❌ Supabase required.")
                    return
                res = (
                    supabase.table("articles")
                    .select("slug, content")
                    .eq("status", "published")
                    .limit(args.limit)
                    .execute()
                )
                for art in res.data or []:
                    sender.process_article(art)
            else:
                wm_p.print_help()

        elif args.action == "fediverse":
            try:
                from agents.distribution_fediverse import FediversePublisher
            except ImportError:
                from distribution_fediverse import FediversePublisher
            pub = FediversePublisher(dry_run=args.dry_run)
            if args.slug:
                if not supabase:
                    print("❌ Supabase required.")
                    return
                res = (
                    supabase.table("articles")
                    .select("slug, title, excerpt, pillar")
                    .eq("slug", args.slug)
                    .maybe_single()
                    .execute()
                )
                if res.data:
                    res_p = pub.post(res.data)
                    print(f"Fediverse result: {res_p}")
            elif args.batch_all:
                if not supabase:
                    print("❌ Supabase required.")
                    return
                res = (
                    supabase.table("articles")
                    .select("slug, title, excerpt, pillar")
                    .eq("status", "published")
                    .limit(args.limit)
                    .execute()
                )
                for art in res.data or []:
                    pub.post(art)
            else:
                fed_p.print_help()

        elif args.action == "archive":
            try:
                from agents.distribution_archive import WaybackArchiver
            except ImportError:
                from distribution_archive import WaybackArchiver
            archiver = WaybackArchiver(dry_run=args.dry_run)
            site_url = os.environ.get("NEXT_PUBLIC_SITE_URL", "https://gworky.com").rstrip("/")
            if args.slug:
                url = f"{site_url}/article/{args.slug}"
                r = archiver.archive_url(url)
                print(f"Archived {url} -> {r}")
            elif args.batch_all:
                if not supabase:
                    print("❌ Supabase required.")
                    return
                res = supabase.table("articles").select("slug").eq("status", "published").limit(args.limit).execute()
                urls = [f"{site_url}/article/{a['slug']}" for a in (res.data or [])]
                archiver.batch_archive(urls)
            else:
                arc_p.print_help()

        elif args.action == "all":
            print("🚀 Executing Full Federated Distribution Pipeline...")
            slug = args.slug
            if not slug and supabase:
                res = supabase.table("articles").select("slug").eq("status", "published").limit(1).execute()
                if res.data:
                    slug = res.data[0]["slug"]

            if not slug:
                print("No article slug provided or found.")
                return

            print(f"\n[1/5] Social Amplification (Bluesky/Pinterest) for {slug}...")
            try:
                from agents.herald import main as herald_main

                old_argv = sys.argv
                sys.argv = ["herald", "--slug", slug]
                if args.dry_run:
                    sys.argv.append("--dry-run")
                try:
                    herald_main()
                finally:
                    sys.argv = old_argv
            except Exception as e:
                print(f"  ⚠️ Social broadcast error: {e}")

            print(f"\n[2/5] W3C Webmention Discovery & Dispatch for {slug}...")
            try:
                from agents.distribution_webmention import WebmentionSender

                sender = WebmentionSender(dry_run=args.dry_run)
                if supabase:
                    res = supabase.table("articles").select("slug, content").eq("slug", slug).maybe_single().execute()
                    if res.data:
                        sender.process_article(res.data)
            except Exception as e:
                print(f"  ⚠️ Webmention error: {e}")

            print(f"\n[3/5] Wayback Machine Archival Snapshot for {slug}...")
            try:
                from agents.distribution_archive import WaybackArchiver

                archiver = WaybackArchiver(dry_run=args.dry_run)
                site_url = os.environ.get("NEXT_PUBLIC_SITE_URL", "https://gworky.com").rstrip("/")
                archiver.archive_url(f"{site_url}/article/{slug}")
            except Exception as e:
                print(f"  ⚠️ Archival error: {e}")

            print(f"\n[4/5] Fediverse / Mastodon Syndication for {slug}...")
            try:
                from agents.distribution_fediverse import FediversePublisher

                pub = FediversePublisher(dry_run=args.dry_run)
                if supabase:
                    res = (
                        supabase.table("articles")
                        .select("slug, title, excerpt, pillar")
                        .eq("slug", slug)
                        .maybe_single()
                        .execute()
                    )
                    if res.data:
                        pub.post(res.data)
            except Exception as e:
                print(f"  ⚠️ Fediverse error: {e}")

            print(f"\n[5/5] Zenodo Open-Science Deposit Check for {slug}...")
            try:
                from agents.distribution_zenodo import ZenodoEngine

                zen = ZenodoEngine(dry_run=args.dry_run)
                if supabase:
                    res = supabase.table("articles").select("*").eq("slug", slug).maybe_single().execute()
                    if res.data:
                        zen.deposit_article(res.data)
            except Exception as e:
                print(f"  ⚠️ Zenodo error: {e}")

            print("\n✨ Full Federated Distribution cycle completed!")
        else:
            dist_parser.print_help()

    elif args.subcommand == "egress":
        try:
            from agents.egress_selector import SmartPolicySelector
        except ImportError:
            from egress_selector import SmartPolicySelector
        selector = SmartPolicySelector()
        if args.action == "test" or args.action == "status":
            selector.print_status()
        else:
            egress_p.print_help()

    elif args.subcommand == "journey":
        try:
            from agents.journey_runner import SCENARIOS, JourneyRunner, print_report, save_report
        except ImportError:
            from journey_runner import SCENARIOS, JourneyRunner, print_report, save_report

        if args.action == "list":
            print("\n📋 Available Journey Scenarios:")
            for name, steps in SCENARIOS.items():
                print(f"\n  🧪 {name}")
                for s in steps:
                    print(f"     • {s.name}: {s.description}")
        elif args.action == "run":
            runner = JourneyRunner(
                scenario=args.scenario,
                headed=args.headed,
                dry_run=args.dry_run,
                engine=args.engine,
                target_slug=args.slug,
            )
            report = asyncio.run(runner.run())
            print_report(report)
            path = save_report(report)
            print(f"\n  📄 Report saved: {path}")
        else:
            jour_p.print_help()

    elif args.subcommand == "proxy-pool":
        try:
            from agents.egress_public_pool import PublicProxyPool
        except ImportError:
            from egress_public_pool import PublicProxyPool

        pool = PublicProxyPool()
        if args.harvest:
            asyncio.run(pool.harvest_feeds())
        if args.validate:
            asyncio.run(pool.validate_batch(limit=args.limit))
        if args.get:
            proxy = pool.get_best_proxy()
            print(f"Top Valid Proxy: {proxy or 'None available'}")
        if args.stats or (not args.harvest and not args.validate and not args.get):
            stats = pool.db.get_stats()
            print("=" * 50)
            print(" 🛡️ GROUNDWORK PUBLIC PROXY POOL STATS")
            print("=" * 50)
            print(f"  Total Ingested  : {stats['total']}")
            print(f"  Healthy Active  : {stats['healthy']}")
            print(f"  Elite Anonymity : {stats['elite']}")
            print("=" * 50)

    elif args.subcommand == "seo-mcp":
        import subprocess
        script_path = os.path.join(_project_root, "scripts", "seo_mcp_server.py")
        cmd = [sys.executable, script_path]
        if args.test:
            cmd.append("--test")
        elif args.serve:
            cmd.extend(["--serve", "--port", str(args.port)])
        elif args.inspect:
            cmd.extend(["--inspect", args.inspect])
        elif args.aeo:
            cmd.extend(["--aeo", args.aeo])
        elif args.paa:
            cmd.extend(["--paa", args.paa])
        elif args.decay:
            cmd.append("--decay")
        elif args.cannibalization:
            cmd.append("--cannibalization")
        subprocess.run(cmd)

    elif args.subcommand == "refine-article":
        try:
            from agents.scribe import refine_decaying_article
        except ImportError:
            from scribe import refine_decaying_article

        if not supabase:
            print("❌ Supabase client required for article remediation.")
            sys.exit(1)

        queries = [q.strip() for q in args.queries.split(",") if q.strip()]
        success = refine_decaying_article(
            slug=args.slug,
            gsc_metrics={"top_queries": queries, "impressions": args.impressions, "ctr": args.ctr},
            supabase=supabase,
        )
        if success:
            print(f"✅ Article '{args.slug}' successfully remediated and updated in Supabase!")
        else:
            print(f"❌ Remediation failed for article '{args.slug}'.")

    elif args.subcommand == "compress":
        try:
            from agents.headroom_compressor import HeadroomCompressor
        except ImportError:
            from headroom_compressor import HeadroomCompressor

        raw_input = args.sample or ""
        if args.file and os.path.exists(args.file):
            with open(args.file, encoding="utf-8") as f:
                raw_input = f.read()
        elif not raw_input:
            raw_input = "<html><body><header>Nav</header><article><h1>Test Guide</h1><p>In today's fast-paced world, finding reliable financial data is crucial.</p></article><footer>Footer</footer></body></html>"

        compressed = HeadroomCompressor.compress_html(raw_input, target_chars=args.max_chars)
        stats = HeadroomCompressor.compression_stats(raw_input, compressed)

        print("=" * 60)
        print(" 🗜️ HEADROOM TOKEN COMPRESSION RESULTS")
        print("=" * 60)
        print(f"  Original Length   : {stats['original_chars']} chars (~{stats['original_tokens']} tokens)")
        print(f"  Compressed Length : {stats['compressed_chars']} chars (~{stats['compressed_tokens']} tokens)")
        print(f"  Tokens Saved      : {stats['tokens_saved']} tokens ({stats['compression_ratio_pct']}% reduction)")
        print("\n--- COMPRESSED OUTPUT ---")
        print(compressed)
        print("=" * 60)

    elif args.subcommand == "humanize":
        try:
            from agents.humanizer import EditorialHumanizer
        except ImportError:
            from humanizer import EditorialHumanizer

        raw_input = args.sample or ""
        if args.file and os.path.exists(args.file):
            with open(args.file, encoding="utf-8") as f:
                raw_input = f.read()
        elif not raw_input:
            raw_input = "Furthermore, it is crucial to delve into this testament to modern technology. In conclusion, it offers a unique blend of features."

        slop = EditorialHumanizer.find_slop_words(raw_input)
        sanitized = EditorialHumanizer.sanitize_text(raw_input)
        burst_stats = EditorialHumanizer.calculate_burstiness(sanitized)

        print("=" * 60)
        print(" ✍️ EDITORIAL HUMANIZER SANITIZATION")
        print("=" * 60)
        print(f"  Detected Slop Terms : {', '.join(slop) if slop else 'None (Clean)'}")
        print(f"  Sentence Count      : {burst_stats['sentence_count']}")
        print(f"  Burstiness Score    : {burst_stats['burstiness_score']} (Natural: {burst_stats['is_natural']})")
        print("\n--- SANITIZED HUMAN TEXT ---")
        print(sanitized)
        print("=" * 60)

    elif args.subcommand == "trace":
        try:
            from agents.eval_tracer import OpikTracer
        except ImportError:
            from eval_tracer import OpikTracer

        tracer = OpikTracer()
        summary = tracer.get_summary_stats()
        print("=" * 60)
        print(" 📊 OPIK LOCAL EVALUATION & TELEMETRY SUMMARY")
        print("=" * 60)
        print(f"  Total Traced Spans : {summary['total_spans']}")
        print(f"  Avg Latency        : {summary['avg_latency_ms']} ms")
        print(f"  Avg Rubric Score   : {summary['avg_rubric_score']} / 100")
        print(f"  Success Rate       : {summary['success_rate_pct']}%")
        print(f"  Models Distribution: {summary['models_used']}")
        print("=" * 60)

        traces = tracer.get_recent_traces(limit=args.limit)
        if traces:
            print(f"\n[Recent {len(traces)} Telemetry Spans]:")
            for t in traces:
                score_str = f"Score: {t.get('rubric_score')}/100" if t.get("rubric_score") else "Score: N/A"
                print(f"  • [{t.get('agent_name', 'agent').upper()}] {t.get('name')} | {t.get('model_name')} | {t.get('latency_ms')}ms | {score_str}")
        print("=" * 60)

    elif args.subcommand == "optimize-prompt":
        try:
            from agents.prompt_optimizer import PromptOptimizer
        except ImportError:
            from prompt_optimizer import PromptOptimizer

        if args.eval_file and os.path.exists(args.eval_file):
            with open(args.eval_file, encoding="utf-8") as f:
                payload = json.load(f)
            res = PromptOptimizer.evaluate_output_quality(payload)
            print("=" * 60)
            print(" 🎯 PROMPT EVALUATION REPORT")
            print("=" * 60)
            print(f"  Rubric Score : {res['score']}/100 ({'PASSED' if res['passed'] else 'REMEDIATE'})")
            print(f"  Word Count   : {res['word_count']}")
            print(f"  Slop Terms   : {res['slop_count']}")
            print(f"  Burstiness   : {res['burstiness']['burstiness_score']}")
            if res["critiques"]:
                print("\n  Critiques:")
                for c in res["critiques"]:
                    print(f"   - {c}")
            print("=" * 60)
        else:
            comp = PromptOptimizer.compare_prompts(args.pillar, "Sample Title", "Sample Notes")
            print("=" * 60)
            print(f" 📐 PROMPT SPECIFICATION [{args.pillar.upper()}]")
            print("=" * 60)
            print(f"  Length: {comp['prompt_length_chars']} chars (~{comp['estimated_prompt_tokens']} tokens)")
            for r in comp["recommendations"]:
                print(f"   ✓ {r}")
            print("=" * 60)

    elif args.subcommand == "satellite":
        if args.action == "sync":
            try:
                from agents.wp_publisher import fetch_unsynced_articles, get_wp_client, publish_article, publish_expired_routes
            except ImportError:
                from wp_publisher import fetch_unsynced_articles, get_wp_client, publish_article, publish_expired_routes

            wp = get_wp_client()
            if args.sync_expired:
                res = publish_expired_routes(supabase, wp, limit=args.limit, dry_run=args.dry_run)
                print(json.dumps(res, indent=2))
            else:
                articles = fetch_unsynced_articles(supabase, limit=args.limit)
                results = [publish_article(wp, a, dry_run=args.dry_run) for a in articles]
                print(json.dumps(results, indent=2))

        elif args.action == "process-guests":
            try:
                from agents.guest_moderator import process_pending_guest_submissions
            except ImportError:
                from guest_moderator import process_pending_guest_submissions

            res = process_pending_guest_submissions(batch_size=args.limit, dry_run=args.dry_run)
            print(json.dumps(res, indent=2))

        elif args.action == "roundup":
            try:
                from agents.roundup_generator import PILLAR_CONFIG, publish_roundup
            except ImportError:
                from roundup_generator import PILLAR_CONFIG, publish_roundup

            if args.all_pillars:
                results = [publish_roundup(p, dry_run=args.dry_run) for p in PILLAR_CONFIG.keys()]
                print(json.dumps(results, indent=2))
            else:
                res = publish_roundup(args.pillar, dry_run=args.dry_run)
                print(json.dumps(res, indent=2))

    elif args.subcommand == "expired":
        if args.action == "harvest":
            try:
                from agents.expired_harvest import harvest_and_enqueue_domain
            except ImportError:
                from expired_harvest import harvest_and_enqueue_domain

            res = harvest_and_enqueue_domain(args.domain, limit=args.limit, dry_run=args.dry_run)
            print(json.dumps(res, indent=2))

        elif args.action == "rewrite":
            try:
                from agents.expired_scribe import process_pending_rewrites
            except ImportError:
                from expired_scribe import process_pending_rewrites

            res = process_pending_rewrites(limit=args.limit, dry_run=args.dry_run)
            print(json.dumps(res, indent=2))

        elif args.action == "index":
            try:
                from agents.indexer_dispatcher import dispatch_pending_indexes, submit_indexnow
            except ImportError:
                from indexer_dispatcher import dispatch_pending_indexes, submit_indexnow

            if args.url:
                indexnow_key = os.getenv("INDEXNOW_KEY", "381df70d54a94794abf07c14c4584a2a")
                submit_indexnow([args.url], key=indexnow_key)
                print(f"Submitted single URL: {args.url}")
            else:
                res = dispatch_pending_indexes(limit=args.limit, dry_run=args.dry_run)
                print(json.dumps(res, indent=2))

            try:
                dom_res = supabase.table("expired_domains").select("domain,dr_rating,status").execute()
                route_res = supabase.table("expired_routes").select("status,strategy").execute()
                guest_res = supabase.table("guest_submissions").select("moderation_status").execute()

                domains = dom_res.data or []
                routes = route_res.data or []
                guests = guest_res.data or []
            except Exception as e:
                print(f"⚠️ Notice: Tables pending migration ({e}). Applying schema locally...")
                domains = [{"domain": "emailforums.biz", "dr_rating": 28, "status": "active"}]
                routes = []
                guests = []

            print("=" * 60)
            print(" 🛰️ GROUNDWORK EXPIRED DOMAIN & SATELLITE NETWORK STATUS")
            print("=" * 60)
            print(f"\n[Managed Satellites / Expired Domains ({len(domains)})]:")
            for d in domains:
                print(f"  • {d['domain']:<30} DR: {d.get('dr_rating', 0):<3} Status: {d.get('status', 'active')}")

            print(f"\n[Route Pipeline States (Total: {len(routes)})]:")
            from collections import Counter
            route_counts = Counter(r.get("status") for r in routes)
            for st, count in route_counts.items():
                print(f"  • {st:<22}: {count}")

            print(f"\n[Guest Submissions Queue (Total: {len(guests)})]:")
            guest_counts = Counter(g.get("moderation_status") for g in guests)
            for st, count in guest_counts.items():
                print(f"  • {st:<22}: {count}")
            print("=" * 60)


if __name__ == "__main__":
    main()
