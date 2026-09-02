"""Groundwork WordPress Authority Target Hunter & Fingerprinter

Discovers and probes high-authority WordPress sites across Groundwork's 5 pillars.
Identifies endpoints for XML-RPC Pingback, REST/HTML comments, and Contributor registration.
Integrates DataImpulse Residential Proxy for stealth operations without IP blocks.

Usage:
    python agents/wordpress_hunter.py --seed-only
    python agents/wordpress_hunter.py --pillar money --probe --limit 10
    python agents/wordpress_hunter.py --discover --pillar tech --limit 5
    python agents/wordpress_hunter.py --stats
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from typing import Any
from urllib.parse import urlparse

import httpx

# Load environment
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

_load_env_local()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("wp_hunter")

# DataImpulse Residential Proxy Setup
DATAIMPULSE_HOST = os.getenv("DATAIMPULSE_HOST", "gw.dataimpulse.com")
DATAIMPULSE_PORT = os.getenv("DATAIMPULSE_PORT", "823")
DATAIMPULSE_LOGIN = os.getenv("DATAIMPULSE_LOGIN", "")
DATAIMPULSE_PASSWORD = os.getenv("DATAIMPULSE_PASSWORD", "")
DEFAULT_PROXY = (
    f"http://{DATAIMPULSE_LOGIN}__cr.us:{DATAIMPULSE_PASSWORD}@{DATAIMPULSE_HOST}:{DATAIMPULSE_PORT}"
    if DATAIMPULSE_LOGIN and DATAIMPULSE_PASSWORD
    else None
)

TIMEOUT = httpx.Timeout(15.0, connect=8.0)
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"


def get_http_client(use_proxy: bool = True) -> httpx.Client:
    """Returns an HTTP client optionally routed through DataImpulse residential proxy."""
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/json,application/xhtml+xml"}
    proxy = DEFAULT_PROXY if use_proxy and DEFAULT_PROXY else None
    if proxy:
        return httpx.Client(timeout=TIMEOUT, headers=headers, proxy=proxy, follow_redirects=True)
    return httpx.Client(timeout=TIMEOUT, headers=headers, follow_redirects=True)


def get_db_connection():
    """Returns a direct PostgreSQL connection to Supabase."""
    import psycopg2
    return psycopg2.connect(
        host=os.getenv("SUPABASE_DB_HOST"),
        port=os.getenv("SUPABASE_DB_PORT", "6543"),
        user=os.getenv("SUPABASE_DB_USER"),
        password=os.getenv("SUPABASE_DB_PASSWORD"),
        dbname="postgres",
        sslmode="require",
    )


def load_dork_matrix() -> dict[str, Any]:
    """Loads the seed sites and search dorks from JSON catalog."""
    catalog_path = os.path.join(os.path.dirname(__file__), "data", "wp_target_dorks.json")
    if not os.path.exists(catalog_path):
        return {"dorks": {}, "seed_sites": []}
    with open(catalog_path, encoding="utf-8") as f:
        return json.load(f)


def seed_database_targets() -> int:
    """Inserts seed sites into Supabase wp_target_sites table."""
    data = load_dork_matrix()
    seeds = data.get("seed_sites", [])
    if not seeds:
        logger.warning("No seed sites found in wp_target_dorks.json")
        return 0

    conn = get_db_connection()
    conn.autocommit = True
    cur = conn.cursor()
    inserted = 0

    for s in seeds:
        domain = s.get("domain", "").strip().lower()
        pillar = s.get("pillar", "money").strip().lower()
        dr = s.get("dr", 50)
        if not domain:
            continue
        cur.execute(
            """
            INSERT INTO public.wp_target_sites (domain, pillar, dr_rating, comments_url, xmlrpc_url)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (domain) DO UPDATE
            SET dr_rating = EXCLUDED.dr_rating,
                pillar = EXCLUDED.pillar,
                updated_at = NOW()
            """,
            (domain, pillar, dr, f"https://{domain}/wp-comments-post.php", f"https://{domain}/xmlrpc.php"),
        )
        inserted += 1

    cur.close()
    conn.close()
    logger.info(f"Successfully synced {inserted} seed WordPress sites into Supabase wp_target_sites.")
    return inserted


def probe_site_capabilities(domain: str, use_proxy: bool = False) -> dict[str, Any]:
    """Probes a target WordPress domain for XML-RPC, Comment forms, and Open Registration."""
    domain = domain.strip().lower()
    clean_domain = domain.replace("https://", "").replace("http://", "").rstrip("/")
    base_url = f"https://{clean_domain}"

    capabilities = {
        "domain": clean_domain,
        "xmlrpc_enabled": False,
        "xmlrpc_url": f"{base_url}/xmlrpc.php",
        "comments_enabled": False,
        "comments_url": f"{base_url}/wp-comments-post.php",
        "registration_enabled": False,
        "registration_url": f"{base_url}/wp-login.php?action=register",
    }

    with get_http_client(use_proxy=use_proxy) as client:
        # 1. Probe XML-RPC Pingback capability
        try:
            xml_payload = (
                '<?xml version="1.0" encoding="iso-8859-1"?>'
                '<methodCall><methodName>system.listMethods</methodName><params></params></methodCall>'
            )
            resp = client.post(capabilities["xmlrpc_url"], content=xml_payload, headers={"Content-Type": "text/xml"})
            if resp.status_code in (200, 405):
                if "pingback.ping" in resp.text or "XML-RPC server accepts POST requests only" in resp.text or resp.status_code == 200:
                    capabilities["xmlrpc_enabled"] = True
                    logger.info(f"[{clean_domain}] XML-RPC pingback active (HTTP {resp.status_code})")
        except Exception as e:
            logger.debug(f"[{clean_domain}] XML-RPC probe failed: {e}")

        # 2. Probe HTML / REST Comment Capability
        try:
            resp = client.get(f"{base_url}/wp-json/wp/v2/comments")
            if resp.status_code in (200, 400):
                capabilities["comments_enabled"] = True
                capabilities["comments_url"] = f"{base_url}/wp-json/wp/v2/comments"
                logger.info(f"[{clean_domain}] REST comments endpoint available (HTTP {resp.status_code})")
            else:
                resp_form = client.get(capabilities["comments_url"])
                if resp_form.status_code in (200, 405, 302):
                    capabilities["comments_enabled"] = True
                    logger.info(f"[{clean_domain}] HTML comments endpoint available (HTTP {resp_form.status_code})")
        except Exception as e:
            logger.debug(f"[{clean_domain}] Comment probe failed: {e}")

        # 3. Probe Open Contributor Registration
        try:
            resp_reg = client.get(capabilities["registration_url"])
            if resp_reg.status_code == 200 and "user_login" in resp_reg.text and "registration is currently not allowed" not in resp_reg.text.lower():
                capabilities["registration_enabled"] = True
                logger.info(f"[{clean_domain}] Open Contributor registration ACTIVE!")
        except Exception as e:
            logger.debug(f"[{clean_domain}] Registration probe failed: {e}")

    return capabilities


def update_probed_capabilities(cap: dict[str, Any]) -> None:
    """Updates the probed flags in Supabase."""
    conn = get_db_connection()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE public.wp_target_sites
        SET xmlrpc_enabled = %s,
            xmlrpc_url = %s,
            comments_enabled = %s,
            comments_url = %s,
            registration_enabled = %s,
            registration_url = %s,
            last_probed_at = NOW(),
            updated_at = NOW()
        WHERE domain = %s
        """,
        (
            cap["xmlrpc_enabled"],
            cap["xmlrpc_url"],
            cap["comments_enabled"],
            cap["comments_url"],
            cap["registration_enabled"],
            cap["registration_url"],
            cap["domain"],
        ),
    )
    cur.close()
    conn.close()


