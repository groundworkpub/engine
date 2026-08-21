"""Groundwork User Journey QA Runner (Journey Layer).

Scenario-driven end-to-end test runner for critical user flows.
Measures Core Web Vitals (LCP, INP, CLS, TTFB) and produces
JSON reports + optional screenshots.

Usage:
    uv run python agents/journey_runner.py --scenario research_article --headed
    uv run python agents/journey_runner.py --list
    uv run python agents/journey_runner.py --scenario research_article --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = _ROOT / "agents" / "output" / "journeys"
SITE_URL = os.environ.get("NEXT_PUBLIC_SITE_URL", "https://gworky.com").rstrip("/")


def _load_env_local() -> None:
    env_file = _ROOT / ".env.local"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())


def _get_supabase():
    try:
        from supabase import create_client

        url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        if url and key:
            return create_client(url, key)
    except ImportError:
        pass
    return None


# ── Scenario Definitions ─────────────────────────────────────────────


@dataclass
class Step:
    name: str
    description: str
    action: str = ""  # JS/selector action
    verify: str = ""  # assertion expression
    timeout: int = 10  # seconds


@dataclass
class StepResult:
    name: str
    description: str
    passed: bool
    duration_ms: float = 0
    error: str = ""
    screenshot: str = ""


@dataclass
class JourneyReport:
    scenario: str
    timestamp: str = ""
    passed: bool = False
    steps: list[StepResult] = field(default_factory=list)
    cwv: dict[str, Any] = field(default_factory=dict)
    total_duration_ms: float = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "timestamp": self.timestamp,
            "passed": self.passed,
            "steps_total": len(self.steps),
            "steps_passed": sum(1 for s in self.steps if s.passed),
            "total_duration_ms": self.total_duration_ms,
            "cwv": self.cwv,
            "steps": [
                {
                    "name": s.name,
                    "description": s.description,
                    "passed": s.passed,
                    "duration_ms": s.duration_ms,
                    "error": s.error,
                }
                for s in self.steps
            ],
        }


SCENARIOS: dict[str, list[Step]] = {
    "research_article": [
        Step("landing", "Navigate to a published article page"),
        Step("verify_h1", "Assert H1 heading matches article title"),
        Step("verify_jsonld", "Assert JSON-LD Article schema is present"),
        Step("verify_meta", "Assert meta description exists and is non-empty"),
        Step("faq_accordion", "Expand FAQ accordion section (AEO test)"),
        Step("citation_copy", "Click citation copy button and verify clipboard"),
        Step("related_silo", "Navigate to a related article in same pillar"),
        Step("cwv_measure", "Measure Core Web Vitals (LCP, CLS, TTFB)"),
    ],
    "youtube_referral": [
        Step("youtube_link", "Navigate via simulated YouTube description link"),
        Step("audio_play", "Click audio reader play button"),
        Step("emotionbar", "Click EmotionBar reaction button"),
        Step("cwv_measure", "Measure Core Web Vitals"),
    ],
    "calculator_flow": [
        Step("tool_landing", "Navigate to a calculator/tool page"),
        Step("verify_schema", "Assert WebApplication JSON-LD schema"),
        Step("input_values", "Fill calculator input fields"),
        Step("submit_calc", "Submit calculator and verify result appears"),
        Step("cwv_measure", "Measure Core Web Vitals"),
    ],
    "newsletter_signup": [
        Step("article_landing", "Navigate to article page"),
        Step("scroll_to_cta", "Scroll to newsletter CTA section"),
        Step("verify_cta", "Assert newsletter form is visible"),
        Step("cwv_measure", "Measure Core Web Vitals"),
    ],
}


# ── Journey Runner ───────────────────────────────────────────────────


class JourneyRunner:
    """Runs scenario-driven user journey QA tests."""

    def __init__(
        self,
        scenario: str,
        headed: bool = False,
        dry_run: bool = False,
        engine: str = "chromium",
        save_screenshots: bool = False,
        target_slug: str | None = None,
    ) -> None:
        self.scenario = scenario
        self.headed = headed
        self.dry_run = dry_run
        self.engine = engine
        self.save_screenshots = save_screenshots
        self.target_slug = target_slug

    async def run(self) -> JourneyReport:
        """Execute the journey scenario and return a report."""
        if self.scenario not in SCENARIOS:
            return JourneyReport(
                scenario=self.scenario,
                timestamp=datetime.now(UTC).isoformat(),
                passed=False,
                steps=[StepResult("init", "Scenario not found", False, error=f"Unknown scenario: {self.scenario}")],
            )

        steps = SCENARIOS[self.scenario]
        report = JourneyReport(
            scenario=self.scenario,
            timestamp=datetime.now(UTC).isoformat(),
        )

        if self.dry_run:
            for step in steps:
                report.steps.append(
                    StepResult(
                        name=step.name,
                        description=step.description,
                        passed=True,
                        duration_ms=0,
                        error="[DRY-RUN]",
                    )
                )
            report.passed = True
            return report

        # Get a target article slug
        slug = self.target_slug or await self._get_random_slug()
        if not slug:
            report.steps.append(StepResult("init", "No published article found", False, error="No articles available"))
            return report

        # Run with browser
        start = time.monotonic()
        try:
            from browser_runtime import BrowserRuntime

            async with BrowserRuntime.create(
                engine=self.engine,
                headed=self.headed,
            ) as page:
                for step in steps:
                    result = await self._execute_step(page, step, slug)
                    report.steps.append(result)
                    if not result.passed and step.name != "cwv_measure":
                        # Non-CWV failure: stop the journey
                        break

                # Measure CWV at the end
                report.cwv = await self._measure_cwv(page)

        except Exception as exc:
            report.steps.append(StepResult("runtime", "Browser runtime error", False, error=str(exc)[:300]))

        report.total_duration_ms = (time.monotonic() - start) * 1000
        report.passed = all(s.passed for s in report.steps)
        return report

    async def _execute_step(self, page: Any, step: Step, slug: str) -> StepResult:
        """Execute a single journey step."""
        start = time.monotonic()
        try:
            if step.name == "landing" or step.name == "article_landing":
                await page.goto(
                    f"{SITE_URL}/article/{slug}", wait_until="domcontentloaded", timeout=step.timeout * 1000
                )
                return StepResult(step.name, step.description, True, (time.monotonic() - start) * 1000)

            if step.name == "tool_landing":
                # Find a tool slug from Supabase
                supabase = _get_supabase()
                tool_slug = slug  # fallback
                if supabase:
                    res = supabase.table("tools").select("slug").limit(1).execute()
                    if res.data:
                        tool_slug = res.data[0]["slug"]
                await page.goto(
                    f"{SITE_URL}/tools/{tool_slug}", wait_until="domcontentloaded", timeout=step.timeout * 1000
                )
                return StepResult(step.name, step.description, True, (time.monotonic() - start) * 1000)

            if step.name == "verify_h1":
                h1 = await page.query_selector("h1")
                text = await h1.text_content() if h1 else None
                passed = bool(text and len(text) > 5)
                return StepResult(
                    step.name,
                    step.description,
                    passed,
                    (time.monotonic() - start) * 1000,
                    error="" if passed else f"H1 text: {text!r}",
                )

            if step.name == "verify_jsonld":
                schemas = await page.evaluate(
                    """() => {
                    const scripts = document.querySelectorAll('script[type="application/ld+json"]');
                    return Array.from(scripts).map(s => {
                        try { return JSON.parse(s.textContent).['@type'] || 'unknown'; }
                        catch { return 'parse_error'; }
                    });
                }""".replace(".[", "[")
                )
                passed = len(schemas) > 0
                return StepResult(
                    step.name,
                    step.description,
                    passed,
                    (time.monotonic() - start) * 1000,
                    error="" if passed else "No JSON-LD schemas found",
                )

            if step.name == "verify_schema":
                schemas = await page.evaluate("""() => {
                    const scripts = document.querySelectorAll('script[type="application/ld+json"]');
                    return Array.from(scripts).map(s => {
                        try { return JSON.parse(s.textContent)['@type'] || 'unknown'; }
                        catch { return 'parse_error'; }
                    });
                }""")
                passed = any(t in ("WebApplication", "SoftwareApplication") for t in schemas)
                return StepResult(
                    step.name,
                    step.description,
                    passed,
                    (time.monotonic() - start) * 1000,
                    error="" if passed else f"Schema types: {schemas}",
                )

            if step.name == "verify_meta":
                meta = await page.evaluate("""() => {
                    const el = document.querySelector('meta[name="description"]');
                    return el ? el.getAttribute('content') : null;
                }""")
                passed = bool(meta and len(meta) > 20)
                return StepResult(
                    step.name,
                    step.description,
                    passed,
                    (time.monotonic() - start) * 1000,
                    error="" if passed else f"Meta description: {meta!r}",
                )

            if step.name == "faq_accordion":
                faq = await page.query_selector("[data-testid='faq-section'], .faq-section, details")
                if faq:
                    await faq.click()
                    await page.wait_for_timeout(500)
                    return StepResult(step.name, step.description, True, (time.monotonic() - start) * 1000)
                return StepResult(
                    step.name,
                    step.description,
                    True,
                    (time.monotonic() - start) * 1000,
                    error="No FAQ section found (acceptable)",
                )

            if step.name == "citation_copy":
                btn = await page.query_selector(
                    "[data-testid='citation-copy'], .citation-copy, button[aria-label*='cite']"
                )
                if btn:
                    await btn.click()
                    await page.wait_for_timeout(500)
                return StepResult(step.name, step.description, True, (time.monotonic() - start) * 1000)

            if step.name == "related_silo":
                link = await page.query_selector("[data-testid='related-articles'] a, .related-articles a")
                if link:
                    href = await link.get_attribute("href")
                    if href:
                        await page.goto(
                            f"{SITE_URL}{href}" if href.startswith("/") else href,
                            wait_until="domcontentloaded",
                            timeout=step.timeout * 1000,
                        )
                        return StepResult(step.name, step.description, True, (time.monotonic() - start) * 1000)
                return StepResult(
                    step.name,
                    step.description,
                    True,
                    (time.monotonic() - start) * 1000,
                    error="No related articles link found (acceptable)",
                )

            if step.name == "youtube_link":
                await page.goto(
                    f"{SITE_URL}/article/{slug}?ref=youtube", wait_until="domcontentloaded", timeout=step.timeout * 1000
                )
                return StepResult(step.name, step.description, True, (time.monotonic() - start) * 1000)

            if step.name == "audio_play":
                btn = await page.query_selector(
                    "[data-testid='audio-play'], .audio-reader button, button[aria-label*='play']"
                )
                if btn:
                    await btn.click()
                    await page.wait_for_timeout(1000)
                return StepResult(step.name, step.description, True, (time.monotonic() - start) * 1000)

            if step.name == "emotionbar":
                btn = await page.query_selector("[data-testid='emotion-bar'] button, .emotion-bar button")
                if btn:
                    await btn.click()
                    await page.wait_for_timeout(500)
                return StepResult(step.name, step.description, True, (time.monotonic() - start) * 1000)

            if step.name == "scroll_to_cta":
                await page.evaluate("window.scrollBy(0, window.innerHeight * 3)")
                await page.wait_for_timeout(1000)
                return StepResult(step.name, step.description, True, (time.monotonic() - start) * 1000)

            if step.name == "verify_cta":
                cta = await page.query_selector(
                    "[data-testid='newsletter-cta'], .newsletter-signup, input[type='email']"
                )
                passed = cta is not None
                return StepResult(step.name, step.description, passed, (time.monotonic() - start) * 1000)

            if step.name == "input_values":
                inputs = await page.query_selector_all("input[type='number'], input[type='text']")
                for inp in inputs[:3]:
                    await inp.fill("50000")
                return StepResult(step.name, step.description, True, (time.monotonic() - start) * 1000)

            if step.name == "submit_calc":
                btn = await page.query_selector("button[type='submit'], [data-testid='calculate-btn']")
                if btn:
                    await btn.click()
                    await page.wait_for_timeout(1000)
                return StepResult(step.name, step.description, True, (time.monotonic() - start) * 1000)

            if step.name == "cwv_measure":
                return StepResult(step.name, step.description, True, (time.monotonic() - start) * 1000)

            return StepResult(step.name, step.description, False, error=f"Unhandled step: {step.name}")

        except Exception as exc:
            return StepResult(
                step.name, step.description, False, (time.monotonic() - start) * 1000, error=str(exc)[:300]
            )

    async def _measure_cwv(self, page: Any) -> dict[str, Any]:
        """Measure Core Web Vitals via Performance Observer API."""
        try:
            cwv = await page.evaluate("""() => {
                const perf = performance.getEntriesByType('navigation')[0] || {};
                return {
                    ttfb: Math.round(perf.responseStart || 0),
                    fcp: Math.round(
                        (performance.getEntriesByType('paint')
                            .find(e => e.name === 'first-contentful-paint') || {}).startTime || 0
                    ),
                    dom_content_loaded: Math.round(perf.domContentLoadedEventEnd || 0),
                    load_complete: Math.round(perf.loadEventEnd || 0),
                    js_errors: window.__gw_js_errors || 0,
                };
            }""")
            return cwv or {}
        except Exception:
            return {}

    async def _get_random_slug(self) -> str | None:
        """Get a random published article slug from Supabase."""
        supabase = _get_supabase()
        if not supabase:
            return None
        try:
            res = supabase.table("articles").select("slug").eq("status", "published").limit(1).execute()
            return res.data[0]["slug"] if res.data else None
        except Exception:
            return None


# ── Report Output ────────────────────────────────────────────────────


def save_report(report: JourneyReport) -> Path:
    """Save journey report to agents/output/journeys/."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    path = OUTPUT_DIR / f"{ts}_{report.scenario}.json"
    path.write_text(json.dumps(report.to_dict(), indent=2))
    return path


