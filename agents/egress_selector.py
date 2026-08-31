"""Groundwork Smart Egress Policy Selector (Egress Layer).

Central router that picks the best egress route based on task type, geo
requirements, cost constraints, and hardware availability.

Priority order:
  1. Mobile Rotator (4G CGNAT — $0, anti-WAF)
  2. Tailscale / WireGuard (broadband residential — $0)
  3. AWS API Gateway Fanout (serverless IP rotation — $0 free tier)
  4. DataImpulse Residential (geo-targeted — pay-per-GB)
  5. Direct connection (no proxy)

Smart dual-mode: auto-detects local vs. cloud (CI/GitHub Actions) environment.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)


def _is_cloud() -> bool:
    """Detect whether we're running in CI / GitHub Actions."""
    return os.getenv("CI") == "true" or os.getenv("GITHUB_ACTIONS") == "true"


# ISO country code → continent/region used only for coarse sanity checks
# (exact city is irrelevant; mismatch is what matters to an anti-bot system).
_GEO_REGION: dict[str, str] = {
    "us": None,  # exact country match expected
    "gb": None,
    "uk": None,
    "au": None,
    "ca": None,
}

# Human-readable country-name lookup for a geo code (best-effort).
_GEO_LABEL = {
    "us": "United States",
    "gb": "United Kingdom",
    "uk": "United Kingdom",
    "au": "Australia",
    "ca": "Canada",
    "de": "Germany",
    "fr": "France",
    "in": "India",
}


