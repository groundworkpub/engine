"""Offline unit tests for the Jobs pipeline (no network, no DB).

Run with:  pytest agents/tests/
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from job_critic import (
    build_rows,
    compute_hash,
    deactivate_stale,
    make_slug,
    validate,
)
from job_scouter import (
    _classify_pillar,
    _clean_text,
    _jobicy_items,
    _map_employment_type,
    _parse_salary,
    _stringified_list,
)

# ─── scouter: normalization ───────────────────────────────────────────────────


def test_parse_salary_range():
    assert _parse_salary("$80k - $120k/year") == (80000, 120000)
    assert _parse_salary("€60.000 - €75.000") == (60000, 75000)


def test_parse_salary_none_when_absent():
    assert _parse_salary("") == (None, None)
    assert _parse_salary("Competitive") == (None, None)


def test_clean_text_strips_html_and_entities():
    html = "<div><h2>About</h2><p>We build &amp; ship tools.</p></div>"
    assert _clean_text(html) == "About\nWe build & ship tools."


def test_map_employment_type():
    assert _map_employment_type(["Full-Time"]) == "full_time"
    assert _map_employment_type("Part-time") == "part_time"
    assert _map_employment_type(["Contract", "Remote"]) == "contractor"
    assert _map_employment_type("Internship") == "internship"


def test_stringified_list():
    assert _stringified_list("['Software Engineering']") == ["Software Engineering"]
    assert _stringified_list(["Full-Time"]) == ["Full-Time"]
    assert _stringified_list("") == []


def test_classify_pillar():
    assert _classify_pillar("Senior Software Engineer", ["Backend"]) == "tech"
    assert _classify_pillar("Marketing Manager", ["Sales"]) == "life"


def test_jobicy_items_maps_fields():
    payload = {
        "jobs": [
            {
                "id": "1",
                "url": "https://jobicy.com/jobs/1",
                "jobTitle": "AI Solutions Builder",
                "companyName": "Smartcat",
                "companyLogo": "https://jobicy.com/logo.png",
                "jobIndustry": "['Software Engineering']",
                "jobType": "['Full-Time']",
                "jobGeo": "Europe",
                "jobDescription": "<p>Build AI systems for enterprises.</p>",
            }
        ]
    }
    items = _jobicy_items(payload)
    assert len(items) == 1
    item = items[0]
    assert item["title"] == "AI Solutions Builder"
    assert item["company"] == "Smartcat"
    assert item["source"] == "jobicy"
    assert item["employment_type"] == "full_time"
    assert item["pillar"] == "tech"
    assert item["company_logo"] == "https://jobicy.com/logo.png"


# ─── critic: hash, slug, validation, dedup ────────────────────────────────────


def test_compute_hash_stable_and_unique():
    a = compute_hash("arbeitnow", "Acme", "Engineer", "https://x.com/1")
    assert a == compute_hash("arbeitnow", "Acme", "Engineer", "https://x.com/1")
    assert a != compute_hash("jobicy", "Acme", "Engineer", "https://x.com/1")


def test_make_slug_is_url_safe_and_unique_per_hash():
    slug = make_slug("Acme Corp", "Senior Software Engineer", "a" * 32)
    assert slug == slug.lower()
    assert not any(c.isupper() or c.isspace() for c in slug)
    other = make_slug("Acme Corp", "Senior Software Engineer", "b" * 32)
    assert slug != other


def test_validate():
    good = {
        "title": "Engineer",
        "company": "Acme",
        "source_url": "https://x.com/1",
        "description": "x" * 200,
    }
    assert validate(good) == (True, "OK")
    assert validate({**good, "title": "A"})[0] is False
    assert validate({**good, "description": "short"})[0] is False
    assert validate({**good, "source_url": ""})[0] is False


def test_build_rows_dedups_and_filters():
    items = [
        {
            "title": "Engineer",
            "company": "Acme",
            "source_url": "https://x.com/1",
            "description": "x" * 200,
            "source": "arbeitnow",
        },
        {
            "title": "Engineer",
            "company": "Acme",
            "source_url": "https://x.com/1",
            "description": "x" * 200,
            "source": "arbeitnow",
        },
        {
            "title": "Too Short",
            "company": "Acme",
            "source_url": "https://x.com/2",
            "description": "short",
            "source": "arbeitnow",
        },
    ]
    rows, new_count, skipped = build_rows(items, {"duplicate-hash"})
    assert len(rows) == 1
    assert new_count == 1
    assert skipped == 2
    assert rows[0]["slug"] == make_slug("Acme", "Engineer", rows[0]["source_hash"])
    assert rows[0]["is_active"] is True


# ─── critic: stale deactivation (DB mocked) ───────────────────────────────────


class FakeTable:
    def __init__(self, rows):
        self._rows = rows
        self.updates = []

    def select(self, columns):
        return self

    def eq(self, column, value):
        return self

    def update(self, payload):
        self.updates.append(payload)
        return self

    def in_(self, column, values):
        self.updates.append((column, values))
        return self

    def execute(self):
        return type("Result", (), {"data": self._rows})()


class FakeSupabase:
    def __init__(self, rows):
        self._table = FakeTable(rows)

    def table(self, name):
        return self._table


def test_deactivate_stale_keeps_fresh_and_seen():
    rows = [
        {
            "id": "1",
            "source_hash": "seen-hash",
            "updated_at": "2026-08-01T00:00:00+00:00",
        },
        {
            "id": "2",
            "source_hash": "old-hash",
            "updated_at": "2026-06-01T00:00:00+00:00",
        },
        {
            "id": "3",
            "source_hash": "fresh-hash",
            "updated_at": "2026-08-12T00:00:00+00:00",
        },
    ]
    supabase = FakeSupabase(rows)
    deactivated = deactivate_stale(supabase, {"seen-hash"})
    assert deactivated == 1
    table = supabase.table("jobs")
    payload, ids = table.updates[-2], table.updates[-1][1]
    assert payload == {"is_active": False}
    assert ids == ["2"]


# ─── scouter: remotive + remoteok ─────────────────────────────────────────────


def test_remotive_items_normalize():
    from job_scouter import _remotive_items

    payload = {
        "jobs": [
            {
                "title": "Senior DevOps Engineer",
                "company_name": "Lemon.io",
                "url": "https://remotive.com/job/123",
                "tags": ["DevOps", "AWS"],
                "salary": "$100k - $140k",
                "candidate_required_location": "USA",
                "job_type": "full_time",
                "company_logo_url": "https://logo.example/l.png",
                "description": "Build pipelines <b>fast</b>.",
            }
        ]
    }
    items = _remotive_items(payload)
    assert len(items) == 1
    item = items[0]
    assert item["source"] == "remotive"
    assert item["title"] == "Senior DevOps Engineer"
    assert item["salary_min"] == 100000
    assert item["salary_max"] == 140000
    assert item["pillar"] == "tech"
    assert "<b>" not in item["description"]


def test_remoteok_items_normalize_skips_header():
    from job_scouter import _remoteok_items

    payload = [
        {"last_updated": 1786752025, "legal": "notice"},
        {
            "position": "React Engineer",
            "company": "ACME",
            "apply_url": "https://remoteok.com/l/xyz",
            "tags": ["react", "remote"],
            "salary_min": "80000",
            "salary_max": "120000",
            "location": "Global",
            "description": "We build SaaS.",
        },
    ]
    items = _remoteok_items(payload)
    assert len(items) == 1
    item = items[0]
    assert item["source"] == "remoteok"
    assert item["title"] == "React Engineer"
    assert item["company"] == "ACME"
    assert item["salary_min"] == 80000
    assert item["salary_max"] == 120000


def test_adzuna_items_normalize_with_salary():
    from job_scouter import _adzuna_items

    payload = {
        "results": [
            {
                "title": "Senior Accountant",
                "company": {"display_name": "Deloitte"},
                "redirect_url": "https://www.adzuna.com/land/ad/123",
                "salary_min": 70000.5,
                "salary_max": 90000.5,
                "location": {"area": ["US", "Chicago"]},
                "category": {"label": "Accounting & Finance Jobs"},
                "description": "Manage ledgers.",
            }
        ]
    }
    items = _adzuna_items(payload)
    assert len(items) == 1
    item = items[0]
    assert item["source"] == "adzuna"
    assert item["salary_min"] == 70000
    assert item["salary_max"] == 90000
    assert item["location"] == "US, Chicago"
    assert item["tags"] == ["Accounting & Finance Jobs"]


def test_himalayas_items_normalize():
    from job_scouter import _himalayas_items

    payload = {
        "jobs": [
            {
                "title": "Staff Security Engineer",
                "companyName": "Acme",
                "applicationLink": "https://himalayas.app/jobs/acme/guid1",
                "minSalary": 81000,
                "maxSalary": 108000,
                "currency": "EUR",
                "salaryPeriod": "annual",
                "employmentType": "FULL_TIME",
                "workType": "Remote",
                "locationRestrictions": ["Worldwide"],
                "categories": ["Security"],
                "description": "Secure our platform.",
            }
        ]
    }
    items = _himalayas_items(payload)
    assert len(items) == 1
    item = items[0]
    assert item["source"] == "himalayas"
    assert item["salary_min"] == 81000
    assert item["salary_max"] == 108000
    assert item["salary_currency"] == "EUR"
    assert item["salary_period"] == "annual"
    assert item["location_type"] == "remote"
    assert item["pillar"] == "tech"
