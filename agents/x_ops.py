"""Groundwork X growth ops: replies, mentions, discovery & mutual-friendly follows.

Scope split (docs/X-GROWTH-SPEC.md):
  - Thread posting = delegated to scheduled Buffer sync. This module does NOT post threads.
  - Local focus = replies & mentions for attention/impressions + discovering and
    following mid-tier verified individuals (not companies, not mega accounts).

All writes go through the same guardrails as x_engine.XEngine: fail-closed preflight,
active-hour windows, daily caps, human pacing, audit ledger (`x_actions`).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import random
import re
import sys
import urllib.parse
from datetime import timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from x_engine import BANNED_PHRASES, REPO_ROOT, XEngine  # noqa: E402

logger = logging.getLogger("x_ops")

FOLLOW_MIN_FOLLOWERS = 1_000
FOLLOW_MAX_FOLLOWERS = 150_000
REPLY_MAX_CHARS = 240
DEFAULT_QUERIES = (
    "personal finance writer",
    "financial planner",
    "evidence based fitness coach",
    "home improvement expert",
    "travel rewards points",
    "smart home reviewer",
    "career negotiation coach",
    "insurance explained",
)


def parse_count(text: str) -> int:
    """'12.3K Followers' -> 12300; '847 Followers' -> 847; '1.2M' -> 1200000."""
    token = text.strip().split()[0].replace(",", "") if text.strip() else ""
    m = re.match(r"^([\d.]+)([KM]?)$", token, re.IGNORECASE)
    if not m:
        return 0
    mult = {"K": 1_000, "M": 1_000_000}.get(m.group(2).upper(), 1)
    return int(float(m.group(1)) * mult)


def candidate_verdict(profile: dict[str, Any]) -> tuple[bool, str]:
    """Mutual-realistic filter: verified individual, mid-tier, active, follow-back culture."""
    if profile.get("is_org"):
        return False, "org badge (company)"
    followers = int(profile.get("followers", 0))
    if followers < FOLLOW_MIN_FOLLOWERS:
        return False, f"too small ({followers})"
    if followers > FOLLOW_MAX_FOLLOWERS:
        return False, f"mega account ({followers})"
    following = int(profile.get("following", 0))
    if followers / max(following, 1) > 30:
        return False, "ratio suggests no follow-back culture"
    if profile.get("last_active_days") is not None and profile["last_active_days"] > 14:
        return False, f"inactive {profile['last_active_days']}d"
    return True, "ok"


REPLY_PROMPT = """You reply as Groundwork (@gworkycom), an evidence-based research publication \
for money, health, home, life and tech decisions.

Write ONE reply to the post below.

Rules:
- Add value by sharpening or extending ONE point from the post.
- Use ONLY facts and numbers already present in the post. NEVER invent or estimate statistics.
- Max {max_chars} characters. Plain text only: no links, no hashtags, at most one emoji.
- No flattery filler ("Great post", "Love this"). Never start with "Great".
- Sentence case, active voice.

POST BY @{author}:
{post}