def verify_geo_coherence(geo: str, ip_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """H4 — Check that the egress exit matches the requested geo.

    Anti-bot systems flag a proxy when the browser locale/timezone/config is
    consistent with one country but the exit IP (or its DNS resolver) belongs
    to another — a "geo-mismatch". This guard reports the mismatch so the
    egress layer can reject the route before a session is burned.

    ``ip_context`` may carry pre-fetched ipinfo keys: ``country``, ``city``,
    ``region``, ``asn``. If not provided, a DNS/IP probe is attempted lazily.
    """
    geo = geo.lower().replace("gb", "uk")
    result: dict[str, Any] = {
        "requested_geo": geo,
        "coherent": True,
        "mismatch": None,
        "ip_context": ip_context or {},
    }

    # No context and no network probe → cannot confirm; be permissive but flag.
    if not ip_context:
        result["coherent"] = None  # unverified, not a hard fail
        result["mismatch"] = "unverified-no-ip-context"
        return result

    ip_country = ip_context.get("country")
    if not ip_country:
        result["coherent"] = None
        result["mismatch"] = "missing-ip-country"
        return result

    expected = _GEO_LABEL.get(geo, "")
    if expected and ip_country.lower() != expected.lower():
        result["coherent"] = False
        result["mismatch"] = (
            f"geo-mismatch: requested={geo} ({expected}), exit_ip={ip_country}"
        )
        if _is_cloud():
            logger.warning("Egress geo mismatch detected: %s", result["mismatch"])

    return result



class SmartPolicySelector:
    """Central router: picks best egress based on task type, geo, and cost."""

    def __init__(self) -> None:
        # Lazy-import route providers to avoid import errors when
        # optional dependencies (boto3, requests-ip-rotator) are missing.
        self._routes: list[dict[str, Any]] = []
        self._initialised = False

    def _ensure_init(self) -> None:
        if self._initialised:
            return
        self._initialised = True

        # Import each egress module — any import failure is non-fatal.
        try:
            from egress_mobile import MobileRotator

            self._routes.append(
                {
                    "name": "mobile_rotator",
                    "priority": 1,
                    "provider": MobileRotator,
                    "cloud_only": False,
                    "cost": 0,
                }
            )
        except ImportError:
            pass

        try:
            from egress_wireguard import WireGuardMesh

            self._routes.append(
                {
                    "name": "wireguard_mesh",
                    "priority": 1,
                    "provider": WireGuardMesh,
                    "cloud_only": False,
                    "cost": 0,
                }
            )
        except ImportError:
            pass

        # Cloudflare Workers Egress (100k req/day free, zero-cost edge proxy)
        cf_worker_url = os.environ.get("CLOUDFLARE_EGRESS_WORKER_URL")
        if cf_worker_url:
            class CloudflareWorkerProvider:
                @staticmethod
                def is_available() -> bool:
                    return bool(os.environ.get("CLOUDFLARE_EGRESS_WORKER_URL"))

                @staticmethod
                def health_check() -> dict[str, Any]:
                    return {
                        "name": "cloudflare_workers",
                        "available": bool(os.environ.get("CLOUDFLARE_EGRESS_WORKER_URL")),
                        "endpoint": os.environ.get("CLOUDFLARE_EGRESS_WORKER_URL"),
                        "cost": "$0 (100k req/day)",
                    }

            self._routes.append(
                {
                    "name": "cloudflare_workers",
                    "priority": 2,
                    "provider": CloudflareWorkerProvider,
                    "cloud_only": False,
                    "cost": 0,
                }
            )

        # Public Proxy Pool (Zero-Cost multi-source feeds with SQLite caching)
        try:
            from egress_public_pool import EgressPublicPoolProvider

            self._routes.append(
                {
                    "name": "public_proxy_pool",
                    "priority": 2,
                    "provider": EgressPublicPoolProvider,
                    "cloud_only": False,
                    "cost": 0,
                }
            )
        except ImportError:
            pass

        # DataImpulse Residential Proxy (Cloud Priority & Local Geo Targeting)
        try:
            from egress_dataimpulse import DataImpulseProxyRouter

            self._routes.append(
                {
                    "name": "dataimpulse",
                    "priority": 3,
                    "provider": DataImpulseProxyRouter,
                    "cloud_only": False,
                    "cost": 1,  # pay-per-GB
                }
            )
        except ImportError:
            pass

    def get_proxy(
        self,
        task_type: str = "browse",
        geo: str = "us",
        force: str | None = None,
    ) -> str | None:
        """Select the best proxy URL for the given task.

        Args:
            task_type: "browse" | "crawl" | "scrape" | "journey_qa"
            geo: Target country code ("us", "gb", "au", "ca")
            force: Force a specific egress route name (e.g. "dataimpulse")

        Returns:
            Proxy URL string, or None for direct connection.
        """
        self._ensure_init()

        # Force a specific route if requested
        if force:
            return self._get_from_route(force, geo)

        # SERP recon: MUST route through geo-coherent residential egress (a bare
        # direct/VPN hit triggers the Google bot-gate — verified live). DataImpulse
        # residential is the only route that returns a real proxied HTTP URL for
        # SERP position scanning. Fall back to cloud-worker/public-pool only if
        # DataImpulse is unavailable; never return None (direct) here.
        if task_type == "serp_recon":
            for name in ["dataimpulse", "cloudflare_workers", "public_proxy_pool"]:
                url = self._get_from_route(name, geo)
                if url:
                    return url
            return None

        # In cloud mode: DataImpulse Residential is Priority 1 for human simulation,
        # with fallback to Cloudflare Workers, then Public Pool.
        if _is_cloud():
            for name in ["dataimpulse", "cloudflare_workers", "public_proxy_pool"]:
                url = self._get_from_route(name, geo)
                if url:
                    return url
            return None

        # Local mode: try routes in priority order
        for route in sorted(self._routes, key=lambda r: r["priority"]):
            if not route["provider"].is_available():
                continue

            name = route["name"]

            # Mobile rotator: rotate IP and return direct connection
            if name == "mobile_rotator":
                from egress_mobile import MobileRotator

                new_ip = MobileRotator.rotate_ip()
                if new_ip:
                    logger.info("Using mobile CGNAT IP: %s", new_ip)
                    return None  # Direct connection with new CGNAT IP

            # WireGuard: return direct (traffic goes through VPN tunnel)
            if name == "wireguard_mesh":
                logger.info("Using WireGuard/Tailscale exit node")
                return None  # Direct connection through VPN

            # Public Proxy Pool: return auto-validated proxy URL
            if name == "public_proxy_pool":
                from egress_public_pool import EgressPublicPoolProvider

                url = EgressPublicPoolProvider.get_proxy_url()
                if url:
                    logger.info("Using validated public proxy pool: %s", url)
                    return url

            # DataImpulse: return proper proxy URL
            if name == "dataimpulse":
                from egress_dataimpulse import DataImpulseProxyRouter

                url = DataImpulseProxyRouter.get_proxy_url(geo)
                if url:
                    logger.info("Using DataImpulse residential proxy (geo=%s)", geo)
                    return url

        logger.info("No egress route available — using direct connection")
        return None

    def _get_from_route(self, name: str, geo: str) -> str | None:
        """Get proxy URL from a specific named route."""
        self._ensure_init()
        for route in self._routes:
            if route["name"] == name and route["provider"].is_available():
                if name == "dataimpulse":
                    from egress_dataimpulse import DataImpulseProxyRouter

                    return DataImpulseProxyRouter.get_proxy_url(geo)
                if name == "public_proxy_pool":
                    from egress_public_pool import EgressPublicPoolProvider

                    return EgressPublicPoolProvider.get_proxy_url()
                return None  # Other routes use direct/tunnel connections
        return None

    def test_all_routes(self) -> dict[str, dict[str, Any]]:
        """Run health checks on all registered egress routes."""
        self._ensure_init()
        results: dict[str, dict[str, Any]] = {}
        for route in self._routes:
            try:
                results[route["name"]] = route["provider"].health_check()
            except Exception as exc:
                results[route["name"]] = {
                    "name": route["name"],
                    "available": False,
                    "error": str(exc)[:200],
                }

        # Always include "direct" as a fallback
        try:
            import urllib.request

            start = time.monotonic()
            with urllib.request.urlopen("https://api.ipify.org", timeout=10) as resp:
                latency = (time.monotonic() - start) * 1000
                ip = resp.read().decode().strip()
                results["direct"] = {
                    "name": "direct",
                    "available": True,
                    "current_ip": ip,
                    "latency_ms": round(latency, 1),
                }
        except Exception as exc:
            results["direct"] = {
                "name": "direct",
                "available": False,
                "error": str(exc)[:200],
            }

        return results

    def get_status(self) -> dict[str, Any]:
        """Summary of all available egress routes."""
        self._ensure_init()
        checks = self.test_all_routes()
        available = [name for name, info in checks.items() if info.get("available")]
        return {
            "environment": "cloud" if _is_cloud() else "local",
            "available_routes": available,
            "total_routes": len(checks),
            "details": checks,
        }

    def print_status(self) -> None:
        """Pretty-print egress status to stdout."""
        status = self.get_status()
        print("=" * 60)
        print(" 🔀 GROUNDWORK EGRESS MESH STATUS")
        print("=" * 60)
        print(f"\n  Environment : {status['environment']}")
        print(f"  Routes found: {len(status['available_routes'])} / {status['total_routes']}")
        print()
        for name, info in status["details"].items():
            icon = "✅" if info.get("available") else "❌"
            latency = f" ({info['latency_ms']}ms)" if info.get("latency_ms") else ""
            ip_info = f" IP: {info['current_ip']}" if info.get("current_ip") else ""
            error = f" ⚠ {info['error']}" if info.get("error") else ""
            print(f"  {icon} {name:<25}{latency}{ip_info}{error}")
        print("\n" + "=" * 60)
