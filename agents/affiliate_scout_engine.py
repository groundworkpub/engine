"""
Unified Tri-Network Autonomous Scout & Monetization Engine.
Orchestrated via LangGraph StateGraph, governed by AQA v2, Deep Attribution Validation,
Circuit Breaker, and Telegram Interactive Human-in-the-Loop Gate.
Networks Supported: Awin, Impact.com, ClickBank.
"""

import argparse
import json
import logging
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, TypedDict

import dotenv
from langgraph.graph import END, START, StateGraph

# Ensure agents/ directory is in sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_DIR = os.path.dirname(SCRIPT_DIR)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
if WORKSPACE_DIR not in sys.path:
    sys.path.insert(0, WORKSPACE_DIR)

dotenv.load_dotenv(os.path.join(WORKSPACE_DIR, ".env.local"))

from affiliate_adapters.awin import AwinAdapter
from affiliate_adapters.base import AffiliateProductDTO
from affiliate_adapters.clickbank import ClickBankAdapter
from affiliate_adapters.impact import ImpactAdapter
from core.aqa_scorer import AQAScoreResult, evaluate_aqa_v2
from core.circuit_breaker import AffiliateCircuitBreaker
from core.deep_validator import DeepAttributionValidator, ValidationResult
from core.telegram_gate import TelegramGate

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("affiliate_scout_engine")


# --- STATE DEFINITIONS ---

class CandidateOffer(TypedDict):
    network: str
    merchant_id: str
    name: str
    slug: str
    title: str
    merchant: str
    destination_url: str
    tracking_url: str
    pillar: str
    category: str
    epc: float
    gravity: float
    commission_rate: str
    cookie_days: int
    approval_rate: float
    target_geos: list[str]
    raw_metadata: dict


class ScoutState(TypedDict):
    run_id: str
    mode: Literal["shadow", "interactive", "execute"]
    network_filter: str
    pillar_filter: str
    dry_run: bool
    limit: int
    candidates: list[CandidateOffer]
    aqa_results: dict[str, dict]
    validation_results: dict[str, dict]
    fast_track_qualified: list[CandidateOffer]
    interactive_pending: list[CandidateOffer]
    watchlist: list[CandidateOffer]
    discarded: list[CandidateOffer]
    synced_links: list[dict]
    circuit_breaker_summary: dict
    errors: list[str]
    logs: list[str]
    status: Literal["success", "error", "partial"]
    timestamp: str


# --- LANGGRAPH NODES ---

def init_node(state: ScoutState) -> dict[str, Any]:
    """Initializes execution state, validates credentials, and sets run ID."""
    run_id = f"scout_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    logs = [f"Initialized scout run {run_id} in {state['mode'].upper()} mode (Network: {state['network_filter']})"]
    logger.info(logs[0])
    return {
        "run_id": run_id,
        "logs": logs,
        "status": "success",
        "errors": []
    }


