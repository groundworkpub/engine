"""Groundwork Resilient Egress & Residential Proxy Router.

Manages geo-targeted residential proxy routing via DataImpulse gateway with
multi-tier zero-cost fallbacks (Cloudflare Edge Worker / Tor SOCKS5 / Direct TLS Jitter)
and consecutive error circuit breakers.
"""

from __future__ import annotations

import logging
import os
import time
import urllib.request
from typing import Any

logger = logging.getLogger("egress_router")

# Country code normalisation map
_COUNTRY_MAP: dict[str, str] = {
    "usa": "us",
    "us": "us",
    "gbr": "gb",
    "gb": "gb",
    "uk": "gb",
    "aus": "au",
    "au": "au",
    "can": "ca",
    "ca": "ca",
}

_CIRCUIT_BREAKER_FAILURES = 0
_CIRCUIT_BREAKER_TRIPPED_UNTIL = 0.0
_CIRCUIT_BREAKER_THRESHOLD = 3
_CIRCUIT_BREAKER_COOLDOWN_SECONDS = 300.0  # 5 minutes cooldown


class DataImpulseProxyRouter:
    """Manages DataImpulse Residential Proxy dynamic routing with resilient fallbacks."""

    @staticmethod
    def get_proxy_url(country: str = "us", session_id: str | None = None) -> str | None:
        """Build a DataImpulse proxy URL with geo-targeting and sticky session.

        Falls back gracefully to Tor (socks5://127.0.0.1:9050), Cloudflare Worker proxy,
        or SIMULATOR_PROXY_URL. Returns None if direct connection should be used.
        """
        global _CIRCUIT_BREAKER_FAILURES, _CIRCUIT_BREAKER_TRIPPED_UNTIL

        # Check circuit breaker
        now = time.monotonic()
        if now < _CIRCUIT_BREAKER_TRIPPED_UNTIL:
            logger.debug("Egress circuit breaker active; falling back to alternative egress.")
            return DataImpulseProxyRouter._get_fallback_proxy()

        login = os.environ.get("DATAIMPULSE_LOGIN")
        pwd = os.environ.get("DATAIMPULSE_PASSWORD")
        host = os.environ.get("DATAIMPULSE_HOST", "gw.dataimpulse.com")
        port = os.environ.get("DATAIMPULSE_PORT", "823")

        if not login or not pwd:
            return DataImpulseProxyRouter._get_fallback_proxy()

        c_tag = _COUNTRY_MAP.get(country.lower(), "us")
        if session_id:
            return f"http://{login}__cr.{c_tag}__sessid.{session_id}:{pwd}@{host}:{port}"
        return f"http://{login}__cr.{c_tag}:{pwd}@{host}:{port}"

    @staticmethod
    def _get_fallback_proxy() -> str | None:
        """Check for zero-cost fallbacks: Tor SOCKS5, Cloudflare Worker Egress, or SIMULATOR_PROXY_URL."""
        # Check custom simulator proxy
        custom = os.environ.get("SIMULATOR_PROXY_URL")
        if custom:
            return custom

        # Check Tor SOCKS5 daemon (standard local or GHA runner port 9050)
        tor_host = os.environ.get("TOR_PROXY_HOST", "127.0.0.1")
        tor_port = os.environ.get("TOR_PROXY_PORT", "9050")
        if os.environ.get("ENABLE_TOR_FALLBACK") == "true":
            return f"socks5://{tor_host}:{tor_port}"

        # Check Cloudflare Worker Egress Proxy endpoint
        cf_proxy = os.environ.get("CLOUDFLARE_WORKER_PROXY_URL")
        if cf_proxy:
            return cf_proxy

        return None

    @staticmethod
    def record_failure() -> None:
        """Record an egress failure and trip circuit breaker if threshold is reached."""
        global _CIRCUIT_BREAKER_FAILURES, _CIRCUIT_BREAKER_TRIPPED_UNTIL
        _CIRCUIT_BREAKER_FAILURES += 1
        if _CIRCUIT_BREAKER_FAILURES >= _CIRCUIT_BREAKER_THRESHOLD:
            _CIRCUIT_BREAKER_TRIPPED_UNTIL = time.monotonic() + _CIRCUIT_BREAKER_COOLDOWN_SECONDS
            logger.warning(
                f"DataImpulse circuit breaker TRIPPED for {_CIRCUIT_BREAKER_COOLDOWN_SECONDS}s after {_CIRCUIT_BREAKER_FAILURES} errors."
            )

    @staticmethod
    def record_success() -> None:
        """Reset consecutive failure counter on success."""
        global _CIRCUIT_BREAKER_FAILURES, _CIRCUIT_BREAKER_TRIPPED_UNTIL
        _CIRCUIT_BREAKER_FAILURES = 0
        _CIRCUIT_BREAKER_TRIPPED_UNTIL = 0.0

    @staticmethod
    def is_available() -> bool:
        """Check whether DataImpulse credentials or fallback proxies are configured."""
        return bool(
            (os.environ.get("DATAIMPULSE_LOGIN") and os.environ.get("DATAIMPULSE_PASSWORD"))
            or os.environ.get("SIMULATOR_PROXY_URL")
            or os.environ.get("CLOUDFLARE_WORKER_PROXY_URL")
        )

    @staticmethod
    def health_check() -> dict[str, Any]:
        """Test connectivity and latency to the DataImpulse gateway via api.ipify.org."""
        result: dict[str, Any] = {
            "name": "dataimpulse",
            "available": False,
            "latency_ms": None,
            "ip": None,
            "error": None,
        }

        if not DataImpulseProxyRouter.is_available():
            result["error"] = "credentials_missing"
            return result

        proxy_url = DataImpulseProxyRouter.get_proxy_url("us")
        if not proxy_url:
            result["error"] = "proxy_url_build_failed"
            return result

        try:
            proxy_handler = urllib.request.ProxyHandler({"https": proxy_url, "http": proxy_url})
            opener = urllib.request.build_opener(proxy_handler)
            start = time.monotonic()
            req = urllib.request.Request(
                "https://api.ipify.org?format=json",
                headers={"User-Agent": "Groundwork-EgressCheck/1.0"},
            )
            with opener.open(req, timeout=8) as resp:
                latency = (time.monotonic() - start) * 1000
                if resp.status == 200:
                    import json
                    body = json.loads(resp.read().decode("utf-8"))
                    result["available"] = True
                    result["latency_ms"] = round(latency, 1)
                    result["ip"] = body.get("ip")
                    DataImpulseProxyRouter.record_success()
                else:
                    result["error"] = f"HTTP {resp.status}"
                    DataImpulseProxyRouter.record_failure()
        except Exception as exc:
            result["error"] = str(exc)[:200]
            DataImpulseProxyRouter.record_failure()

        return result
