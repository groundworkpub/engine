"""Groundwork Mobile IP Rotator (Egress Layer).

Controls 4G USB modems or Android devices via ADB to rotate CGNAT IP
addresses by toggling airplane mode.  Designed for zero-cost IP rotation
using cellular ISP connections.

Auto-detects connected devices — gracefully returns ``is_available() = False``
when no hardware is present, so callers never crash.
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MobileDevice:
    device_id: str
    device_type: str  # "adb" | "usb_modem"
    model: str
    status: str  # "online" | "offline"


class MobileRotator:
    """Controls 4G USB modem / Android ADB for CGNAT IP rotation."""

    ADB_TIMEOUT = 10  # seconds

    # ── Device Detection ─────────────────────────────────────────────

    @staticmethod
    def detect_devices() -> list[MobileDevice]:
        """Auto-scan USB + ADB devices.  Returns empty list if none found."""
        devices: list[MobileDevice] = []

        # 1. Try ADB devices
        try:
            result = subprocess.run(
                ["adb", "devices", "-l"],
                capture_output=True,
                text=True,
                timeout=MobileRotator.ADB_TIMEOUT,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().splitlines()[1:]:
                    if not line.strip() or "offline" in line:
                        continue
                    parts = line.split()
                    if len(parts) >= 2 and parts[1] == "device":
                        device_id = parts[0]
                        model_match = re.search(r"model:(\S+)", line)
                        model = model_match.group(1) if model_match else "unknown"
                        devices.append(
                            MobileDevice(
                                device_id=device_id,
                                device_type="adb",
                                model=model,
                                status="online",
                            )
                        )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass  # ADB not installed or timed out

        # 2. Try USB modem detection (Linux/macOS: look for /dev/ttyUSB* or /dev/cu.*)
        try:
            import glob

            modem_paths = glob.glob("/dev/ttyUSB*") + glob.glob("/dev/cu.HUAWEIMobile*")
            for path in modem_paths:
                devices.append(
                    MobileDevice(
                        device_id=path,
                        device_type="usb_modem",
                        model="USB_Modem",
                        status="online",
                    )
                )
        except Exception:
            pass

        return devices

    # ── IP Rotation ──────────────────────────────────────────────────

    @staticmethod
    def toggle_airplane_mode(device_id: str) -> bool:
        """Toggle airplane mode on Android via ADB to obtain a new CGNAT IP.

        Sequence: airplane ON (2s) → airplane OFF (3s wait for reconnect).
        Returns True if toggle succeeded.
        """
        try:
            # Turn airplane mode ON
            subprocess.run(
                ["adb", "-s", device_id, "shell", "settings", "put", "global", "airplane_mode_on", "1"],
                capture_output=True,
                timeout=MobileRotator.ADB_TIMEOUT,
            )
            subprocess.run(
                ["adb", "-s", device_id, "shell", "am", "broadcast", "-a", "android.intent.action.AIRPLANE_MODE"],
                capture_output=True,
                timeout=MobileRotator.ADB_TIMEOUT,
            )
            time.sleep(2)

            # Turn airplane mode OFF
            subprocess.run(
                ["adb", "-s", device_id, "shell", "settings", "put", "global", "airplane_mode_on", "0"],
                capture_output=True,
                timeout=MobileRotator.ADB_TIMEOUT,
            )
            subprocess.run(
                ["adb", "-s", device_id, "shell", "am", "broadcast", "-a", "android.intent.action.AIRPLANE_MODE"],
                capture_output=True,
                timeout=MobileRotator.ADB_TIMEOUT,
            )
            time.sleep(3)  # Wait for cellular reconnect

            logger.info("Airplane mode toggled on device %s", device_id)
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            logger.warning("Failed to toggle airplane mode on %s: %s", device_id, exc)
            return False

    @staticmethod
    def get_current_ip(device_id: str | None = None) -> str | None:
        """Get the current public IP address via the mobile connection."""
        try:
            if device_id:
                # Via ADB: use curl through the device
                result = subprocess.run(
                    ["adb", "-s", device_id, "shell", "curl", "-s", "https://api.ipify.org"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if result.returncode == 0 and result.stdout.strip():
                    return result.stdout.strip()
            # Fallback: local curl
            import urllib.request

            with urllib.request.urlopen("https://api.ipify.org", timeout=10) as resp:
                return resp.read().decode().strip()
        except Exception:
            return None

    @classmethod
    def rotate_ip(cls, device_id: str | None = None) -> str | None:
        """Toggle airplane mode and return the new IP address."""
        devices = cls.detect_devices()
        if not devices:
            logger.info("No mobile devices detected for IP rotation")
            return None

        target = device_id or devices[0].device_id
        target_device = next((d for d in devices if d.device_id == target), None)

        if not target_device or target_device.device_type != "adb":
            logger.info("Device %s is not an ADB device, skipping rotation", target)
            return None

        old_ip = cls.get_current_ip(target)
        if cls.toggle_airplane_mode(target):
            new_ip = cls.get_current_ip(target)
            if new_ip and new_ip != old_ip:
                logger.info("IP rotated: %s → %s", old_ip, new_ip)
                return new_ip
            logger.warning("IP did not change after rotation (still %s)", old_ip)
            return new_ip
        return None

    @classmethod
    def is_available(cls) -> bool:
        """Check if any mobile device is connected and usable."""
        return len(cls.detect_devices()) > 0

    @classmethod
    def health_check(cls) -> dict[str, Any]:
        """Full health check: device detection + IP verification."""
        result: dict[str, Any] = {
            "name": "mobile_rotator",
            "available": False,
            "devices": [],
            "current_ip": None,
            "error": None,
        }
        try:
            devices = cls.detect_devices()
            result["devices"] = [{"id": d.device_id, "type": d.device_type, "model": d.model} for d in devices]
            result["available"] = len(devices) > 0
            if devices:
                result["current_ip"] = cls.get_current_ip(devices[0].device_id)
        except Exception as exc:
            result["error"] = str(exc)[:200]
        return result
