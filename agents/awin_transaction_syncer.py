#!/usr/bin/env python3
"""
agents/awin_transaction_syncer.py — Singer-style Transaction ETL for Awin S2S Attribution

Fetches confirmed transactions from the Awin Publisher API and streams them
into Supabase `public.affiliate_conversions`.

Supports:
  --dry-run: Fetch and display transactions without inserting into database.
  --sample: Generate and insert a verified test conversion record for testing.
  --days <N>: Lookback window in days (default: 30).

Complies with Rule 2.12: Handles missing credentials cleanly (SKIPPED_NO_CREDS, code 0).
"""

import argparse
import datetime
import json
import logging
import os
import sys
from typing import Any

import httpx
import psycopg2
from dotenv import load_dotenv

load_dotenv(".env.local")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("awin_syncer")

class AwinTransactionSyncer:
    def __init__(self, oauth_token: str | None = None, publisher_id: str | None = None):
        self.oauth_token = oauth_token or os.getenv("AWIN_OAUTH_TOKEN", "")
        self.publisher_id = publisher_id or os.getenv("AWIN_PUBLISHER_ID", "3081079")
        self.base_url = "https://api.awin.com"

        # Supabase PostgreSQL connection params
        self.db_host = os.getenv("SUPABASE_DB_HOST")
        self.db_port = os.getenv("SUPABASE_DB_PORT", "6543")
        self.db_user = os.getenv("SUPABASE_DB_USER")
        self.db_password = os.getenv("SUPABASE_DB_PASSWORD")
        self.db_name = "postgres"

    def is_configured(self) -> bool:
        return bool(self.oauth_token and self.publisher_id)

    def fetch_transactions(self, days: int = 30) -> list[dict[str, Any]]:
        """Fetches transactions from Awin Publisher API within the lookback window."""
        if not self.is_configured():
            logger.info("Awin OAuth credentials not configured (SKIPPED_NO_CREDS).")
            return []

        now = datetime.datetime.now(datetime.UTC)
        start_date = (now - datetime.timedelta(days=days)).strftime("%Y-%m-%dT00:00:00")
        end_date = now.strftime("%Y-%m-%dT23:59:59")

        url = f"{self.base_url}/publishers/{self.publisher_id}/transactions/"
        params = {
            "startDate": start_date,
            "endDate": end_date,
            "timezone": "UTC",
            "status": "approved",
        }
        headers = {
            "Authorization": f"Bearer {self.oauth_token}",
            "Accept": "application/json",
            "User-Agent": "GroundworkMedia/1.0 (Singer ETL Syncer)",
        }

        try:
            with httpx.Client(timeout=15.0) as client:
                res = client.get(url, headers=headers, params=params)
                if res.status_code == 200:
                    data = res.json()
                    logger.info("Successfully fetched %d transactions from Awin API", len(data))
                    return data
                elif res.status_code == 401 or res.status_code == 403:
                    logger.warning("Awin API authorization error (%d): %s", res.status_code, res.text)
                    return []
                else:
                    logger.warning("Awin API returned HTTP %d: %s", res.status_code, res.text)
                    return []
        except Exception as e:
            logger.error("Exception occurred while contacting Awin API: %s", e)
            return []

    def get_db_connection(self):
        return psycopg2.connect(
            host=self.db_host,
            port=self.db_port,
            user=self.db_user,
            password=self.db_password,
            dbname=self.db_name,
            sslmode="require",
        )

    def upsert_conversions(self, conversions: list[dict[str, Any]]) -> int:
        """Upserts normalized conversion records into public.affiliate_conversions."""
        if not conversions:
            logger.info("No conversions to upsert.")
            return 0

        conn = self.get_db_connection()
        conn.autocommit = True
        cur = conn.cursor()
        inserted_count = 0

        sql = """
        INSERT INTO public.affiliate_conversions (
            transaction_id, network, partner_slug, amount, commission, currency,
            status, sub_id, clickref, converted_at, raw_payload
        ) VALUES (
            %(transaction_id)s, %(network)s, %(partner_slug)s, %(amount)s, %(commission)s, %(currency)s,
            %(status)s, %(sub_id)s, %(clickref)s, %(converted_at)s, %(raw_payload)s
        )
        ON CONFLICT (transaction_id) DO UPDATE SET
            status = EXCLUDED.status,
            amount = EXCLUDED.amount,
            commission = EXCLUDED.commission,
            raw_payload = EXCLUDED.raw_payload;
        """

        for conv in conversions:
            try:
                cur.execute(sql, {
                    "transaction_id": conv["transaction_id"],
                    "network": conv.get("network", "awin"),
                    "partner_slug": conv.get("partner_slug", "awin-partner"),
                    "amount": conv.get("amount", 0.0),
                    "commission": conv.get("commission", 0.0),
                    "currency": conv.get("currency", "USD"),
                    "status": conv.get("status", "confirmed"),
                    "sub_id": conv.get("sub_id"),
                    "clickref": conv.get("clickref"),
                    "converted_at": conv.get("converted_at"),
                    "raw_payload": json.dumps(conv.get("raw_payload", {})),
                })
                inserted_count += 1
            except Exception as e:
                logger.error("Failed to upsert transaction %s: %s", conv.get("transaction_id"), e)

        cur.close()
        conn.close()
        logger.info("Successfully upserted %d conversions into Supabase.", inserted_count)
        return inserted_count

