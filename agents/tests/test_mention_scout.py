"""Tests for agents/mention_scout.py (T3.1 unlinked mention discovery)."""

import base64

from agents.mention_scout import (
    Finding,
    extract_bing_result_urls,
    extract_ddg_result_urls,
    find_unlinked,
)


def _b64(url: str) -> str:
    return base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")


class TestFindUnlinked:
    def test_mention_without_link_is_unlinked(self):
        html = "<p>I read a great guide on gworky about solar savings.</p>"
        assert find_unlinked(html) is True

    def test_mention_with_link_is_linked(self):
        html = '<p>See <a href="https://gworky.com/article/x">gworky</a> for details.</p>'
        assert find_unlinked(html) is False

    def test_no_mention_is_not_finding(self):
        html = "<p>Totally unrelated content about gardening.</p>"
        assert find_unlinked(html) is False

    def test_link_case_insensitive(self):
        html = '<a HREF="HTTPS://GWORKY.COM/home">x</a>'
        assert find_unlinked(html) is False


class TestExtractBingUrls:
    def test_decodes_ck_a_redirect(self):
        target = "https://example.com/post/mentioning-gworky"
        encoded = _b64(target)
        html = f'<a href="https://www.bing.com/ck/a?!&amp;&amp;p=abc&amp;u=a1{encoded}&amp;ntb=1">r</a>'
        assert extract_bing_result_urls(html) == [target]

    def test_skips_internal_and_brand_urls(self):
        internal = "https://www.bing.com/images/search?q=x"
        brand = "https://gworky.com/article/y"
        html = (
            f'<a href="https://www.bing.com/ck/a?p=1&amp;u=a1{_b64(internal)}">i</a>'
            f'<a href="https://www.bing.com/ck/a?p=2&amp;u=a1{_b64(brand)}">b</a>'
        )
        assert extract_bing_result_urls(html) == []

    def test_dedupes_repeated_results(self):
        target = "https://example.org/page"
        html = (
            f'<a href="https://www.bing.com/ck/a?u=a1{_b64(target)}">a</a>'
            f'<a href="https://www.bing.com/ck/a?u=a1{_b64(target)}">b</a>'
        )
        assert extract_bing_result_urls(html) == [target]


class TestExtractDdgUrls:
    def test_decodes_uddg_param(self):
        target = "https://example.net/foo"
        html = f'<a href="//duckduckgo.com/l/?uddg={target.replace("/", "%2F")}&rut=abc">r</a>'
        assert extract_ddg_result_urls(html) == [target]

    def test_skips_own_domain(self):
        html = '<a href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fgworky.com%2Fx">r</a>'
        assert extract_ddg_result_urls(html) == []


class TestFinding:
    def test_to_dict_truncates_snippet(self):
        f = Finding(source="hn", url="https://u", title="t", snippet="s" * 500)
        d = f.to_dict()
        assert len(d["snippet"]) == 200
