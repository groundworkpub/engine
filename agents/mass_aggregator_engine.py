import contextlib
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

load_dotenv(REPO_ROOT / ".env.local")
load_dotenv(".env.local")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mass_aggregator_engine")

DATA_DIR = REPO_ROOT / "agents" / "data"
TEMPLATES_FILE = DATA_DIR / "backlink_templates_4500.json"
STATE_FILE = REPO_ROOT / "agents" / "output" / "mass_aggregator_state.json"


def load_templates() -> list[str]:
    """Loads raw templates list (4,500+ targets)."""
    if not TEMPLATES_FILE.exists():
        logger.warning(f"Templates file {TEMPLATES_FILE} not found. Using curated fallback.")
        return [
            "http://website.informer.com/{{DOMAIN}}/",
            "https://builtwith.com/{{DOMAIN}}",
            "https://hypestat.com/info/{{DOMAIN}}",
            "https://spywords.ru/sword.php?sword={{DOMAIN}}",
            "https://www.hostsearch.com/hostsearch/{{DOMAIN}}",
            "https://www.woorank.com/en/teaser-review/{{DOMAIN}}?usecase=all",
            "https://sites.ipaddress.com/{{DOMAIN}}/",
            "https://www.statscrop.com/www/{{DOMAIN}}",
            "https://topwebsiteworth.com/{{DOMAIN}}",
            "https://smart-seo-tools.com/domain/{{DOMAIN}}",
        ]
    try:
        data = json.loads(TEMPLATES_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except Exception as e:
        logger.error(f"Error reading templates: {e}")
    return []


def build_target_urls(templates: list[str], domain: str = "gworky.com") -> list[str]:
    clean_domain = domain.replace("https://", "").replace("http://", "").strip("/")
    urls = []
    for t in templates:
        url = t.replace("{{DOMAIN}}", clean_domain)
        url = url.replace("{{NOPROTOCOL_URL}}", clean_domain)
        url = url.replace("{{ENCODE_NOPROTOCOL_URL}}", clean_domain)
        url = url.replace("{{ENCODE_URL}}", f"https%3A%2F%2F{clean_domain}")
        urls.append(url)
    return urls


def run_mass_drip_ping(domain: str = "gworky.com", batch_size: int = 15, dry_run: bool = False) -> list[dict[str, Any]]:
    """Pings a curated batch of aggregator URLs with status validation and state sync."""
    templates = load_templates()
    all_urls = build_target_urls(templates, domain=domain)
    logger.info(f"Loaded {len(all_urls)} total aggregator URL templates.")

    # Load state
    state = {"cursor": 0, "successful_pings": [], "last_run": None}
    if STATE_FILE.exists():
        with contextlib.suppress(Exception):
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))

    cursor = state.get("cursor", 0)
    batch = all_urls[cursor : cursor + batch_size]
    if not batch:
        logger.info("Reached end of 4,500 list. Cycling back to beginning.")
        cursor = 0
        batch = all_urls[:batch_size]

    logger.info(f"Processing batch #{cursor // batch_size + 1} ({len(batch)} URLs from index {cursor})...")
    results = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    with httpx.Client(timeout=10.0, headers=headers, follow_redirects=True) as client:
        for url in batch:
            if dry_run:
                logger.info(f"[DRY-RUN] Would ping: {url[:70]}")
                results.append({"url": url, "status": "dry_run"})
                continue

            try:
                resp = client.get(url)
                status = "success" if resp.status_code in (200, 202, 301, 302, 403) else f"http_{resp.status_code}"
                logger.info(f"[{'✅' if status == 'success' else '⚠️'}] {url[:60]} -> {resp.status_code}")
                results.append({"url": url, "status_code": resp.status_code, "status": status})
                if status == "success":
                    state["successful_pings"].append({"url": url, "status_code": resp.status_code, "at": datetime.now(UTC).isoformat()})
            except Exception as exc:
                logger.debug(f"Ping note for {url[:50]}: {exc}")
                results.append({"url": url, "status": "error", "error": str(exc)[:80]})

            time.sleep(0.5)

    if not dry_run:
        state["cursor"] = cursor + len(batch)
        state["last_run"] = datetime.now(UTC).isoformat()
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")

    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Mass Aggregator Drip Engine (4,500+ Targets)")
    parser.add_argument("--batch", type=int, default=15, help="Batch size per run")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without network calls")
    args = parser.parse_args()

    res = run_mass_drip_ping(batch_size=args.batch, dry_run=args.dry_run)
    success_count = sum(1 for r in res if r.get("status") == "success")
    print(f"\n✅ Batch complete: {success_count}/{len(res)} URLs active and pinged successfully.")
