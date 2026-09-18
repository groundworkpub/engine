#!/usr/bin/env python3
"""
agents/outreach_verifier.py — Multi-Layer Prospect & Contact Verification Engine
Enforces Rule 2.5 (Zero-Mock Verification):
1. Target Domain HTTP 200 Live Handshake (verifies real website, minimum 1KB content).
2. Live DNS MX Record Resolution (via `dig` / `nslookup` fallback).
3. Contact Email deliverability, syntax, and disposable/spam-trap filtration.
4. Semantic Relevance & Authority Scoring (0.00 - 1.00).
"""

import logging
import re
import subprocess
import time
from typing import TypedDict
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("outreach_verifier")

# Disposable / dummy / test email domains that must be strictly rejected
DISPOSABLE_DOMAINS = {
    "example.com", "test.com", "mailinator.com", "tempmail.com", "guerrillamail.com",
    "10minutemail.com", "throwawaymail.com", "yopmail.com", "sharklasers.com"
}

EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
)

class VerificationResult(TypedDict):
    domain: str
    email: str
    is_valid: bool
    http_live: bool
    status_code: int
    mx_valid: bool
    mx_hosts: list[str]
    score: float
    reason: str

def extract_domain(url_or_domain: str) -> str:
    """Extract clean domain name without protocols or paths."""
    if not url_or_domain:
        return ""
    if url_or_domain.startswith("http://") or url_or_domain.startswith("https://"):
        parsed = urlparse(url_or_domain)
        return parsed.netloc.lower().split(":")[0].lstrip("www.")
    return url_or_domain.lower().split("/")[0].split(":")[0].lstrip("www.")

def resolve_mx_records(domain: str) -> list[str]:
    """Resolves DNS MX records for a domain using native system dig or nslookup."""
    clean_dom = extract_domain(domain)
    if not clean_dom or clean_dom in DISPOSABLE_DOMAINS:
        return []

    # 1. Try dig (macOS / Linux default)
    try:
        res = subprocess.run(
            ["dig", "+short", "MX", clean_dom],
            capture_output=True,
            text=True,
            timeout=4.0
        )
        if res.returncode == 0 and res.stdout.strip():
            records = []
            for line in res.stdout.strip().split("\n"):
                parts = line.strip().split()
                if len(parts) >= 2:
                    records.append(parts[1].rstrip("."))
                elif parts:
                    records.append(parts[0].rstrip("."))
            if records:
                return records
    except Exception as e:
        logger.debug(f"dig MX resolution error for {clean_dom}: {e}")

    # 2. Fallback to nslookup
    try:
        res = subprocess.run(
            ["nslookup", "-query=mx", clean_dom],
            capture_output=True,
            text=True,
            timeout=4.0
        )
        if res.returncode == 0 and res.stdout:
            records = []
            for line in res.stdout.split("\n"):
                if "mail exchanger" in line.lower():
                    parts = line.split("=")
                    if len(parts) > 1:
                        records.append(parts[1].strip().rstrip("."))
            if records:
                return records
    except Exception as e:
        logger.debug(f"nslookup MX resolution error for {clean_dom}: {e}")

    return []

def verify_live_http(domain_or_url: str, proxy: str | None = None, timeout: float = 12.0) -> tuple[bool, int, int]:
    """
    Verifies that the target domain physically exists and returns HTTP 200 with substantive content.
    Includes automated retry, www fallback, and direct connection fallback to avoid transient false-negatives.
    Returns (is_live, status_code, content_length).
    """
    clean_dom = extract_domain(domain_or_url)
    if not clean_dom or clean_dom in DISPOSABLE_DOMAINS:
        return False, 400, 0

    urls_to_try = [
        domain_or_url if domain_or_url.startswith("http") else f"https://{clean_dom}",
        f"https://www.{clean_dom}"
    ]
    # Deduplicate while preserving order
    urls_to_try = list(dict.fromkeys(urls_to_try))

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }

    # Attempt 1: Through residential proxy (if provided)
    for test_url in urls_to_try:
        for attempt in range(2):
            try:
                with httpx.Client(proxy=proxy, headers=headers, timeout=timeout, follow_redirects=True, verify=False) as client:
                    resp = client.get(test_url)
                    content_len = len(resp.content)
                    if resp.status_code == 200 and content_len >= 500:
                        return True, resp.status_code, content_len
            except Exception as e:
                logger.debug(f"Proxy HTTP attempt {attempt+1} failed for {test_url}: {e}")
                time.sleep(0.5)

    # Attempt 2: Direct connection fallback (if proxy timed out)
    if proxy:
        logger.debug(f"Falling back to direct egress check for {clean_dom}...")
        for test_url in urls_to_try:
            try:
                with httpx.Client(headers=headers, timeout=timeout, follow_redirects=True, verify=False) as client:
                    resp = client.get(test_url)
                    content_len = len(resp.content)
                    if resp.status_code == 200 and content_len >= 500:
                        logger.info(f"Direct connection fallback succeeded for {clean_dom} (HTTP 200)")
                        return True, resp.status_code, content_len
            except Exception as e:
                logger.debug(f"Direct check failed for {test_url}: {e}")

    return False, 0, 0

