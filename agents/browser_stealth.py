"""Groundwork Shared Browser Stealth Hardening (Single Source of Truth).

Consumed by ``organic_simulator.py``, ``traffic_cli.py`` and ``browser_runtime.py``
to eliminate duplicated CDP stealth scripts and per-file ad-firewall lists.

Provides:
  - ``AD_BLOCK_DOMAINS`` — AdSense / Mediavine zero-fraud firewall domain list.
  - ``WEBGL_MATRIX`` / hardware matrix — OS/platform -> WebGL vendor, renderer,
    navigator.platform, hardwareConcurrency, deviceMemory sync.
  - ``build_stealth_script(...)`` — persona-aware CDP stealth injection script
    (fingerprint matrix sync + canvas noise + WebRTC hardening).

Axiom I compliance: shared universal contract lives here, not forked per agent.
"""

from __future__ import annotations

from typing import Any

# ── AdSense / Mediavine Zero-Fraud Firewall ────────────────────────────────
# CRITICAL: Every simulation session MUST block ALL ad & tracker domains.
# Any intercepted ad impression = IVT violation -> account suspension risk.
# Domains cover: DSPs, SSPs, DMPs, pixel trackers, analytics beacons.
AD_BLOCK_DOMAINS: frozenset[str] = frozenset(
    [
        # Google Ads ecosystem
        "googlesyndication.com",
        "doubleclick.net",
        "googleadservices.com",
        "googletagservices.com",
        "googletagmanager.com",
        "googletag.com",
        "google-analytics.com",
        "analytics.google.com",
        # Programmatic DSPs / SSPs
        "adnxs.com",
        "appnexus.com",  # Xandr / AppNexus
        "rubiconproject.com",
        "rubiconads.com",  # Rubicon (Magnite)
        "openx.net",
        "openx.com",  # OpenX
        "pubmatic.com",  # PubMatic
        "casalemedia.com",  # Index Exchange
        "indexexchange.com",
        "media.net",  # Media.net
        "contextweb.com",  # Pulsepoint
        "sovrn.com",
        "lijit.com",  # Sovrn
        "districtm.io",  # District M
        "triplelift.com",  # TripleLift
        "sharethrough.com",  # Sharethrough
        "spotxchange.com",
        "spotx.tv",  # SpotX
        "33across.com",  # 33Across
        "yieldmo.com",  # Yieldmo
        "adsrvr.org",  # The Trade Desk
        # Retargeting & performance
        "criteo.com",
        "criteo.net",
        "adroll.com",
        "adrollapp.com",
        "amazon-adsystem.com",
        "smartadserver.com",
        "advertising.com",
        "impact.com",
        "impact-ad.com",
        "outbrain.com",  # Content recommendation
        "taboola.com",
        # DMPs & audience trackers
        "demdex.net",  # Adobe Audience Manager
        "everesttech.net",  # Adobe
        "omtrdc.net",  # Adobe Analytics
        "scorecardresearch.com",  # comScore
        "quantserve.com",  # Quantcast
        "moatads.com",
        "moat.com",  # Oracle Moat (viewability)
        # Social pixels (no ad delivery, but prevent accidental re-targeting)
        "connect.facebook.net",
        "ads.twitter.com",
        "analytics.twitter.com",
        "snap.licdn.com",
        "ads.linkedin.com",
    ]
)

# ── Fingerprint Matrix (OS/platform -> WebGL vendor & renderer) ─────────────
# 1:1 sync between User-Agent platform and WebGL/navigator hardware identity.
# Prevents the "Windows UA + Apple M1 GPU" mismatch heuristic that flags
# sessions as Anomalous / Synthetic Bot.
WEBGL_MATRIX: dict[str, dict[str, str]] = {
    "macos": {
        "webgl_vendor": "Google Inc. (Apple)",
        "webgl_renderer": "ANGLE (Apple, Apple M1 Pro, OpenGL 4.1)",
        "navigator_platform": "MacIntel",
    },
    "windows": {
        "webgl_vendor": "Google Inc. (NVIDIA)",
        "webgl_renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "navigator_platform": "Win32",
    },
    "ios": {
        "webgl_vendor": "Apple Inc.",
        "webgl_renderer": "Apple GPU",
        "navigator_platform": "iPhone",
    },
    "android": {
        "webgl_vendor": "Google Inc. (Qualcomm)",
        "webgl_renderer": "ANGLE (Qualcomm, Adreno (TM) 750, OpenGL ES 3.2)",
        "navigator_platform": "Linux armv8l",
    },
}

