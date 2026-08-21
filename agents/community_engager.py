"""Community Answer Engine — T3.2 (Priority #1).

Discovers decision-seeking questions on Reddit and Hacker News that match
Groundwork keyword clusters, drafts value-first answers grounded in published
research, and queues every draft to Telegram for one-click human approval.

Compliance contract (non-negotiable):
- One official identity per platform. No multi-account operation.
- Hard rate cap: max ``RATE_LIMIT_PER_DAY`` approved answers per platform/day.
- Nothing is ever posted automatically in v1: approved drafts are delivered
  back to Telegram as copy-paste-ready text for manual posting.
- Brand mentions only when contextually relevant, always disclosed as the
  Groundwork research team.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agents.authority_injector import _load_env_local  # noqa: E402

_load_env_local()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_FOUNDER_CHAT_ID", "")
REDDIT_CLIENT_ID = os.environ.get("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET", "")
REDDIT_UA = "script:groundwork-community-engager:v1 (by /u/GroundworkResearch)"

STATE_PATH = _ROOT / "state" / "community_engager_state.json"
RATE_LIMIT_PER_DAY = 2
MAX_DRAFTS_PER_RUN = 5
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

KEYWORD_CLUSTERS: dict[str, list[str]] = {
    "money": [
        "best high yield savings", "is it worth refinancing", "how much life insurance",
        "debt snowball vs avalanche", "roth ira vs 401k", "best index funds",
    ],
    "body": [
        "is creatine worth it", "best way to lose weight", "how much protein per day",
        "is intermittent fasting worth", "best sleep tracker",
    ],
    "home": [
        "best solar installer", "is whole home generator worth it", "heat pump vs furnace",
        "how much does solar cost", "is home warranty worth it",
    ],
    "life": [
        "is an ev worth it", "best travel insurance", "how much should i have saved",
        "lease vs buy car", "best credit card for travel",
    ],
    "tech": [
        "best password manager", "is chatgpt plus worth it", "best vpn reddit",
        "smart home starter kit", "best budget laptop",
    ],
}

QUESTION_SIGNALS = re.compile(
    r"\b(best|worth it|worth|how much|should i|vs|versus|recommend|advice|help me choose|"
    r"anyone (used|tried|know))\b",
    re.IGNORECASE,
)

DRAFT_FALLBACK_CHAIN = ["groq/openai/gpt-oss-120b", "groq/openai/gpt-oss-20b"]

DRAFT_SYSTEM_PROMPT = """You are the Groundwork research team's community voice.
Write a native, value-first answer to a community question (Reddit/HN style).
Rules:
- Lead with the direct answer, then reasoning a newcomer can follow.
- Ground claims in evidence; cite specific numbers when known.
- Mention gworky.com ONLY if a published guide/calculator genuinely helps,
  at most once, phrased transparently ("we research this at Groundwork...").
