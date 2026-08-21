"""
agents/podcast_distributor.py — Autonomous Podcast Directory Ingestion & Ping Engine

Master SSOT: docs/AUTONOMOUS-AUDIO-PODCAST-SPEC.md
"""

import hashlib
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

import jwt

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("podcast_distributor")


def _load_env_local():
    """Load variables from .env.local if not already in os.environ."""
    env_path = Path(__file__).resolve().parent.parent / ".env.local"
    if env_path.exists():
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'").strip('"')
                if k and k not in os.environ:
                    os.environ[k] = v


_load_env_local()


class PodcastDistributor:
    def __init__(self):
        self.feed_url = os.getenv("PODCAST_FEED_URL", "https://gworky.com/podcast/feed.xml")

        # Podcast Index Credentials
        self.pi_key = os.getenv("PODCASTINDEX_API_KEY", "")
        self.pi_secret = os.getenv("PODCASTINDEX_API_SECRET", "")

        # Apple Podcasts Connect Private Key
        self.apple_key_path = os.getenv(
            "APPLE_PODCASTS_KEY_PATH", "docs/secrets/6c004c00-479a-4430-8af1-18d9e415f652_JNLY200VTXAD.pem"
        )
        self.apple_key_id = os.getenv("APPLE_PODCASTS_KEY_ID", "JNLY200VTXAD")
        self.apple_issuer_id = os.getenv("APPLE_PODCASTS_ISSUER_ID", "6c004c00-479a-4430-8af1-18d9e415f652")

    # ── 1. Podcast Index API Ping ─────────────────────────────────────────────

    def ping_podcast_index(self) -> bool:
        """Broadcasts RSS feed to 50+ open podcast apps via Podcast Index HMAC-SHA1 API."""
        if not self.pi_key or not self.pi_secret:
            logger.info("Podcast Index API credentials not configured. Skipping.")
            return False

        epoch_time = str(int(time.time()))
        data_to_hash = self.pi_key + self.pi_secret + epoch_time
        auth_hash = hashlib.sha1(data_to_hash.encode("utf-8")).hexdigest()

        url = f"https://api.podcastindex.org/api/1.0/add/byfeedurl?url={urllib.parse.quote(self.feed_url)}"
        headers = {
            "User-Agent": "GroundworkAudioBot/1.0",
            "X-Auth-Date": epoch_time,
            "X-Auth-Key": self.pi_key,
            "Authorization": auth_hash,
        }

        req = urllib.request.Request(url, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                logger.info(f"Podcast Index API Response: {data.get('description', data.get('status'))}")
                return True
        except Exception as e:
            logger.error(f"Podcast Index API Ping failed: {e}")
            return False

    # ── 2. Apple Podcasts Connect JWT Auth Token ──────────────────────────────

    def generate_apple_jwt(self) -> str | None:
        """Generates an ES256 JWT token using Apple Podcasts Connect private key."""
        if not os.path.exists(self.apple_key_path):
            logger.warning(f"Apple Podcasts private key not found at: {self.apple_key_path}")
            return None

        try:
            with open(self.apple_key_path) as f:
                private_key = f.read()

            now = int(time.time())
            payload = {
                "iss": self.apple_issuer_id,
                "exp": now + (20 * 60),  # 20 minutes expiration
                "aud": "appstoreconnect-v1",
            }
            headers = {"alg": "ES256", "kid": self.apple_key_id, "typ": "JWT"}

            token = jwt.encode(payload, private_key, algorithm="ES256", headers=headers)
            logger.info("Generated valid Apple Podcasts Connect ES256 JWT.")
            return token
        except Exception as e:
            logger.error(f"Failed to generate Apple Podcasts JWT: {e}")
            return None

    # ── 3. Google WebSub / PubSubHubbub Ping ───────────────────────────────────

    def ping_websub_hubs(self) -> bool:
        """Pings Google PubSubHubbub and Superfeedr hubs to trigger immediate RSS crawl."""
        hubs = ["https://pubsubhubbub.appspot.com/", "https://pubsubhubbub.superfeedr.com/"]

        success = True
        for hub in hubs:
            try:
                data = urllib.parse.urlencode({"hub.mode": "publish", "hub.url": self.feed_url}).encode("utf-8")
                req = urllib.request.Request(hub, data=data, method="POST")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    logger.info(f"WebSub Ping to {hub}: HTTP {resp.status}")
            except Exception as e:
                logger.warning(f"WebSub ping failed for {hub}: {e}")
                success = False

        return success

    # ── 4. Broadcast All Directories ──────────────────────────────────────────

    def broadcast_all(self) -> dict[str, bool]:
        logger.info(f"=== Broadcasting Podcast Feed: {self.feed_url} ===")
        results = {
            "websub": self.ping_websub_hubs(),
            "podcast_index": self.ping_podcast_index(),
            "apple_jwt_valid": bool(self.generate_apple_jwt()),
        }
        logger.info(f"Broadcast results: {results}")
        return results


def main():
    distributor = PodcastDistributor()
    distributor.broadcast_all()


if __name__ == "__main__":
    main()
