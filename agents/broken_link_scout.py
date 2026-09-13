#!/usr/bin/env python3
"""
agents/broken_link_scout.py — Groundwork Broken Link & Academic Authority Prospector
Scans high-authority university extension portals (.edu) and public resource hubs (.gov)
via DataImpulse US Residential Proxy (gw.dataimpulse.com:823) to discover broken outbound
links (HTTP 404/410), generates an academic courtesy pitch from Elena Vance, and registers
opportunities in Supabase `outreach_prospects` with Telegram alerts to @gwelena_bot.
"""

import os
import sys
import json
import time
import logging
import argparse
import httpx
from bs4 import BeautifulSoup

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from agents.egress_dataimpulse import DataImpulseProxyRouter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("broken_link_scout")

RESOURCES_PATH = os.path.join(ROOT_DIR, "config", "outreach_seeds.json")
if not os.path.exists(RESOURCES_PATH):
    RESOURCES_PATH = os.path.join(ROOT_DIR, "docs", "seo", "outreach_seed_resources.json")

def load_env():
    """Load credentials from .env.local if present."""
    env_file = os.path.join(ROOT_DIR, ".env.local")
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("\"'")
                if k not in os.environ:
                    os.environ[k] = v

def load_academic_seeds() -> list[dict]:
    """Load academic extension seeds from SSOT JSON and dynamic seeds pool for continuous expansion."""
    seeds = []
    if os.path.exists(RESOURCES_PATH):
        try:
            with open(RESOURCES_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                seeds.extend(data.get("academic_extensions", []))
        except Exception as e:
            logger.warning(f"Failed to parse {RESOURCES_PATH}: {e}")

    # Merge dynamically discovered seeds
    pool_file = os.path.join(ROOT_DIR, "docs", "seo", "dynamic_seeds_pool.json")
    if os.path.exists(pool_file):
        try:
            with open(pool_file, "r", encoding="utf-8") as f:
                dyn_seeds = json.load(f)
                for ds in dyn_seeds:
                    if not any(s.get("url") == ds.get("url") for s in seeds):
                        seeds.append(ds)
                logger.info(f"Merged {len(dyn_seeds)} dynamically discovered seeds from pool.")
        except Exception as err:
            logger.warning(f"Error loading dynamic seeds pool: {err}")

    if seeds:
        return seeds

    return [
        {
            "institution": "Penn State Extension",
            "domain": "extension.psu.edu",
            "url": "https://extension.psu.edu/solar-energy-resources",
            "pillar": "home",
            "our_match": "https://gworky.com/tools/solar-payback-calculator",
            "coordinator_contact": "energy-extension@psu.edu"
        },
        {
            "institution": "UMN Extension CERTs",
            "domain": "extension.umn.edu",
            "url": "https://extension.umn.edu/energy/clean-energy-resources",
            "pillar": "home",
            "our_match": "https://gworky.com/article/structural-insulated-panel-sip-repair-guide",
            "coordinator_contact": "info@cleanenergyresourceteams.org"
        }
    ]

def generate_academic_pitch(prospect: dict) -> str:
    """Generate professional, courteous academic replacement pitch from Elena Vance."""
    anchor = prospect.get("anchor_text", "energy resource")
    return (
        f"Subject: Broken resource on your educational guide / {prospect['seed_domain']}\n\n"
        f"Dear Extension Coordinator,\n\n"
        f"While reviewing your public guide at {prospect['seed_url']}, I noticed that the outbound "
        f"link with anchor text \"{anchor}\" ({prospect['dead_link']}) is currently returning "
        f"HTTP {prospect['status_code']} and appears permanently offline.\n\n"
        f"If you are seeking an updated, un-gated alternative for local homeowners, our research desk "
        f"maintains an open, ad-free calculation model and empirical guide at:\n"
        f"{prospect['suggested_replacement']}\n\n"
        f"It models current 2026 federal Section 25D tax credits and regional efficiency metrics "
        f"without requiring lead forms, cookies, or sponsored financial bias.\n\n"
        f"Thank you for your continued educational work for local homeowners.\n\n"
        f"Best regards,\n"
        f"Elena Vance\n"
        f"Chief Research Editor | Groundwork Research Desk\n"
        f"elena@gworky.com | https://gworky.com\n"
    )

def send_telegram_alert(prospect: dict):
    """Send formatted Telegram alert card to @gwelena_bot with target site and contact links."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_FOUNDER_CHAT_ID")
    if not bot_token or not chat_id:
        logger.debug("Telegram credentials not configured; skipping bot alert.")
        return

    domain = prospect['seed_domain']
    target_page = prospect['seed_url']
    contact = prospect.get('coordinator_contact', f"info@{domain}")
    replacement = prospect['suggested_replacement']

    text = (
        f"🎯 <b>[Broken Link Scout] Educational Opportunity!</b>\n\n"
        f"🏛️ <b>Target Institution:</b>\n"
        f"• <b>Host:</b> <a href=\"https://{domain}\">{domain}</a>\n"
        f"• <b>Resource Page:</b> <a href=\"{target_page}\">{target_page}</a>\n"
        f"• <b>Coordinator Contact:</b> <a href=\"mailto:{contact}\"><code>{contact}</code></a>\n\n"
        f"🔍 <b>Dead Outbound Link Detected:</b>\n"
        f"• <b>Dead URL:</b> <code>{prospect['dead_link']}</code> (HTTP {prospect['status_code']})\n"
        f"• <b>Anchor Text:</b> <i>\"{prospect['anchor_text'][:45]}\"</i>\n\n"
        f"💡 <b>Proposed Replacement:</b>\n"
        f"• <b>Groundwork Asset:</b> <a href=\"{replacement}\">{replacement}</a>\n"
        f"• <b>Pillar:</b> <code>{prospect['pillar'].upper()}</code> | <b>Relevance Score:</b> <b>{prospect.get('score', 0.85):.2f}</b>\n\n"
        f"<i>Status: Saved to Supabase outreach_prospects ({prospect.get('status', 'human_review')})</i>"
    )

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
            )
            if resp.status_code == 200:
                logger.info(f"Telegram notification sent for {prospect['seed_domain']}")
            else:
                logger.warning(f"Telegram returned status {resp.status_code}")
    except Exception as e:
        logger.warning(f"Failed to deliver Telegram alert: {e}")

def save_prospect_to_supabase(prospect: dict) -> bool:
    """Record discovered prospect to Supabase outreach_prospects table matching PostgreSQL schema."""
    url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL", "https://keflumlrmggffyrsrmlk.supabase.co").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not key:
        logger.warning("SUPABASE_SERVICE_ROLE_KEY missing; skipping database persist.")
        return False

    draft = generate_academic_pitch(prospect)
    row = {
        "url": prospect["seed_url"],
        "contact": prospect.get("coordinator_contact") or f"info@{prospect['seed_domain']}",
        "source_type": "broken_link",
        "pillar": prospect["pillar"],
        "target_asset": prospect["suggested_replacement"],
        "status": "human_review",
        "draft_outreach": draft,
        "link_secured": False,
        "gray_tier": None
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            # 1. Deduplication check: Do NOT re-contact existing prospects
            target_url = prospect["seed_url"]
            check = client.get(
                f"{url}/rest/v1/outreach_prospects?url=eq.{target_url}&select=id,status",
                headers={"apikey": key, "Authorization": f"Bearer {key}"}
            )
            if check.status_code == 200 and check.json():
                logger.info(f"Target {target_url} already queued/contacted. Skipping duplicate.")
                return False

            resp = client.post(
                f"{url}/rest/v1/outreach_prospects",
                headers={
                    "apikey": key,
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal"
                },
                json=row
            )
            if resp.status_code in (200, 201):
                logger.info(f"Prospect recorded to Supabase outreach_prospects: {prospect['seed_domain']}")
                return True
            else:
                logger.warning(f"Supabase returned status {resp.status_code}: {resp.text}")
                return False
    except Exception as e:
        logger.warning(f"Failed to record prospect to Supabase: {e}")
        return False

def record_snowball_seed(discovered_url: str, pillar: str):
    """Save newly discovered .edu/.gov resource hubs to dynamic seeds pool for continuous expansion."""
    pool_file = os.path.join(ROOT_DIR, "docs", "seo", "dynamic_seeds_pool.json")
    seeds_pool = []
    if os.path.exists(pool_file):
        try:
            with open(pool_file, "r", encoding="utf-8") as f:
                seeds_pool = json.load(f)
        except Exception:
            seeds_pool = []

    if not any(s.get("url") == discovered_url for s in seeds_pool):
        from urllib.parse import urlparse
        domain = urlparse(discovered_url).netloc
        new_entry = {
            "institution": f"Discovered Extension Hub ({domain})",
            "domain": domain,
            "url": discovered_url,
            "pillar": pillar,
            "discovered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "our_match": f"https://gworky.com/{pillar}"
        }
        seeds_pool.append(new_entry)
        try:
            with open(pool_file, "w", encoding="utf-8") as f:
                json.dump(seeds_pool, f, indent=2)
            logger.info(f"[SNOWBALL DISCOVERY] Appended fresh seed to dynamic pool: {discovered_url}")
        except Exception as err:
            logger.warning(f"Could not persist dynamic seed: {err}")

def calculate_semantic_score(anchor: str, topic: str, replacement_url: str) -> float:
    """Calculate semantic relevance score (0.0 - 1.0) between broken link anchor and Groundwork asset."""
    clean_anchor = anchor.lower()
    asset_slug = replacement_url.split("/")[-1].replace("-", " ")
    
    # Keyword overlap count
    words = [w for w in set(clean_anchor.split()) if len(w) > 3]
    if not words:
        return 0.50
    matches = sum(1 for w in words if w in asset_slug or w in topic.lower())
    ratio = matches / len(words)
    
    # Boost if high-value technical terms match
    if any(k in clean_anchor for k in ["solar", "calculator", "payback", "tax credit", "insulation", "mortgage", "heat pump", "refinance"]):
        ratio = min(1.0, ratio + 0.35)
        
    return round(max(0.60, min(0.95, ratio)), 2)

def send_autonomous_resend_email(to_email: str, subject: str, body: str) -> bool:
    """Dispatches autonomous email via Resend API if credentials exist and rate limit allows."""
    resend_key = os.environ.get("RESEND_API_KEY")
    if not resend_key:
        logger.info(f"[SIMULATED DISPATCH] Autonomous pitch ready for {to_email} (RESEND_API_KEY not set).")
        return False

    url = "https://api.resend.com/emails"
    headers = {
        "Authorization": f"Bearer {resend_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "from": "Elena Vance <elena@gworky.com>",
        "to": [to_email],
        "subject": subject,
        "text": body,
        "headers": {
            "List-Unsubscribe": "<mailto:elena@gworky.com?subject=unsubscribe>"
        }
    }
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, json=payload, headers=headers)
            if resp.status_code in (200, 201):
                logger.info(f"✅ [AUTONOMOUS DISPATCH SUCCESS] Pitch delivered to {to_email}")
                return True
            else:
                logger.warning(f"Resend dispatch error ({resp.status_code}): {resp.text}")
                return False
    except Exception as e:
        logger.error(f"Resend connection failed: {e}")
        return False

def scan_seed_for_broken_links(seed: dict, max_links: int = 15, timeout: float = 12.0) -> list[dict]:
    """Scan a target seed page for dead outbound links using DataImpulse residential proxy."""
    target_url = seed["url"]
    proxy_url = DataImpulseProxyRouter.get_proxy_url(country="us")
    logger.info(f"Targeting {target_url} via DataImpulse Egress: {'Active' if proxy_url else 'Direct'}")

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9"
    }

    prospects = []
    try:
        with httpx.Client(proxy=proxy_url, headers=headers, timeout=timeout, follow_redirects=True, verify=False) as client:
            resp = client.get(target_url)
            if resp.status_code != 200:
                logger.warning(f"Failed to fetch seed {target_url}, status: {resp.status_code}")
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            anchors = soup.find_all("a", href=True)
            
            outbound_urls = []
            for a in anchors:
                href = a["href"]
                if href.startswith("http") and seed["domain"] not in href:
                    anchor_text = a.get_text(strip=True)
                    outbound_urls.append((href, anchor_text))

            logger.info(f"Discovered {len(outbound_urls)} outbound links on {seed['domain']}. Checking top {max_links}...")

            # Snowball Target Expansion: Automatically extract fresh .edu / .gov extension resource hubs
            for href, _ in outbound_urls:
                if any(ext in href.lower() for ext in [".edu", ".gov", "extension.", "agrilife"]):
                    if seed["domain"] not in href and not href.endswith((".pdf", ".jpg", ".png", ".zip")):
                        record_snowball_seed(href, seed.get("pillar", "home"))

            for out_url, anchor in outbound_urls[:max_links]:
                time.sleep(0.5)
                try:
                    head_resp = client.head(out_url, timeout=5.0)
                    status = head_resp.status_code
                    if status in (404, 410):
                        logger.info(f"[PROSPECT FOUND] Dead link on {seed['domain']}: {out_url} (HTTP {status}) -> Anchor: '{anchor}'")
                        score = calculate_semantic_score(anchor or "resource", seed.get("topics", [""])[0], seed["our_match"])
                        item = {
                            "seed_domain": seed["domain"],
                            "seed_url": target_url,
                            "dead_link": out_url,
                            "anchor_text": anchor or "link",
                            "status_code": status,
                            "suggested_replacement": seed["our_match"],
                            "pillar": seed["pillar"],
                            "coordinator_contact": seed.get("coordinator_contact"),
                            "score": score
                        }
                        
                        # Full-Autonomous Decision Matrix: score >= 0.85 dispatches directly
                        if score >= 0.85 and item.get("coordinator_contact"):
                            draft = generate_academic_pitch(item)
                            subject = f"Broken educational resource on {seed['domain']}"
                            dispatched = send_autonomous_resend_email(item["coordinator_contact"], subject, draft)
                            item["status"] = "sent" if dispatched else "human_review"
                        else:
                            item["status"] = "human_review"

                        prospects.append(item)
                        save_prospect_to_supabase(item)
                        send_telegram_alert(item)
                except Exception as err:
                    logger.debug(f"Checked {out_url}: {err}")

    except Exception as e:
        logger.error(f"Error crawling seed {target_url}: {e}")

    return prospects

def main():
    parser = argparse.ArgumentParser(description="Groundwork Broken Link Hunter (.edu Academic Extensions)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without network mutation")
    parser.add_argument("--max-check", type=int, default=10, help="Max outbound links to check per seed")
    parser.add_argument("--limit-seeds", type=int, default=2, help="Number of seeds to scan")
    parser.add_argument("--deep", action="store_true", help="Execute deep recursive crawl (depth=2) on discovered hubs")
    args = parser.parse_args()

    load_env()
    logger.info("Initializing Broken Link Prospector via DataImpulse...")
    diag = DataImpulseProxyRouter.health_check()
    logger.info(f"DataImpulse Egress Status: {diag['available']} (IP: {diag['ip']}, Latency: {diag['latency_ms']}ms)")

    seeds = load_academic_seeds()
    logger.info(f"Loaded {len(seeds)} academic extension seeds.")

    all_prospects = []
    if args.dry_run:
        logger.info(f"[DRY-RUN] Verified {len(seeds)} academic extension seeds and proxy health. No requests executed.")
        return

    # Phase 1: Scan primary seeds
    for seed in seeds[:args.limit_seeds]:
        prospects = scan_seed_for_broken_links(seed, max_links=args.max_check)
        all_prospects.extend(prospects)

    # Phase 2: If deep recursive enabled, immediately scan newly discovered depth-2 seeds
    if args.deep and len(seeds) > args.limit_seeds:
        logger.info("Executing Phase 2 Deep Recursive Crawl on discovered extension hubs...")
        for seed in seeds[args.limit_seeds:args.limit_seeds + 2]:
            prospects = scan_seed_for_broken_links(seed, max_links=args.max_check)
            all_prospects.extend(prospects)

    logger.info(f"Prospecting run completed. Total broken link prospects processed: {len(all_prospects)}")

if __name__ == "__main__":
    main()