def generate_sample_conversion() -> dict[str, Any]:
    """Generates a verified sample conversion record for end-to-end integration testing."""
    sample_id = f"awin-tx-sample-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
    return {
        "transaction_id": sample_id,
        "network": "awin",
        "partner_slug": "awin-nordvpn-security",
        "amount": 89.90,
        "commission": 45.00,
        "currency": "USD",
        "status": "confirmed",
        "sub_id": "calc-rent-vs-buy",
        "clickref": "portal-article-speed-teardown",
        "converted_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "raw_payload": {
            "advertiserId": 7168,
            "advertiserName": "NordVPN",
            "commissionStatus": "approved",
            "clickDate": "2026-09-08T12:00:00",
            "transactionDate": "2026-09-08T14:30:00",
        },
    }

def main():
    parser = argparse.ArgumentParser(description="Awin Singer-Style Transaction Syncer")
    parser.add_argument("--days", type=int, default=30, help="Lookback window in days")
    parser.add_argument("--dry-run", action="store_true", help="Fetch only, do not write to DB")
    parser.add_argument("--sample", action="store_true", help="Insert a verified sample conversion")
    args = parser.parse_args()

    syncer = AwinTransactionSyncer()

    if args.sample:
        logger.info("Running in --sample mode: Generating test conversion...")
        sample_record = generate_sample_conversion()
        if args.dry_run:
            print(json.dumps(sample_record, indent=2))
        else:
            syncer.upsert_conversions([sample_record])
        return

    if not syncer.is_configured():
        logger.info("Awin credentials not set. Exiting cleanly (SKIPPED_NO_CREDS).")
        sys.exit(0)

    raw_txs = syncer.fetch_transactions(days=args.days)
    if not raw_txs:
        logger.info("No approved transactions returned by Awin API.")
        return

    normalized = []
    for tx in raw_txs:
        normalized.append({
            "transaction_id": str(tx.get("id")),
            "network": "awin",
            "partner_slug": f"awin-merchant-{tx.get('advertiserId', 'unknown')}",
            "amount": float(tx.get("saleAmount", {}).get("amount", 0.0)),
            "commission": float(tx.get("commissionAmount", {}).get("amount", 0.0)),
            "currency": tx.get("saleAmount", {}).get("currency", "USD"),
            "status": "confirmed" if tx.get("commissionStatus") == "approved" else "pending",
            "sub_id": tx.get("clickRefs", {}).get("clickRef"),
            "clickref": tx.get("clickRefs", {}).get("clickRef"),
            "converted_at": tx.get("transactionDate") or datetime.datetime.now(datetime.UTC).isoformat(),
            "raw_payload": tx,
        })

    if args.dry_run:
        logger.info("DRY RUN: Would upsert %d transactions.", len(normalized))
        print(json.dumps(normalized[:3], indent=2))
    else:
        syncer.upsert_conversions(normalized)

if __name__ == "__main__":
    main()
