"""Groundwork WireGuard / Tailscale Mesh (Egress Layer).

Leverages personal broadband connections as residential exit nodes.
Supports both Tailscale CLI and raw WireGuard (wg-quick).

Auto-detects which backend is installed — gracefully returns
``is_available() = False`` when neither is present.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ExitNode:
    node_id: str
    name: str
    ip: str
    country: str
    online: bool
    backend: str  # "tailscale" | "wireguard"


class WireGuardMesh:
    """Tailscale CLI or wg-quick wrapper for residential broadband egress."""

    CMD_TIMEOUT = 10

    # ── Backend Detection ────────────────────────────────────────────

    @staticmethod
    def detect_backend() -> str:
        """Detect which VPN backend is available.

        Returns: "tailscale" | "wireguard" | "none"
        """
        # Check Tailscale first (preferred — managed mesh)
        try:
            result = subprocess.run(
                ["tailscale", "version"],
                capture_output=True,
                text=True,
                timeout=WireGuardMesh.CMD_TIMEOUT,
            )
            if result.returncode == 0:
                return "tailscale"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Check wg-quick / wg
        try:
            result = subprocess.run(
                ["wg", "show"],
                capture_output=True,
                text=True,
                timeout=WireGuardMesh.CMD_TIMEOUT,
            )
            if result.returncode == 0:
                return "wireguard"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        return "none"

    # ── Tailscale Operations ─────────────────────────────────────────

    @classmethod
    def list_exit_nodes(cls) -> list[ExitNode]:
        """List available exit nodes from Tailscale or WireGuard configs."""
        backend = cls.detect_backend()

        if backend == "tailscale":
            return cls._tailscale_exit_nodes()
        if backend == "wireguard":
            return cls._wireguard_interfaces()
        return []

    @staticmethod
    def _tailscale_exit_nodes() -> list[ExitNode]:
        """Query Tailscale for exit nodes in the tailnet via CLI or REST API."""
        # 1. Try local CLI first
        try:
            result = subprocess.run(
                ["tailscale", "status", "--json"],
                capture_output=True,
                text=True,
                timeout=WireGuardMesh.CMD_TIMEOUT,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                nodes: list[ExitNode] = []
                for _key, peer in (data.get("Peer") or {}).items():
                    if peer.get("ExitNode") or peer.get("ExitNodeOption"):
                        nodes.append(
                            ExitNode(
                                node_id=peer.get("PublicKey", "")[:12],
                                name=peer.get("HostName", "unknown"),
                                ip=peer.get("TailscaleIPs", [""])[0] if peer.get("TailscaleIPs") else "",
                                country=peer.get("Location", {}).get("CountryCode", "??")
                                if peer.get("Location")
                                else "??",
                                online=peer.get("Online", False),
                                backend="tailscale",
                            )
                        )
                if nodes:
                    return nodes
        except Exception:
            pass

        # 2. Try REST API v2 fallback if TAILSCALE_API_KEY is configured
        import os

        api_key = os.environ.get("TAILSCALE_API_KEY")
        if api_key:
            try:
                import urllib.request

                tailnet = os.environ.get("TAILSCALE_TAILNET", "tail290b4e.ts.net")
                url = f"https://api.tailscale.com/api/v2/tailnet/{tailnet}/devices"
                req = urllib.request.Request(
                    url, headers={"Authorization": f"Bearer {api_key}", "User-Agent": "Groundwork/1.0"}
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    dev_data = json.loads(resp.read().decode("utf-8"))
                    api_nodes: list[ExitNode] = []
                    for dev in dev_data.get("devices", []):
                        api_nodes.append(
                            ExitNode(
                                node_id=str(dev.get("id")),
                                name=dev.get("name", "tailscale-node"),
                                ip=dev.get("addresses", [""])[0] if dev.get("addresses") else "",
                                country="ID",
                                online=True,
                                backend="tailscale_api",
                            )
                        )
                    return api_nodes
            except Exception as exc:
                logger.debug("Tailscale REST API query failed: %s", exc)

        return []

    @staticmethod
    def _wireguard_interfaces() -> list[ExitNode]:
        """List WireGuard interfaces from wg show."""
        try:
            result = subprocess.run(
                ["wg", "show", "interfaces"],
                capture_output=True,
                text=True,
                timeout=WireGuardMesh.CMD_TIMEOUT,
            )
            if result.returncode != 0:
                return []
            interfaces = result.stdout.strip().split()
            return [
                ExitNode(
                    node_id=iface,
                    name=iface,
                    ip="",
                    country="??",
                    online=True,
                    backend="wireguard",
                )
                for iface in interfaces
            ]
        except Exception:
            return []

    # ── Connection Management ────────────────────────────────────────

    @classmethod
    def connect(cls, node_id: str) -> bool:
        """Connect to a specific exit node."""
        backend = cls.detect_backend()

        if backend == "tailscale":
            try:
                result = subprocess.run(
                    ["tailscale", "set", "--exit-node", node_id],
                    capture_output=True,
                    text=True,
                    timeout=cls.CMD_TIMEOUT,
                )
                return result.returncode == 0
            except Exception as exc:
                logger.warning("Failed to connect to Tailscale exit node %s: %s", node_id, exc)
                return False

        if backend == "wireguard":
            try:
                result = subprocess.run(
                    ["wg-quick", "up", node_id],
                    capture_output=True,
                    text=True,
                    timeout=cls.CMD_TIMEOUT,
                )
                return result.returncode == 0
            except Exception as exc:
                logger.warning("Failed to bring up WireGuard interface %s: %s", node_id, exc)
                return False

        return False

    @staticmethod
    def get_public_ip() -> str | None:
        """Get current public IP address."""
        try:
            import urllib.request

            with urllib.request.urlopen("https://api.ipify.org", timeout=10) as resp:
                return resp.read().decode().strip()
        except Exception:
            return None

    @classmethod
    def is_available(cls) -> bool:
        """Check if Tailscale or WireGuard is installed."""
        return cls.detect_backend() != "none"

    @classmethod
    def health_check(cls) -> dict[str, Any]:
        """Full health check: backend detection + exit node listing."""
        result: dict[str, Any] = {
            "name": "wireguard_mesh",
            "available": False,
            "backend": "none",
            "exit_nodes": [],
            "current_ip": None,
            "error": None,
        }
        try:
            backend = cls.detect_backend()
            result["backend"] = backend
            result["available"] = backend != "none"
            if backend != "none":
                nodes = cls.list_exit_nodes()
                result["exit_nodes"] = [
                    {"id": n.node_id, "name": n.name, "country": n.country, "online": n.online} for n in nodes
                ]
                result["current_ip"] = cls.get_public_ip()
        except Exception as exc:
            result["error"] = str(exc)[:200]
        return result