HARDWARE_CONCURRENCY: dict[str, tuple[int, ...]] = {
    "macos": (8, 10),
    "windows": (8, 12, 16),
    "ios": (2, 4),
    "android": (8, 8),
}

DEVICE_MEMORY: dict[str, tuple[int, ...]] = {
    "macos": (8, 16),
    "windows": (16, 32),
    "ios": (),
    "android": (4, 8),
}

# SannySoft / Incolumitas / CreepJS benchmark targets
BENCHMARK_TARGETS: dict[str, str] = {
    "sannysoft": "https://bot.sannysoft.com",
    "browserleaks_js": "https://browserleaks.com/javascript",
    "browserleaks_webrtc": "https://browserleaks.com/webrtc",
}


def normalize_platform(platform: str) -> str:
    """Map a persona platform string to a stable fingerprint-matrix key."""
    key = (platform or "").lower()
    if "win" in key:
        return "windows"
    if "ios" in key or "iphone" in key or "ipad" in key:
        return "ios"
    if "android" in key or "linux" in key:
        return "android"
    return "macos"


def build_stealth_script(
    *,
    platform: str,
    is_mobile: bool = False,
    is_firefox: bool = False,
    session_seed: str = "",
) -> str:
    """Build a persona-aware CDP stealth injection script.

    The returned JS syncs WebGL vendor/renderer, navigator.platform,
    hardwareConcurrency and deviceMemory with the persona's OS so the
    fingerprint matrix is internally consistent (Windows persona -> NVIDIA,
    macOS persona -> Apple, iOS -> Apple GPU, Android -> Adreno).

    Adds sparse canvas-noise so each session produces a unique but subtle
    canvas hash, and hardens WebRTC to avoid local-IP leakage.
    """
    key = normalize_platform(platform)
    gl = WEBGL_MATRIX[key]
    concurrency_choices = HARDWARE_CONCURRENCY.get(key, (8,))
    memory_choices = DEVICE_MEMORY.get(key, (8,))
    # Deterministic-ish per session: pick from the pool using the seed so
    # repeated runs of the same persona don't always pick the first value.
    _seed_hash = sum(ord(c) for c in session_seed) if session_seed else 0

    def _pick(choices: tuple[int, ...]) -> int:
        if not choices:
            return 8
        return choices[_seed_hash % len(choices)] if _seed_hash else choices[0]

    hw = _pick(concurrency_choices)
    mem = _pick(memory_choices)
    platform_val = gl["navigator_platform"]

    plugins_mock = (
        """
    Object.defineProperty(navigator, 'plugins', {
        get: () => []
    });
    Object.defineProperty(navigator, 'mimeTypes', {
        get: () => []
    });
"""
        if is_firefox or is_mobile
        else """
    Object.defineProperty(navigator, 'plugins', {
        get: () => [
            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
            { name: 'Native Client', filename: 'internal-nacl-plugin' }
        ]
    });
"""
    )

    return f"""
(() => {{
    'use strict';
    // 1. Remove automation indicators
    Object.defineProperty(navigator, 'webdriver', {{ get: () => undefined }});
    delete navigator.__proto__.webdriver;

    // 2. Mock browser runtime (Chrome-compatible)
    if (!window.chrome) {{
        window.chrome = {{
            runtime: {{}},
            loadTimes: function() {{}},
            csi: function() {{}},
            app: {{}}
        }};
    }}

    // 3. Mock plugins / mimeTypes
{plugins_mock}

    // 4. Sync WebGL renderer + vendor to persona OS matrix
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(parameter) {{
        if (parameter === 37445) return '{gl['webgl_vendor']}';
        if (parameter === 37446) return '{gl['webgl_renderer']}';
        return getParameter.apply(this, arguments);
    }};

    // 5. Sync navigator.platform
    Object.defineProperty(navigator, 'platform', {{ get: () => '{platform_val}' }});

    // 6. Sync hardwareConcurrency & deviceMemory (consistent with persona)
    Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {hw} }});
    try {{
        Object.defineProperty(navigator, 'deviceMemory', {{ get: () => {mem} }});
    }} catch (e) {{}}

    // 7. Canvas noise: add sparse per-session pixel variance (invisible to eye,
    //    unique hash per session — defeats uniform headless canvas fingerprints)
    const origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
    CanvasRenderingContext2D.prototype.getImageData = function(x, y, w, h) {{
        const data = origGetImageData.call(this, x, y, w, h);
        const px = data.data;
        const seed = Date.now() & 0xff;
        for (let i = 0; i < px.length; i += 211) {{
            px[i] = (px[i] + ((seed + i) % 7)) & 255;
        }}
        return data;
    }};

    // 8. Spoof Battery & Permissions API
    if (navigator.getBattery) {{
        navigator.getBattery = async () => ({{
            charging: true,
            chargingTime: 0,
            dischargingTime: Infinity,
            level: 0.98,
            addEventListener: () => {{}}
        }});
    }}
    const origQuery = Permissions.prototype.query;
    Permissions.prototype.query = async function(p) {{
        if (p && p.name === 'notifications') return {{ state: 'denied' }};
        return origQuery.call(this, p);
    }};
}})();
"""


