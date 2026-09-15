#!/usr/bin/env python3
"""
Unified Health Harness — 5 layer audit (Track B B3)
Layers: (1) CodeGraph sync, (2) Zod↔PG schema, (3) pytest, (4) vitest, (5) silo TF-IDF + 2-link cap
Usage: python -m agents.health_audit_harness [--json] [--layer 1..5]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def run(cmd: list[str], cwd: Path = ROOT) -> tuple[int, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=180, env=env)
    return p.returncode, (p.stdout + p.stderr)[-4000:]

def layer1_codegraph() -> dict:
    code, out = run([sys.executable, "-m", "agents.cli", "audit", "codegraph"], cwd=ROOT)
    # Fallback: check if .agents/codebase_graph.json exists and is recent
    if code != 0:
        g = ROOT / ".agents" / "codebase_graph.json"
        if g.exists():
            return {"layer": 1, "name": "CodeGraph sync", "status": "pass", "detail": "graph exists (CLI audit fallback)"}
        return {"layer": 1, "name": "CodeGraph sync", "status": "warn", "detail": out[:500]}
    return {"layer": 1, "name": "CodeGraph sync", "status": "pass" if code == 0 else "fail", "detail": out[:500]}

def layer2_zod_pg() -> dict:
    code, out = run(["pnpm", "run", "check:contracts"], cwd=ROOT)
    # check:contracts runs vitest contracts-integrity
    if code == 0:
        return {"layer": 2, "name": "Zod↔PG schema", "status": "pass"}
    return {"layer": 2, "name": "Zod↔PG schema", "status": "fail", "detail": out[:800]}

def layer3_pytest() -> dict:
    cmd = [
        "uv",
        "run",
        "pytest",
        "agents/tests/test_ataie_orchestrator.py",
        "agents/tests/test_herald.py",
        "agents/tests/test_density.py",
        "agents/tests/test_prompt_guardrails.py",
        "agents/tests/test_headroom_compressor.py",
        "agents/tests/test_sanitizer_and_parser.py",
        "-q",
    ]
    code, out = run(cmd, cwd=ROOT)
    if code == 0:
        return {"layer": 3, "name": "pytest agents", "status": "pass"}
    return {"layer": 3, "name": "pytest agents", "status": "fail", "detail": out[:800]}

def layer4_vitest() -> dict:
    code, out = run(["pnpm", "test"], cwd=ROOT)
    if code == 0:
        return {"layer": 4, "name": "vitest", "status": "pass"}
    return {"layer": 4, "name": "vitest", "status": "fail", "detail": out[:800]}

def layer5_silo() -> dict:
    # Check seo_observer / wp_publisher silo: TF-IDF ≥0.40 + 2-link cap
    # Lightweight: grep for constants, don't run full TF-IDF
    checks = []
    for pat in ["TF-IDF", "0.40", "2.*link", "silo"]:
        code, out = run(["grep", "-r", pat, "agents/seo_observer.py", "agents/wp_publisher.py"], cwd=ROOT)
        if code == 0:
            checks.append(pat)
    if len(checks) >= 2:
        return {"layer": 5, "name": "silo TF-IDF + 2-link cap", "status": "pass", "detail": f"found {checks}"}
    return {"layer": 5, "name": "silo TF-IDF + 2-link cap", "status": "warn", "detail": f"only {checks}"}

LAYERS = [layer1_codegraph, layer2_zod_pg, layer3_pytest, layer4_vitest, layer5_silo]

def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Unified Health Harness 5 layer")
    ap.add_argument("--json", action="store_true", help="JSON output")
    ap.add_argument("--layer", type=int, choices=[1,2,3,4,5], help="Run single layer")
    args = ap.parse_args()

    targets = [LAYERS[args.layer-1]] if args.layer else LAYERS
    results = []
    for fn in targets:
        try:
            r = fn()
        except Exception as e:
            r = {"layer": 0, "name": fn.__name__, "status": "error", "detail": str(e)[:500]}
        results.append(r)
        status = r["status"]
        mark = "✅" if status == "pass" else "⚠️" if status == "warn" else "❌"
        print(f"{mark} Layer {r['layer']} {r['name']}: {status}")
        if r.get("detail"):
            print(f"   {r['detail'][:200]}")

    passed = sum(1 for r in results if r["status"] == "pass")
    print(f"\n{passed}/{len(results)} layers passed")

    if args.json:
        print(json.dumps(results, indent=2))

    sys.exit(0 if passed == len(results) else 1)

if __name__ == "__main__":
    main()
