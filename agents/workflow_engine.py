"""Groundwork Dify-Inspired Autonomous Workflow Engine.

Orchestrates the multi-agent pipeline as a Directed Acyclic Graph (DAG):
  Scouter (Harvest) -> Critic (Filter) -> Scribe (Draft) -> SEO Optimizer (Tri-Signal) -> Herald (Distribute)

Synthesizes:
- langgenius/dify: Declarative Node Execution, Observability, and State Graph
- groundwork-12factor-agent: Zero-cost constraints, audit trails, and Supabase pipeline_runs logging
"""

import json
import logging
import os
import sys
import time
from datetime import UTC, datetime
from typing import Any, Callable, Dict, List, Optional

from dotenv import load_dotenv
load_dotenv(".env.local")

from supabase import Client, create_client
from agents.llm_router import router, call_llm, call_llm_json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("workflow_engine")


def get_supabase_client() -> Optional[Client]:
    url = os.getenv("NEXT_PUBLIC_SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")
    if not url or not key:
        return None
    try:
        return create_client(url, key)
    except Exception as e:
        logger.error(f"Failed to create Supabase client: {e}")
        return None


class WorkflowNode:
    def __init__(self, name: str, execute_fn: Callable[[Dict[str, Any]], Dict[str, Any]]):
        self.name = name
        self.execute_fn = execute_fn

    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        logger.info(f"Executing DAG Node: [{self.name}]")
        start = time.time()
        try:
            result = self.execute_fn(state)
            latency = round(time.time() - start, 2)
            logger.info(f"DAG Node [{self.name}] completed in {latency}s.")
            state[f"node_{self.name}_latency"] = latency
            state[f"node_{self.name}_status"] = "success"
            return result
        except Exception as e:
            latency = round(time.time() - start, 2)
            logger.error(f"DAG Node [{self.name}] failed after {latency}s: {e}")
            state[f"node_{self.name}_latency"] = latency
            state[f"node_{self.name}_status"] = "error"
            state[f"node_{self.name}_error"] = str(e)
            return state


class DifyWorkflowPipeline:
    """DAG Pipeline orchestrator executing chained agent workflows with Supabase logging."""

    def __init__(self, pipeline_name: str = "groundwork_full_pipeline"):
        self.pipeline_name = pipeline_name
        self.nodes: List[WorkflowNode] = []
        self.supabase = get_supabase_client()

    def add_node(self, name: str, execute_fn: Callable[[Dict[str, Any]], Dict[str, Any]]) -> "DifyWorkflowPipeline":
        self.nodes.append(WorkflowNode(name, execute_fn))
        return self

    def execute(self, initial_state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        state = initial_state or {}
        state["pipeline_id"] = f"{self.pipeline_name}_{int(time.time())}"
        state["started_at"] = datetime.now(UTC).isoformat()
        
        logger.info(f"Starting DAG Pipeline Execution: {self.pipeline_name}")

        overall_status = "success"
        items_processed = 0

        for node in self.nodes:
            state = node.run(state)
            if state.get(f"node_{node.name}_status") == "error":
                overall_status = "partial"

        state["completed_at"] = datetime.now(UTC).isoformat()
        items_processed = state.get("items_processed", 1)

        # Log run into Supabase pipeline_runs
        if self.supabase:
            try:
                run_record = {
                    "agent": self.pipeline_name,
                    "status": overall_status,
                    "items_processed": items_processed,
                    "run_at": datetime.now(UTC).isoformat(),
                    "error_log": json.dumps({k: v for k, v in state.items() if "error" in k}),
                }
                self.supabase.table("pipeline_runs").insert(run_record).execute()
                logger.info(f"Pipeline run [{self.pipeline_name}] recorded in Supabase pipeline_runs.")
            except Exception as e:
                logger.warning(f"Could not record pipeline_run in Supabase: {e}")

        return state


# ─── Default Production Pipeline ───────────────────────────────────────────

def run_production_dag(limit: int = 1) -> Dict[str, Any]:
    """Build and execute the end-to-end production DAG."""
    pipeline = DifyWorkflowPipeline("groundwork_production_dag")

    def node_seo_optimization(state: Dict[str, Any]) -> Dict[str, Any]:
        from agents.seo_optimizer import run_batch_seo_optimization
        run_batch_seo_optimization(limit=limit)
        state["items_processed"] = limit
        return state

    pipeline.add_node("seo_optimizer", node_seo_optimization)
    return pipeline.execute()


if __name__ == "__main__":
    limit_arg = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    run_production_dag(limit=limit_arg)
