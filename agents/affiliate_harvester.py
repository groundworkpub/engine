#!/usr/bin/env python3
"""
Affiliate Harvester Orchestrator.
Dispatches across the modular AffiliateAdapterRegistry (ClickBank, Awin, and pluggable networks),
filters products with strict E-E-A-T & Anti-Slop gates, and synchronizes verified offers into Supabase.
"""

import argparse
import logging
import os
import sys

# Ensure repository root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

def load_env_file():
    """Load variables from .env.local into os.environ if not already set."""
    env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env.local"))
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("\"'")
                    if k not in os.environ:
                        os.environ[k] = v

load_env_file()

from agents.affiliate_adapters.base import AffiliateProductDTO
from agents.affiliate_adapters.registry import registry

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("affiliate_harvester")


def get_supabase_client():
    """Create Supabase client using environment credentials."""
    from supabase import create_client
    url = os.getenv("NEXT_PUBLIC_SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")
    if not url or not key:
        raise ValueError("Supabase URL or Key missing in environment.")
    return create_client(url, key)


def harvest_all_offers(pillar: str = None, limit_per_adapter: int = 10) -> list[AffiliateProductDTO]:
    """Query all configured modular adapters and aggregate compliant offers."""
    adapters = registry.get_configured()
    if not adapters:
        logger.warning("No affiliate adapters are currently configured with valid credentials.")
        # Fall back to testing with all adapters
        adapters = registry.get_all()

    all_offers: list[AffiliateProductDTO] = []
    for adapter in adapters:
        logger.info(f"Harvesting offers from network: {adapter.network_name} (pillar={pillar or 'ALL'})")
        try:
            offers = adapter.fetch_offers(pillar=pillar, limit=limit_per_adapter)
            compliant_offers = [o for o in offers if adapter.is_compliant(o)]
            logger.info(f"Network {adapter.network_name}: fetched {len(offers)}, compliant {len(compliant_offers)}")
            all_offers.extend(compliant_offers)
        except Exception as e:
            logger.error(f"Error harvesting from {adapter.network_name}: {e}")

    return all_offers


def sync_to_supabase(offers: list[AffiliateProductDTO], dry_run: bool = False) -> int:
    """Upsert harvested affiliate offers into public.affiliate_links."""
    if not offers:
        logger.info("No offers to sync.")
        return 0

    if dry_run:
        logger.info(f"[DRY-RUN] Would sync {len(offers)} offers to Supabase affiliate_links:")
        for o in offers:
            logger.info(f"  - [{o.network.upper()}] {o.slug}: {o.title} -> {o.destination_url} (pillar={o.pillar})")
        return len(offers)

    sb = get_supabase_client()
    synced_count = 0

    for o in offers:
        adapter = registry.get(o.network)
        tracking_url = adapter.build_tracking_link(o, clickref="portal") if adapter else o.destination_url

        payload = {
            "slug": o.slug,
            "name": o.title,
            "merchant": o.merchant,
            "url": tracking_url,
            "display_text": o.display_text,
            "rel": "sponsored",
            "disclosure": o.disclosure,
            "pillar": o.pillar,
            "network": o.network,
            "enabled": True,
        }

        try:
            # Upsert on slug conflict
            res = sb.table("affiliate_links").upsert(payload, on_conflict="slug").execute()
            if res.data:
                synced_count += 1
                logger.info(f"✓ Upserted affiliate link: {o.slug} ({o.network})")
        except Exception as e:
            logger.error(f"Failed to upsert {o.slug} to Supabase: {e}")

    logger.info(f"Supabase synchronization complete. Total synced: {synced_count}/{len(offers)}")
    return synced_count


def main():
    parser = argparse.ArgumentParser(description="Groundwork Modular Affiliate Harvester")
    parser.add_argument("--pillar", type=str, choices=["money", "body", "home", "tech", "life"], help="Filter by pillar")
    parser.add_argument("--limit", type=int, default=10, help="Max offers per adapter")
    parser.add_argument("--sync-db", action="store_true", help="Sync compliant offers into Supabase")
    parser.add_argument("--generate-reviews", action="store_true", help="Generate programmatic in-depth reviews via Review Scribe & Critic")
    parser.add_argument("--dry-run", action="store_true", help="Log output without mutating database")
    args = parser.parse_args()

    logger.info("Initializing Modular Affiliate Harvester...")
    offers = harvest_all_offers(pillar=args.pillar, limit_per_adapter=args.limit)
    logger.info(f"Harvested {len(offers)} compliant offers across modular adapters.")

    if args.sync_db or args.dry_run:
        sync_to_supabase(offers, dry_run=args.dry_run)
    else:
        for o in offers:
            adapter = registry.get(o.network)
            track_link = adapter.build_tracking_link(o, clickref="sample") if adapter else o.destination_url
            print(f"[{o.network.upper()}] {o.title}")
            print(f"  Pillar: {o.pillar} | Gravity/Rate: {o.gravity_or_commission}")
            print(f"  Tracked Link: {track_link}\n")

    if args.generate_reviews:
        logger.info("Triggering Review Scribe & Critic pipeline for harvested offers...")
        try:
            from agents.review_scribe import generate_review_for_product, get_supabase_client
            sb = get_supabase_client()
            for o in offers:
                p_dict = {
                    "slug": o.slug,
                    "name": o.title,
                    "merchant": o.merchant,
                    "pillar": o.pillar,
                    "network": o.network,
                    "url": o.destination_url,
                }
                generate_review_for_product(p_dict, sb, dry_run=args.dry_run)
        except Exception as err:
            logger.error(f"Review Scribe generation failed: {err}")



if __name__ == "__main__":
    main()