def scout_node(state: ScoutState) -> dict[str, Any]:
    """Harvests candidates across Awin, Impact, and ClickBank adapters."""
    candidates: list[CandidateOffer] = []
    logs = list(state.get("logs", []))
    errors = list(state.get("errors", []))
    net_filter = state["network_filter"].lower()
    pil_filter = state["pillar_filter"].lower() if state["pillar_filter"] != "all" else None
    limit = state.get("limit", 20)

    # 1. Awin Scout
    if net_filter in ("awin", "all"):
        try:
            awin_adapter = AwinAdapter()
            if awin_adapter.is_configured():
                # Query approved joined offers
                aw_offers = awin_adapter.fetch_offers(pillar=pil_filter, limit=limit)
                for o in aw_offers:
                    candidates.append({
                        "network": "awin",
                        "merchant_id": o.product_id,
                        "name": o.merchant,
                        "slug": o.slug,
                        "title": o.title,
                        "merchant": o.merchant,
                        "destination_url": o.destination_url,
                        "tracking_url": awin_adapter.build_tracking_link(o),
                        "pillar": o.pillar,
                        "category": o.category,
                        "epc": 0.45,  # Baseline standard for vetted Awin merchants
                        "gravity": 0.0,
                        "commission_rate": f"{o.gravity_or_commission}%",
                        "cookie_days": 30,
                        "approval_rate": 92.0,
                        "target_geos": ["US", "UK", "AU"],
                        "raw_metadata": o.extra_meta
                    })
                logs.append(f"Awin scout harvested {len(aw_offers)} programs")
        except Exception as exc:
            err_msg = f"Awin scout error: {exc}"
            logger.error(err_msg)
            errors.append(err_msg)

    # 2. ClickBank Scout
    if net_filter in ("clickbank", "all"):
        try:
            cb_adapter = ClickBankAdapter()
            if cb_adapter.is_configured():
                cb_offers = cb_adapter.fetch_offers(pillar=pil_filter, limit=limit)
                for o in cb_offers:
                    candidates.append({
                        "network": "clickbank",
                        "merchant_id": o.product_id,
                        "name": o.merchant,
                        "slug": o.slug,
                        "title": o.title,
                        "merchant": o.merchant,
                        "destination_url": o.destination_url,
                        "tracking_url": cb_adapter.build_tracking_link(o),
                        "pillar": o.pillar,
                        "category": o.category,
                        "epc": 0.0,
                        "gravity": float(o.gravity_or_commission),
                        "commission_rate": "50-70% margin",
                        "cookie_days": 60,
                        "approval_rate": 95.0,
                        "target_geos": ["US", "CA", "UK", "AU"],
                        "raw_metadata": o.extra_meta
                    })
                logs.append(f"ClickBank scout harvested {len(cb_offers)} candidates")
        except Exception as exc:
            err_msg = f"ClickBank scout error: {exc}"
            logger.error(err_msg)
            errors.append(err_msg)

    # 3. Impact Scout
    if net_filter in ("impact", "all"):
        try:
            impact_adapter = ImpactAdapter()
            if impact_adapter.is_configured():
                imp_offers = impact_adapter.fetch_offers(pillar=pil_filter, limit=limit)
                for o in imp_offers:
                    candidates.append({
                        "network": "impact",
                        "merchant_id": o.product_id,
                        "name": o.merchant,
                        "slug": o.slug,
                        "title": o.title,
                        "merchant": o.merchant,
                        "destination_url": o.destination_url,
                        "tracking_url": impact_adapter.build_tracking_link(o),
                        "pillar": o.pillar,
                        "category": o.category,
                        "epc": 0.65,
                        "gravity": 0.0,
                        "commission_rate": f"${o.gravity_or_commission} CPA",
                        "cookie_days": 30,
                        "approval_rate": 88.0,
                        "target_geos": ["US", "UK", "AU"],
                        "raw_metadata": o.extra_meta
                    })
                logs.append(f"Impact scout harvested {len(imp_offers)} campaigns")
        except Exception as exc:
            err_msg = f"Impact scout error: {exc}"
            logger.error(err_msg)
            errors.append(err_msg)

    logger.info(f"Scout complete. Total raw candidates: {len(candidates)}")
    return {
        "candidates": candidates,
        "logs": logs,
        "errors": errors
    }


def aqa_scoring_node(state: ScoutState) -> dict[str, Any]:
    """Applies AQA v2 scoring formula across all harvested candidates."""
    aqa_results: dict[str, dict] = {}
    fast_track: list[CandidateOffer] = []
    interactive: list[CandidateOffer] = []
    watchlist: list[CandidateOffer] = []
    discarded: list[CandidateOffer] = []
    logs = list(state.get("logs", []))

    for cand in state["candidates"]:
        cand_key = f"{cand['network']}:{cand['slug']}"
        res: AQAScoreResult = evaluate_aqa_v2(cand)
        aqa_results[cand_key] = {
            "total_score": res.total_score,
            "decision": res.decision,
            "pillar_score": res.pillar_score,
            "commercial_score": res.commercial_score,
            "health_score": res.health_score,
            "geo_score": res.geo_score,
            "demographic_score": res.demographic_score,
            "penalty": res.penalty,
            "reasons": res.reasons
        }

        if res.decision == "fast_track":
            fast_track.append(cand)
        elif res.decision == "interactive":
            interactive.append(cand)
        elif res.decision == "watchlist":
            watchlist.append(cand)
        else:
            discarded.append(cand)

    summary_msg = (
        f"AQA v2 Evaluation: {len(fast_track)} fast-track (S>=85), "
        f"{len(interactive)} interactive (75<=S<85), {len(watchlist)} watchlist (60<=S<75), "
        f"{len(discarded)} discarded (S<60 or penalty)"
    )
    logs.append(summary_msg)
    logger.info(summary_msg)

    return {
        "aqa_results": aqa_results,
        "fast_track_qualified": fast_track,
        "interactive_pending": interactive,
        "watchlist": watchlist,
        "discarded": discarded,
        "logs": logs
    }


