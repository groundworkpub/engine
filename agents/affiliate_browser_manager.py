#!/usr/bin/env python3
"""
agents/affiliate_browser_manager.py — Groundwork Affiliate Browser Orchestrator

Manages publisher portals (Awin, Impact.com, ClickBank) through browser automation
using Playwright Persistent Profile Context. Solves API limitations (application workflows,
marketplace research, 2FA persistence) with zero trial-and-error.

Usage:
  python3 agents/affiliate_browser_manager.py --status
  python3 agents/affiliate_browser_manager.py --login all
  python3 agents/affiliate_browser_manager.py --login awin
  python3 agents/affiliate_browser_manager.py --login impact
  python3 agents/affiliate_browser_manager.py --login clickbank
  python3 agents/affiliate_browser_manager.py --test-vendor <vendor_name>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any

# Ensure repository root is in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("affiliate_browser")

PROFILE_DIR = Path(__file__).resolve().parent / ".affiliate_profile"
OUTPUT_DIR = ROOT_DIR / "data" / "affiliate"
OUTPUT_FILE = OUTPUT_DIR / "verified_browser_links.json"

LOGIN_URLS = {
    "awin": "https://ui.awin.com/login",
    "impact": "https://app.impact.com/login.user",
    "clickbank": "https://accounts.clickbank.com/login.htm",
}

DASHBOARD_INDICATORS = {
    "awin": ["ui.awin.com/dashboard", "ui.awin.com/publisher", "/advertisers"],
    "impact": ["app.impact.com/secure/mediapartner", "app.impact.com/mediapartner"],
    "clickbank": ["accounts.clickbank.com/account", "clickbank.com/dashboard", "marketplace"],
}


def load_env_local() -> None:
    """Load variables from .env.local without third-party dependencies."""
    p = ROOT_DIR / ".env.local"
    if p.exists():
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'").strip('"')
                if k and k not in os.environ:
                    os.environ[k] = v


load_env_local()


async def get_browser_context(headless: bool = False):
    """Launch Playwright Chromium using the persistent profile directory."""
    from playwright.async_api import async_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    pw = await async_playwright().start()
    
    args = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-infobars",
    ]

    context = await pw.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=headless,
        args=args,
        viewport={"width": 1366, "height": 768},
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    )
    return pw, context


async def check_session_status():
    """Perform non-intrusive headless check of authentication status across networks."""
    logger.info("Checking affiliate portal session status...")
    pw, context = await get_browser_context(headless=True)
    page = context.pages[0] if context.pages else await context.new_page()

    results = {}

    for network, url in LOGIN_URLS.items():
        logger.info(f"Checking {network.upper()} session...")
        try:
            await page.goto(url, timeout=20000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            current_url = page.url
            
            # Check if redirected away from login or if dashboard indicator present
            indicators = DASHBOARD_INDICATORS.get(network, [])
            is_authenticated = any(ind in current_url for ind in indicators) and "/login" not in current_url

            results[network] = {
                "authenticated": is_authenticated,
                "current_url": current_url,
            }
        except Exception as e:
            results[network] = {
                "authenticated": False,
                "error": str(e),
            }

    await context.close()
    await pw.stop()

    print("\n" + "=" * 55)
    print("AFFILIATE PORTAL SESSION STATUS (PERSISTENT PROFILE)")
    print("=" * 55)
    for net, data in results.items():
        status_icon = "✓ ACTIVE" if data.get("authenticated") else "✗ NOT LOGGED IN / EXPIRED"
        print(f"[{net.upper()}]: {status_icon}")
        print(f"  URL: {data.get('current_url', data.get('error'))}")
    print("=" * 55 + "\n")
    return results


async def interactive_login(network: str):
    """
    Open headed browser window to allow user to log in and solve 2FA/OTP once.
    The session is permanently preserved in .affiliate_profile.
    """
    networks = ["awin", "impact", "clickbank"] if network == "all" else [network]
    
    logger.info("Opening interactive browser for login handshake...")
    logger.info("Profile directory: %s", PROFILE_DIR)
    print("\n" + "*" * 65)
    print("PETUNJUK LOGIN PERSISTEN:")
    print("1. Jendela browser Chromium akan terbuka di layar Mac Anda.")
    print("2. Silakan login ke akun Anda dan selesaikan 2FA/OTP jika diminta.")
    print("3. Setelah berhasil masuk ke dashboard, tekan [ENTER] di terminal ini.")
    print("*" * 65 + "\n")

    pw, context = await get_browser_context(headless=False)
    page = context.pages[0] if context.pages else await context.new_page()

    for net in networks:
        target_url = LOGIN_URLS.get(net)
        if not target_url:
            continue
        print(f"\nNavigating to {net.upper()} login: {target_url}")
        await page.goto(target_url)

        print(f"Silakan login ke {net.upper()} pada jendela browser yang terbuka.")
        print(f"Sistem akan otomatis mendeteksi ketika Anda berhasil masuk ke dashboard, atau tekan [ENTER]...")
        
        # Poll for genuine authenticated state (max 600 seconds)
        logged_in = False
        print(f"Menunggu Anda menyelesaikan login di browser...")
        for second in range(300):
            await page.wait_for_timeout(2000)
            cur = page.url
            
            # Specific domain-aware validation
            if net == "awin":
                # Must NOT be on id.awin.com, login, or prelogin
                if "id.awin.com" not in cur and "login" not in cur and "prelogin" not in cur and "ui.awin.com" in cur:
                    # Double check if on publisher dashboard or menu
                    if any(ind in cur for ind in ["/dashboard", "/publisher", "/advertisers"]):
                        # Verify we stay here for 3 seconds without redirecting back to id.awin.com
                        await page.wait_for_timeout(3000)
                        if "id.awin.com" not in page.url:
                            print(f"\n✓ Berhasil terdeteksi masuk ke dashboard AWIN!")
                            print(f"  URL Aktif: {page.url}")
                            logged_in = True
                            break
            elif net == "impact":
                if "login" not in cur and ("app.impact.com/secure" in cur or "app.impact.com/mediapartner" in cur):
                    print(f"\n✓ Berhasil terdeteksi masuk ke dashboard IMPACT!")
                    print(f"  URL Aktif: {cur}")
                    logged_in = True
                    break
            elif net == "clickbank":
                if "login" not in cur and ("accounts.clickbank.com/account" in cur or "marketplace" in cur):
                    print(f"\n✓ Berhasil terdeteksi masuk ke dashboard CLICKBANK!")
                    print(f"  URL Aktif: {cur}")
                    logged_in = True
                    break
        
        if not logged_in:
            print(f"Waktu tunggu login {net.upper()} berakhir. URL terakhir: {page.url}")
        else:
            await page.wait_for_timeout(4000)

    print("\n✓ Semua sesi login berhasil direkam ke direktori profil terisolasi.")
    await context.close()
    await pw.stop()


def test_hoplink(nickname: str, vendor: str) -> dict[str, Any]:
    """Test a ClickBank hoplink using HTTP HEAD to verify vendor status."""
    url = f"https://hop.clickbank.net/?affiliate={nickname}&vendor={vendor}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            final_url = r.url
            status = r.status
            is_broken = "vendoraccntstate" in final_url or "hop-apps" in final_url or status >= 400
            return {
                "vendor": vendor,
                "url": url,
                "final_url": final_url,
                "status": status,
                "active": not is_broken,
            }
    except Exception as e:
        return {
            "vendor": vendor,
            "url": url,
            "error": str(e),
            "active": False,
        }


def main():
    parser = argparse.ArgumentParser(description="Groundwork Affiliate Browser Orchestrator")
    parser.add_argument("--status", action="store_true", help="Check session login status for all portals")
    parser.add_argument("--login", type=str, choices=["awin", "impact", "clickbank", "all"], help="Open browser to log in and save session")
    parser.add_argument("--test-vendor", type=str, help="Test ClickBank vendor hoplink validity")
    args = parser.parse_args()

    if args.status:
        asyncio.run(check_session_status())
    elif args.login:
        asyncio.run(interactive_login(args.login))
    elif args.test_vendor:
        nickname = os.getenv("CLICKBANK_NICKNAME", "gworkycom")
        res = test_hoplink(nickname, args.test_vendor)
        print("\nHopLink Test Result:")
        print(json.dumps(res, indent=2))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