def discover_dynamic_articles(pillar: str, limit: int = 5, use_proxy: bool = False) -> list[str]:
    """Uses Serper API or DuckDuckGo to discover recent WordPress articles for a pillar."""
    serper_key = os.getenv("SERPER_API_KEY")
    matrix = load_dork_matrix()
    dorks = matrix.get("dorks", {}).get(pillar, [])
    if not dorks:
        dorks = [f'"{pillar}" site:edu inurl:blog "leave a comment"']

    discovered_urls: list[str] = []
    query = dorks[0]

    logger.info(f"Running dynamic discovery for pillar '{pillar}' using query: {query}")

    if serper_key:
        try:
            with httpx.Client(timeout=TIMEOUT) as client:
                resp = client.post(
                    "https://google.serper.dev/search",
                    headers={"X-API-KEY": serper_key, "Content-Type": "application/json"},
                    json={"q": query, "num": limit},
                )
                if resp.status_code == 200:
                    results = resp.json().get("organic", [])
                    for r in results:
                        link = r.get("link")
                        if link and "wp-" not in link and link.startswith("http"):
                            discovered_urls.append(link)
        except Exception as e:
            logger.warning(f"Serper discovery error: {e}")

    # Fallback to DuckDuckGo HTML scraper with DataImpulse proxy
    if not discovered_urls:
        try:
            with get_http_client(use_proxy=use_proxy) as client:
                resp = client.get(f"https://html.duckduckgo.com/html/?q={query}")
                if resp.status_code == 200:
                    matches = re.findall(r'href="//duckduckgo.com/l/\?uddg=([^"&]+)', resp.text)
                    import urllib.parse
                    for m in matches[:limit]:
                        decoded = urllib.parse.unquote(m)
                        if decoded.startswith("http") and "duckduckgo" not in decoded:
                            discovered_urls.append(decoded)
        except Exception as e:
            logger.warning(f"DuckDuckGo fallback error: {e}")

    logger.info(f"Discovered {len(discovered_urls)} target URLs for pillar '{pillar}'")
    return discovered_urls