def deep_validation_node(state: ScoutState) -> dict[str, Any]:
    """
    Traces multi-hop redirect chains and confirms attribution parameter integrity
    for fast_track and interactive candidates. Demotes dormant links.
    """
    validator = DeepAttributionValidator(timeout_sec=10.0, max_hops=5)
    validation_results: dict[str, dict] = {}
    verified_fast_track: list[CandidateOffer] = []
    verified_interactive: list[CandidateOffer] = []
    discarded = list(state.get("discarded", []))
    logs = list(state.get("logs", []))

    candidates_to_validate = [
        ("fast_track", c) for c in state["fast_track_qualified"]
    ] + [
        ("interactive", c) for c in state["interactive_pending"]
    ]

    for tier, cand in candidates_to_validate:
        cand_key = f"{cand['network']}:{cand['slug']}"
        val_res: ValidationResult = validator.validate(
            candidate_id=cand["merchant_id"],
            network=cand["network"],
            tracking_url=cand["tracking_url"]
        )

        validation_results[cand_key] = {
            "http_status": val_res.http_status,
            "resolved_url": val_res.final_resolved_url,
            "redirect_hops": val_res.redirect_hops,
            "is_dormant": val_res.is_dormant,
            "is_geo_blocked": val_res.is_geo_blocked,
            "attribution_param_detected": val_res.attribution_param_detected,
            "passed": val_res.validation_passed,
            "details": val_res.details
        }

        if val_res.validation_passed:
            if tier == "fast_track":
                verified_fast_track.append(cand)
            else:
                verified_interactive.append(cand)
        else:
            logger.warning(f"Demoted {cand['slug']} to discarded: {val_res.details}")
            discarded.append(cand)

    val_msg = (
        f"Deep Validation Complete: {len(verified_fast_track)}/{len(state['fast_track_qualified'])} fast-track verified, "
        f"{len(verified_interactive)}/{len(state['interactive_pending'])} interactive verified. "
        f"{len(discarded) - len(state.get('discarded', []))} dormant/invalid links dropped."
    )
    logs.append(val_msg)
    logger.info(val_msg)

    return {
        "validation_results": validation_results,
        "fast_track_qualified": verified_fast_track,
        "interactive_pending": verified_interactive,
        "discarded": discarded,
        "logs": logs
    }


def gate_and_action_node(state: ScoutState) -> dict[str, Any]:
    """
    Enforces mode-based execution:
    - Shadow: Record all to watchlist; zero automated applications.
    - Interactive: Dispatch interactive Telegram cards for human one-click approval.
    - Execute: Auto-sync verified fast-track; interactive for remainder.
    """
    mode = state["mode"]
    telegram = TelegramGate()
    logs = list(state.get("logs", []))
    watchlist = list(state.get("watchlist", []))
    fast_track = list(state.get("fast_track_qualified", []))
    interactive = list(state.get("interactive_pending", []))

    if mode == "shadow":
        # In shadow mode, demote fast-track and interactive to watchlist with zero execution
        shadow_msg = "[SHADOW MODE] All qualified candidates diverted to candidate watchlist. No external apply initiated."
        watchlist.extend(fast_track)
        watchlist.extend(interactive)
        logs.append(shadow_msg)
        logger.info(shadow_msg)
        return {
            "fast_track_qualified": [],
            "interactive_pending": [],
            "watchlist": watchlist,
            "logs": logs
        }

    elif mode in ("interactive", "execute"):
        # Send interactive telegram approval requests for candidates in interactive tier
        sent_count = 0
        for cand in interactive:
            cand_key = f"{cand['network']}:{cand['slug']}"
            aqa_data = state["aqa_results"].get(cand_key, {})
            score = aqa_data.get("total_score", 75.0)
            reasons = aqa_data.get("reasons", [])

            comm_metric = (
                f"EPC: ${cand['epc']:.2f}" if cand['epc'] > 0
                else f"Gravity: {cand['gravity']:.1f}" if cand['gravity'] > 0
                else cand['commission_rate']
            )

            success = telegram.send_approval_request(
                candidate_name=cand["name"],
                network=cand["network"],
                pillar=cand["pillar"],
                total_score=score,
                commercial_metric=comm_metric,
                reasons=reasons,
                candidate_id=cand["merchant_id"]
            )
            if success:
                sent_count += 1

        gate_msg = f"Dispatched {sent_count} interactive review requests to Telegram."
        logs.append(gate_msg)
        logger.info(gate_msg)

    return {"logs": logs}


