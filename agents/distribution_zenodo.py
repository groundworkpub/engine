"""Groundwork Zenodo DOI Engine (Distribution Layer).

CLI wrapper for the Zenodo API — deposits flagship articles as citable
open-science publications with permanent DOIs from CERN.

Complements the Next.js endpoint ``/api/zenodo/deposit`` with batch
processing, sandbox mode, and CLI interface.

Usage:
    uv run python agents/distribution_zenodo.py --slug [slug]
    uv run python agents/distribution_zenodo.py --batch-all --limit 10
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from agents.resilience import redact_message
except ImportError:
    from resilience import redact_message

logger = logging.getLogger(__name__)

# ── Environment helpers ──────────────────────────────────────────────

_ROOT = Path(__file__).resolve().parent.parent


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


# ── Zenodo API ───────────────────────────────────────────────────────

ZENODO_SANDBOX = "https://sandbox.zenodo.org/api"
ZENODO_PRODUCTION = "https://zenodo.org/api"
SITE_URL = "https://gworky.com"


def _sanitize_pdf(text: str) -> str:
    replacements = {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2014": " - ",
        "\u2013": "-",
        "\u2022": "-",
        "•": "-",
        "·": "-",
        "\u2026": "...",
        "\u00a0": " ",
        "–": "-",
        "—": " - ",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text.encode("latin-1", "replace").decode("latin-1")


def compile_academic_pdf(article: dict[str, Any], pub_date: str) -> bytes:
    """Compile an article into a searchable academic IMRAD PDF for Google Scholar."""
    try:
        from fpdf import FPDF
        import io

        pdf = FPDF(format="A4")
        pdf.set_auto_page_break(auto=True, margin=18)
        pdf.add_page()
        ew = pdf.epw

        title = _sanitize_pdf(str(article.get("title") or "Groundwork Research Report"))
        author = _sanitize_pdf(str(article.get("author_name") or "Groundwork Editorial"))
        slug = str(article.get("slug") or "")
        pillar = _sanitize_pdf(str(article.get("pillar") or "research").upper())

        pdf.set_title(title)
        pdf.set_author(author)
        pdf.set_subject(f"Groundwork Evidence-Based Research ({pillar})")
        pdf.set_keywords("evidence-based, research, decision-support, methodology, scholarly")

        # Header banner
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(100, 116, 139)
        pdf.set_x(pdf.l_margin)
        pdf.cell(w=ew, h=5, text=f"GROUNDWORK SCHOLARLY RESEARCH SERIES · {pillar}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 8)
        pdf.set_x(pdf.l_margin)
        pdf.cell(w=ew, h=4, text=f"Published: {pub_date} · Canonical: https://gworky.com/article/{slug}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + ew, pdf.get_y())
        pdf.ln(6)

        # Title
        pdf.set_font("Helvetica", "B", 18)
        pdf.set_text_color(15, 23, 42)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(w=ew, h=8, text=title)
        pdf.ln(4)

        # Author & Institutional Attribution
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(4, 120, 87)
        pdf.set_x(pdf.l_margin)
        pdf.cell(w=ew, h=5, text=f"Author: {author}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(71, 85, 105)
        pdf.set_x(pdf.l_margin)
        pdf.cell(w=ew, h=5, text="Affiliation: Groundwork Research Collective (Open Science Standard)", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(6)

        # Abstract
        takeaway = str(article.get("takeaway") or article.get("excerpt") or "")
        if takeaway:
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(15, 23, 42)
            pdf.set_x(pdf.l_margin)
            pdf.cell(w=ew, h=6, text="ABSTRACT", new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 9.5)
            pdf.set_text_color(51, 65, 85)
            clean_takeaway = _sanitize_pdf(takeaway.replace("**", "").replace("*", "").replace("`", ""))
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(w=ew, h=5.5, text=clean_takeaway)
            pdf.ln(6)
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + ew, pdf.get_y())
            pdf.ln(6)

        # Body Content
        content = str(article.get("content") or "")
        if content:
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(15, 23, 42)
            pdf.set_x(pdf.l_margin)
            pdf.cell(w=ew, h=6, text="RESEARCH ANALYSIS & METHODOLOGY", new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(51, 65, 85)

            for line in content.splitlines()[:150]:
                line_str = line.strip()
                if not line_str:
                    pdf.ln(3)
                    continue
                pdf.set_x(pdf.l_margin)
                if line_str.startswith("# "):
                    pdf.ln(4)
                    pdf.set_font("Helvetica", "B", 14)
                    pdf.set_text_color(15, 23, 42)
                    pdf.multi_cell(w=ew, h=7, text=_sanitize_pdf(line_str[2:]))
                    pdf.set_font("Helvetica", "", 9)
                    pdf.set_text_color(51, 65, 85)
                elif line_str.startswith("## "):
                    pdf.ln(3)
                    pdf.set_font("Helvetica", "B", 12)
                    pdf.set_text_color(15, 23, 42)
                    pdf.multi_cell(w=ew, h=6, text=_sanitize_pdf(line_str[3:]))
                    pdf.set_font("Helvetica", "", 9)
                    pdf.set_text_color(51, 65, 85)
                elif line_str.startswith("### "):
                    pdf.ln(2)
                    pdf.set_font("Helvetica", "B", 10.5)
                    pdf.set_text_color(15, 23, 42)
                    pdf.multi_cell(w=ew, h=5.5, text=_sanitize_pdf(line_str[4:]))
                    pdf.set_font("Helvetica", "", 9)
                    pdf.set_text_color(51, 65, 85)
                elif line_str.startswith("- ") or line_str.startswith("* "):
                    clean_item = _sanitize_pdf(line_str[2:].replace("**", "").replace("*", "").replace("`", ""))
                    pdf.multi_cell(w=ew, h=5, text=f"  -  {clean_item}")
                else:
                    clean_para = _sanitize_pdf(line_str.replace("**", "").replace("*", "").replace("`", ""))
                    pdf.multi_cell(w=ew, h=5, text=clean_para)

        # References / Provenance Footer
        pdf.ln(6)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + ew, pdf.get_y())
        pdf.ln(4)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_x(pdf.l_margin)
        pdf.cell(w=ew, h=5, text="OPEN DATA & CITATION PROVENANCE", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(100, 116, 139)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(w=ew, h=4.5, text=f"Groundwork Research Platform · https://gworky.com/article/{slug}\nLicense: Creative Commons Attribution-NonCommercial 4.0 (CC BY-NC 4.0)\nIndexing: Google Scholar, CERN Zenodo, DataCite, OpenAIRE.")

        buf = io.BytesIO()
        pdf.output(buf)
        return buf.getvalue()
    except Exception as exc:
        logger.warning("Academic PDF compilation failed: %s", exc)
        return b""


class ZenodoEngine:
    """Deposits articles to Zenodo and mints permanent DOIs."""

    def __init__(self, sandbox: bool = False, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self.token = os.environ.get("ZENODO_SANDBOX_TOKEN" if sandbox else "ZENODO_TOKEN", "")
        self.base_url = ZENODO_SANDBOX if sandbox else ZENODO_PRODUCTION
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def is_available(self) -> bool:
        return bool(self.token)

    def deposit_article(self, article: dict[str, Any]) -> dict[str, Any]:
        """Create a Zenodo deposit for a single article."""
        import httpx

        if self.dry_run:
            logger.info("[DRY-RUN] Would deposit: %s", article.get("title"))
            return {"dry_run": True, "title": article.get("title")}

        if not self.is_available():
            return {"error": "ZENODO_TOKEN not configured"}

        # Create deposition with metadata
        pub_date = article.get("published_at")
        if pub_date:
            try:
                pub_date = datetime.fromisoformat(pub_date.replace("Z", "+00:00")).strftime("%Y-%m-%d")
            except Exception:
                pub_date = datetime.now(UTC).strftime("%Y-%m-%d")
        else:
            pub_date = datetime.now(UTC).strftime("%Y-%m-%d")

        metadata_payload = {
            "metadata": {
                "title": article.get("title", "Untitled"),
                "upload_type": "publication",
                "publication_type": "article",
                "description": article.get("takeaway") or article.get("excerpt") or article.get("title", ""),
                "creators": [{"name": article.get("author_name", "Groundwork Editorial"), "affiliation": "Groundwork Research"}],
                "keywords": [article.get("pillar", "research"), "groundwork", "evidence-based"],
                "license": "cc-by-4.0",
                "related_identifiers": [
                    {
                        "identifier": f"{SITE_URL}/article/{article.get('slug', '')}",
                        "relation": "isSupplementedBy",
                        "scheme": "url",
                    }
                ],
                "publication_date": pub_date,
            }
        }

        r = httpx.post(
            f"{self.base_url}/deposit/depositions",
            headers={**self.headers, "Content-Type": "application/json"},
            json=metadata_payload,
            timeout=30,
        )
        if r.status_code not in (200, 201):
            return {"error": f"Create deposit failed: {r.status_code} {r.text[:200]}"}

        deposit = r.json()
        deposit_id = deposit["id"]
        bucket_url = deposit.get("links", {}).get("bucket")

        # 2. Upload academic PDF and preprint markdown artifact to bucket
        if bucket_url:
            slug = article.get("slug", f"record-{deposit_id}")
            
            # Compile searchable academic PDF (IMRAD format for Google Scholar)
            pdf_bytes = compile_academic_pdf(article, pub_date)
            if pdf_bytes:
                pdf_file_name = f"groundwork-{slug}.pdf"
                try:
                    r_pdf = httpx.put(
                        f"{bucket_url}/{pdf_file_name}",
                        headers={**self.headers, "Content-Type": "application/pdf"},
                        content=pdf_bytes,
                        timeout=45,
                    )
                    if r_pdf.status_code in (200, 201):
                        logger.info("Uploaded academic PDF to Zenodo bucket: %s (%d bytes)", pdf_file_name, len(pdf_bytes))
                    else:
                        logger.warning("Academic PDF upload returned %s: %s", r_pdf.status_code, r_pdf.text[:150])
                except Exception as pdf_err:
                    logger.warning("Academic PDF upload failed: %s", redact_message(str(pdf_err)))

            # Supplementary Markdown artifact
            file_name = f"groundwork-{slug}.md"
            preprint_md = f"""# {article.get('title', 'Groundwork Report')}