def stealth_launch_args() -> list[str]:
    """Chromium launch args shared across browser sessions.

    Includes WebRTC hardening so STUN candidates never leak the machine's
    real local/public IP.
    """
    return [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--no-sandbox",
        "--force-webrtc-ip-handling-policy=default_public_interface_only",
        "--webrtc-ip-handling-policy=default_public_interface_only",
    ]


def domain_is_blocked(url: str) -> bool:
    """Return True if url hits any AdSense firewall domain."""
    return any(domain in url for domain in AD_BLOCK_DOMAINS)


def export_stealth_contract() -> dict[str, Any]:
    """Serializable summary for diagnostics / benchmark reports."""
    return {
        "ad_block_domain_count": len(AD_BLOCK_DOMAINS),
        "webgl_matrix": WEBGL_MATRIX,
        "hardware_concurrency": {k: list(v) for k, v in HARDWARE_CONCURRENCY.items()},
        "device_memory": {k: list(v) for k, v in DEVICE_MEMORY.items()},
        "benchmark_targets": BENCHMARK_TARGETS,
        "launch_args": stealth_launch_args(),
    }


# ── Browser Engine Selector (2026 benchmark-driven) ─────────────────────────
# Empirical anti-detection benchmark (I. Paterson, 651 verdicts / 31 CF targets):
#   nodriver             28 OK / 0 blocked (system Chrome via raw CDP — best)
#   patchright(ch=chrome) 25 OK / 3 blocked (Playwright API, system Chrome)
#   camoufox (Firefox)    25 OK / 3 blocked (engine-level, ~700MB, Firefox-only)
#   vanilla playwright    24 OK / 5 blocked (current default — weakest)
#
# Groundwork keeps Playwright as the DEFAULT engine for safety, and exposes the
# selector so operators can opt into stronger engines per persona/target without
# breaking the stable Playwright code path.
ENGINE_PLAYWRIGHT = "playwright"
ENGINE_PATCHRIGHT = "patchright"
ENGINE_NODRIVER = "nodriver"
ENGINE_CAMOUFOX = "camoufox"

# Valid engines (also the union accepted by `--engine`).
SUPPORTED_ENGINES: tuple[str, ...] = (
    ENGINE_PLAYWRIGHT,
    ENGINE_PATCHRIGHT,
    ENGINE_NODRIVER,
    ENGINE_CAMOUFOX,
)

# Default engine when none is explicitly requested.
DEFAULT_ENGINE = ENGINE_PLAYWRIGHT

# Persona-based engine hint: Firefox personas are the natural fit for Camoufox;
# Chrome/Safari personas prefer nodriver or patchright. This is only a hint —
# the operator can always force an engine via `--engine`.
def recommended_engine_for(persona: dict[str, Any] | None = None) -> str:
    """Return the engine best matching a persona (best-effort hint)."""
    if not persona:
        return DEFAULT_ENGINE
    ua = str(persona.get("user_agent", ""))
    is_firefox = "firefox" in ua.lower()
    if is_firefox:
        return ENGINE_CAMOUFOX
    # Chrome/Chromium/Safari (the majority of personas) are best served by the
    # Playwright code path unless the operator opts into a stealth engine.
    return DEFAULT_ENGINE


def validate_engine(engine: str) -> str:
    """Validate a requested engine string; returns the canonical name.

    Raises ValueError for unknown engines so callers fail loudly on typo'd flags
    rather than silently falling back to Playwright.
    """
    if engine not in SUPPORTED_ENGINES:
        raise ValueError(
            f"Unknown browser engine '{engine}'. Supported: {', '.join(SUPPORTED_ENGINES)}"
        )
    return engine


def get_engine(
    requested: str | None = None,
    persona: dict[str, Any] | None = None,
) -> str:
    """Resolve the effective browser engine for a session.

    Priority: explicit `requested` (validated) > persona hint > DEFAULT_ENGINE.
    """
    if requested:
        return validate_engine(requested)
    return recommended_engine_for(persona)

