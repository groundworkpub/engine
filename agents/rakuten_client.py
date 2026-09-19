#!/usr/bin/env python3
"""
agents/rakuten_client.py — Groundwork Rakuten Advertising OAuth 2.0 & Affiliate API Client

Implements the official LinkShare/Rakuten OAuth 2.0 token exchange and authenticated API endpoints:
- Base URL: https://api.linksynergy.com
- Publisher SID: 4752883 | Site ID: 597352
- Token Endpoint: POST https://api.linksynergy.com/token
"""

import os
import time
import base64
import logging
from pathlib import Path
from typing import Optional, Dict, Any
import httpx
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env.local")

logger = logging.getLogger("rakuten_client")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")


class RakutenClient:
    def __init__(self):
        self.client_id = os.getenv("RAKUTEN_CLIENT_ID")
        self.client_secret = os.getenv("RAKUTEN_CLIENT_SECRET")
        self.sid = os.getenv("RAKUTEN_SID", "4752883")
        self.site_id = os.getenv("RAKUTEN_SITE_ID", "597352")
        self.token_url = "https://api.linksynergy.com/token"
        
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.token_expiry: float = 0.0

    def _get_auth_header(self) -> str:
        creds = f"{self.client_id}:{self.client_secret}".encode()
        return base64.b64encode(creds).decode()

    def get_access_token(self, force_refresh: bool = False) -> str:
        """Retrieves or refreshes an OAuth 2.0 Bearer token for Publisher SID."""
        if not force_refresh and self.access_token and time.time() < (self.token_expiry - 120):
            return self.access_token

        if not self.client_id or not self.client_secret:
            raise ValueError("Missing RAKUTEN_CLIENT_ID or RAKUTEN_CLIENT_SECRET in .env.local")

        token_key = self._get_auth_header()
        headers = {
            "Authorization": f"Bearer {token_key}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {
            "grant_type": "password",
            "scope": self.sid,
        }

        logger.info("Authenticating with Rakuten OAuth 2.0 (Scope: %s)...", self.sid)
        resp = httpx.post(self.token_url, headers=headers, data=data, timeout=15.0)

        if resp.status_code != 200:
            logger.error("Failed to authenticate with Rakuten API: %d - %s", resp.status_code, resp.text)
            raise RuntimeError(f"Rakuten auth error {resp.status_code}: {resp.text}")

        data = resp.json()
        self.access_token = data.get("access_token")
        self.refresh_token = data.get("refresh_token")
        expires_in = int(data.get("expires_in", 3600))
        self.token_expiry = time.time() + expires_in

        logger.info("✓ Successfully acquired Rakuten Bearer Token (expires in %d seconds)", expires_in)
        return self.access_token

    def get_headers(self) -> Dict[str, str]:
        token = self.get_access_token()
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "GroundworkResearch/1.0 (+https://gworky.com)",
        }

    def search_merchants(self, keyword: str = "") -> Any:
        """Query Rakuten Advertiser Search API."""
        headers = self.get_headers()
        url = f"https://api.linksynergy.com/v1/advertisers"
        params = {"keyword": keyword} if keyword else {}
        resp = httpx.get(url, headers=headers, params=params, timeout=15.0)
        return resp.json() if resp.status_code == 200 else {"status": resp.status_code, "error": resp.text}


if __name__ == "__main__":
    client = RakutenClient()
    token = client.get_access_token()
    print(f"Verified Token: {token[:12]}... (len: {len(token)})")