- Never use marketing language, never overpromise, no emoji spam.
- Length: 120-220 words. Plain text with line breaks, no markdown headers.
Return JSON: {"answer": "...", "mentions_brand": true/false}"""


@dataclass
class ThreadCandidate:
    platform: str
    thread_id: str
    title: str
    url: str
    subreddit_or_tag: str = ""
    score: int = 0
    num_comments: int = 0
    created_utc: float = 0.0
    relevance: float = 0.0
    matched_cluster: str = ""
    pillar: str = ""


@dataclass
class AnswerDraft:
    draft_id: str
    platform: str
    thread_url: str
    thread_title: str
    pillar: str
    answer: str
    mentions_brand: bool
    drafted_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    status: str = "pending"


def load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {"seen_threads": {}, "daily_counts": {}, "drafts": {}}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def today_key() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def within_rate_limit(state: dict[str, Any], platform: str) -> bool:
    used = state.get("daily_counts", {}).get(f"{platform}:{today_key()}", 0)
    return used < RATE_LIMIT_PER_DAY


async def get_reddit_token(client: httpx.AsyncClient) -> str | None:
    """Read-only OAuth token via official client_credentials grant (free tier)."""
    if not (REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET):
        return None
    try:
        res = await client.post(
            "https://www.reddit.com/api/v1/access_token",
            data={"grant_type": "client_credentials"},
            auth=(REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET),
            headers={"User-Agent": REDDIT_UA},
        )
        if res.status_code != 200:
            return None
        return str(res.json().get("access_token")) or None
    except Exception:
        return None


_reddit_skip_warned = False


async def discover_reddit(client: httpx.AsyncClient, cluster: str, queries: list[str]) -> list[ThreadCandidate]:
    global _reddit_skip_warned
    candidates: list[ThreadCandidate] = []
    token = await get_reddit_token(client)
    if not token:
        if not _reddit_skip_warned:
            print("[community_engager] Reddit skipped: set REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET (OAuth script app)")
            _reddit_skip_warned = True
        return candidates
    for query in queries:
        try:
            res = await client.get(
                "https://oauth.reddit.com/search",
                params={"q": query, "sort": "new", "t": "week", "limit": 10},
                headers={"Authorization": f"Bearer {token}", "User-Agent": REDDIT_UA},
            )
            if res.status_code != 200:
                continue
            for child in res.json().get("data", {}).get("children", []):
                d = child.get("data", {})
                candidates.append(
                    ThreadCandidate(
                        platform="reddit",
                        thread_id=d.get("id", ""),
                        title=d.get("title", ""),
                        url=f"https://www.reddit.com{d.get('permalink', '')}",
                        subreddit_or_tag=d.get("subreddit", ""),
                        score=int(d.get("score", 0)),
                        num_comments=int(d.get("num_comments", 0)),
                        created_utc=float(d.get("created_utc", 0)),
                        matched_cluster=cluster,
                    )
                )
        except Exception:
            continue
    return candidates


async def discover_hn(client: httpx.AsyncClient, cluster: str, queries: list[str]) -> list[ThreadCandidate]:
    candidates: list[ThreadCandidate] = []
    for query in queries:
        try:
            res = await client.get(
                "https://hn.algolia.com/api/v1/search_by_date",
                params={"query": query, "tags": "(story,comment)", "hitsPerPage": 10},
            )
            if res.status_code != 200:
                continue
            for hit in res.json().get("hits", []):
                object_id = hit.get("objectID", "")
                story_id = hit.get("story_id") or object_id
                candidates.append(
                    ThreadCandidate(
                        platform="hn",
                        thread_id=object_id,
                        title=hit.get("title") or hit.get("story_title") or "",
                        url=f"https://news.ycombinator.com/item?id={story_id}",
                        subreddit_or_tag="hn",
                        score=int(hit.get("points") or 0),
                        num_comments=int(hit.get("num_comments") or 0),
                        created_utc=float(hit.get("created_at_i") or 0),
                        matched_cluster=cluster,
                    )
                )
        except Exception:
            continue
    return candidates


def _cluster_relevance(title: str, terms: list[str]) -> float:
    """Fraction-based phrase overlap using exact word tokens (substring-safe)."""
    title_words = set(re.findall(r"[a-z0-9]+", title.lower()))
    hits = 0.0
    for term in terms:
        term_words = [w for w in re.findall(r"[a-z0-9]+", term) if len(w) >= 3]
        if not term_words:
            continue
        matched = sum(1 for w in term_words if w in title_words)
        hits += matched / len(term_words)
    return min(hits, 5.0)


def score_candidate(c: ThreadCandidate) -> float:
    cluster_terms = KEYWORD_CLUSTERS.get(c.matched_cluster, [])
    term_hits = _cluster_relevance(c.title, cluster_terms)
    question_signal = 1.5 if QUESTION_SIGNALS.search(c.title) else 0.0
    engagement = min((c.num_comments + c.score) / 50.0, 2.0)
    age_days = max((time.time() - c.created_utc) / 86400, 0.01) if c.created_utc else 30.0
    freshness = max(0.0, 1.0 - age_days / 7)
    return round(term_hits * 1.0 + question_signal + engagement + freshness, 2)


async def draft_answer(question_title: str, pillar: str) -> dict[str, Any] | None:
    try:
        from agents.scribe import (
            DEFAULT_FALLBACK_CHAIN,
            call_llm_with_fallback,
            clean_json_response,
        )

        raw = await asyncio.to_thread(
            call_llm_with_fallback,
            f"Pillar: {pillar}\nCommunity question: {question_title}",
            DRAFT_FALLBACK_CHAIN + list(DEFAULT_FALLBACK_CHAIN),
            0.6,
            900,
            system_prompt=DRAFT_SYSTEM_PROMPT,
        )
        parsed = clean_json_response(raw)
        if isinstance(parsed, dict) and parsed.get("answer"):
            return {"answer": str(parsed["answer"]), "mentions_brand": bool(parsed.get("mentions_brand", False))}
        # Model ignored the JSON contract but returned usable prose — degrade gracefully.
        text = raw.strip()
        if len(text) >= 200:
            return {"answer": text, "mentions_brand": "gworky" in text.lower()}
        return None
    except Exception:
        return None


async def send_approval_card(draft: AnswerDraft) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    brand_note = "\n⚠️ <b>Contains brand mention</b> (disclosed)" if draft.mentions_brand else ""
    text = (
        f"💬 <b>[COMMUNITY ANSWER DRAFT — {draft.platform.upper()}]</b>\n\n"
        f"• <b>Thread:</b> {draft.thread_title}\n"
        f"• <b>URL:</b> {draft.thread_url}\n"
        f"• <b>Pillar:</b> {draft.pillar}{brand_note}\n\n"
        f"<b>Draft (copy-paste ready):</b>\n<i>{draft.answer[:3500]}</i>"
    )
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": {
            "inline_keyboard": [
                [
                    {"text": "✅ Approve (manual paste)", "callback_data": f"approve_answer:{draft.draft_id}"},
                    {"text": "❌ Dismiss", "callback_data": f"reject_answer:{draft.draft_id}"},
                ]
            ]
        },
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json=payload
            )
            return res.status_code == 200
    except Exception:
        return False


def build_clients() -> tuple[httpx.AsyncClient, httpx.AsyncClient]:
    """Return (reddit_client, general_client). OAuth endpoints allow datacenter egress."""
    base = {"timeout": 15.0, "follow_redirects": True, "headers": {"User-Agent": BROWSER_UA}}
    return httpx.AsyncClient(**base), httpx.AsyncClient(**base)


CB_PATTERN = re.compile(r"^(approve|reject)_answer:(.+)$")


async def process_pending_callbacks() -> int:
    """Drain Telegram approve/reject callback queries and update draft state.

    Returns the number of updates consumed. Safe to run before every discovery
    cycle so approval decisions made between runs are never lost.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return 0
    state = load_state()
    offset = int(state.get("tg_update_offset", 0))
    processed = 0
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.get(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
                params={
                    "allowed_updates": json.dumps(["callback_query"]),
                    "offset": offset,
                    "timeout": 0,
                },
            )
            if res.status_code != 200:
                return 0
            for update in res.json().get("result", []):
                state["tg_update_offset"] = int(update["update_id"]) + 1
                cb = update.get("callback_query") or {}
                match = CB_PATTERN.match(cb.get("data", ""))
                if not match or cb.get("data") == "noop":
                    continue
                action, draft_id = match.groups()
                draft = state.get("drafts", {}).get(draft_id)
                if not draft:
                    continue
                approved = action == "approve"
                draft["status"] = "approved" if approved else "rejected"
                if approved:
                    key = f"{draft['platform']}:{today_key()}"
                    counts = state.setdefault("daily_counts", {})
                    counts[key] = int(counts.get(key, 0)) + 1
                await client.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
                    json={"callback_query_id": cb.get("id", "")},
                )
                msg = cb.get("message") or {}
                chat_id, message_id = msg.get("chat", {}).get("id"), msg.get("message_id")
                if chat_id and message_id:
                    await client.post(
                        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageReplyMarkup",
                        json={
                            "chat_id": chat_id,
                            "message_id": message_id,
                            "reply_markup": {
                                "inline_keyboard": [
                                    [
                                        {
                                            "text": "✅ Approved — paste manually" if approved else "❌ Rejected",
                                            "callback_data": "noop",
                                        }
                                    ]
                                ]
                            },
                        },
                    )
                processed += 1
    except Exception:
        return processed
    save_state(state)
    return processed


