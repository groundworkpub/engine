import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from opportunity_engine import (
    detect_calculator_gap,
    detect_outdated_data,
    detect_source_to_tool,
    detect_unlinked_mention,
    evaluate_page_opportunity,
    generate_one_sentence_proposition,
    match_best_asset,
    score_opportunity,
)


def test_detect_calculator_gap_positive():
    html = "<p>To manage your savings, you can calculate your emergency fund by multiplying your essential expenses by 6.</p>"
    text = "To manage your savings, you can calculate your emergency fund by multiplying your essential expenses by 6."
    res = detect_calculator_gap(html, text, "https://example.com/savings")
    assert res is not None
    assert res["type"] == "CALCULATOR_GAP"
    assert "calculate your" in res["phrase"].lower()


def test_detect_calculator_gap_negative_when_form_exists():
    html = "<div><form action='/calc'><input type='number' name='amount' /><button>Calculate</button></form></div>"
    text = "You can calculate your monthly mortgage payment here."
    res = detect_calculator_gap(html, text, "https://example.com/calc")
    assert res is None


def test_detect_source_to_tool():
    html = "<p>According to the Bureau of Labor Statistics, the median hourly rate for tech consultants is $65.</p>"
    text = "According to the Bureau of Labor Statistics, the median hourly rate for tech consultants is $65."
    res = detect_source_to_tool(html, text, "https://example.com/salary")
    assert res is not None
    assert res["type"] == "SOURCE_TO_TOOL"
    assert "Bureau of Labor Statistics" in res["agency"]


def test_detect_outdated_data():
    html = "<p>As of 2021 average mortgage rates hovered near 3.5% across US metros.</p>"
    text = "As of 2021 average mortgage rates hovered near 3.5% across US metros."
    res = detect_outdated_data(html, text, "https://example.com/mortgage")
    assert res is not None
    assert res["type"] == "OUTDATED_DATA"
    assert res["year"] == "2021"


def test_detect_unlinked_mention():
    html = "<p>Recent research by Groundwork indicates that solar payback periods have shortened.</p>"
    text = "Recent research by Groundwork indicates that solar payback periods have shortened."
    res = detect_unlinked_mention(html, text, "https://example.com/energy")
    assert res is not None
    assert res["type"] == "UNLINKED_MENTION"
    assert res["entity"] == "Groundwork"


def test_match_best_asset_money():
    text = "If you want to refinance your mortgage, calculate your break-even closing costs and monthly payment."
    asset = match_best_asset(text, pillar_hint="money")
    assert asset["slug"] == "mortgage-refinance"
    assert "Mortgage" in asset["title"]


def test_score_opportunity_thresholds():
    asset = {"title": "Emergency Fund Sizer", "keywords": ["emergency fund"]}
    scores = score_opportunity("CALCULATOR_GAP", asset, "harvard.edu", "Instruction found without form")
    assert scores["total_score"] >= 80.0
    assert scores["intent_score"] == 92.0


def test_generate_one_sentence_proposition():
    asset = {"title": "Solar Panel ROI Engine", "keywords": ["solar"]}
    prop = generate_one_sentence_proposition("CALCULATOR_GAP", asset, "solar payback")
    assert "Groundwork provides that missing interactive step" in prop


def test_evaluate_page_opportunity():
    import asyncio
    sample_html = """
    <html>
      <body>
        <h1>Planning Your Emergency Fund</h1>
        <p>You can calculate your emergency fund target by multiplying your fixed expenses by 3 or 6 months.</p>
        <p>According to the Consumer Financial Protection Bureau, over 40% of households lack $1,000 in savings.</p>
      </body>
    </html>
    """
    opp = asyncio.run(evaluate_page_opportunity(
        url="https://finaid.stanford.edu/savings",
        domain="stanford.edu",
        pillar="money",
        html=sample_html,
    ))
    assert opp is not None
    assert opp.domain == "stanford.edu"
    assert opp.status == "QUALIFIED"
    assert opp.total_score > 75.0
    assert "Elena Vance" in opp.pitch_draft
