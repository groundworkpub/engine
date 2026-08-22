"""Groundwork X Growth Engine — compliance-aware browser autopilot.

Design contract (docs/X-GROWTH-SPEC.md v3.1):
  - Official X API write access is NOT available (free tier removed - verified live).
    Writes go through an authenticated browser session instead.
  - Stable identity: one persistent browser profile; optional single sticky proxy.
    NEVER rotate egress mid-campaign (rotation is a ban signal).
  - Human pacing: randomized typing, inter-tweet jitter, US/UK active-hour windows,
    hard daily caps with warmup ramp.
  - Quality gate: only own-article threads passing deterministic brand checks ship.
  - Fail-closed: missing cookies, DB errors, health-check failure => no write.
  - Audit: every attempt lands in Supabase `x_actions`; daily digest via Telegram.

CLI:
    python agents/x_engine.py --dry-run          # plan + render thread, no writes
    python agents/x_engine.py --live             # execute real posts (guarded)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger("x_engine")

REPO_ROOT = Path(__file__).resolve().parent.parent
ET_ZONE = ZoneInfo("America/New_York")
URL_WEIGHT = 23  # t.co counting for https links

BANNED_PHRASES = (
    "game-changer",
    "unlock your potential",
    "revolutionary",
    "you won't believe",
    "mind-blowing",
    "secret sauce",
    "100% guaranteed",
    "insane hack",
)

THREAD_PROMPT = """You are the voice of Groundwork ({site}), an evidence-based research publication \
for adults making high-stakes money, health, home, life and tech decisions.

Turn the article below into a {n_tweets}-post X thread.

Rules:
- Post 1 is a hook: one concrete claim or number from the article. No hashtags.
- Middle posts deliver the substance: specific data, comparisons, or steps.
- Last post ends with a one-sentence takeaway and this exact link appended on its own line: {link}
- Sentence case. Active voice. No emojis except at most one total. No hashtag spam.
- Each post max 260 characters excluding the link. Plain text only.
- Banned phrases: {banned}.

Return ONLY a JSON array of strings, one string per post.

