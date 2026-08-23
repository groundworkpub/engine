"""Unit tests for the shared browser stealth module (SSOT fingerprint matrix)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from browser_stealth import (
    AD_BLOCK_DOMAINS,
    BENCHMARK_TARGETS,
    build_stealth_script,
    domain_is_blocked,
    normalize_platform,
    stealth_launch_args,
)


class TestNormalizePlatform:
    def test_windows_variants(self) -> None:
        for p in ("Windows", "Windows 10", "WIN32", "windows 11"):
            assert normalize_platform(p) == "windows"

    def test_macos(self) -> None:
        assert normalize_platform("macOS") == "macos"
        assert normalize_platform("MacIntel") == "macos"
        assert normalize_platform("") == "macos"

    def test_ios_and_android(self) -> None:
        assert normalize_platform("iOS") == "ios"
        assert normalize_platform("iPhone") == "ios"
        assert normalize_platform("iPad") == "ios"
        assert normalize_platform("Android") == "android"
        assert normalize_platform("Linux armv8l") == "android"


class TestBuildStealthScript:
    def test_windows_gets_nvidia_matrix(self) -> None:
        script = build_stealth_script(platform="Windows", session_seed="s1")
        assert "NVIDIA" in script
        assert "Win32" in script
        assert "Google Inc. (NVIDIA)" in script

    def test_macos_gets_apple_matrix(self) -> None:
        script = build_stealth_script(platform="macOS", session_seed="s2")
        assert "Apple M1 Pro" in script
        assert "MacIntel" in script

    def test_ios_gets_apple_gpu(self) -> None:
        script = build_stealth_script(platform="iOS", is_mobile=True, session_seed="s3")
        assert "Apple GPU" in script
        assert "iPhone" in script
        # Mobile persona: empty plugins array (Safari has no plugins)
        assert "plugins" in script
        assert "mimeTypes" in script

    def test_android_gets_adreno(self) -> None:
        script = build_stealth_script(platform="Android", is_mobile=True, session_seed="s4")
        assert "Adreno" in script

    def test_webdriver_hidden(self) -> None:
        script = build_stealth_script(platform="macOS", session_seed="s5")
        assert "webdriver" in script
        assert "get: () => undefined" in script

    def test_canvas_noise_and_webrtc(self) -> None:
        script = build_stealth_script(platform="macOS", session_seed="s6")
        assert "getImageData" in script
        assert "hardwareConcurrency" in script

    def test_firefox_has_empty_plugins(self) -> None:
        script = build_stealth_script(platform="Windows", is_firefox=True, session_seed="s7")
        assert "get: () => []" in script

    def test_session_seed_is_deterministic(self) -> None:
        a = build_stealth_script(platform="macOS", session_seed="same")
        b = build_stealth_script(platform="macOS", session_seed="same")
        assert a == b


class TestFirewall:
    def test_blocks_google_ads(self) -> None:
        assert domain_is_blocked("https://securepubads.g.doubleclick.net/tag/js/gpt.js")
        assert domain_is_blocked("https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js")

    def test_blocks_programmatic(self) -> None:
        assert domain_is_blocked("https://ads.pubmatic.com/AdServer/js/pwt.js")
        assert domain_is_blocked("https://cdn.criteo.net/js/ld/publishertag.js")
        assert domain_is_blocked("https://acdn.adnxs.com/ast/ast.js")

    def test_allows_groundwork_and_benign(self) -> None:
        assert not domain_is_blocked("https://gworky.com/article/foo")
        assert not domain_is_blocked("https://fonts.googleapis.com/css")
        assert not domain_is_blocked("https://gworky.com/_next/static/chunk.js")

    def test_domain_list_is_comprehensive(self) -> None:
        # SSOT must keep parity with the strictest prior list (40+ entries)
        assert len(AD_BLOCK_DOMAINS) >= 40
        for critical in (
            "googlesyndication.com",
            "doubleclick.net",
            "googleadservices.com",
            "googletagmanager.com",
            "criteo.com",
            "adnxs.com",
            "taboola.com",
            "outbrain.com",
            "scorecardresearch.com",
        ):
            assert critical in AD_BLOCK_DOMAINS


class TestLaunchArgs:
    def test_webdriver_and_webrtc_hardening(self) -> None:
        args = stealth_launch_args()
        assert "--disable-blink-features=AutomationControlled" in args
        assert any("webrtc" in a for a in args)


class TestBenchmarkTargets:
    def test_sannysoft_target_present(self) -> None:
        assert BENCHMARK_TARGETS["sannysoft"] == "https://bot.sannysoft.com"
        assert "browserleaks_webrtc" in BENCHMARK_TARGETS