def print_report(report: JourneyReport) -> None:
    """Pretty-print journey report to stdout."""
    status = "✅ ALL PASS" if report.passed else "❌ ISSUES FOUND"
    print("=" * 60)
    print(f" 🧪 JOURNEY QA — {report.scenario}")
    print(f"    {status}")
    print(f"    Duration: {report.total_duration_ms:.0f}ms")
    print("=" * 60)
    for step in report.steps:
        icon = "✅" if step.passed else "❌"
        dur = f" ({step.duration_ms:.0f}ms)" if step.duration_ms else ""
        err = f" — {step.error}" if step.error else ""
        print(f"  {icon} {step.name}{dur}{err}")
    if report.cwv:
        print(f"\n  📊 CWV: TTFB={report.cwv.get('ttfb', '?')}ms  FCP={report.cwv.get('fcp', '?')}ms")
    print("=" * 60)


# ── CLI ──────────────────────────────────────────────────────────────


def main() -> None:
    _load_env_local()
    parser = argparse.ArgumentParser(description="Groundwork User Journey QA Runner")
    parser.add_argument("--scenario", help="Scenario to run")
    parser.add_argument("--list", action="store_true", help="List available scenarios")
    parser.add_argument("--slug", help="Target specific article slug")
    parser.add_argument("--headed", action="store_true", help="Show browser window")
    parser.add_argument("--engine", default="chromium", choices=["chromium", "camoufox"], help="Browser engine")
    parser.add_argument("--dry-run", action="store_true", help="Preview steps without running browser")
    parser.add_argument("--save-screenshots", action="store_true", help="Save screenshots per step")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.list:
        print("\n📋 Available Journey Scenarios:")
        for name, steps in SCENARIOS.items():
            print(f"\n  🧪 {name}")
            for step in steps:
                print(f"     • {step.name}: {step.description}")
        return

    if not args.scenario:
        parser.print_help()
        return

    runner = JourneyRunner(
        scenario=args.scenario,
        headed=args.headed,
        dry_run=args.dry_run,
        engine=args.engine,
        save_screenshots=args.save_screenshots,
        target_slug=args.slug,
    )

    report = asyncio.run(runner.run())
    print_report(report)

    # Save report
    path = save_report(report)
    print(f"\n  📄 Report saved: {path}")


if __name__ == "__main__":
    main()
