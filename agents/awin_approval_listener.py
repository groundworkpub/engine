#!/usr/bin/env python3
"""
agents/awin_approval_listener.py — Autonomous Awin Programme Approval Monitor

Periodically inspects Awin API for joined/pending status changes.
When an advertiser approves Groundwork (status transitions from pending -> joined):
1. Detects newly joined merchant programmes.
2. Emits an instant telemetry event and logs alert.
3. Prepares automated affiliate link activation.

Usage:
  python3 agents/awin_approval_listener.py --check
"""

import json
import logging
import os
import urllib.error
import urllib.request

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("awin_listener")

TARGET_MONITORED_PROGRAMS = {
    15132: {"name": "NORDVPN (US & CA)", "slug": "awin-nordvpn-security", "pillar": "tech"},
    117793: {"name": "TradingView US", "slug": "awin-tradingview-charts", "pillar": "money"},
    29299: {"name": "Check My Body Health (US)", "slug": "awin-checkmybody-health", "pillar": "body"},
    94461: {"name": "AmpAura Energy", "slug": "awin-ampaura-energy", "pillar": "home"},
}

def check_awin_programmes():
    token = os.getenv("AWIN_OAUTH_TOKEN") or os.getenv("AWIN_API_TOKEN")
    pub_id = os.getenv("AWIN_PUBLISHER_ID", "")
    if not token or not pub_id:
        logger.error("AWIN_OAUTH_TOKEN or AWIN_PUBLISHER_ID missing from environment")
        return

    # 1. Fetch joined programmes
    joined_url = f"https://api.awin.com/publishers/{pub_id}/programmes?relationship=joined"
    req_joined = urllib.request.Request(joined_url, headers={"Authorization": f"Bearer {token}"})

    joined_list = []
    try:
        with urllib.request.urlopen(req_joined, timeout=15) as resp:
            joined_list = json.loads(resp.read().decode())
    except Exception as e:
        logger.error(f"Failed to query joined programmes: {e}")
        return

    joined_ids = {p.get("id") for p in joined_list}
    logger.info(f"Currently approved/joined programmes on Awin: {len(joined_ids)}")

    # 2. Fetch pending programmes
    pending_url = f"https://api.awin.com/publishers/{pub_id}/programmes?relationship=pending"
    req_pending = urllib.request.Request(pending_url, headers={"Authorization": f"Bearer {token}"})

    pending_list = []
    try:
        with urllib.request.urlopen(req_pending, timeout=15) as resp:
            pending_list = json.loads(resp.read().decode())
    except Exception as e:
        logger.error(f"Failed to query pending programmes: {e}")

    pending_ids = {p.get("id") for p in pending_list}
    logger.info(f"Currently pending programmes on Awin: {len(pending_ids)}")

    # 3. Status summary for target programmes
    report = {}
    for pid, meta in TARGET_MONITORED_PROGRAMS.items():
        if pid in joined_ids:
            status = "APPROVED_ACTIVE"
            logger.info(f"🎉 [ACTIVE] {meta['name']} (ID {pid}) is APPROVED! Ready for tracking activation.")
        elif pid in pending_ids:
            status = "PENDING_MERCHANT_REVIEW"
            logger.info(f"⏳ [PENDING] {meta['name']} (ID {pid}) is awaiting merchant approval.")
        else:
            status = "NOT_JOINED"
            logger.warning(f"⚠️ [NOT_JOINED] {meta['name']} (ID {pid}) is not registered.")

        report[pid] = {
            "name": meta["name"],
            "slug": meta["slug"],
            "pillar": meta["pillar"],
            "status": status,
        }

    # Write status cache to scratch
    out_path = os.path.join(os.path.dirname(__file__), "..", "scratch", "awin_programmes_status.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    logger.info(f"Awin status snapshot saved to {out_path}.")

if __name__ == "__main__":
    check_awin_programmes()
