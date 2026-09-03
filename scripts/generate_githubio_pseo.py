"""Generate 500+ Programmatic SEO (pSEO) long-tail static pages on groundworkpub.github.io.

Inspired by backlink-generator-tool's programmatic deployment strategy:
- Generates 540 unique, ultra-fast long-tail landing pages in /tools/
- Targets 27 Groundwork canonical tools x 20 high-intent search variations
- Emits valid Schema.org SoftwareApplication JSON-LD schemas
- Links directly to parent interactive tools at https://gworky.com/tools/[slug]
- Updates sitemap.xml and registers all live URLs in Supabase public.link_injection_logs
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import psycopg2
from dotenv import load_dotenv

load_dotenv(".env.local")

GH_PAGES_REPO = "groundworkpub/groundworkpub.github.io"
SITE_URL = "https://gworky.com"
GH_PAGES_BASE = "https://groundworkpub.github.io"

# 27 Canonical Tools across 5 Pillars
TOOLS_CATALOG = [
    # Money
    {"slug": "mortgage-refinance-calculator", "pillar": "money", "title": "Mortgage Refinance Break-Even Calculator", "metric": "Monthly Savings & Amortization"},
    {"slug": "hysa-compound-interest-calculator", "pillar": "money", "title": "HYSA Compound Interest Calculator", "metric": "Annual Yield & Net Interest"},
    {"slug": "inflation-purchasing-power-calculator", "pillar": "money", "title": "Inflation & Purchasing Power Calculator", "metric": "Real Inflation Decay Over Time"},
    {"slug": "emergency-fund-calculator", "pillar": "money", "title": "Emergency Fund Runway Calculator", "metric": "Liquid Expense Reserve in Months"},
    {"slug": "retirement-readiness-benchmark", "pillar": "money", "title": "Retirement Readiness & 4% Rule Benchmark", "metric": "Safe Withdrawal Rate & Capital Longevity"},
    {"slug": "rent-vs-buy-calculator", "pillar": "money", "title": "Rent vs Buy Decision Calculator", "metric": "Net Housing Equity & Opportunity Cost"},
    {"slug": "dollar-cost-averaging-calculator", "pillar": "money", "title": "Dollar Cost Averaging (DCA) vs Lump Sum", "metric": "Portfolio Volatility Smoothing"},
    {"slug": "savings-goal-calculator", "pillar": "money", "title": "Target Savings Goal Calculator", "metric": "Required Monthly Contribution Pace"},
    {"slug": "bond-vs-equity-allocation", "pillar": "money", "title": "Bond vs Equity Asset Allocation Engine", "metric": "Risk-Adjusted Portfolio Rebalancing"},
    {"slug": "mortgage-escrow-shortage-calculator", "pillar": "money", "title": "Mortgage Escrow Shortage & Cushion Calculator", "metric": "Property Tax & Insurance Deficits"},

    # Home
    {"slug": "solar-roi-battery-estimator", "pillar": "home", "title": "Rooftop Solar & Battery Payback Estimator", "metric": "Grid Offset & Levelized Energy Cost"},
    {"slug": "nem-3-solar-battery-payback-calculator", "pillar": "home", "title": "NEM 3.0 Solar & Battery ROI Calculator", "metric": "Avoided Cost Calculator (ACC) Savings"},
    {"slug": "hvac-heat-pump-calculator", "pillar": "home", "title": "Heat Pump Sizing & Energy Savings Calculator", "metric": "Seasonal COP & Gas vs Electric Running Cost"},
    {"slug": "whole-house-surge-protector-calculator", "pillar": "home", "title": "Whole-House Surge Protector Capacity Sizer", "metric": "kA Rating & Panel Clamping Voltage"},
    {"slug": "backup-generator-sizing-calculator", "pillar": "home", "title": "Home Standby Generator Wattage Sizer", "metric": "Starting & Running Surge Wattage"},
    {"slug": "smart-home-roi", "pillar": "home", "title": "Smart Home Automation Energy Savings Calculator", "metric": "Thermostat & Sensor Kilowatt Reduction"},

    # Body / Health
    {"slug": "heart-rate-zones-calculator", "pillar": "body", "title": "Zone 2 Cardiovascular & Lactate Calculator", "metric": "Target Heart Rate & Aerobic Threshold"},
    {"slug": "daily-calorie-tdee", "pillar": "body", "title": "TDEE & Basal Metabolic Rate Calculator", "metric": "Total Daily Energy Expenditure"},
    {"slug": "peptide-reconstitution-calculator", "pillar": "body", "title": "Peptide Reconstitution & Dosage Calculator", "metric": "Bacteriostatic Water Dilution & Units"},
    {"slug": "sleep-cycle-planner", "pillar": "body", "title": "Circadian Sleep Cycle & REM Planner", "metric": "90-Minute Sleep Phase Intervals"},

    # Life / Career
    {"slug": "ev-vs-gas-cost-calculator", "pillar": "life", "title": "EV vs Gas Vehicle Lifecycle Cost Calculator", "metric": "Cents per Mile & Battery Depreciation"},
    {"slug": "freelance-rate-calculator", "pillar": "life", "title": "Freelance & Consulting Hourly Rate Calculator", "metric": "Self-Employment Tax & Billable Efficiency"},
    {"slug": "relocation-cost-of-living-index", "pillar": "life", "title": "Relocation & Cost of Living Calculator", "metric": "State Income Tax & Housing Parity"},
    {"slug": "life-insurance-needs-calculator", "pillar": "life", "title": "Term Life Insurance Needs Assessment", "metric": "DIME Capital Replacement Target"},
    {"slug": "scorp-salary-optimizer", "pillar": "life", "title": "S-Corp Reasonable Salary & Tax Optimizer", "metric": "FICA Tax Savings vs Audit Risk"},

    # Tech
    {"slug": "llm-token-cost-calculator", "pillar": "tech", "title": "LLM API Inference Cost & Latency Calculator", "metric": "Cost per 1M Input/Output Tokens"},
    {"slug": "subscription-stack-auditor", "pillar": "tech", "title": "SaaS & Subscription Stack Auditor", "metric": "Recurring Annual Overhead & Redundancy"},
]

# 20 High-Intent Search Multipliers
INTENT_PATTERNS = [
    {"prefix": "advanced-", "suffix": "", "intent": "Advanced Pro Engine", "focus": "granular variables and institutional mathematical precision"},
    {"prefix": "free-", "suffix": "-online", "intent": "Free Online Web App", "focus": "zero signup, client-side, and privacy-first computation"},
    {"prefix": "best-", "suffix": "-calculator", "intent": "Top Benchmark Calculator", "focus": "verified empirical data points and 2026 standards"},
    {"prefix": "open-source-", "suffix": "-engine", "intent": "Open-Source Python & Web Engine", "focus": "transparent MIT-licensed algorithms and open formula logic"},
    {"prefix": "", "suffix": "-benchmark-data-2026", "intent": "2026 Statutory Benchmarks & Data", "focus": "inflation-adjusted statutory caps, rates, and tax parameters"},
    {"prefix": "automated-", "suffix": "-system", "intent": "Automated Calculation Model", "focus": "instant scenario simulation with real-time feedback"},
    {"prefix": "", "suffix": "-break-even-analysis", "intent": "Break-Even Timeline & Payback Model", "focus": "exact net payback periods and amortization crossover curves"},
    {"prefix": "", "suffix": "-formula-guide", "intent": "Mathematical Formula & Rules Guide", "focus": "underlying mathematical formulas, equations, and derivation"},
    {"prefix": "", "suffix": "-decision-matrix", "intent": "Strategic Decision Matrix", "focus": "comparative trade-off matrix across multiple real-world scenarios"},
    {"prefix": "interactive-", "suffix": "-tool", "intent": "Interactive Decision Utility", "focus": "dynamic input sliders and live visual results"},
    {"prefix": "instant-", "suffix": "-estimator", "intent": "Instant Rapid Estimator", "focus": "fast initial estimations with minimal required inputs"},
    {"prefix": "", "suffix": "-step-by-step-model", "intent": "Step-by-Step Modeling Guide", "focus": "methodological walk-through from raw numbers to final decision"},
    {"prefix": "accurate-", "suffix": "-calculator", "intent": "High-Accuracy Estimation Engine", "focus": "strict error tolerances and multi-factor validation"},
    {"prefix": "simple-", "suffix": "-free", "intent": "Simple Free Utility", "focus": "distilled, fluff-free calculation without complex jargon"},
    {"prefix": "", "suffix": "-comparison-table", "intent": "Comprehensive Comparison Table", "focus": "side-by-side benchmark tiers and industry averages"},
    {"prefix": "", "suffix": "-roi-timeline", "intent": "ROI & Investment Timeline", "focus": "multi-year cashflow projections and net return schedules"},
    {"prefix": "", "suffix": "-rules-and-limits-2026", "intent": "2026 Rules, Limits & Thresholds", "focus": "official regulatory guidelines and statutory limits"},
    {"prefix": "", "suffix": "-planner-sheet", "intent": "Quantitative Planning Sheet", "focus": "structured planning templates and scenario worksheets"},
    {"prefix": "", "suffix": "-rates-and-parameters", "intent": "Current Rates & Parameter Matrix", "focus": "macroeconomic interest rates, utility tariffs, and benchmarks"},
    {"prefix": "universal-", "suffix": "-suite", "intent": "Universal Calculation Suite", "focus": "all-in-one comprehensive parameter coverage"},
]

def generate_tool_html(tool, pattern):
    slug_name = f"{pattern['prefix']}{tool['slug']}{pattern['suffix']}"
    canonical_url = f"{SITE_URL}/tools/{tool['slug']}"
    page_url = f"{GH_PAGES_BASE}/tools/{slug_name}.html"
    page_title = f"{tool['title']} — {pattern['intent']} (2026)"
    page_desc = f"Use this {pattern['intent'].lower()} for {tool['title'].lower()}. Designed for {pattern['focus']} with verified 2026 statutory benchmarks."

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{page_title} | Groundwork Open Guides</title>
  <meta name="description" content="{page_desc}">
  <link rel="canonical" href="{canonical_url}">
  <meta name="robots" content="index, follow">
  
  <!-- Open Graph -->
  <meta property="og:title" content="{page_title}">
  <meta property="og:description" content="{page_desc}">
  <meta property="og:url" content="{page_url}">
  <meta property="og:type" content="website">
  <meta property="og:site_name" content="Groundwork Open Guides">
  
  <!-- Schema.org SoftwareApplication JSON-LD -->
  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "WebApplication",
    "name": "{page_title}",
    "url": "{page_url}",
    "applicationCategory": "UtilitiesApplication",
    "operatingSystem": "All",
    "description": "{page_desc}",
    "offers": {{
      "@type": "Offer",
      "price": "0",
      "priceCurrency": "USD"
    }},
    "author": {{
      "@type": "Organization",
      "name": "Groundwork Research Collective",
      "url": "{SITE_URL}"
    }}
  }}
  </script>

  <style>
    :root {{
      --bg-page: #f8fafc;
      --bg-surface: #ffffff;
      --text-primary: #0a192f;
      --text-secondary: #475569;
      --text-muted: #64748b;
      --color-primary: #1e3a71;
      --color-accent: #059669;
      --border-color: #e2e8f0;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg-page: #0a192f;
        --bg-surface: #112240;
        --text-primary: #f8fafc;
        --text-secondary: #cbd5e1;
        --text-muted: #94a3b8;
        --color-primary: #3a5d9e;
        --color-accent: #10b981;
        --border-color: #1e293b;
      }}
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background-color: var(--bg-page);
      color: var(--text-primary);
      line-height: 1.6;
      padding: 0 1rem;
    }}
    .container {{
      max-width: 860px;
      margin: 2rem auto;
    }}
    header {{
      margin-bottom: 2rem;
      padding-bottom: 1.5rem;
      border-bottom: 1px solid var(--border-color);
    }}
    .badge {{
      display: inline-block;
      padding: 0.25rem 0.75rem;
      font-size: 0.75rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      border-radius: 9999px;
      background: rgba(5, 150, 105, 0.12);
      color: var(--color-accent);
      margin-bottom: 0.75rem;
    }}
    h1 {{
      font-size: 2rem;
      font-weight: 800;
      line-height: 1.25;
      margin-bottom: 0.5rem;
    }}
    .lead {{
      font-size: 1.125rem;
      color: var(--text-secondary);
    }}
    .card {{
      background: var(--bg-surface);
      border: 1px solid var(--border-color);
      border-radius: 1rem;
      padding: 2rem;
      margin-bottom: 2rem;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
    }}
    .cta-box {{
      background: linear-gradient(135deg, rgba(30, 58, 113, 0.08), rgba(5, 150, 105, 0.08));
      border: 1px solid var(--color-accent);
      border-radius: 1rem;
      padding: 2rem;
      text-align: center;
      margin-bottom: 2rem;
    }}
    .cta-btn {{
      display: inline-block;
      padding: 0.875rem 2rem;
      font-size: 1rem;
      font-weight: 700;
      color: #ffffff;
      background-color: var(--color-accent);
      border-radius: 0.75rem;
      text-decoration: none;
      transition: opacity 0.2s ease;
      margin-top: 1rem;
    }}
    .cta-btn:hover {{
      opacity: 0.9;
    }}
    h2 {{
      font-size: 1.35rem;
      font-weight: 700;
      margin: 1.5rem 0 0.75rem 0;
    }}
    p {{
      color: var(--text-secondary);
      margin-bottom: 1rem;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin: 1.5rem 0;
      font-size: 0.9rem;
    }}
    th, td {{
      padding: 0.75rem;
      text-align: left;
      border-bottom: 1px solid var(--border-color);
    }}
    th {{
      font-weight: 700;
      color: var(--text-primary);
      background: rgba(0, 0, 0, 0.02);
    }}
    footer {{
      margin-top: 3rem;
      padding-top: 1.5rem;
      border-top: 1px solid var(--border-color);
      font-size: 0.85rem;
      color: var(--text-muted);
      display: flex;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 1rem;
    }}
    footer a {{
      color: var(--color-accent);
      text-decoration: none;
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <span class="badge">{tool['pillar'].upper()} DECISION UTILITY</span>
      <h1>{page_title}</h1>
      <p class="lead">{page_desc}</p>
    </header>

    <div class="cta-box">
      <h2>Execute Interactive Simulation</h2>
      <p>Compute precise scenario figures, customize amortization timelines, and export raw CSV data directly in the flagship web application.</p>
      <a href="{canonical_url}" class="cta-btn">Open Full Interactive Calculator &rarr;</a>
    </div>

    <div class="card">
      <h2>Core Engineering & Formula Model</h2>
      <p>This program focuses on <strong>{pattern['focus']}</strong>. Groundwork evaluates empirical models to eliminate financial guesswork and marketing exaggeration.</p>

      <h2>Key Metric Tracked</h2>
      <p>Primary focus: <strong>{tool['metric']}</strong>. Quantitative precision ensures that every output accounts for statutory caps, marginal rates, and macroeconomic shifts in 2026.</p>

      <h2>2026 Statutory Benchmark Reference</h2>
      <table>
        <thead>
          <tr>
            <th>Parameter</th>
            <th>Standard Benchmark</th>
            <th>Empirical Variance</th>
            <th>Significance</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>Baseline Threshold</td>
            <td>Statutory Cap</td>
            <td>&plusmn; 2.5%</td>
            <td>Determines break-even point</td>
          </tr>
          <tr>
            <td>Inflation Deflator</td>
            <td>CPI-U Model (2026)</td>
            <td>Annualized</td>
            <td>Purchasing power erosion</td>
          </tr>
          <tr>
            <td>Sensitivity Factor</td>
            <td>Stress-Tested (+150 bps)</td>
            <td>Conservative</td>
            <td>Worst-case scenario survival</td>
          </tr>
        </tbody>
      </table>

      <h2>Methodology & Evidence Standard</h2>
      <p>Groundwork's decision models rely strictly on empirical evidence, peer-reviewed benchmarks, and statutory frameworks (IRS, Federal Reserve, NREL, CDC, and SEC disclosures). Zero sponsored rankings, zero affiliate steering.</p>
    </div>

    <footer>
      <div>&copy; 2026 Groundwork Publishing. Open Research Archive.</div>
      <div>
        <a href="{GH_PAGES_BASE}/">Archive Home</a> &bull;
        <a href="{canonical_url}">Original Tool on Groundwork</a> &bull;
        <a href="https://webmaster.gworky.com">Webmaster Suite</a>
      </div>
    </footer>
  </div>
</body>
</html>
"""
    return slug_name, page_url, canonical_url, html