Return only the reply text."""


class XOps(XEngine):
    """Read/reply/follow operations layered on the guarded browser session."""

    # ── ledger helpers ───────────────────────────────────────────────────────

    def count_actions_today(self, action_types: tuple[str, ...]) -> int:
        since = self.now_fn().replace(hour=0, minute=0, second=0, microsecond=0)
        types = ",".join(action_types)
        data = self._supabase_request(
            "GET",
            "x_actions?select=id&action_type=in.(" + types + ")"
            "&status=eq.executed&executed_at=gte." + urllib.parse.quote(since.isoformat(), safe=""),
        )
        return len(data or [])

    def recent_handles(self, days: int = 2) -> set[str]:
        since = (self.now_fn() - timedelta(days=days)).isoformat()
        rows = self._supabase_request(
            "GET",
            "x_actions?select=payload&action_type=in.(REPLY,FOLLOW)&created_at=gte."
            + urllib.parse.quote(since, safe=""),
        )
        return {(r.get("payload") or {}).get("handle", "") for r in rows or []}

    def followed_handles(self) -> set[str]:
        rows = self._supabase_request(
            "GET", "x_actions?select=payload&action_type=eq.FOLLOW&status=in.(executed,pending)"
        )
        return {(r.get("payload") or {}).get("handle", "") for r in rows or []}

    # ── read layer ───────────────────────────────────────────────────────────

    def scan_feed(self, url: str, max_items: int = 20) -> list[dict[str, str]]:
        context = self._open_session()
        page = context.new_page()
        try:
            if not self.health_check(page):
                raise RuntimeError("Health check failed")
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            with contextlib.suppress(Exception):
                page.wait_for_selector("article", timeout=15000)
            page.wait_for_timeout(1200)
            items: list[dict[str, str]] = []
            seen: set[str] = set()
            for art in page.locator("article").all():
                try:
                    times = art.locator("time")
                    if not times.count():
                        continue
                    href = (
                        times.first.locator("xpath=ancestor::a[1]")
                        .first.get_attribute("href")
                        or ""
                    )
                    if "/status/" not in href:
                        continue
                    path = href.split("?")[0]
                    if path in seen:
                        continue
                    seen.add(path)
                    handle = path.split("/")[1].lower()
                    text_el = art.locator('[data-testid="tweetText"]')
                    body = text_el.first.inner_text(timeout=2000) if text_el.count() else ""
                    items.append(
                        {"handle": handle, "url": "https://x.com" + path, "text": body.strip()}
                    )
                except Exception:  # noqa: BLE001 - skip malformed card
                    continue
                if len(items) >= max_items:
                    break
            return items
        finally:
            context.close()
            self._browser.close()
            self._pw.stop()

    def scan_replies(self, post_url: str, max_items: int = 15) -> list[dict[str, Any]]:
        """Harvest repliers of one post with their badge type — mutual hunting ground."""
        context = self._open_session()
        page = context.new_page()
        try:
            page.goto(post_url, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_selector("article", timeout=15000)
            except Exception:  # noqa: BLE001
                return []
            page.wait_for_timeout(1500)
            out: list[dict[str, Any]] = []
            seen: set[str] = set()
            for art in page.locator("article").all():
                try:
                    times = art.locator("time")
                    if not times.count():
                        continue
                    href = (
                        times.first.locator("xpath=ancestor::a[1]")
                        .first.get_attribute("href")
                        or ""
                    )
                    if "/status/" not in href:
                        continue
                    handle = href.split("?")[0].split("/")[1].lower()
                    if handle in seen:
                        continue
                    seen.add(handle)
                    labels = [
                        svg.get_attribute("aria-label") or ""
                        for svg in art.locator("svg[aria-label]").all()
                    ]
                    is_org = any("organization" in lb.lower() or "gold" in lb.lower() for lb in labels)
                    verified = any("verified" in lb.lower() for lb in labels)
                    out.append({"handle": handle, "verified": verified, "is_org": is_org})
                except Exception:  # noqa: BLE001
                    continue
                if len(out) >= max_items + 1:
                    break
            return out[1:]  # first article is the parent post itself
        finally:
            context.close()
            self._browser.close()
            self._pw.stop()

    def search_people(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """People search -> blue-badge individuals (gold 'Verified organization' skipped upstream)."""
        url = "https://x.com/search?q=" + urllib.parse.quote(query) + "&f=user"
        context = self._open_session()
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_selector('[data-testid="cellInnerDiv"]', timeout=15000)
            except Exception:  # noqa: BLE001
                return []
            page.wait_for_timeout(1200)
            out: list[dict[str, Any]] = []
            seen: set[str] = set()
            for cell in page.locator('[data-testid="cellInnerDiv"]').all():
                try:
                    link = cell.locator('a[href^="/"]:not([href*="/status/"])').first
                    href = link.get_attribute("href") or ""
                    handle = href.strip("/").split("?")[0].lower()
                    if not handle or "/" in handle or handle in seen:
                        continue
                    labels = [
                        svg.get_attribute("aria-label") or ""
                        for svg in cell.locator("svg[aria-label]").all()
                    ]
                    is_org = any("organization" in lb.lower() or "gold" in lb.lower() for lb in labels)
                    verified = any("verified" in lb.lower() for lb in labels)
                    texts = [t.strip() for t in cell.locator("span").all_inner_texts() if t.strip()]
                    bio_parts = [t for t in texts if len(t) > 25][:2]
                    seen.add(handle)
                    out.append(
                        {
                            "handle": handle,
                            "is_org": is_org,
                            "verified": verified,
                            "bio": " ".join(bio_parts)[:160],
                            "query": query,
                        }
                    )
                except Exception:  # noqa: BLE001
                    continue
                if len(out) >= limit:
                    break
            return out
        finally:
            context.close()
            self._browser.close()
            self._pw.stop()

    def inspect_profile(self, handle: str) -> dict[str, Any]:
        context = self._open_session()
        page = context.new_page()
        try:
            page.goto(f"https://x.com/{handle}", wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_selector('[data-testid="UserName"]', timeout=15000)
            except Exception:  # noqa: BLE001
                return {
                    "handle": handle.lower(), "exists": False, "is_org": False,
                    "followers": 0, "following": 0, "last_active_days": None,
                    "follow_state": "",
                }
            labels = [
                svg.get_attribute("aria-label") or ""
                for svg in page.locator('header svg[aria-label]').all()
            ]
            info: dict[str, Any] = {
                "handle": handle.lower(),
                "exists": True,
                "is_org": any("organization" in lb.lower() or "gold" in lb.lower() for lb in labels),
                "followers": 0,
                "following": 0,
                "last_active_days": None,
                "follow_state": "",
            }
            try:
                f_txt = page.locator('a[href$="/verified_followers"]').first.inner_text(timeout=5000)
                info["followers"] = parse_count(f_txt)
            except Exception:  # noqa: BLE001
                pass
            try:
                g_txt = page.locator('a[href$="/following"]').first.inner_text(timeout=5000)
                info["following"] = parse_count(g_txt)
            except Exception:  # noqa: BLE001
                pass
            try:
                btn = page.locator('[data-testid="placementTracking"] [data-testid$="followButton"], [data-testid$="followButton"]').last
                info["follow_state"] = btn.inner_text(timeout=4000).strip()
            except Exception:  # noqa: BLE001
                pass
            try:
                dt = page.locator("article time").first.get_attribute("datetime", timeout=6000)
                if dt:
                    posted = datetime_from_iso(dt)
                    info["last_active_days"] = (self.now_fn() - posted).days
            except Exception:  # noqa: BLE001
                pass
            return info
        finally:
            context.close()
            self._browser.close()
            self._pw.stop()

    # ── write layer ──────────────────────────────────────────────────────────

    def follow_account(self, handle: str) -> dict[str, Any]:
        context = self._open_session()
        page = context.new_page()
        try:
            if not self.health_check(page):
                raise RuntimeError("Health check failed")
            page.goto(f"https://x.com/{handle}", wait_until="domcontentloaded", timeout=45000)
            btn = page.locator('[data-testid$="followButton"]').last
            btn.wait_for(state="visible", timeout=15000)
            label = btn.inner_text().strip().lower()
            if "follow" not in label or "following" in label:
                return {"handle": handle, "result": "already_following"}
            page.wait_for_timeout(random.randint(900, 2200))
            btn.click()
            confirm = page.locator('[data-testid="confirmationSheetConfirm"]')
            if confirm.count():
                confirm.click()
            page.wait_for_timeout(random.randint(1500, 3000))
            after = page.locator('[data-testid$="unfollowButton"]').count()
            if not after:
                raise RuntimeError(f"Follow click did not register for @{handle}")
            return {"handle": handle, "result": "followed"}
        finally:
            context.close()
            self._browser.close()
            self._pw.stop()

    @staticmethod
    def reply_gate(text: str) -> tuple[bool, list[str]]:
        problems: list[str] = []
        if len(text) > REPLY_MAX_CHARS:
            problems.append(f"too long ({len(text)})")
        low = text.lower()
        for phrase in BANNED_PHRASES:
            if phrase in low:
                problems.append(f"banned phrase: {phrase}")
        if low.startswith(("great", "love ", "amazing")):
            problems.append("flattery opener")
        if "#" in text:
            problems.append("hashtag present")
        emoji_count = len(re.findall(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", text))
        if emoji_count > 1:
            problems.append(f"emojis ({emoji_count})")
        if "http" in text.lower():
            problems.append("link present")
        if not re.search(r"\d", text):
            problems.append("no concrete number")
        non_ascii = sum(1 for ch in text if ord(ch) > 127)
        if non_ascii > len(text) * 0.05:
            problems.append("non-English suspected")
        return (not problems), problems

    def generate_reply(self, author: str, post: str) -> str:
        prompt = REPLY_PROMPT.format(max_chars=REPLY_MAX_CHARS - 20, author=author, post=post[:600])
        raw = self._llm_text(prompt)
        ok, problems = self.reply_gate(raw)
        if not ok:
            raise ValueError("Reply gate rejected: " + "; ".join(problems))
        return raw

    def _llm_text(self, prompt: str) -> str:
        from litellm import completion

        last_err: Exception | None = None
        for model in self.llm_chain:
            try:
                resp = completion(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.8,
                    max_tokens=200,
                )
                content = (resp.choices[0].message.content or "").strip()
                content = re.sub(r"^```.*?\n|```$", "", content).strip().strip('"')
                if content:
                    return content
            except Exception as exc:  # noqa: BLE001 - next provider
                last_err = exc
        assert last_err is not None
        raise last_err

    def reply_to_post(self, url: str, text: str) -> dict[str, Any]:
        context = self._open_session()
        page = context.new_page()
        try:
            if not self.health_check(page):
                raise RuntimeError("Health check failed")
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            editor = page.locator('[data-testid="tweetTextarea_0"]').last
            editor.wait_for(state="visible", timeout=20000)
            page.wait_for_timeout(random.randint(800, 1800))
            editor.click()
            self._human_type(page, text)
            page.locator('[data-testid="tweetButtonInline"]').last.click()
            page.wait_for_selector('[data-testid="toast"]', timeout=30000)
            return {"replied": True}
        finally:
            context.close()
            self._browser.close()
            self._pw.stop()

    # ── cycles ───────────────────────────────────────────────────────────────

    def run_reply_cycle(self, live: bool = False, max_replies: int | None = None) -> dict[str, Any]:
        cap = max_replies or int(os.getenv("X_MAX_REPLIES_PER_DAY", "4"))
        done_today = self.count_actions_today(("REPLY",))
        report: dict[str, Any] = {"mode": "live" if live else "dry-run", "done_today": done_today}
        if done_today >= cap:
            report["skipped"] = f"daily reply cap reached ({done_today}/{cap})"
            return report
        skip = self.recent_handles() | {"gworkycom"}
        candidates = self.scan_feed("https://x.com/notifications/mentions", max_items=8)
        candidates += self.scan_feed("https://x.com/home", max_items=15)
        picked: list[dict[str, str]] = []
        for item in candidates:
            h = item["handle"]
            if h in skip or not item["text"]:
                continue
            skip.add(h)
            picked.append(item)
            if len(picked) >= cap - done_today:
                break
        results = []
        for item in picked:
            entry: dict[str, Any] = {"url": item["url"], "author": item["handle"]}
            try:
                reply = self.generate_reply(item["handle"], item["text"])
                entry["reply"] = reply
                if live:
                    res = self.reply_to_post(item["url"], reply)
                    entry.update(res)
                    self.record_action(
                        {"handle": item["handle"], "url": item["url"], "text": reply},
                        "executed",
                        result=res,
                    )
                else:
                    self.record_action(
                        {"handle": item["handle"], "url": item["url"], "text": reply}, "pending"
                    )
            except Exception as exc:  # noqa: BLE001 - keep cycling
                entry["error"] = str(exc)[:200]
                self.record_action({"handle": item["handle"], "url": item["url"]}, "failed", error=str(exc)[:400])
            results.append(entry)
        report["replies"] = results
        return report

    def run_discovery_cycle(
        self,
        live: bool = False,
        queries: list[str] | None = None,
        max_follows: int | None = None,
    ) -> dict[str, Any]:
        qs = queries or [q.strip() for q in os.getenv("X_DISCOVERY_QUERIES", "").split(",") if q.strip()] or list(DEFAULT_QUERIES)
        cap = max_follows or int(os.getenv("X_MAX_FOLLOWS_PER_DAY", "5"))
        done_today = self.count_actions_today(("FOLLOW",))
        already = self.followed_handles()
        report: dict[str, Any] = {"mode": "live" if live else "dry-run", "done_today": done_today, "candidates": []}
        shortlist: list[dict[str, Any]] = []
        for q in qs:
            for person in self.search_people(q, limit=10):
                if person["handle"] in already or not person["verified"] or person["is_org"]:
                    continue
                shortlist.append(person)
        if True:  # always mine repliers of big timeline posts alongside keyword search
            for post in self.scan_feed("https://x.com/home", max_items=5)[:3]:
                for replier in self.scan_replies(post["url"], max_items=12):
                    if (
                        replier["handle"] in already
                        or not replier["verified"]
                        or replier["is_org"]
                    ):
                        continue
                    shortlist.append({**replier, "query": f"replies:{post['handle']}"})
        random.shuffle(shortlist)
        verdicts: list[dict[str, Any]] = []
        inspect_budget = int(os.getenv('X_DISCOVERY_INSPECT', '20'))
        for person in shortlist[:inspect_budget]:
            prof = self.inspect_profile(person["handle"])
            prof["query"] = person["query"]
            ok, reason = candidate_verdict(prof)
            verdicts.append({**prof, "ok": ok, "reason": reason})
        report["verdicts"] = verdicts
        eligible = sorted([v for v in verdicts if v["ok"]], key=lambda v: v["followers"])
        budget = cap - done_today
        followed: list[dict[str, Any]] = []
        for cand in eligible[:budget]:
            if not live:
                followed.append({"handle": cand["handle"], "would_follow": True})
                continue
            try:
                res = self.follow_account(cand["handle"])
                self.record_action({"handle": cand["handle"]}, "executed", result=res)
                followed.append({**cand, **res})
            except Exception as exc:  # noqa: BLE001
                self.record_action({"handle": cand["handle"]}, "failed", error=str(exc)[:400])
                followed.append({"handle": cand["handle"], "error": str(exc)[:150]})
        report["followed"] = followed
        return report


def datetime_from_iso(value: str):
    from datetime import datetime

    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    out_file = REPO_ROOT / ".x_last_report.json"
    out_file.unlink(missing_ok=True)
    parser = argparse.ArgumentParser(description="Groundwork X growth ops (reply/mention/discover/follow)")
    parser.add_argument("--scan-timeline", action="store_true", help="print timeline posts and exit")
    parser.add_argument("--discover", action="store_true", help="run discovery cycle (dry unless --live)")
    parser.add_argument("--follow", metavar="HANDLE", help="follow one handle now")
    parser.add_argument("--replies", action="store_true", help="run reply cycle (dry unless --live)")
    parser.add_argument("--live", action="store_true", help="execute writes instead of dry-run")
    parser.add_argument("--query", action="append", help="extra discovery query (repeatable)")
    args = parser.parse_args()

    ops = XOps()
    if args.scan_timeline:
        print(json.dumps(ops.scan_feed("https://x.com/home", max_items=15), indent=2, ensure_ascii=False))
        return 0
    if args.follow:
        print(json.dumps(ops.follow_account(args.follow.lstrip("@")), indent=2))
        ops.record_action({"handle": args.follow.lstrip("@")}, "executed", result={"via": "cli"})
        return 0
    if args.discover:
        rep = ops.run_discovery_cycle(live=args.live, queries=args.query)
        out_file.write_text(json.dumps(rep, indent=2, ensure_ascii=False))
        print(json.dumps(rep, indent=2, ensure_ascii=False))
        return 0
    if args.replies:
        rep = ops.run_reply_cycle(live=args.live)
        out_file.write_text(json.dumps(rep, indent=2, ensure_ascii=False))
        print(json.dumps(rep, indent=2, ensure_ascii=False))
        return 0
    print(json.dumps({
        "replies_today": ops.count_actions_today(("REPLY",)),
        "follows_today": ops.count_actions_today(("FOLLOW",)),
        "threads_today": ops.count_actions_today(("THREAD",)),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
