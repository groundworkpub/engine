"""Job Scouter — harvests remote job listings from zero-cost public feeds.

Sources:
  - Arbeitnow Job Board API (https://www.arbeitnow.com/api/job-board-api)
  - Jobicy Remote Jobs API (https://jobicy.com/api/v2/remote-jobs)

Each source is normalized into a common ``JobItem`` dict so the critic and
pipeline never care where a listing came from.
"""

import ast
import html
import json
import logging
import os
import re
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

SOURCES = {
    "arbeitnow": "https://www.arbeitnow.com/api/job-board-api",
    "jobicy": "https://jobicy.com/api/v2/remote-jobs?count=50",
    "remotive": "https://remotive.com/api/remote-jobs?limit=100",
    "remoteok": "https://remoteok.com/api",
    "himalayas": "https://himalayas.app/jobs/api?limit=50",
}

# Adzuna is a configurable source — its URL embeds credentials from env, so it
# is built at call time (see _adzuna_url). It is only attempted when the
# ADZUNA_APP_ID / ADZUNA_APP_KEY env vars are present.
ADZUNA_COUNTRIES = {
    "us": "https://api.adzuna.com/v1/api/jobs/us/search/1",
    "gb": "https://api.adzuna.com/v1/api/jobs/gb/search/1",
    "au": "https://api.adzuna.com/v1/api/jobs/au/search/1",
}


def _adzuna_url() -> str | None:
    app_id = os.environ.get("ADZUNA_APP_ID", "")
    app_key = os.environ.get("ADZUNA_APP_KEY", "")
    if not app_id or not app_key:
        return None
    # US primary + GB/AU supplement: build one US URL; more countries can be
    # added by extending run_job_scouter to iterate ADZUNA_COUNTRIES.
    base = ADZUNA_COUNTRIES["us"]
    return f"{base}?app_id={app_id}&app_key={app_key}&results_per_page=50&content-type=application/json"


FETCH_TIMEOUT_SECONDS = 20
USER_AGENT = "GroundworkJobsBot/1.0 (+https://gworky.com)"

TECH_PATTERNS = re.compile(
    r"\b(software|engineer|engineering|developer|devops|data(?:base| science| engineer| analyst)?"
    r"|backend|frontend|full[- ]?stack|sre|infrastructure|security|python|javascript|typescript"
    r"|react|node|cloud|aws|gcp|azure|ml|machine learning|ai|product (?:manager|designer)|ux|qa)\b",
    re.IGNORECASE,
)

SALARY_RANGE_RE = re.compile(r"(?:[$€£]\s*)?([\d][\d,]*(?:\.\d+)?k?)", re.IGNORECASE)

employment_map: dict[str, str] = {
    "part time": "part_time",
    "contract": "contractor",
    "freelance": "contractor",
    "intern": "internship",
}