def sync_and_persistence_node(state: ScoutState) -> dict[str, Any]:
    """
    Syncs verified links into Supabase public.affiliate_links and logs pipeline run.
    """
    synced_links: list[dict] = []
    logs = list(state.get("logs", []))
    errors = list(state.get("errors", []))

    # Links eligible for production sync
    eligible_links = state["fast_track_qualified"]

    if not eligible_links:
        logs.append("No new links staged for immediate production sync.")
        return {"synced_links": [], "logs": logs}

    if state.get("dry_run", False):
        logs.append(f"[DRY-RUN] Would sync {len(eligible_links)} links to Supabase affiliate_links.")
        return {"synced_links": eligible_links, "logs": logs}

    # Supabase Client
    sb_url = os.getenv("NEXT_PUBLIC_SUPABASE_URL")
    sb_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not (sb_url and sb_key):
        msg = "Supabase credentials missing. Storing in local state only."
        logs.append(msg)
        logger.warning(msg)
        return {"synced_links": eligible_links, "logs": logs}

    try:
        from supabase import create_client
        sb = create_client(sb_url, sb_key)

        for cand in eligible_links:
            payload = {
                "slug": cand["slug"],
                "name": cand["title"],
                "merchant": cand["merchant"],
                "url": cand["tracking_url"],
                "display_text": f"Explore {cand['merchant']}",
                "rel": "sponsored",
                "disclosure": "Groundwork receives an affiliate commission for verified purchases.",
                "pillar": cand["pillar"],
                "network": cand["network"],
                "enabled": True,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            res = sb.table("affiliate_links").upsert(payload, on_conflict="slug").execute()
            if res.data:
                synced_links.append(payload)

        sync_msg = f"Successfully synced {len(synced_links)} links to Supabase 'affiliate_links'."
        logs.append(sync_msg)
        logger.info(sync_msg)

        # Log pipeline run
        run_record = {
            "agent": "affiliate_scout_engine",
            "status": "success" if not errors else "partial",
            "items_processed": len(state["candidates"]),
            "items_published": len(synced_links),
            "error_log": json.dumps(errors) if errors else None,
            "run_at": datetime.now(timezone.utc).isoformat()
        }
        sb.table("pipeline_runs").insert(run_record).execute()

    except Exception as exc:
        err_msg = f"Supabase sync failed: {exc}"
        logger.error(err_msg)
        errors.append(err_msg)

    return {
        "synced_links": synced_links,
        "logs": logs,
        "errors": errors
    }


def telemetry_node(state: ScoutState) -> dict[str, Any]:
    """
    Evaluates circuit breaker and pushes operational telemetry summary to Telegram.
    """
    circuit_breaker = AffiliateCircuitBreaker()
    telegram = TelegramGate()
    logs = list(state.get("logs", []))

    # Record operations to circuit breaker
    for cand in state["candidates"]:
        circuit_breaker.record_success(f"scout_{cand['network']}")
    for err in state.get("errors", []):
        circuit_breaker.record_failure("scout_error", err)

    cb_summary = circuit_breaker.get_summary()

    # Dispatch Telegram summary
    telegram.send_run_summary(
        mode=state["mode"],
        total_scouted=len(state["candidates"]),
        qualified_fast_track=len(state["fast_track_qualified"]),
        interactive_requests=len(state["interactive_pending"]),
        watchlist_added=len(state["watchlist"]),
        links_synced=len(state["synced_links"])
    )

    final_status: Literal["success", "error", "partial"] = "success"
    if state.get("errors"):
        final_status = "partial" if state.get("candidates") else "error"

    logs.append(f"Engine completed with status '{final_status}'. Run ID: {state['run_id']}")
    logger.info(logs[-1])

    return {
        "circuit_breaker_summary": cb_summary,
        "status": final_status,
        "logs": logs
    }


# --- GRAPH CONSTRUCTION ---

def build_affiliate_scout_graph() -> StateGraph:
    """Builds and compiles the LangGraph StateGraph workflow."""
    workflow = StateGraph(ScoutState)

    # Add Nodes
    workflow.add_node("init_node", init_node)
    workflow.add_node("scout_node", scout_node)
    workflow.add_node("aqa_scoring_node", aqa_scoring_node)
    workflow.add_node("deep_validation_node", deep_validation_node)
    workflow.add_node("gate_and_action_node", gate_and_action_node)
    workflow.add_node("sync_and_persistence_node", sync_and_persistence_node)
    workflow.add_node("telemetry_node", telemetry_node)

    # Add Edges
    workflow.add_edge(START, "init_node")
    workflow.add_edge("init_node", "scout_node")
    workflow.add_edge("scout_node", "aqa_scoring_node")
    workflow.add_edge("aqa_scoring_node", "deep_validation_node")
    workflow.add_edge("deep_validation_node", "gate_and_action_node")
    workflow.add_edge("gate_and_action_node", "sync_and_persistence_node")
    workflow.add_edge("sync_and_persistence_node", "telemetry_node")
    workflow.add_edge("telemetry_node", END)

    return workflow.compile()


# --- CLI ENTRYPOINT ---

def main():
    parser = argparse.ArgumentParser(
        description="Groundwork Unified Tri-Network Scout & Monetization Engine"
    )
    parser.add_argument(
        "--mode",
        choices=["shadow", "interactive", "execute"],
        default="shadow",
        help="Operational autonomy mode (default: shadow)"
    )
    parser.add_argument(
        "--network",
        choices=["awin", "impact", "clickbank", "all"],
        default="all",
        help="Target affiliate network (default: all)"
    )
    parser.add_argument(
        "--pillar",
        choices=["money", "body", "home", "tech", "life", "all"],
        default="all",
        help="Groundwork editorial pillar (default: all)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate execution without committing changes to database"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum candidates to evaluate per network"
    )

    args = parser.parse_args()

    print("\n" + "="*70)
    print("🚀 GROUNDWORK UNIFIED TRI-NETWORK MONETIZATION ENGINE")
    print(f"Mode: {args.mode.upper()} | Network: {args.network.upper()} | Pillar: {args.pillar.upper()}")
    print("="*70 + "\n")

    initial_state: ScoutState = {
        "run_id": "",
        "mode": args.mode,
        "network_filter": args.network,
        "pillar_filter": args.pillar,
        "dry_run": args.dry_run,
        "limit": args.limit,
        "candidates": [],
        "aqa_results": {},
        "validation_results": {},
        "fast_track_qualified": [],
        "interactive_pending": [],
        "watchlist": [],
        "discarded": [],
        "synced_links": [],
        "circuit_breaker_summary": {},
        "errors": [],
        "logs": [],
        "status": "success",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    graph = build_affiliate_scout_graph()
    final_output = graph.invoke(initial_state)

    print("\n" + "="*70)
    print("🎯 EXECUTION SUMMARY")
    print("="*70)
    print(f"• Run ID: {final_output['run_id']}")
    print(f"• Status: {final_output['status'].upper()}")
    print(f"• Candidates Harvested: {len(final_output['candidates'])}")
    print(f"• Fast-Track Qualified: {len(final_output['fast_track_qualified'])}")
    print(f"• Interactive Pending: {len(final_output['interactive_pending'])}")
    print(f"• Watchlist Recorded: {len(final_output['watchlist'])}")
    print(f"• Discarded: {len(final_output['discarded'])}")
    print(f"• Production Links Synced: {len(final_output['synced_links'])}")
    print("="*70 + "\n")

    return 0 if final_output["status"] in ("success", "partial") else 1


if __name__ == "__main__":
    sys.exit(main())