def print_stats() -> None:
    """Prints current status of WordPress target inventory."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM public.wp_target_sites")
    total = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM public.wp_target_sites WHERE xmlrpc_enabled = true")
    xmlrpc = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM public.wp_target_sites WHERE comments_enabled = true")
    comments = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM public.wp_target_sites WHERE registration_enabled = true")
    registration = cur.fetchone()[0]

    cur.execute("SELECT pillar, COUNT(*) FROM public.wp_target_sites GROUP BY pillar")
    pillars = cur.fetchall()

    cur.close()
    conn.close()

    print("\n" + "=" * 55)
    print(" GROUNDWORK WORDPRESS TARGET INVENTORY STATUS")
    print("=" * 55)
    print(f" Total Registered Sites : {total}")
    print(f" XML-RPC Pingback Ready : {xmlrpc}")
    print(f" Comments Form Ready    : {comments}")
    print(f" Open Registration Ready: {registration}")
    print("-" * 55)
    print(" Distribution by Pillar :")
    for p, c in pillars:
        print(f"   • {p.upper():<8}: {c} sites")
    print("=" * 55 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="WordPress Target Hunter")
    parser.add_argument("--seed-only", action="store_true", help="Sync seed sites from wp_target_dorks.json to Supabase")
    parser.add_argument("--pillar", choices=["money", "body", "home", "life", "tech", "all"], default="all")
    parser.add_argument("--probe", action="store_true", help="Probe endpoints for capability flags")
    parser.add_argument("--discover", action="store_true", help="Run dynamic Google Dork discovery")
    parser.add_argument("--limit", type=int, default=10, help="Max sites to probe or discover")
    parser.add_argument("--proxy", action="store_true", help="Route traffic via DataImpulse residential proxy")
    parser.add_argument("--stats", action="store_true", help="Print summary statistics")
    args = parser.parse_args()

    if args.stats:
        print_stats()
        return

    if args.seed_only or args.probe or args.discover or True:
        # Always ensure seeds exist
        seed_database_targets()

    if args.probe:
        conn = get_db_connection()
        cur = conn.cursor()
        pillar_clause = "" if args.pillar == "all" else f"AND pillar = '{args.pillar}'"
        cur.execute(f"SELECT domain FROM public.wp_target_sites WHERE last_probed_at IS NULL {pillar_clause} ORDER BY dr_rating DESC LIMIT {args.limit}")
        rows = cur.fetchall()
        cur.close()
        conn.close()

        logger.info(f"Probing capabilities for {len(rows)} targets...")
        for (domain,) in rows:
            cap = probe_site_capabilities(domain, use_proxy=args.proxy)
            update_probed_capabilities(cap)
            time.sleep(1.0)

    if args.discover:
        pillar = "money" if args.pillar == "all" else args.pillar
        urls = discover_dynamic_articles(pillar, limit=args.limit, use_proxy=args.proxy)
        print(f"\nDiscovered {len(urls)} live WordPress URLs for {pillar}:")
        for u in urls:
            print(f"  → {u}")


if __name__ == "__main__":
    main()