async def run(max_drafts: int = MAX_DRAFTS_PER_RUN) -> list[AnswerDraft]:
    await process_pending_callbacks()
    state = load_state()
    seen: dict[str, Any] = state.setdefault("seen_threads", {})
    all_candidates: list[ThreadCandidate] = []

    reddit_client, hn_client = build_clients()
    async with reddit_client, hn_client:
        tasks = []
        for cluster, queries in KEYWORD_CLUSTERS.items():
            tasks.append(discover_reddit(reddit_client, cluster, queries))
            tasks.append(discover_hn(hn_client, cluster, queries))
        for coro in asyncio.as_completed(tasks):
            all_candidates.extend(await coro)

    fresh: list[ThreadCandidate] = []
    for c in all_candidates:
        if not c.thread_id or seen.get(c.thread_id):
            continue
        c.pillar = c.matched_cluster
        c.relevance = score_candidate(c)
        if QUESTION_SIGNALS.search(c.title):
            fresh.append(c)
    fresh.sort(key=lambda c: c.relevance, reverse=True)

    drafts: list[AnswerDraft] = []
    for c in fresh:
        if len(drafts) >= max_drafts:
            break
        if not within_rate_limit(state, c.platform):
            continue
        result = await draft_answer(c.title, c.pillar)
        if not result or not result.get("answer"):
            continue
        draft = AnswerDraft(
            draft_id=f"{c.platform}-{c.thread_id}-{int(time.time())}",
            platform=c.platform,
            thread_url=c.url,
            thread_title=c.title,
            pillar=c.pillar,
            answer=result["answer"],
            mentions_brand=bool(result.get("mentions_brand", False)),
        )
        drafts.append(draft)
        state.setdefault("drafts", {})[draft.draft_id] = asdict(draft)
        seen[c.thread_id] = {"url": c.url, "relevance": c.relevance, "at": today_key()}
        await send_approval_card(draft)

    save_state(state)
    return drafts


