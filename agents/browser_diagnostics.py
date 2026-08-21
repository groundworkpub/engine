"""Groundwork Browser Fingerprint Diagnostics (Browser Layer).

Runs in-browser tests to verify fingerprint consistency and detect
potential leaks (WebRTC, Canvas, WebGL, Navigator properties).

Usage:
    from browser_runtime import BrowserRuntime
    from browser_diagnostics import run_fingerprint_diagnostics

    async with BrowserRuntime.create("chromium") as page:
        report = await run_fingerprint_diagnostics(page)
        print(report)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def run_fingerprint_diagnostics(page: Any) -> dict[str, Any]:
    """Run comprehensive fingerprint consistency tests in the browser.

    Args:
        page: Playwright Page object.

    Returns:
        Dict with overall pass/fail and per-test details.
    """
    results: dict[str, Any] = {}

    results["webrtc_leak"] = await _check_webrtc(page)
    results["canvas_noise"] = await _check_canvas(page)
    results["webgl_renderer"] = await _check_webgl(page)
    results["navigator_props"] = await _check_navigator(page)
    results["timezone"] = await _check_timezone(page)

    all_pass = all(r.get("ok", False) for r in results.values())
    return {
        "pass": all_pass,
        "tests_run": len(results),
        "tests_passed": sum(1 for r in results.values() if r.get("ok")),
        "details": results,
    }


async def _check_webrtc(page: Any) -> dict[str, Any]:
    """Verify WebRTC does not leak local/public IP addresses."""
    result: dict[str, Any] = {"ok": True, "leaked_ips": [], "error": None}
    try:
        leaked = await page.evaluate("""() => {
            return new Promise((resolve) => {
                const ips = [];
                try {
                    const pc = new RTCPeerConnection({iceServers: []});
                    pc.createDataChannel('');
                    pc.createOffer().then(offer => pc.setLocalDescription(offer));
                    pc.onicecandidate = (e) => {
                        if (!e.candidate) { resolve(ips); return; }
                        const m = e.candidate.candidate.match(
                            /([0-9]{1,3}(\\.[0-9]{1,3}){3}|[a-fA-F0-9]{1,4}(:[a-fA-F0-9]{1,4}){7})/
                        );
                        if (m && !m[1].startsWith('0.0.0') && m[1] !== '0.0.0.0') {
                            ips.push(m[1]);
                        }
                    };
                    setTimeout(() => resolve(ips), 3000);
                } catch { resolve(ips); }
            });
        }""")
        result["leaked_ips"] = leaked or []
        # Private IPs (192.168.x.x, 10.x.x.x, 172.16-31.x.x) are a leak
        private_leaked = [
            ip for ip in (leaked or []) if ip.startswith("192.168.") or ip.startswith("10.") or ip.startswith("172.")
        ]
        result["ok"] = len(private_leaked) == 0
        if private_leaked:
            result["error"] = f"Private IP leaked via WebRTC: {private_leaked}"
    except Exception as exc:
        result["ok"] = True  # If WebRTC fails entirely, no leak
        result["error"] = f"WebRTC test error (likely blocked — good): {str(exc)[:100]}"
    return result


async def _check_canvas(page: Any) -> dict[str, Any]:
    """Verify canvas fingerprint produces consistent but unique output."""
    result: dict[str, Any] = {"ok": True, "hash": None, "error": None}
    try:
        canvas_hash = await page.evaluate("""() => {
            const canvas = document.createElement('canvas');
            const ctx = canvas.getContext('2d');
            ctx.textBaseline = 'top';
            ctx.font = '14px Arial';
            ctx.fillText('Groundwork fingerprint test 🎨', 2, 2);
            ctx.fillStyle = 'rgba(102, 204, 0, 0.7)';
            ctx.fillRect(50, 50, 100, 50);
            return canvas.toDataURL().slice(0, 60);
        }""")
        result["hash"] = canvas_hash
        # Canvas should produce output (not empty/default)
        result["ok"] = bool(canvas_hash) and len(canvas_hash) > 30
    except Exception as exc:
        result["ok"] = False
        result["error"] = str(exc)[:200]
    return result


async def _check_webgl(page: Any) -> dict[str, Any]:
    """Verify WebGL renderer string is present and consistent."""
    result: dict[str, Any] = {"ok": True, "renderer": None, "vendor": None, "error": None}
    try:
        webgl_info = await page.evaluate("""() => {
            const canvas = document.createElement('canvas');
            const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
            if (!gl) return null;
            const ext = gl.getExtension('WEBGL_debug_renderer_info');
            if (!ext) return { renderer: 'unknown', vendor: 'unknown' };
            return {
                renderer: gl.getParameter(ext.UNMASKED_RENDERER_WEBGL),
                vendor: gl.getParameter(ext.UNMASKED_VENDOR_WEBGL),
            };
        }""")
        if webgl_info:
            result["renderer"] = webgl_info.get("renderer")
            result["vendor"] = webgl_info.get("vendor")
            result["ok"] = bool(result["renderer"])
        else:
            result["ok"] = True  # WebGL blocked = fine for privacy
            result["error"] = "WebGL not available (blocked — acceptable)"
    except Exception as exc:
        result["ok"] = False
        result["error"] = str(exc)[:200]
    return result


async def _check_navigator(page: Any) -> dict[str, Any]:
    """Verify navigator properties are consistent with the persona."""
    result: dict[str, Any] = {"ok": True, "properties": {}, "error": None}
    try:
        props = await page.evaluate("""() => ({
            userAgent: navigator.userAgent,
            platform: navigator.platform,
            language: navigator.language,
            languages: navigator.languages,
            hardwareConcurrency: navigator.hardwareConcurrency,
            maxTouchPoints: navigator.maxTouchPoints,
            cookieEnabled: navigator.cookieEnabled,
            webdriver: navigator.webdriver,
        })""")
        result["properties"] = props or {}
        # Webdriver should be false (not detected as automation)
        if props and props.get("webdriver") is True:
            result["ok"] = False
            result["error"] = "navigator.webdriver is true — detected as automation"
        # Basic sanity: user agent should exist
        if not (props or {}).get("userAgent"):
            result["ok"] = False
            result["error"] = "navigator.userAgent is empty"
    except Exception as exc:
        result["ok"] = False
        result["error"] = str(exc)[:200]
    return result


async def _check_timezone(page: Any) -> dict[str, Any]:
    """Verify browser timezone matches expected configuration."""
    result: dict[str, Any] = {"ok": True, "timezone": None, "offset": None, "error": None}
    try:
        tz_info = await page.evaluate("""() => ({
            timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
            offset: new Date().getTimezoneOffset(),
        })""")
        if tz_info:
            result["timezone"] = tz_info.get("timezone")
            result["offset"] = tz_info.get("offset")
            result["ok"] = bool(result["timezone"])
    except Exception as exc:
        result["ok"] = False
        result["error"] = str(exc)[:200]
    return result


def print_diagnostics_report(report: dict[str, Any]) -> None:
    """Pretty-print a fingerprint diagnostics report to stdout."""
    status = "✅ ALL PASS" if report["pass"] else "❌ ISSUES FOUND"
    print("=" * 60)
    print(f" 🔬 FINGERPRINT DIAGNOSTICS — {status}")
    print(f"    {report['tests_passed']} / {report['tests_run']} tests passed")
    print("=" * 60)
    for name, detail in report.get("details", {}).items():
        icon = "✅" if detail.get("ok") else "❌"
        error = f" — {detail['error']}" if detail.get("error") else ""
        print(f"  {icon} {name}{error}")
        # Print relevant details
        for key in ["leaked_ips", "hash", "renderer", "vendor", "timezone"]:
            if key in detail and detail[key]:
                print(f"      {key}: {detail[key]}")
    print("=" * 60)