**Author:** {article.get('author_name', 'Groundwork Editorial')}
**Publisher:** Groundwork Research Platform
**Date:** {pub_date}
**URL:** {SITE_URL}/article/{slug}

## Findings
{article.get('takeaway') or article.get('excerpt') or ''}

## Content
{article.get('content', '')}
""".encode()

            try:
                r_file = httpx.put(
                    f"{bucket_url}/{file_name}",
                    headers={**self.headers, "Content-Type": "application/octet-stream"},
                    content=preprint_md,
                    timeout=30,
                )
                if r_file.status_code not in (200, 201):
                    logger.warning("Preprint upload returned %s: %s", r_file.status_code, r_file.text[:150])
            except Exception as file_err:
                logger.warning("Preprint upload failed: %s", redact_message(str(file_err)))

        # 3. Publish deposition and mint final DOI
        pub_result = self.publish(deposit_id)
        final_doi = pub_result.get("doi") or deposit.get("metadata", {}).get("prereserve_doi", {}).get("doi") or f"10.5281/zenodo.{deposit_id}"

        return {
            "deposit_id": deposit_id,
            "doi": final_doi,
            "record_url": pub_result.get("record_url") or deposit.get("links", {}).get("html"),
            "status": "published" if pub_result.get("status") == "published" else "draft",
            "title": article.get("title"),
        }

    def publish(self, deposit_id: int) -> dict[str, Any]:
        """Publish a deposit and mint the final DOI."""
        import httpx

        if self.dry_run:
            return {"dry_run": True, "deposit_id": deposit_id}

        r = httpx.post(
            f"{self.base_url}/deposit/depositions/{deposit_id}/actions/publish",
            headers=self.headers,
            timeout=30,
        )
        if r.status_code not in (200, 201, 202):
            return {"error": f"Publish failed: {r.status_code} {r.text[:200]}"}

        data = r.json()
        return {
            "deposit_id": deposit_id,
            "doi": data.get("doi"),
            "record_url": data.get("links", {}).get("record_html") or data.get("links", {}).get("html"),
            "status": "published",
        }

    def batch_deposit(self, articles: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
        """Deposit multiple articles in batch."""
        results = []
        for article in articles[:limit]:
            result = self.deposit_article(article)
            results.append(result)
            logger.info("Deposited: %s → %s", article.get("title", "?")[:50], result.get("status", "?"))
        return results


# ── CLI ──────────────────────────────────────────────────────────────


def main() -> None:
    _load_env_local()
    parser = argparse.ArgumentParser(description="Groundwork Zenodo DOI Engine")
    parser.add_argument("--slug", help="Deposit a single article by slug")
    parser.add_argument("--batch-all", action="store_true", help="Deposit all flagship articles")
    parser.add_argument("--limit", type=int, default=10, help="Max articles for batch (default 10)")
    parser.add_argument("--sandbox", action="store_true", help="Use Zenodo Sandbox (testing)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without depositing")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    engine = ZenodoEngine(sandbox=args.sandbox, dry_run=args.dry_run)

    if not engine.is_available() and not args.dry_run:
        print("❌ ZENODO_TOKEN not configured in .env.local")
        sys.exit(1)

    supabase = _get_supabase()
    if not supabase:
        print("❌ Supabase not configured — cannot fetch articles")
        sys.exit(1)

    if args.slug:
        res = supabase.table("articles").select("*").eq("slug", args.slug).maybe_single().execute()
        if not res.data:
            print(f"❌ Article not found: {args.slug}")
            sys.exit(1)
        result = engine.deposit_article(res.data)
        print(json.dumps(result, indent=2))
    elif args.batch_all:
        res = (
            supabase.table("articles")
            .select("*")
            .eq("status", "published")
            .eq("is_flagship", True)
            .is_("doi", "null")
            .limit(args.limit)
            .execute()
        )
        articles = res.data or []
        print(f"📜 Depositing {len(articles)} flagship articles to Zenodo...")
        results = engine.batch_deposit(articles, limit=args.limit)
        for r in results:
            icon = "✅" if r.get("status") == "deposited" else "⚠️"
            print(f"  {icon} {r.get('title', '?')[:60]} → {r.get('status', '?')}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