def main() -> None:
    parser = argparse.ArgumentParser(description="Groundwork Community Answer Engine (T3.2)")
    parser.add_argument("--max-drafts", type=int, default=MAX_DRAFTS_PER_RUN)
    parser.add_argument("--dry-run", action="store_true", help="Discovery+scoring only, no LLM/Telegram")
    args = parser.parse_args()

    if args.dry_run:

        async def dry() -> list[ThreadCandidate]:
            state = load_state()
            seen: dict[str, Any] = state.setdefault("seen_threads", {})
            out: list[ThreadCandidate] = []
            reddit_client, hn_client = build_clients()
            async with reddit_client, hn_client:
                tasks = []
                for cluster, queries in KEYWORD_CLUSTERS.items():
                    tasks.append(discover_reddit(reddit_client, cluster, queries))
                    tasks.append(discover_hn(hn_client, cluster, queries))
                for coro in asyncio.as_completed(tasks):
                    out.extend(await coro)
            scored = []
            for c in out:
                if c.thread_id and not seen.get(c.thread_id):
                    c.pillar = c.matched_cluster
                    c.relevance = score_candidate(c)
                    if QUESTION_SIGNALS.search(c.title):
                        scored.append(c)
            scored.sort(key=lambda x: x.relevance, reverse=True)
            return scored[:15]

        for c in asyncio.run(dry()):
            print(f"[{c.relevance:5.2f}] {c.platform:6} r/{c.subreddit_or_tag}: {c.title[:80]}")
        return

    drafts = asyncio.run(run(args.max_drafts))
    print(f"Queued {len(drafts)} approval card(s) to Telegram.")


if __name__ == "__main__":
    main()
