"""Groundwork Google Search Console (GSC) Operations & Verification Manager.

Manages property registration, verification status, and sitemap submission
for gworky.com, groundworkpub.github.io, and satellite nodes using the
gwelena service account.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx
import jwt

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agents.authority_injector import _load_env_local

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("gsc_manager")

SCOPES = "https://www.googleapis.com/auth/webmasters https://www.googleapis.com/auth/siteverification"


def get_gsc_access_token() -> tuple[str, str]:
    """Generates an OAuth2 access token using the GSC service account JSON."""
    _load_env_local()
    gsc_b64 = os.environ.get("GSC_SERVICE_ACCOUNT_JSON_B64")
    if not gsc_b64:
        raise ValueError("GSC_SERVICE_ACCOUNT_JSON_B64 not found in environment.")

    sa = json.loads(base64.b64decode(gsc_b64).decode("utf-8"))
    client_email = sa["client_email"]

    now = int(time.time())
    payload = {
        "iss": client_email,
        "sub": client_email,
        "aud": "https://oauth2.googleapis.com/token",
        "iat": now,
        "exp": now + 3600,
        "scope": SCOPES,
    }

    signed_jwt = jwt.encode(payload, sa["private_key"], algorithm="RS256")
    resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": signed_jwt,
        },
        timeout=15.0,
    )

    if resp.status_code != 200:
        raise RuntimeError(f"Failed to obtain OAuth2 token: {resp.text}")

    return resp.json()["access_token"], client_email


def list_gsc_sites() -> list[dict[str, Any]]:
    """Lists all registered sites in Search Console for the service account."""
    token, email = get_gsc_access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    resp = httpx.get("https://www.googleapis.com/webmasters/v3/sites", headers=headers, timeout=15.0)
    if resp.status_code == 200:
        return resp.json().get("siteEntry", [])
    logger.error("Failed to list GSC sites: %d %s", resp.status_code, resp.text)
    return []


def register_gsc_site(site_url: str) -> dict[str, Any]:
    """Registers or checks a site URL in Search Console."""
    token, email = get_gsc_access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    encoded_site = urllib.parse.quote(site_url, safe="")

    # PUT site to add if not present
    put_resp = httpx.put(
        f"https://www.googleapis.com/webmasters/v3/sites/{encoded_site}",
        headers=headers,
        timeout=15.0,
    )

    # GET site details
    get_resp = httpx.get(
        f"https://www.googleapis.com/webmasters/v3/sites/{encoded_site}",
        headers=headers,
        timeout=15.0,
    )

    return {
        "site_url": site_url,
        "put_status": put_resp.status_code,
        "info": get_resp.json() if get_resp.status_code == 200 else None,
    }


def submit_gsc_sitemap(site_url: str, sitemap_url: str) -> dict[str, Any]:
    """Submits a sitemap URL to Search Console for a verified site."""
    token, email = get_gsc_access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    encoded_site = urllib.parse.quote(site_url, safe="")
    encoded_sitemap = urllib.parse.quote(sitemap_url, safe="")

    resp = httpx.put(
        f"https://www.googleapis.com/webmasters/v3/sites/{encoded_site}/sitemaps/{encoded_sitemap}",
        headers=headers,
        timeout=15.0,
    )

    return {
        "sitemap_url": sitemap_url,
        "status_code": resp.status_code,
        "response": resp.text if resp.text else "OK",
    }


if __name__ == "__main__":
    token, email = get_gsc_access_token()
    print(f"Service Account: {email}")
    sites = list_gsc_sites()
    print(f"Registered Sites ({len(sites)}):")
    for s in sites:
        print(f"  • {s.get('siteUrl')} (Permission: {s.get('permissionLevel')})")

    # Ensure groundworkpub.github.io is registered
    target = "https://groundworkpub.github.io/"
    res = register_gsc_site(target)
    print(f"\nTarget Registration ({target}):")
    print(f"  Status: {res['put_status']}")
    print(f"  Details: {json.dumps(res['info'], indent=2)}")
