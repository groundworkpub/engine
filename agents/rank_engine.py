"""agents/rank_engine.py

Groundwork RankEngine 100 Evaluator (Python 3.12).
Evaluates articles using the 100-point RankMath + AEO + Flesch Reading Ease model.
"""

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RankTestResult:
    test_id: str
    name: str
    category: str
    max_score: int
    score: int
    passed: bool = False
    message: str = ""


@dataclass
class RankReport:
    score: int
    grade: str
    flesch_reading_ease: float
    tests: list[dict[str, Any]] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


def count_syllables(word: str) -> int:
    word = word.lower().strip()
    if len(word) <= 3:
        return 1
    word = re.sub(r"(?:[^laeiouy]|ed|es|e)$", "", word)
    word = re.sub(r"^y", "", word)
    matches = re.findall(r"[aeiouy]{1,2}", word)
    return max(1, len(matches)) if matches else 1


def calculate_flesch_reading_ease(text: str) -> float:
    clean = re.sub(r"[#*`_\[\]()]", "", text).strip()
    if not clean:
        return 0.0

    sentences = [s.strip() for s in re.split(r"[.!?]+", clean) if s.strip()]
    sentence_count = max(1, len(sentences))

    words = [re.sub(r"[^a-zA-Z]", "", w).lower() for w in clean.split() if w]
    words = [w for w in words if w]
    word_count = max(1, len(words))

    syllable_count = sum(count_syllables(w) for w in words)

    asl = word_count / sentence_count
    asw = syllable_count / word_count

    score = 206.835 - (1.015 * asl) - (84.6 * asw)
    return max(0.0, min(100.0, round(score, 1)))


def evaluate_content(
    title: str,
    description: str,
    slug: str,
    content: str,
    focus_keyword: str,
    has_answer_box: bool | None = None,
    has_citations: bool | None = None,
) -> RankReport:
    kw = focus_keyword.lower().strip()
    t_clean = title.lower().strip()
    d_clean = description.lower().strip()
    s_clean = slug.lower().strip()
    c_clean = content.lower()

    words = [w for w in re.sub(r"[#*`_\[\]()]", "", content).split() if w]
    word_count = len(words)

    tests: list[dict[str, Any]] = []
    recs: list[str] = []

    # Category 1: Basic SEO (35 pts)
    kw_in_title = kw in t_clean
    tests.append({
        "id": "kw_in_title",
        "name": "Focus Keyword in Title",
        "category": "basic",
        "max": 10,
        "score": 10 if kw_in_title else 0,
        "passed": kw_in_title,
    })
    if not kw_in_title:
        recs.append(f"Add focus keyword '{focus_keyword}' to title.")

    kw_in_desc = kw in d_clean
    tests.append({
        "id": "kw_in_desc",
        "name": "Focus Keyword in Description",
        "category": "basic",
        "max": 8,
        "score": 8 if kw_in_desc else 0,
        "passed": kw_in_desc,
    })
    if not kw_in_desc:
        recs.append(f"Add focus keyword '{focus_keyword}' to description.")

    kw_in_slug = kw.replace(" ", "-") in s_clean or kw.replace(" ", "") in s_clean
    tests.append({
        "id": "kw_in_slug",
        "name": "Focus Keyword in Slug",
        "category": "basic",
        "max": 6,
        "score": 6 if kw_in_slug else 0,
        "passed": kw_in_slug,
    })

    intro_len = max(200, int(len(c_clean) * 0.1))
    kw_in_intro = kw in c_clean[:intro_len]
    tests.append({
        "id": "kw_in_intro",
        "name": "Keyword in First 10%",
        "category": "basic",
        "max": 6,
        "score": 6 if kw_in_intro else 0,
        "passed": kw_in_intro,
    })

    word_score = 5 if word_count >= 800 else (3 if word_count >= 400 else 1)
    tests.append({
        "id": "word_count",
        "name": "Content Length",
        "category": "basic",
        "max": 5,
        "score": word_score,
        "passed": word_count >= 800,
    })

    # Category 2: Advanced SEO (25 pts)
    headings = " ".join(re.findall(r"^#{2,3}\s+(.+)$", content, flags=re.MULTILINE)).lower()
    kw_in_headings = kw in headings
    tests.append({
        "id": "kw_in_headings",
        "name": "Keyword in H2/H3 Headings",
        "category": "advanced",
        "max": 8,
        "score": 8 if kw_in_headings else 0,
        "passed": kw_in_headings,
    })

    kw_count = len(re.findall(re.escape(kw), c_clean))
    density = (kw_count / word_count * 100) if word_count > 0 else 0
    density_pass = 0.5 <= density <= 2.5
    tests.append({
        "id": "keyword_density",
        "name": "Keyword Density",
        "category": "advanced",
        "max": 7,
        "score": 7 if density_pass else (3 if density > 0 else 0),
        "passed": density_pass,
    })

    title_len = len(title)
    title_pass = 40 <= title_len <= 65
    tests.append({
        "id": "title_length",
        "name": "Title Length (40-65 chars)",
        "category": "advanced",
        "max": 5,
        "score": 5 if title_pass else 2,
        "passed": title_pass,
    })

    desc_len = len(description)
    desc_pass = 110 <= desc_len <= 160
    tests.append({
        "id": "desc_length",
        "name": "Description Length (110-160 chars)",
        "category": "advanced",
        "max": 5,
        "score": 5 if desc_pass else 2,
        "passed": desc_pass,
    })

    # Category 3: AEO Signals (20 pts)
    answer_box = has_answer_box if has_answer_box is not None else any(
        k in c_clean for k in ["key takeaway", "quick answer", "definition", "overview"]
    )
    tests.append({
        "id": "aeo_answer_box",
        "name": "AEO Direct Answer Box",
        "category": "aeo",
        "max": 10,
        "score": 10 if answer_box else 0,
        "passed": answer_box,
    })

    citations = has_citations if has_citations is not None else bool(
        re.search(r"\[.+\]\(https?://.+\)", content) or "doi:" in c_clean or "source:" in c_clean
    )
    tests.append({
        "id": "citations_backing",
        "name": "Evidence Citations",
        "category": "aeo",
        "max": 10,
        "score": 10 if citations else 0,
        "passed": citations,
    })

    # Category 4: Readability (20 pts)
    fre = calculate_flesch_reading_ease(content)
    fre_pass = 50.0 <= fre <= 75.0
    fre_score = 12 if fre_pass else (8 if 40.0 <= fre <= 85.0 else 4)
    tests.append({
        "id": "flesch_reading_ease",
        "name": "Flesch Reading Ease",
        "category": "readability",
        "max": 12,
        "score": fre_score,
        "passed": fre_pass,
    })

    paragraphs = [p for p in content.split("\n\n") if p.strip()]
    long_p = [p for p in paragraphs if len(p.split()) > 100]
    p_pass = len(long_p) == 0
    tests.append({
        "id": "paragraph_scannability",
        "name": "Paragraph Scannability",
        "category": "readability",
        "max": 8,
        "score": 8 if p_pass else 4,
        "passed": p_pass,
    })

    total_score = sum(t["score"] for t in tests)
    grade = "EXCELLENT" if total_score >= 85 else ("GOOD" if total_score >= 70 else ("FAIR" if total_score >= 50 else "POOR"))

    return RankReport(
        score=total_score,
        grade=grade,
        flesch_reading_ease=fre,
        tests=tests,
        recommendations=recs,
    )
