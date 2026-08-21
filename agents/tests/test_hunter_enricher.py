import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from hunter_enricher import HunterClient, enrich_opportunity_surgically, send_telegram_opportunity_card
from opportunity_engine import Opportunity


def test_hunter_client_offline_mock():
    client = HunterClient(api_key="")
    acc = asyncio.run(client.get_account_info())
    assert acc.get("mock") is True

    contacts = asyncio.run(client.domain_search("example.edu"))
    assert len(contacts) > 0
    assert contacts[0]["email"] == "editorial@example.edu"

    ver = asyncio.run(client.verify_email("editorial@example.edu"))
    assert ver.get("result") == "deliverable"


def test_enrich_opportunity_surgically_qualified():
    opp = Opportunity(
        id="test_opp_123",
        target_url="https://finaid.stanford.edu/resources",
        domain="stanford.edu",
        pillar="money",
        opportunity_type="CALCULATOR_GAP",
        evidence_snippet="Formula found without form",
        matching_asset={"title": "Emergency Fund Sizer", "keywords": ["emergency fund"], "url": "https://gworky.com/tools/emergency-fund-calculator"},
        total_score=88.5,
        status="QUALIFIED",
    )

    mock_client = MagicMock(spec=HunterClient)
    mock_client.domain_search = AsyncMock(return_value=[
        {"first_name": "Sarah", "last_name": "Jenkins", "email": "sjenkins@stanford.edu", "position": "Financial Literacy Director", "confidence": 94, "source": "hunter_domain_search"}
    ])
    mock_client.verify_email = AsyncMock(return_value={"result": "deliverable", "score": 95})

    enriched = asyncio.run(enrich_opportunity_surgically(opp, hunter_client=mock_client))
    assert enriched.target_person == "Sarah Jenkins"
    assert enriched.target_email == "sjenkins@stanford.edu"
    assert enriched.hunter_confidence == 94
    assert "Hi Sarah," in enriched.pitch_draft


def test_enrich_opportunity_surgically_no_go():
    opp = Opportunity(
        id="test_opp_low",
        target_url="https://random.com",
        domain="random.com",
        pillar="general",
        opportunity_type="UNKNOWN",
        evidence_snippet="None",
        matching_asset={"title": "General", "keywords": ["general"], "url": "https://gworky.com"},
        total_score=45.0,
        status="NO_GO",
    )

    mock_client = MagicMock(spec=HunterClient)
    enriched = asyncio.run(enrich_opportunity_surgically(opp, hunter_client=mock_client))
    # Shouldn't make any domain search calls
    mock_client.domain_search.assert_not_called()
    assert enriched.target_email is None


def test_send_telegram_opportunity_card():
    opp = Opportunity(
        id="test_opp_card",
        target_url="https://extension.harvard.edu/resources/",
        domain="harvard.edu",
        pillar="money",
        opportunity_type="CALCULATOR_GAP",
        evidence_snippet="Instructions to calculate mortgage without input",
        matching_asset={"title": "Mortgage Refinance Break-Even Engine", "keywords": ["mortgage"], "url": "https://gworky.com/tools/mortgage-refinance-calculator"},
        total_score=89.0,
        target_person="Elena Editor",
        target_email="editorial@harvard.edu",
        hunter_confidence=92,
        one_sentence_proposition="Groundwork provides the missing calculation step.",
        pitch_draft="Hi Elena, ...",
        status="QUALIFIED",
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "123:ABC", "TELEGRAM_FOUNDER_CHAT_ID": "1790593350"}):
            import hunter_enricher as he
            he.TELEGRAM_BOT_TOKEN = "123:ABC"
            he.TELEGRAM_CHAT_ID = "1790593350"
            res = asyncio.run(send_telegram_opportunity_card(opp))
            assert res is True
            mock_post.assert_called_once()
            call_payload = mock_post.call_args[1]["json"]
            assert "CALCULATOR_GAP" in call_payload["text"]
            assert "approve_opp:test_opp_card" in str(call_payload["reply_markup"])