def _fetch_json(url: str) -> dict[str, Any] | None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
        raw = response.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def _clean_text(raw: str) -> str:
    """Very small HTML→text for Arbeitnow/Jobicy descriptions (no BS dependency)."""
    text = html.unescape(raw or "")
    text = re.sub(r"<[^>]+>", "\n", text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


def _parse_salary(raw: str) -> tuple[int | None, int | None]:
    """Best-effort ``$80k - $120k`` / ``80000 - 120000`` → (80000, 120000)."""
    if not raw:
        return None, None
    # Split on the range dash, then extract the leading number from each half.
    parts = re.split(r"\s*[-–]\s*", raw)
    if len(parts) < 2:
        return None, None

    def to_int(segment: str) -> int | None:
        match = SALARY_RANGE_RE.search(segment)
        if not match:
            return None
        value = match.group(1)
        is_k = value.lower().endswith("k")
        digits = value[:-1] if is_k else value
        try:
            number = float(digits.replace(",", "").replace(".", ""))
        except ValueError:
            return None
        return int(number * 1000 if is_k else number)

    return to_int(parts[0]), to_int(parts[1])


def _map_employment_type(raw: Any) -> str:
    tokens = " ".join(raw) if isinstance(raw, list) else str(raw or "")
    tokens = re.sub(r"[\s\-_]+", " ", tokens.lower()).strip()
    for needle, mapped in employment_map.items():
        if needle.replace("_", " ") in tokens:
            return mapped
    return "full_time"


def _classify_pillar(title: str, tags: list[str]) -> str:
    haystack = " ".join([title, *tags])
    return "tech" if TECH_PATTERNS.search(haystack) else "life"


def _arbeitnow_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for job in payload.get("data") or []:
        title = (job.get("title") or "").strip()
        company = (job.get("company_name") or "").strip()
        source_url = job.get("url") or ""
        if not title or not company or not source_url:
            continue
        salary_min, salary_max = _parse_salary(job.get("salary") or "")
        tags = [str(tag) for tag in job.get("tags") or []]
        items.append(
            {
                "title": title,
                "company": company,
                "company_url": None,
                "company_logo": None,
                "location": (job.get("location") or "Remote").strip() or "Remote",
                "location_type": "remote",
                "employment_type": _map_employment_type(job.get("job_types") or []),
                "salary_min": salary_min,
                "salary_max": salary_max,
                "salary_currency": "USD",
                "salary_period": "yearly",
                "description": _clean_text(job.get("description") or ""),
                "tags": tags,
                "pillar": _classify_pillar(title, tags),
                "source": "arbeitnow",
                "source_url": source_url,
            }
        )
    return items


def _stringified_list(value: Any) -> list[str]:
    """Jobicy returns fields like ``"['Full-Time']"`` — parse the repr string."""
    if isinstance(value, list):
        return [str(v) for v in value]
    text = str(value or "")
    try:
        parsed = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        parsed = text
    if isinstance(parsed, list):
        return [str(v) for v in parsed]
    if parsed and text:
        return [text]
    return []


def _jobicy_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for job in payload.get("jobs") or []:
        title = (job.get("jobTitle") or "").strip()
        company = (job.get("companyName") or "").strip()
        source_url = job.get("url") or ""
        if not title or not company or not source_url:
            continue
        tags = _stringified_list(job.get("jobIndustry") or [])
        geo = job.get("jobGeo") or ""
        items.append(
            {
                "title": title,
                "company": company,
                "company_url": None,
                "company_logo": job.get("companyLogo"),
                "location": geo.strip() or "Remote",
                "location_type": "remote",
                "employment_type": _map_employment_type(_stringified_list(job.get("jobType") or [])),
                "salary_min": None,
                "salary_max": None,
                "salary_currency": "USD",
                "salary_period": "yearly",
                "description": _clean_text(job.get("jobDescription") or ""),
                "tags": tags,
                "pillar": _classify_pillar(title, tags),
                "source": "jobicy",
                "source_url": source_url,
            }
        )
    return items


def _remotive_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for job in payload.get("jobs") or []:
        title = (job.get("title") or "").strip()
        company = (job.get("company_name") or "").strip()
        source_url = job.get("url") or ""
        if not title or not company or not source_url:
            continue
        tags = [str(tag) for tag in job.get("tags") or []]
        salary_min, salary_max = _parse_salary(job.get("salary") or "")
        items.append(
            {
                "title": title,
                "company": company,
                "company_url": None,
                "company_logo": job.get("company_logo_url") or job.get("company_logo"),
                "location": (job.get("candidate_required_location") or "Remote").strip() or "Remote",
                "location_type": "remote",
                "employment_type": _map_employment_type(job.get("job_type") or "full_time"),
                "salary_min": salary_min,
                "salary_max": salary_max,
                "salary_currency": "USD",
                "salary_period": "yearly",
                "description": _clean_text(job.get("description") or ""),
                "tags": tags,
                "pillar": _classify_pillar(title, tags),
                "source": "remotive",
                "source_url": source_url,
            }
        )
    return items


def _remoteok_items(payload: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    # Remote OK returns a list; index 0 is a notice header object, the rest are jobs.
    jobs = payload if isinstance(payload, list) else []
    for job in jobs:
        if not isinstance(job, dict) or "position" not in job:
            continue
        title = (job.get("position") or "").strip()
        company = (job.get("company") or "").strip()
        source_url = job.get("apply_url") or job.get("url") or ""
        if not title or not company or not source_url:
            continue
        salary_min, salary_max = _parse_salary(f"{job.get('salary_min') or ''} - {job.get('salary_max') or ''}")
        items.append(
            {
                "title": title,
                "company": company,
                "company_url": None,
                "company_logo": job.get("company_logo"),
                "location": (job.get("location") or "Remote").strip() or "Remote",
                "location_type": "remote",
                "employment_type": _map_employment_type(job.get("type") or "full_time"),
                "salary_min": salary_min,
                "salary_max": salary_max,
                "salary_currency": "USD",
                "salary_period": "yearly",
                "description": _clean_text(job.get("description") or ""),
                "tags": [str(tag) for tag in job.get("tags") or []],
                "pillar": _classify_pillar(title, [str(tag) for tag in job.get("tags") or []]),
                "source": "remoteok",
                "source_url": source_url,
            }
        )
    return items


def _adzuna_items(payload: dict[str, Any], country: str = "us") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for job in payload.get("results") or []:
        title = (job.get("title") or "").strip()
        company = (job.get("company") or {}).get("display_name") or ""
        source_url = job.get("redirect_url") or ""
        if not title or not company or not source_url:
            continue
        salary_min = round(job["salary_min"]) if job.get("salary_min") else None
        salary_max = round(job["salary_max"]) if job.get("salary_max") else None
        location = ""
        area = (job.get("location") or {}).get("area") or []
        if area:
            location = str(area[0]) + (f", {area[-1]}" if len(area) > 1 and area[-1] != area[0] else "")
        tags = []
        category = (job.get("category") or {}).get("label")
        if category:
            tags.append(category)
        items.append(
            {
                "title": title,
                "company": company,
                "company_url": None,
                "company_logo": None,
                "location": location or "US",
                "location_type": "onsite",
                "employment_type": _map_employment_type(job.get("contract_type") or "full_time"),
                "salary_min": salary_min,
                "salary_max": salary_max,
                "salary_currency": "USD",
                "salary_period": "yearly",
                "description": _clean_text(job.get("description") or ""),
                "tags": tags,
                "pillar": _classify_pillar(title, tags),
                "source": "adzuna",
                "source_url": source_url,
            }
        )
    return items


def _himalayas_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for job in payload.get("jobs") or []:
        title = (job.get("title") or "").strip()
        company = (job.get("companyName") or job.get("companySlug") or "").strip()
        source_url = (
            job.get("applicationLink")
            or f"https://himalayas.app/jobs/{job.get('companySlug', '')}/{job.get('guid', '')}"
        )
        if not title or not company:
            continue
        salary_min = round(job["minSalary"]) if job.get("minSalary") else None
        salary_max = round(job["maxSalary"]) if job.get("maxSalary") else None
        employment = str(job.get("employmentType") or "")
        employment_type = {
            "full_time": "full_time",
            "part_time": "part_time",
            "contract": "contractor",
        }.get(employment.lower(), "full_time")
        work_mode = str(job.get("workType") or "").lower()
        location_type = "remote" if "remote" in work_mode else "hybrid" if "hybrid" in work_mode else "onsite"
        categories = [str(c) for c in job.get("categories") or []]
        tags = [str(c) for c in job.get("parentCategories") or []] + categories
        seniority = str(job.get("seniority") or "").lower()
        experience_level = {
            "entry": "entry",
            "junior": "mid",
            "mid": "mid",
            "senior": "senior",
            "lead": "lead",
            "manager": "lead",
            "executive": "executive",
        }.get(seniority) or (seniority if seniority else None)
        items.append(
            {
                "title": title,
                "company": company,
                "company_url": None,
                "company_logo": job.get("companyLogo"),
                "location": ", ".join(job.get("locationRestrictions") or []) or "Remote",
                "location_type": location_type,
                "employment_type": employment_type,
                "salary_min": salary_min,
                "salary_max": salary_max,
                "salary_currency": (job.get("currency") or "USD").upper(),
                "salary_period": (job.get("salaryPeriod") or "yearly").lower(),
                "experience_level": experience_level,
                "tech_stack": categories,
                "work_mode": work_mode,
                "description": _clean_text(job.get("description") or ""),
                "tags": tags,
                "pillar": _classify_pillar(title, tags),
                "source": "himalayas",
                "source_url": source_url,
            }
        )
    return items


def run_job_scouter(enabled_sources: list[str] | None = None) -> list[dict[str, Any]]:
    """Fetch and normalize job listings from every enabled source."""
    sources = enabled_sources or list(SOURCES.keys())
    items: list[dict[str, Any]] = []
    for source in sources:
        if source == "adzuna":
            url = _adzuna_url()
            if not url:
                logger.info("Adzuna skipped: ADZUNA_APP_ID/ADZUNA_APP_KEY not set")
                continue
        else:
            url = SOURCES.get(source)
        if not url:
            logger.warning("Unknown job source: %s", source)
            continue
        try:
            payload = _fetch_json(url)
            if payload is None:
                logger.warning("Job source %s returned no payload", source)
                continue
            if source == "arbeitnow":
                items.extend(_arbeitnow_items(payload))
            elif source == "jobicy":
                items.extend(_jobicy_items(payload))
            elif source == "remotive":
                items.extend(_remotive_items(payload))
            elif source == "remoteok":
                items.extend(_remoteok_items(payload))
            elif source == "adzuna":
                items.extend(_adzuna_items(payload))
            elif source == "himalayas":
                items.extend(_himalayas_items(payload))
            logger.info("Job source %s: %d items", source, len(items))
        except Exception as exc:  # noqa: BLE001 - a broken source must not kill the run
            logger.warning("Job source %s failed: %s", source, exc)
    return items
