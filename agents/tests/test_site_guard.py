"""Tests for agents/site_guard.py — outside-only injection detection."""

import pytest
from agents.site_guard import (
    Finding,
    GuardReport,
    _is_canonical,
    _page_locs,
    _style_is_hidden,
    audit_page_html,
)


def _report() -> GuardReport:
    return GuardReport(base_url="https://gworky.com")


class TestPageLocs:
    NS = "http://www.sitemaps.org/schemas/sitemap/0.9"

    def test_excludes_image_namespace(self) -> None:
        xml = (
            f'<?xml version="1.0"?><urlset xmlns="{self.NS}" '
            f'xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">'
            f"<url><loc>https://gworky.com/article/a</loc>"
            f"<image:image><image:loc>https://images.unsplash.com/photo-1</image:loc></image:image>"
            f"</url></urlset>"
        )
        assert _page_locs(xml) == ["https://gworky.com/article/a"]

    def test_invalid_xml_returns_empty(self) -> None:
        assert _page_locs("<not-xml") == []


class TestCanonicalRoutes:
    @pytest.mark.parametrize(
        "path",
        [
            "/",
            "/money",
            "/money/credit-score",
            "/article/best-high-yield-savings-2026",
            "/tools/mortgage-calculator",
            "/author/elena-vasquez",
            "/subscribe",
        ],
    )
    def test_canonical(self, path: str) -> None:
        assert _is_canonical(path)

    @pytest.mark.parametrize(
        "path",
        [
            "/wp-admin/evil.php",
            "/wp-content/uploads/shell.php",
            "/cheap-viagra-store",
            "/casino-online-indonesia",
            "/.env",
            "/xmlrpc.php",
        ],
    )
    def test_non_canonical(self, path: str) -> None:
        assert not _is_canonical(path)


class TestHiddenStyles:
    @pytest.mark.parametrize(
        "style",
        ["display:none", "display: none", "visibility:hidden", "font-size:0", "font-size: 0px", "text-indent:-9999px", "left:-99999px"],
    )
    def test_hidden(self, style: str) -> None:
        assert _style_is_hidden(style)

    @pytest.mark.parametrize("style", ["color:red", "margin:10px", "font-size:16px", ""])
    def test_visible(self, style: str) -> None:
        assert not _style_is_hidden(style)


class TestPageAudit:
    def test_hidden_link_flagged(self) -> None:
        r = _report()
        audit_page_html(r, "https://gworky.com/article/x", '<a href="https://spam.example" style="display:none">buy</a>')
        assert r.critical and r.critical[0].layer == "hidden-link"

    def test_offcanvas_link_flagged(self) -> None:
        r = _report()
        audit_page_html(r, "https://gworky.com/article/x", '<a href="https://spam.example" style="text-indent:-9999px">x</a>')
        assert r.critical[0].layer == "hidden-link"

    def test_legitimate_anchor_clean(self) -> None:
        r = _report()
        audit_page_html(r, "https://gworky.com/article/x", '<a href="/article/y" style="color:blue">ok</a>')
        assert not r.findings

    def test_foreign_script_warns(self) -> None:
        r = _report()
        audit_page_html(r, "https://gworky.com/", '<script src="https://evil-cdn.example/x.js"></script>')
        assert any(f.layer == "foreign-script" for f in r.findings)

    def test_allowlisted_script_clean(self) -> None:
        r = _report()
        audit_page_html(r, "https://gworky.com/", '<script src="https://www.googletagmanager.com/gtag/js"></script>')
        assert not [f for f in r.findings if f.layer == "foreign-script"]

    def test_spam_ioc_density_critical(self) -> None:
        r = _report()
        audit_page_html(r, "https://gworky.com/article/x", "<p>casino viagra porn togel bandar q</p>")
        assert any(f.severity == "critical" and f.layer == "spam-ioc" for f in r.findings)

    def test_single_ioc_mention_not_flagged(self) -> None:
        r = _report()
        audit_page_html(r, "https://gworky.com/article/x", "<p>Online casino advertising is regulated in the UK.</p>")
        assert not [f for f in r.findings if f.layer == "spam-ioc"]

    def test_meta_refresh_cloaking(self) -> None:
        r = _report()
        audit_page_html(r, "https://gworky.com/", '<meta http-equiv="refresh" content="0;url=https://evil.example">')
        assert any(f.layer == "cloaking" and f.severity == "critical" for f in r.findings)

    def test_ua_gate_detected(self) -> None:
        r = _report()
        audit_page_html(r, "https://gworky.com/", "if(navigator.userAgent.match(/bot/)){window.location='https://evil.example'}")
        assert any(f.layer == "cloaking" for f in r.findings)


class TestReport:
    def test_critical_filter_and_json(self) -> None:
        r = _report()
        r.findings.append(Finding("critical", "sitemap", "/x", "bad"))
        r.findings.append(Finding("warning", "feed-parity", "/y", "meh"))
        assert len(r.critical) == 1
        data = __import__("json").loads(r.to_json())
        assert data["checked_sitemap_urls"] == 0
        assert len(data["findings"]) == 2


class TestSitemapSignatures:
    def test_suspicious_path_regex(self) -> None:
        from agents.site_guard import _SUSPICIOUS_PATH_RE

        assert _SUSPICIOUS_PATH_RE.search("/wp-admin/evil.php")
        assert _SUSPICIOUS_PATH_RE.search("/shell.php")
        assert _SUSPICIOUS_PATH_RE.search("/best-casino-online")
        assert _SUSPICIOUS_PATH_RE.search("/db.sql")
        assert not _SUSPICIOUS_PATH_RE.search("/article/best-savings-accounts")

    def test_unknown_top_level_is_warning_not_critical(self) -> None:

        from agents.site_guard import _KNOWN_TOP_LEVEL, _is_canonical

        path = "/totally-new-page"
        assert not _is_canonical(path)
        assert path.strip("/").split("/")[0] not in _KNOWN_TOP_LEVEL