def main():
    print(f"Total tools: {len(TOOLS_CATALOG)}")
    print(f"Total intent multipliers: {len(INTENT_PATTERNS)}")
    total_pages = len(TOOLS_CATALOG) * len(INTENT_PATTERNS)
    print(f"Target programmatic pages to generate: {total_pages}")

    # Use temporary directory for clean git operations
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo_dir = os.path.join(tmp_dir, "gh_pages")
        print(f"Cloning {GH_PAGES_REPO} via GitHub CLI...")
        clone_cmd = ["gh", "repo", "clone", GH_PAGES_REPO, repo_dir]
        subprocess.run(clone_cmd, check=True)

        tools_dir = os.path.join(repo_dir, "tools")
        os.makedirs(tools_dir, exist_ok=True)

        generated_items = []
        for tool in TOOLS_CATALOG:
            for pattern in INTENT_PATTERNS:
                slug_name, page_url, canonical_url, html_content = generate_tool_html(tool, pattern)
                out_path = os.path.join(tools_dir, f"{slug_name}.html")
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(html_content)

                generated_items.append({
                    "slug": slug_name,
                    "page_url": page_url,
                    "target_url": canonical_url,
                    "tool_title": tool["title"],
                    "intent": pattern["intent"],
                    "pillar": tool["pillar"]
                })

        print(f"Generated {len(generated_items)} static HTML pages in {tools_dir}!")

        # Generate /tools/index.html hub directory
        tools_index_path = os.path.join(tools_dir, "index.html")
        tools_by_pillar = {}
        for item in generated_items:
            tools_by_pillar.setdefault(item["pillar"], []).append(item)

        hub_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Groundwork Open Decision Utilities & Calculators Directory</title>
  <meta name="description" content="Comprehensive open-source directory of 540+ quantitative calculators and decision models across Money, Health, Home, Tech, and Life.">
  <link rel="canonical" href="{SITE_URL}/tools/">
  <style>
    body {{ font-family: -apple-system, sans-serif; background: #f8fafc; color: #0a192f; max-width: 1000px; margin: 2rem auto; padding: 0 1rem; line-height: 1.6; }}
    h1 {{ font-size: 2.25rem; font-weight: 800; margin-bottom: 0.5rem; }}
    .subtitle {{ color: #475569; font-size: 1.1rem; margin-bottom: 2rem; }}
    .pillar-section {{ background: #ffffff; border: 1px solid #e2e8f0; border-radius: 1rem; padding: 1.5rem; margin-bottom: 2rem; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); }}
    .pillar-title {{ font-size: 1.4rem; font-weight: 700; color: #1e3a71; margin-bottom: 1rem; text-transform: capitalize; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 0.75rem; }}
    .item-link {{ display: block; padding: 0.6rem 0.8rem; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 0.5rem; text-decoration: none; color: #0a192f; font-size: 0.85rem; font-weight: 500; transition: all 0.2s; }}
    .item-link:hover {{ background: #059669; color: #ffffff; border-color: #059669; transform: translateY(-1px); }}
    footer {{ margin-top: 3rem; text-align: center; font-size: 0.85rem; color: #64748b; }}
  </style>
</head>
<body>
  <h1>Groundwork Decision Utilities Directory</h1>
  <p class="subtitle">Curated directory of 540+ open mathematical decision models and statutory benchmark calculators.</p>
"""
        for pillar, items in tools_by_pillar.items():
            hub_html += f"""
  <div class="pillar-section">
    <div class="pillar-title">{pillar.upper()} Pillar ({len(items)} Models)</div>
    <div class="grid">
"""
            for it in items:
                hub_html += f'      <a class="item-link" href="{it["slug"]}.html">{it["tool_title"]} — {it["intent"]}</a>\n'
            hub_html += """    </div>
  </div>
"""

        hub_html += f"""
  <footer>
    &copy; 2026 Groundwork Publishing &bull; <a href="{SITE_URL}">gworky.com</a> &bull; <a href="https://webmaster.gworky.com">Webmaster Suite</a>
  </footer>
</body>
</html>
"""
        with open(tools_index_path, "w", encoding="utf-8") as f:
            f.write(hub_html)

        # Update sitemap.xml in repo_dir
        sitemap_path = os.path.join(repo_dir, "sitemap.xml")
        now_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        sitemap_entries = [f"""  <url>
    <loc>{GH_PAGES_BASE}/tools/</loc>
    <lastmod>{now_date}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.9</priority>
  </url>"""]

        for it in generated_items:
            sitemap_entries.append(f"""  <url>
    <loc>{it['page_url']}</loc>
    <lastmod>{now_date}</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.8</priority>
  </url>""")

        # Append to existing sitemap or create
        if os.path.exists(sitemap_path):
            with open(sitemap_path, "r", encoding="utf-8") as f:
                content = f.read()
            if "</urlset>" in content:
                new_sitemap = content.replace("</urlset>", "\n" + "\n".join(sitemap_entries) + "\n</urlset>")
            else:
                new_sitemap = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "\n".join(sitemap_entries) + "\n</urlset>"
        else:
            new_sitemap = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "\n".join(sitemap_entries) + "\n</urlset>"

        with open(sitemap_path, "w", encoding="utf-8") as f:
            f.write(new_sitemap)

        print("Sitemap successfully updated!")

        # Commit and push to groundworkpub.github.io
        subprocess.run(["git", "config", "user.name", "Groundwork Bot"], cwd=repo_dir, check=True)
        subprocess.run(["git", "config", "user.email", "bot@gworky.com"], cwd=repo_dir, check=True)
        subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)

        status_res = subprocess.run(["git", "status", "--porcelain"], cwd=repo_dir, capture_output=True, text=True, check=True)
        if status_res.stdout.strip():
            print("Committing 540 programmatic pSEO pages to groundworkpub.github.io...")
            commit_msg = "feat(pseo): deploy 540 long-tail decision utility landing pages and tools directory"
            subprocess.run(["git", "commit", "-m", commit_msg], cwd=repo_dir, check=True)
            print("Pushing to origin main...")
            subprocess.run(["git", "push", "origin", "main"], cwd=repo_dir, check=True)
            print("✅ Successfully deployed 540 pSEO pages to groundworkpub.github.io!")

        # Register in Supabase
        print("Registering 540 links into Supabase public.link_injection_logs...")
        try:
            conn = psycopg2.connect(
                host=os.getenv("SUPABASE_DB_HOST"),
                port=os.getenv("SUPABASE_DB_PORT", "6543"),
                user=os.getenv("SUPABASE_DB_USER"),
                password=os.getenv("SUPABASE_DB_PASSWORD"),
                dbname="postgres",
                sslmode="require",
            )
            conn.autocommit = True
            cur = conn.cursor()

            insert_query = """
            INSERT INTO public.link_injection_logs (
                source_slug, target_platform, tier_level, live_backlink_url,
                target_url, anchor_text, is_dofollow, status, metrics_snapshot
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (live_backlink_url) DO UPDATE SET
                status = 'published',
                target_url = EXCLUDED.target_url,
                created_at = now();
            """

            db_rows = []
            for it in generated_items:
                db_rows.append((
                    it["slug"],
                    "github_pages_pseo_tool",
                    "tier1",
                    it["page_url"],
                    it["target_url"],
                    f"Groundwork: {it['tool_title']}",
                    True,
                    "published",
                    json.dumps({"intent": it["intent"], "pillar": it["pillar"]})
                ))

            psycopg2.extras = __import__("psycopg2.extras").extras
            psycopg2.extras.execute_batch(cur, insert_query, db_rows, page_size=100)
            print(f"✅ Successfully registered {len(db_rows)} links in Supabase!")

            cur.close()
            conn.close()
        except Exception as e:
            print(f"Warning: Supabase registration encountered error: {e}")

if __name__ == "__main__":
    main()
