import asyncio
import logging
import os
import re
import sys
from datetime import UTC, datetime
from typing import Any, cast

import yaml
from supabase import create_client

from critic import run_critic
from scouter import run_scouter
from scribe import run_scribe

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("pipeline")


class TokenBudgetGuard:
    """Enforces strict token and budget limits ($0 constraint) per pipeline run."""

    def __init__(self, max_tokens: int = 500_000, max_cost_usd: float = 0.0) -> None:
        self.max_tokens = max_tokens
        self.max_cost_usd = max_cost_usd
        self.total_tokens_used = 0
        self.estimated_cost_usd = 0.0

    def record_usage(self, tokens: int, cost: float = 0.0) -> None:
        self.total_tokens_used += tokens
        self.estimated_cost_usd += cost
        if self.total_tokens_used > self.max_tokens:
            raise RuntimeError(f"Token budget exceeded: {self.total_tokens_used} > {self.max_tokens} max tokens.")
        if self.estimated_cost_usd > self.max_cost_usd:
            raise RuntimeError(
                f"Cost budget exceeded: ${self.estimated_cost_usd:.4f} > ${self.max_cost_usd:.2f} max USD limit."
            )


def _sanitize_error(error: Exception) -> str:
    """Scrub potential secrets from an error message before persisting to DB."""
    text = str(error)[:2000]
    # Redact common secret patterns: API keys, bearer tokens, JWT-like payloads.
    text = re.sub(r"(?i)(api[_-]?key|token|secret|authorization|password)\s*[=:]\s*\S+", r"\1=[REDACTED]", text)
    text = re.sub(r"(?i)\b(token|secret|apikey|api_key)\s+\S+", r"\1 [REDACTED]", text)
    text = re.sub(r"Bearer\s+\S+", "Bearer [REDACTED]", text)
    return text