ARTICLE TITLE: {title}
ARTICLE EXCERPT: {excerpt}
ARTICLE BODY (truncated):
{body}
"""


def adjusted_length(text: str) -> int:
    """X weighted length: every https URL counts as 23 chars."""
    urls = re.findall(r"https?://\S+", text)
    total = len(text)
    for u in urls:
        total -= len(u)
        total += URL_WEIGHT
    return total


def parse_active_hours(spec: str) -> list[tuple[int, int]]:
    """Parse '12-16,19-22' into [(12,16),(19,22)] in America/New_York time."""
    windows: list[tuple[int, int]] = []
    for part in spec.split(","):
        start_s, _, end_s = part.strip().partition("-")
        try:
            windows.append((int(start_s), int(end_s)))
        except ValueError:
            logger.warning("Bad active-hour window ignored: %r", part)
    return windows


def within_active_hours(now: datetime, windows: list[tuple[int, int]]) -> bool:
    et_hour = now.astimezone(ET_ZONE).hour
    return any(start <= et_hour < end for start, end in windows)


class XEngine:
    """Browser-session X autopilot with deterministic guardrails."""

    def __init__(self, now_fn: Callable[[], datetime] | None = None):
        self._load_env_local()
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.auth_token = os.getenv("X_AUTH_TOKEN", "")
        self.ct0 = os.getenv("X_CT0", "")
        self.proxy_url = os.getenv("X_PROXY_URL", "")
        self.profile_dir = Path(os.getenv("X_PROFILE_DIR", REPO_ROOT / "agents" / ".x_profile"))
        self.max_posts_day = int(os.getenv("X_MAX_POSTS_PER_DAY", "3"))
        self.active_hours = parse_active_hours(os.getenv("X_ACTIVE_HOURS_ET", "12-16,19-22"))
        self.llm_chain = [
            m.strip()
            for m in os.getenv(
                "X_LLM_CHAIN",
                "openrouter/google/gemma-4-26b-a4b-it:free,"
                "openrouter/z-ai/glm-5.2:free,"
                "openrouter/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
            ).split(",")
            if m.strip()
        ]
        self.digest_chat_id = os.getenv("TELEGRAM_DIGEST_CHAT_ID", "")
        self.site = os.getenv("NEXT_PUBLIC_SITE_URL", "https://gworky.com").rstrip("/")
        self.supabase_url = (
            os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL") or ""
        ).rstrip("/")
        self.supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY", "")

    # ── environment ──────────────────────────────────────────────────────────

    @staticmethod
    def _load_env_local() -> None:
        env_file = REPO_ROOT / ".env.local"
        if not env_file.exists():
            return
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())

    # ── guardrails ───────────────────────────────────────────────────────────

    def preflight(self) -> tuple[bool, str]:
        """Fail-closed configuration check before any session work."""
        if not self.auth_token or not self.ct0:
            return False, "Missing X_AUTH_TOKEN / X_CT0 cookies"
        if not self.supabase_url or not self.supabase_key:
            return False, "Missing Supabase credentials (audit ledger unavailable)"
        if not within_active_hours(self.now_fn(), self.active_hours):
            return False, f"Outside active hours {self.active_hours} ET"
        posted_today = self.count_posts_today()
        if posted_today >= self.max_posts_day:
            return False, f"Daily cap reached ({posted_today}/{self.max_posts_day})"
        return True, f"OK ({posted_today}/{self.max_posts_day} today)"

    def count_posts_today(self) -> int:
        since = self.now_fn().replace(hour=0, minute=0, second=0, microsecond=0)
        data = self._supabase_request(
            "GET",
            "x_actions?select=id&status=eq.executed&action_type=in.(THREAD,POST)&executed_at=gte."
            + urllib.parse.quote(since.isoformat(), safe=""),
        )
        return len(data or [])

    # ── supabase audit ledger ────────────────────────────────────────────────

    def _supabase_request(self, method: str, endpoint: str, body: dict[str, Any] | None = None) -> Any:
        req = urllib.request.Request(
            f"{self.supabase_url}/rest/v1/{endpoint}",
            data=json.dumps(body).encode() if body else None,
            method=method,
            headers={
                "apikey": self.supabase_key,
                "Authorization": f"Bearer {self.supabase_key}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
        return json.loads(raw) if raw else None

    def record_action(self, payload: dict[str, Any], status: str, result: dict[str, Any] | None = None, error: str | None = None) -> None:
        self._supabase_request(
            "POST",
            "x_actions",
            {
                "action_type": "THREAD",
                "payload": payload,
                "status": status,
                "result": result,
                "error": error,
                "executed_at": self.now_fn().isoformat() if status == "executed" else None,
            },
        )

    # ── content selection & generation ───────────────────────────────────────

    def fetch_candidate_article(self) -> dict[str, Any] | None:
        """Newest published article not yet posted to X (flagship first)."""
        rows = self._supabase_request(
            "GET",
            "articles?select=slug,title,excerpt,content,pillar,is_flagship,published_at"
            "&status=eq.published&order=published_at.desc&limit=40",
        )
        posted = self._recent_posted_slugs()
        for row in rows or []:
            if row["slug"] not in posted:
                return row
        return None

    def _recent_posted_slugs(self) -> set[str]:
        since = (self.now_fn() - timedelta(days=45)).isoformat()
        rows = self._supabase_request(
            "GET",
            f"x_actions?select=payload&status=in.(executed,pending)&created_at=gte.{urllib.parse.quote(since, safe='')}",
        )
        return {(r.get("payload") or {}).get("slug", "") for r in rows or []}

    def campaign_link(self, slug: str) -> str:
        return f"{self.site}/article/{urllib.parse.quote(slug)}?utm_source=x&utm_medium=organic&utm_campaign={slug}"

    def generate_thread(self, article: dict[str, Any]) -> list[str]:
        """LLM draft -> deterministic quality gate. Raises on gate failure."""
        link = self.campaign_link(article["slug"])
        prompt = THREAD_PROMPT.format(
            site=self.site,
            n_tweets=random.choice((3, 4)),
            link=link,
            banned=", ".join(BANNED_PHRASES),
            title=article["title"],
            excerpt=(article.get("excerpt") or "")[:300],
            body=(article.get("content") or "")[:4000],
        )
        draft = self._llm_json_array(prompt)
        ok, problems = self.quality_gate(draft, link)
        if not ok:
            raise ValueError("Quality gate rejected thread: " + "; ".join(problems))
        return draft

    def _llm_json_array(self, prompt: str) -> list[str]:
        from litellm import completion  # lazy import keeps offline tests light

        strict_prompt = prompt + "\n\nReturn ONLY the JSON array. No markdown fences, no commentary."
        last_err: Exception | None = None
        for attempt_no in range(2):
            prompt_used = prompt if attempt_no == 0 else strict_prompt
            for model in self.llm_chain:
                try:
                    resp = completion(
                        model=model,
                        messages=[{"role": "user", "content": prompt_used}],
                        temperature=0.7,
                        max_tokens=900,
                    )
                    raw = resp.choices[0].message.content or ""
                    return self._parse_json_array(raw)
                except ValueError as exc:
                    logger.warning("Unparseable LLM output from %s: %s", model, str(exc)[:120])
                    last_err = exc
                except Exception as exc:  # noqa: BLE001 - try next provider in chain
                    logger.warning("LLM %s failed: %s", model, str(exc)[:120])
                    last_err = exc
        assert last_err is not None
        raise last_err

    @staticmethod
    def _parse_json_array(raw: str) -> list[str]:
        """Extract a JSON string array from noisy LLM output."""
        cleaned = raw.strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned).strip()
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
        candidates = [cleaned]
        match = re.search(r"\[[\s\S]*\]", cleaned)
        if match:
            candidates.append(match.group(0))
        for candidate in candidates:
            variants = [candidate]
            sanitized = (
                candidate.replace("\u201c", '"')
                .replace("\u201d", '"')
                .replace("\u2018", "'")
                .replace("\u2019", "'")
                .replace("\r", "")
            )
            sanitized = re.sub(r",\s*([\]\}])", r"\1", sanitized)
            variants.append(sanitized)
            for variant in variants:
                try:
                    arr = json.loads(variant)
                except json.JSONDecodeError:
                    continue
                if isinstance(arr, list) and all(isinstance(t, str) for t in arr):
                    return [t.strip() for t in arr if t.strip()]
        raise ValueError(f"no parsable JSON array in: {raw[:150]!r}")

    @staticmethod
    def quality_gate(tweets: list[str], expected_link: str) -> tuple[bool, list[str]]:
        problems: list[str] = []
        if not 3 <= len(tweets) <= 5:
            problems.append(f"thread length {len(tweets)} outside 3-5")
        joined = " ".join(tweets).lower()
        for phrase in BANNED_PHRASES:
            if phrase in joined:
                problems.append(f"banned phrase: {phrase}")
        if expected_link not in tweets[-1]:
            problems.append("closing post missing campaign link")
        emoji_count = len(re.findall(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", joined))
        if emoji_count > 2:
            problems.append(f"too many emojis ({emoji_count})")
        hashtag_count = joined.count("#")
        if hashtag_count > 1:
            problems.append(f"too many hashtags ({hashtag_count})")
        non_ascii = sum(1 for ch in joined if ord(ch) > 127)
        if non_ascii > len(joined) * 0.05:
            problems.append("non-English/emoji-heavy content suspected")
        numeric_posts = sum(1 for t in tweets if re.search(r"\d", t))
        if numeric_posts < max(1, len(tweets) // 2):
            problems.append("thread lacks concrete numbers/data")
        for i, t in enumerate(tweets, 1):
            if adjusted_length(t) > 275:
                problems.append(f"post {i} too long ({adjusted_length(t)} weighted chars)")
        return (not problems), problems

    # ── browser session ──────────────────────────────────────────────────────

    def _resolve_proxy(self) -> tuple[str, str | None, str | None] | None:
        """Explicit X_PROXY_URL wins; else sticky DataImpulse residential (same IP on any machine).

        Returns (server, username, password); Playwright rejects credentials
        embedded in the server URL, so they are split out here."""
        target = self.proxy_url
        if not target:
            login = os.getenv("DATAIMPULSE_LOGIN")
            pwd = os.getenv("DATAIMPULSE_PASSWORD")
            if not login or not pwd:
                logger.warning("No residential proxy configured; using direct egress")
                return None
            session_id = os.getenv("X_STICKY_SESSION_ID", "gworky-x-01")
            host = os.getenv("DATAIMPULSE_HOST", "gw.dataimpulse.com")
            port = os.getenv("DATAIMPULSE_PORT", "823")
            target = f"http://{login}__cr.us__sessid.{session_id}:{pwd}@{host}:{port}"
        parsed = urllib.parse.urlparse(target)
        return (
            f"{parsed.scheme or 'http'}://{parsed.hostname}:{parsed.port}",
            parsed.username,
            parsed.password,
        )

    def _open_session(self):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        launch_kwargs: dict[str, Any] = {"headless": True}
        proxy_resolved = self._resolve_proxy()
        if proxy_resolved:
            server, p_user, p_pass = proxy_resolved
            proxy_cfg: dict[str, Any] = {"server": server}
            if p_user:
                proxy_cfg["username"] = p_user
                proxy_cfg["password"] = p_pass or ""
            launch_kwargs["proxy"] = proxy_cfg
            logger.info(
                "Egress: residential proxy %s (user=%s…)",
                server,
                (p_user or "")[:12],
            )
        self._browser = self._pw.chromium.launch(**launch_kwargs)
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        context = self._browser.new_context(
            viewport={"width": 1366, "height": 850},
            locale="en-US",
            timezone_id="America/New_York",
            storage_state=None,
        )
        context.add_cookies(
            [
                {"name": "auth_token", "value": self.auth_token, "domain": ".x.com", "path": "/", "httpOnly": True, "secure": True},
                {"name": "ct0", "value": self.ct0, "domain": ".x.com", "path": "/", "secure": True},
            ]
        )

        def block_heavy(route):
            if route.request.resource_type in ("image", "media", "font"):
                route.abort()
            else:
                route.continue_()

        context.route("**/*", block_heavy)
        return context

    def health_check(self, page) -> bool:
        for _ in range(3):
            page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=60000)
            try:
                page.wait_for_selector('[data-testid="primaryColumn"]', timeout=25000)
            except Exception:  # noqa: BLE001 - selector timeout -> retry
                page.wait_for_timeout(3000)
                continue
            url = page.url
            if any(marker in url for marker in ("/login", "/challenge", "/suspended", "/account/access")):
                logger.error("Session unhealthy, redirected to %s", url)
                return False
            return True
        logger.error("Session unhealthy: feed never mounted")
        return False

    def post_thread(self, tweets: list[str]) -> dict[str, Any]:
        """Publish a thread atomically through the compose modal."""
        context = self._open_session()
        page = context.new_page()
        try:
            if not self.health_check(page):
                raise RuntimeError("Health check failed - aborting write (fail-closed)")
            page.goto("https://x.com/compose/post", wait_until="domcontentloaded", timeout=45000)
            box = page.locator('[data-testid="tweetTextarea_0"]')
            box.wait_for(state="visible", timeout=20000)

            for i, text in enumerate(tweets):
                if i > 0:
                    page.locator('[data-testid="addButton"]').click()
                    page.locator(f'[data-testid="tweetTextarea_{i}"]').wait_for(state="visible", timeout=10000)
                    page.wait_for_timeout(random.randint(600, 1600))
                editor = page.locator(f'[data-testid="tweetTextarea_{i}"]')
                editor.click()
                self._human_type(page, text)

            page.wait_for_timeout(random.randint(1200, 2500))
            page.locator('[data-testid="tweetButton"]').click()
            page.wait_for_selector('[data-testid="toast"]', timeout=30000)
            page.wait_for_timeout(2500)
            return {"posted": True, "posts": len(tweets)}
        finally:
            context.close()
            self._browser.close()
            self._pw.stop()

    @staticmethod
    def _human_type(page, text: str) -> None:
        """Type in word chunks with jittered delays - avoids paste/instant-fill signals."""
        for chunk in text.split(" "):
            page.keyboard.type(chunk + " ", delay=random.uniform(18, 55))
            if random.random() < 0.08:
                page.wait_for_timeout(random.randint(250, 700))

    # ── orchestration ────────────────────────────────────────────────────────

    def run_cycle(self, live: bool = False) -> dict[str, Any]:
        report: dict[str, Any] = {"mode": "live" if live else "dry-run"}
        ok, reason = self.preflight()
        if not ok:
            report["skipped"] = reason
            self.notify(f"🛑 X engine skipped: {reason}")
            return report

        article = self.fetch_candidate_article()
        if not article:
            report["skipped"] = "no unposted candidate article"
            return report

        try:
            tweets = self.generate_thread(article)
        except Exception as exc:  # noqa: BLE001 - fail-closed, keep audit trail
            self.record_action({"slug": article["slug"]}, "failed", error=str(exc)[:400])
            report["error"] = str(exc)
            self.notify(f"⚠️ Thread generation failed for {article['slug']}: {exc}")
            return report

        report["slug"] = article["slug"]
        report["thread"] = tweets
        if not live:
            self.record_action({"slug": article["slug"], "thread": tweets}, "pending")
            return report

        try:
            result = self.post_thread(tweets)
            self.record_action({"slug": article["slug"], "thread": tweets}, "executed", result)
            report["result"] = result
            self.notify(f"✅ Posted thread for {article['slug']} ({len(tweets)} posts)")
        except Exception as exc:  # noqa: BLE001
            self.record_action({"slug": article["slug"], "thread": tweets}, "failed", error=str(exc)[:400])
            report["error"] = str(exc)
            self.notify(f"❌ Post failed for {article['slug']}: {str(exc)[:200]}")
        return report

    def notify(self, message: str) -> None:
        if not self.digest_chat_id:
            return
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not token:
            return
        try:
            urllib.request.urlopen(
                urllib.request.Request(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    data=json.dumps({"chat_id": self.digest_chat_id, "text": message[:500]}).encode(),
                    headers={"Content-Type": "application/json"},
                ),
                timeout=15,
            )
        except Exception as exc:  # noqa: BLE001 - digest must never break the cycle
            logger.warning("Telegram digest failed: %s", exc)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Groundwork X growth engine")
    parser.add_argument("--live", action="store_true", help="actually publish (default: dry-run)")
    args = parser.parse_args()

    engine = XEngine()
    report = engine.run_cycle(live=args.live)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