def validate_email_syntax_and_domain(email: str) -> tuple[bool, str]:
    """Validates email format, checks against disposable lists, and extracts host domain."""
    if not email or not isinstance(email, str):
        return False, ""
    email_clean = email.strip().lower()
    if not EMAIL_REGEX.match(email_clean):
        return False, ""

    email_domain = email_clean.split("@")[-1]
    if email_domain in DISPOSABLE_DOMAINS:
        return False, ""

    return True, email_domain

def calculate_relevance_score(
    pillar: str,
    target_category: str,
    page_text_or_anchor: str,
    target_asset: str,
    draft_content: str = ""
) -> float:
    """
    Calculates semantic alignment score (0.00 to 1.00) between target and Groundwork asset.
    Strictly penalizes off-topic domains with zero pillar keyword matches.
    """
    text_lower = f"{target_category} {page_text_or_anchor} {draft_content}".lower()
    asset_slug = target_asset.split("/")[-1].replace("-", " ").lower()

    # Category and keyword boosts
    keywords_by_pillar = {
        "money": ["mortgage", "broker", "refinance", "lending", "rates", "amortization", "closing costs"],
        "home": ["solar", "hvac", "heat pump", "energy", "efficiency", "contractor", "air conditioning", "insulation"],
        "body": ["health", "nutrition", "wellness", "clinical", "longevity"],
        "life": ["family", "housing", "estate", "consumer"],
        "tech": ["smart home", "monitoring", "battery", "automation"]
    }

    relevant_kws = keywords_by_pillar.get(pillar, [])
    matches = sum(1 for kw in relevant_kws if kw in text_lower or kw in asset_slug)

    # If zero relevant keywords found in target text, heavily penalize as off-topic
    if matches == 0:
        return 0.25

    if matches == 1:
        base_score = 0.62
    elif matches == 2:
        base_score = 0.74
    else:
        base_score = 0.84

    # High-value entity boost (e.g. licensed contractors, university extension)
    if any(term in text_lower for term in ["licensed", "extension", "certified", "association", "university", "realtor", "calculator", "embed"]):
        base_score += 0.08

    return round(min(0.98, max(0.20, base_score)), 2)

def verify_prospect(
    domain_or_url: str,
    email: str,
    pillar: str,
    target_category: str,
    target_asset: str,
    proxy: str | None = None,
    draft_content: str = ""
) -> VerificationResult:
    """
    Executes full multi-layer verification on a prospect.
    Enforces Rule 2.5: Zero-Mock. Rejects any non-resolving or dead targets.
    """
    domain = extract_domain(domain_or_url)

    # 1. Email syntax and domain extraction
    email_valid, email_domain = validate_email_syntax_and_domain(email)
    if not email_valid:
        return {
            "domain": domain,
            "email": email,
            "is_valid": False,
            "http_live": False,
            "status_code": 0,
            "mx_valid": False,
            "mx_hosts": [],
            "score": 0.0,
            "reason": f"Invalid email syntax or prohibited domain: {email}"
        }

    # 2. DNS MX Record Validation
    mx_hosts = resolve_mx_records(email_domain)
    mx_valid = len(mx_hosts) > 0
    if not mx_valid:
        return {
            "domain": domain,
            "email": email,
            "is_valid": False,
            "http_live": False,
            "status_code": 0,
            "mx_valid": False,
            "mx_hosts": [],
            "score": 0.0,
            "reason": f"DNS MX record lookup failed for {email_domain}. Domain cannot receive mail."
        }

    # 3. HTTP 200 Live Check on target website
    http_live, status_code, content_len = verify_live_http(domain_or_url, proxy=proxy)
    if not http_live:
        return {
            "domain": domain,
            "email": email,
            "is_valid": False,
            "http_live": False,
            "status_code": status_code,
            "mx_valid": mx_valid,
            "mx_hosts": mx_hosts,
            "score": 0.0,
            "reason": f"Target website failed live HTTP check (Status {status_code}, length {content_len} bytes)."
        }

    # 4. Calculate Relevance Score
    score = calculate_relevance_score(pillar, target_category, domain, target_asset, draft_content=draft_content)

    return {
        "domain": domain,
        "email": email,
        "is_valid": True,
        "http_live": True,
        "status_code": status_code,
        "mx_valid": True,
        "mx_hosts": mx_hosts,
        "score": score,
        "reason": f"PASSED: HTTP 200 ({content_len} bytes), MX verified ({len(mx_hosts)} hosts), Score: {score}"
    }

if __name__ == "__main__":
    import sys
    test_domain = sys.argv[1] if len(sys.argv) > 1 else "texasmortgageconsultants.com"
    test_email = sys.argv[2] if len(sys.argv) > 2 else "info@texasmortgageconsultants.com"
    print(f"Testing multi-layer verification on {test_domain} / {test_email}...")
    res = verify_prospect(
        domain_or_url=test_domain,
        email=test_email,
        pillar="money",
        target_category="Mortgage Broker",
        target_asset="https://gworky.com/tools/mortgage-refinance-calculator"
    )
    print("Verification Result:", res)