def _load_env_local() -> None:
    """Auto-loads .env.local from project root if present."""
    root_env = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env.local")
    if os.path.exists(root_env):
        try:
            with open(root_env, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'").strip('"')
                    if k not in os.environ:
                        os.environ[k] = v
        except Exception:
            pass


def main() -> None:
    _load_env_local()
    budget_guard = TokenBudgetGuard(max_tokens=1_000_000, max_cost_usd=0.0)
    logger.info(f"Initialized TokenBudgetGuard: max_tokens={budget_guard.max_tokens}")

    # Load config
    config_path = os.path.join(os.path.dirname(__file__), "config.yml")
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Init Supabase (service role for write access)
    supabase_url = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")
    if not supabase_url or not supabase_key:
        raise KeyError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY environment variable")
    supabase = create_client(supabase_url, supabase_key)

    revalidate_url = os.environ.get("REVALIDATE_URL", "")
    revalidate_secret = os.environ.get("REVALIDATE_SECRET", "")

    run_log: dict[str, object] = {
        "agent": "pipeline",
        "status": "running",
        "items_processed": 0,
        "items_published": 0,
        "run_at": datetime.now(UTC).isoformat(),
    }

    try:
        logger.info("=" * 60)
        logger.info("GROUNDWORK CONTENT PIPELINE — START")
        logger.info("=" * 60)

        # Agent 1: Scouter
        logger.info("[1/3] Running Scouter...")
        raw_payload = run_scouter(config, supabase)
        run_log["items_processed"] = len(raw_payload)

        if not raw_payload:
            logger.info("Scouter found 0 new items. Pipeline complete (no work to do).")
            run_log["status"] = "success"
            return

        # Agent 2: Critic
        logger.info("[2/3] Running Critic...")
        filtered = run_critic(raw_payload, supabase, config)

        if not filtered:
            logger.info("Critic filtered all items. Pipeline complete.")
            run_log["status"] = "success"
            return

        is_dry_run = "--dry-run" in sys.argv
        if is_dry_run:
            logger.info("=" * 60)
            logger.info("[DRY RUN] Scouter harvested %d items, Critic approved %d candidates.", len(raw_payload), len(filtered))
            for idx, item in enumerate(filtered[:5], 1):
                logger.info("  %d. [%s] %s (%s)", idx, item.get("pillar"), item.get("title"), item.get("url"))
            logger.info("=" * 60)
            run_log["status"] = "success"
            return

        # Agent 3: Scribe
        # Cap items per run so the CI job always finishes inside its timeout
        # window even when every LLM provider is degraded (each item carries a
        # worst-case wall-clock budget in llm_router).
        max_items = int(os.getenv("SCRIBE_MAX_ITEMS", "8"))
        if len(filtered) > max_items:
            logger.info(f"Capping Scribe batch to {max_items} of {len(filtered)} candidates (SCRIBE_MAX_ITEMS).")
            filtered = filtered[:max_items]
        logger.info(f"[3/3] Running Scribe on {len(filtered)} items...")
        written = run_scribe(
            filtered,
            supabase,
            revalidate_secret,
            revalidate_url,
            config,
            budget_guard=budget_guard,
        )

        # Scribe returns the number of items it auto-published (those that
        # passed the quality gate). Report the real number instead of 0 so the
        # dashboard telemetry is accurate (content_audit_report.md Bug 1).
        run_log["items_published"] = written

        if written == 0:
            # Every candidate failed to produce a valid draft. The LLM tier is
            # likely down or rate-limited — surface this so the workflow's
            # failure notifier fires instead of logging a silent "success".
            run_log["status"] = "partial"
            run_log["error_log"] = f"Scribe wrote 0/{len(filtered)} items — LLM providers may be down or rate-limited"
            logger.warning(
                "PIPELINE PARTIAL: 0 items written out of %d candidates",
                len(filtered),
            )
            sys.exit(2)

        run_log["status"] = "success"

        # Agent 4 / Authority Injection: Auto-Syndicate published articles to DEV.to, Blogger Satellite, & IndexNow
        if written > 0:
            try:
                from authority_injector import run_syndication_for_article

                res = (
                    supabase.table("articles")
                    .select("*")
                    .eq("status", "published")
                    .order("published_at", desc=True)
                    .limit(written)
                    .execute()
                )
                articles_list = cast(list[dict[str, Any]], res.data or [])
                for art in articles_list:
                    if isinstance(art, dict):
                        run_syndication_for_article(supabase, art, live=True)
            except Exception as auth_err:
                logger.warning(f"Authority syndication completed with notice: {auth_err}")

            # Agent 5 / GraphMind: Entity Knowledge Graph Extraction Hook
            try:
                from entity_graph_builder import extract_entity_graph_from_article

                for art in articles_list:
                    if isinstance(art, dict) and art.get("id"):
                        extract_entity_graph_from_article(
                            title=art.get("title", ""),
                            content=art.get("content", ""),
                            pillar=art.get("pillar", "money"),
                            article_id=art.get("id"),
                            supabase=supabase,
                        )
                logger.info("Entity knowledge graph extraction completed for %d articles", len(articles_list))
            except Exception as ent_err:
                logger.warning("Entity graph extraction notice: %s", ent_err)

            # Phase AA / Audio Producer & Podcast Syndication
            try:
                from audio_producer import AudioProducer
                from podcast_distributor import PodcastDistributor
                from video_broadcaster import VideoBroadcaster
                from youtube_uploader import upload_video

                yt_enabled = os.getenv("YOUTUBE_UPLOAD_ENABLED", "1") == "1"
                yt_max = int(os.getenv("YOUTUBE_MAX_UPLOADS_PER_RUN", "2"))
                yt_privacy = os.getenv("YOUTUBE_PRIVACY_STATUS", "public")
                yt_uploaded = 0

                audio_prod = AudioProducer()
                res = (
                    supabase.table("articles")
                    .select("*")
                    .eq("status", "published")
                    .order("published_at", desc=True)
                    .limit(written)
                    .execute()
                )
                articles_list = cast(list[dict[str, Any]], res.data or [])
                for art in articles_list:
                    if not isinstance(art, dict):
                        continue
                    slug = art.get("slug")

                    # Render a Shorts video only when the episode is not on YouTube yet
                    want_shorts = False
                    if yt_enabled and yt_uploaded < yt_max and slug:
                        try:
                            chk = (
                                supabase.table("podcast_episodes")
                                .select("youtube_video_id")
                                .eq("slug", slug)
                                .maybe_single()
                                .execute()
                            )
                            existing_row = getattr(chk, "data", None) or {}
                            want_shorts = not existing_row.get("youtube_video_id")
                        except Exception as chk_err:
                            logger.warning(f"Episode lookup notice for {slug}: {chk_err}")
                            want_shorts = True

                    ep = asyncio.run(
                        audio_prod.process_article(
                            art, generate_video=want_shorts, video_format="shorts",
                            shorts_max_seconds=float(os.getenv("YOUTUBE_SHORTS_MAX_SECONDS", "58")),
                        )
                    )
                    if not (want_shorts and ep and slug):
                        continue

                    # Publish the rendered 9:16 audiogram to YouTube (idempotent per slug)
                    try:
                        video_file = f"public/audio/videos/{slug}.mp4"
                        if not os.path.exists(video_file):
                            logger.warning(f"YouTube Shorts skipped, no rendered video: {slug}")
                            continue
                        meta = VideoBroadcaster().build_youtube_metadata(ep, is_shorts=True)
                        result = upload_video(
                            video_file,
                            title=meta["snippet"]["title"],
                            description=meta["snippet"]["description"],
                            tags=meta["snippet"]["tags"],
                            privacy_status=yt_privacy,
                        )
                        supabase.table("podcast_episodes").update(
                            {"youtube_video_id": result["id"]}
                        ).eq("slug", slug).execute()
                        yt_uploaded += 1
                        logger.info(f"YouTube Shorts published: {slug} -> https://youtu.be/{result['id']}")
                    except Exception as yt_err:
                        logger.warning(f"YouTube Shorts step notice for {slug}: {yt_err}")

                # Broadcast pings to open directories & hubs
                distributor = PodcastDistributor()
                distributor.broadcast_all()
                logger.info("Autonomous Audio Producer & Directory Syndication completed successfully.")

                # Revalidate podcast feed so new episodes appear immediately
                if revalidate_url and revalidate_secret:
                    try:
                        import httpx
                        httpx.post(
                            revalidate_url,
                            json={"path": "/podcast/feed.xml"},
                            headers={"x-revalidate-secret": revalidate_secret},
                            timeout=10.0,
                        )
                        logger.info("Podcast feed revalidation pinged.")
                    except Exception as rev_err:
                        logger.warning(f"Feed revalidate notice: {rev_err}")
            except Exception as audio_err:
                logger.warning(f"Audio production completed with notice: {audio_err}")

            # Agent 5 / Webmention Sender: Send outbound W3C Webmentions
            try:
                from distribution_webmention import WebmentionSender

                sender = WebmentionSender()
                res = (
                    supabase.table("articles")
                    .select("*")
                    .eq("status", "published")
                    .order("published_at", desc=True)
                    .limit(written)
                    .execute()
                )
                articles_list = cast(list[dict[str, Any]], res.data or [])
                wm_sent = 0
                for art in articles_list:
                    if isinstance(art, dict):
                        result = sender.process_article(art)
                        wm_sent += len(result.get("sent", []))
                logger.info(f"Webmention sender completed: {wm_sent} outbound webmentions sent.")
            except Exception as wm_err:
                logger.warning(f"Webmention sender completed with notice: {wm_err}")

            # Agent 4 / Unified Multi-Format Production & Multi-Channel Syndication (Herald)
            try:
                from herald import PLATFORMS, amplify_article

                res = (
                    supabase.table("articles")
                    .select("*")
                    .eq("status", "published")
                    .order("published_at", desc=True)
                    .limit(written)
                    .execute()
                )
                articles_list = cast(list[dict[str, Any]], res.data or [])
                total_amplified = 0
                for art in articles_list:
                    if isinstance(art, dict):
                        amplified_rows = amplify_article(supabase, art, PLATFORMS)
                        total_amplified += sum(1 for r in amplified_rows if r.get("status") == "posted")
                logger.info("Herald multi-channel amplification completed successfully: %s posts queued/dispatched.", total_amplified)
            except Exception as soc_err:
                logger.warning(f"Social amplification completed with notice: {soc_err}")

            # Autonomous Self-Healing: Review Queue Reconciler
            try:
                from review_reconciler import run_review_reconciler

                reconcile_res = run_review_reconciler(supabase)
                logger.info(f"Auto-Reconciler completed: {reconcile_res}")
            except Exception as rec_err:
                logger.warning(f"Review reconciler notice: {rec_err}")

        logger.info("=" * 60)
        logger.info(f"PIPELINE COMPLETE: {written} articles written, voice synthesized & syndicated")
        logger.info("=" * 60)

    except Exception as e:
        run_log["status"] = "error"
        run_log["error_log"] = _sanitize_error(e)
        logger.exception(f"Pipeline FAILED: {e}")
        sys.exit(1)

    finally:
        try:
            run_log["run_at"] = datetime.now(UTC).isoformat()
            supabase.table("pipeline_runs").insert(cast(dict[str, Any], run_log)).execute()
        except Exception as e:
            logger.warning(f"Failed to log pipeline run: {e}")


if __name__ == "__main__":
    main()
