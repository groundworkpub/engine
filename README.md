# Groundwork Engine

Autonomous research and distribution engine that powers [gworky.com](https://gworky.com) — a Tier-1 English-language media and interactive utility platform covering money, body, home, life, and tech.

The Engine is a fleet of Python agents running on GitHub Actions cron schedules. It handles source harvesting, quality filtering, AI-assisted drafting with human review gates, multi-channel distribution, SEO observation, and satellite network operations. All state lives in PostgreSQL (Supabase); all credentials are injected at runtime via encrypted Actions secrets — nothing sensitive is stored in this repository.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                 GITHUB ACTIONS (cron schedules)             │
│                                                             │
│  scouter ──► critic ──► scribe ──► publisher ──► herald     │
│  (harvest)   (dedup/    (draft +   (Supabase   (social      │
│               filter)    review)    + ISR ping)  + feeds)   │
│                                                             │
│  support planes:                                            │
│   • seo_observer .... indexation control plane (GSC/Bing)   │
│   • keyword_scout ... trend & query mining                  │
│   • opportunity_engine ... internal gap detection           │
│   • envoy ........... digital PR prospecting                │
│   • site egress mesh . proxy routing with circuit breaker   │
└──────────────────────────┬──────────────────────────────────┘
                           │
              ┌────────────▼────────────┐
              │   SUPABASE POSTGRESQL   │
              │   (state + content)     │
              └────────────┬────────────┘
                           │ webhook → ISR revalidate
              ┌────────────▼────────────┐
              │  NEXT.JS FRONTEND (Vercel) │
              └─────────────────────────┘
```

## Agent catalog

| Layer | Agents | Role |
|---|---|---|
| Ingestion | `scouter.py`, `job_scouter.py`, `expired_harvest.py` | RSS/DOM harvesting, idempotent by URL |
| Quality | `critic.py`, `job_critic.py` | MD5 dedup, profanity and quality filters |
| Drafting | `scribe.py`, `expired_scribe.py`, `humanizer.py` | LLM-assisted drafts with review gates, anti-slop post-processing |
| Routing | `llm_router.py`, `prompt_optimizer.py`, `headroom_compressor.py` | Multi-provider fallback chain, prompt cost control |
| Distribution | `herald.py`, `broadcaster.py`, `podcast_distributor.py`, `distribution_*.py` | Bluesky, Mastodon, Pinterest catalog, Zenodo DOI, webmentions |
| SEO ops | `seo_observer.py`, `keyword_graph.py`, `indexer_dispatcher.py`, `link_watch.py`, `seo_optimizer.py` | Indexation control, keyword clustering, IndexNow pings, mention monitoring |
| Outreach | `envoy.py`, `link_prospector.py`, `link_drafts.py`, `outreach_dispatcher.py`, `hunter_enricher.py` | Digital PR prospecting with human approval gates and kill-switches |
| Satellite | `wp_publisher.py`, `guest_moderator.py`, `syndicator.py`, `authority_injector.py` | WordPress satellite publishing, canonical-tag enforcement |
| Egress | `egress_*.py`, `egress_selector.py` | Pluggable proxy mesh (residential/datacenter/mobile) with health checks and circuit breaker |
| Ops | `cli.py`, `pipeline.py`, `workflow_engine.py`, `metabolism.py`, `telegram_daemon.py` | Orchestration, pipeline runs logging, Telegram approval cards |

Every outbound-facing agent follows the same contract: dry-run mode by default, explicit kill-switch, rate caps, and a single identified account per platform. Human approval is required before any public posting made in the brand's name.

## Operations model

- **Compute:** GitHub Actions standard runners (`ubuntu-latest`), cron-scheduled workflows in `.github/workflows/`
- **State:** Supabase PostgreSQL with row-level security
- **Edge:** Cloudflare Workers for cache warming and lightweight triggers ([separate worker repo](https://github.com/groundworkpub/media))
- **Egress:** DataImpulse residential proxies with geo-targeting, enforced bandwidth caps, per-source exception handling
- **Secrets:** GitHub Actions secrets only; actions pinned to full commit SHAs; least-privilege `permissions` on every workflow

## Requirements

- Python 3.12+
- `pip install -r agents/requirements.txt`
- A Supabase project (schema managed separately)

## Running locally

```bash
python -m agents.cli --help          # master operations CLI
PYTHONPATH=. python agents/scouter.py --dry-run
PYTHONPATH=. pytest agents/tests/
ruff check agents
```

## Related research

The agents above exist to support the editorial research published at [gworky.com](https://gworky.com) — evidence-based guides, calculators, and original data studies across personal finance, health, home, career, and technology decisions.

## License

AGPL-3.0. See [LICENSE](LICENSE).
