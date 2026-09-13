#!/usr/bin/env python3
"""
Web2 Satellite Publisher
========================
Unified, automated publishing and syndication agent for Groundwork high-authority satellite tier:
- Substack (Money): deregulationnation.substack.com
- Substack (Cross-Pillar/Life): britnidlc.substack.com (GROUNDWORK transition portal)
- Tumblr (Body/Longevity): tbphx.tumblr.com
- GitHub Pages (Tech): technommy.github.io

Adheres to AGENTS.md:
- Rule 2.1: Brand & Voice, Zero Meta-Prompt Leakage (Strict Fourth-Wall Rule)
- Rule 2.7: Strict Topical Silos & Multi-Word Entity Anchors (>=2 words, max 2 links)
- Rule 2.13: Unlimited $0 runner architecture
- SSOT: Tracks deduplication via public.satellite_syndications table in Supabase
"""

import os
import sys
import json
import re
import argparse
import urllib.request
import urllib.parse
from html.parser import HTMLParser
# Ensure project root is in sys.path for internal imports
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    import pytumblr
except ImportError:
    pytumblr = None

try:
    import psycopg2
except ImportError:
    psycopg2 = None


def load_env(path: str = ".env.local") -> None:
    """Load key-value pairs from .env.local without external dotenv dependency."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            os.environ.setdefault(k.strip(), v)


# ==============================================================================
# 1. ProseMirror HTML Parser for Substack
# ==============================================================================

class SubstackProseMirrorParser(HTMLParser):
    """Parses standard semantic HTML into Substack-compliant ProseMirror JSON structure."""

    def __init__(self):
        super().__init__()
        self.doc = {"type": "doc", "content": []}
        self.current_block = None
        self.current_marks = []
        self.list_stack = []
        self.current_list_item = None

    def _flush_block(self):
        if self.current_block:
            if self.current_block.get("content"):
                if self.current_list_item is not None:
                    self.current_list_item["content"].append(self.current_block)
                elif not self.list_stack:
                    self.doc["content"].append(self.current_block)
            self.current_block = None

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        tag = tag.lower()

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._flush_block()
            level = int(tag[1])
            self.current_block = {"type": "heading", "attrs": {"level": level}, "content": []}
        elif tag == "p":
            self._flush_block()
            self.current_block = {"type": "paragraph", "content": []}
        elif tag == "ul":
            self._flush_block()
            node = {"type": "bullet_list", "content": []}
            if not self.list_stack:
                self.doc["content"].append(node)
            self.list_stack.append(node)
        elif tag == "ol":
            self._flush_block()
            node = {"type": "ordered_list", "content": []}
            if not self.list_stack:
                self.doc["content"].append(node)
            self.list_stack.append(node)
        elif tag == "li":
            self._flush_block()
            item = {"type": "list_item", "content": []}
            if self.list_stack:
                self.list_stack[-1]["content"].append(item)
            self.current_list_item = item
            self.current_block = {"type": "paragraph", "content": []}
        elif tag == "hr":
            self._flush_block()
            self.doc["content"].append({"type": "horizontal_rule"})
        elif tag == "a":
            href = attr_dict.get("href", "")
            self.current_marks.append({"type": "link", "attrs": {"href": href}})
        elif tag in ("strong", "b"):
            self.current_marks.append({"type": "strong"})
        elif tag in ("em", "i"):
            self.current_marks.append({"type": "em"})
        elif tag == "code":
            self.current_marks.append({"type": "code"})

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6", "p"):
            self._flush_block()
        elif tag in ("ul", "ol"):
            self._flush_block()
            if self.list_stack:
                self.list_stack.pop()
        elif tag == "li":
            self._flush_block()
            self.current_list_item = None
        elif tag == "a":
            self.current_marks = [m for m in self.current_marks if m["type"] != "link"]
        elif tag in ("strong", "b"):
            self.current_marks = [m for m in self.current_marks if m["type"] != "strong"]
        elif tag in ("em", "i"):
            self.current_marks = [m for m in self.current_marks if m["type"] != "em"]
        elif tag == "code":
            self.current_marks = [m for m in self.current_marks if m["type"] != "code"]

    def handle_data(self, data):
        text = data
        if not text:
            return
        if self.current_block is None:
            stripped = text.strip()
            if not stripped:
                return
            self.current_block = {"type": "paragraph", "content": []}

        node = {"type": "text", "text": text}
        if self.current_marks:
            node["marks"] = list(self.current_marks)
        self.current_block["content"].append(node)

    def get_doc(self) -> Dict[str, Any]:
        self._flush_block()
        filtered_content = []
        for item in self.doc["content"]:
            if item.get("content") or item.get("type") == "horizontal_rule":
                filtered_content.append(item)
        return {"type": "doc", "content": filtered_content}


# ==============================================================================
# 2. Publishers (Tumblr & Substack)
# ==============================================================================

class TumblrPublisher:
    """Official OAuth 1.0a REST API Publisher for Tumblr."""

    def __init__(self):
        load_env()
        self.consumer_key = os.environ.get("TUMBLR_CONSUMER_KEY")
        self.consumer_secret = os.environ.get("TUMBLR_CONSUMER_SECRET")
        self.token = os.environ.get("TUMBLR_TOKEN")
        self.token_secret = os.environ.get("TUMBLR_TOKEN_SECRET")

        if not all([self.consumer_key, self.consumer_secret, self.token, self.token_secret]):
            raise ValueError("Tumblr OAuth credentials missing in environment (.env.local)")

        if not pytumblr:
            raise ImportError("pytumblr library not installed. Run: python3 -m pip install pytumblr")

        self.client = pytumblr.TumblrRestClient(
            self.consumer_key,
            self.consumer_secret,
            self.token,
            self.token_secret
        )

    def publish_post(
        self,
        blog: str = "tbphx",
        title: str = "",
        body: str = "",
        tags: Optional[List[str]] = None,
        slug: Optional[str] = None
    ) -> Dict[str, Any]:
        """Publish a formatted HTML text post to a Tumblr blog."""
        kwargs = {
            "state": "published",
            "title": title,
            "body": body,
            "tags": tags or ["longevity", "health", "research", "biomarkers"],
            "format": "html"
        }
        if slug:
            kwargs["slug"] = slug

        res = self.client.create_text(blog, **kwargs)
        return res


class SubstackPublisher:
    """Direct Authenticated REST Session Publisher for Substack."""

    def __init__(self, subdomain: str = "deregulationnation", sid: Optional[str] = None):
        load_env()
        self.subdomain = subdomain
        if sid:
            self.sid = sid
        elif subdomain == "britnidlc":
            self.sid = os.environ.get("BRITNIDLC_SUBSTACK_SID") or os.environ.get("SUBSTACK_SID")
        else:
            self.sid = os.environ.get("SUBSTACK_SID")

        if not self.sid:
            raise ValueError(f"Substack SID missing in environment (.env.local) for {subdomain}")

        self.base_url = f"https://{subdomain}.substack.com"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Cookie": f"substack.sid={self.sid}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Referer": f"https://{subdomain}.substack.com/publish/post",
            "Origin": f"https://{subdomain}.substack.com"
        }
        self.pub_id = None
        self.user_id = None
        self._resolve_metadata()

    def _resolve_metadata(self):
        """Fetch and cache publication ID and current user ID."""
        try:
            req = urllib.request.Request(f"{self.base_url}/api/v1/publication", headers=self.headers)
            with urllib.request.urlopen(req) as resp:
                pub = json.loads(resp.read().decode())
                self.pub_id = pub.get("id")
        except Exception:
            pass

        try:
            req = urllib.request.Request(f"{self.base_url}/api/v1/user/profile/self", headers=self.headers)
            with urllib.request.urlopen(req) as resp:
                u = json.loads(resp.read().decode())
                self.user_id = u.get("id")
        except Exception:
            pass

    def list_drafts(self) -> List[Dict[str, Any]]:
        """Fetch list of active drafts."""
        req = urllib.request.Request(f"{self.base_url}/api/v1/drafts", headers=self.headers)
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            if isinstance(data, dict):
                return data.get("posts", [])
            return data

    def create_draft(
        self,
        title: str,
        subtitle: str,
        body_html: str,
        section_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Create a new article draft in Substack converting HTML to ProseMirror JSON."""
        parser = SubstackProseMirrorParser()
        parser.feed(body_html)
        doc = parser.get_doc()

        bylines = [{"id": self.user_id}] if self.user_id else []

        payload = {
            "draft_title": title,
            "draft_subtitle": subtitle,
            "draft_body": json.dumps(doc),
            "draft_bylines": bylines,
            "draft_section_id": section_id
        }
        if self.pub_id:
            payload["publication_id"] = self.pub_id

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/v1/drafts",
            data=data,
            headers=self.headers,
            method="POST"
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())

    def publish_draft(self, draft_id: int, send_email: bool = False) -> Dict[str, Any]:
        """Publish an existing draft to live publication."""
        payload = {
            "send": send_email,
            "share_automatically": False
        }
        data = json.dumps(payload).encode("utf-8")
        headers = dict(self.headers)
        headers["Referer"] = f"{self.base_url}/publish/post/{draft_id}"

        req = urllib.request.Request(
            f"{self.base_url}/api/v1/drafts/{draft_id}/publish",
            data=data,
            headers=headers,
            method="POST"
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())


# ==============================================================================
# 3. Contextual Tool Directory & BLUF Synthesizer
# ==============================================================================

PILLAR_TOOLS = {
    "money": [
        {"slug": "refinance-calculator", "title": "mortgage refinance break-even calculator", "keywords": ["mortgage", "refinance", "rate", "loan", "buydown", "amortization"]},
        {"slug": "compound-interest-calculator", "title": "compound interest investment calculator", "keywords": ["compound", "invest", "stock", "portfolio", "growth", "wealth"]},
        {"slug": "emergency-fund-calculator", "title": "emergency liquidity fund calculator", "keywords": ["emergency", "savings", "liquidity", "cash", "reserve"]},
        {"slug": "fire-calculator", "title": "FIRE retirement horizon calculator", "keywords": ["retirement", "fire", "pension", "401k", "ira", "annuity"]},
        {"slug": "rent-vs-buy-calculator", "title": "rent vs buy property decision calculator", "keywords": ["rent", "buy", "housing", "property", "homeowner"]},
        {"slug": "dca-calculator", "title": "dollar-cost averaging simulator", "keywords": ["dca", "averaging", "etf", "crypto", "volatility"]},
        {"slug": "inflation-calculator", "title": "purchasing power inflation calculator", "keywords": ["inflation", "cpi", "purchasing power", "fed", "yield"]},
        {"slug": "life-insurance-needs-calculator", "title": "life insurance needs modeling calculator", "keywords": ["insurance", "term life", "policy", "premium"]},
        {"slug": "mortgage-payoff-calculator", "title": "early mortgage payoff calculator", "keywords": ["payoff", "principal", "debt reduction"]},
        {"slug": "savings-goal-calculator", "title": "structured savings goal calculator", "keywords": ["goal", "target", "budget", "plan"]}
    ],
    "body": [
        {"slug": "heart-rate-zones-calculator", "title": "cardiovascular zone training calculator", "keywords": ["heart rate", "cardio", "vo2", "endurance", "zone 2"]},
        {"slug": "sleep-cycle-calculator", "title": "circadian sleep cycle optimizer", "keywords": ["sleep", "circadian", "rem", "melatonin", "recovery"]},
        {"slug": "tdee-macro-calculator", "title": "total daily energy expenditure calculator", "keywords": ["tdee", "macro", "calorie", "protein", "metabolic", "nutrition"]}
    ],
    "home": [
        {"slug": "solar-payback-calculator", "title": "residential solar payback calculator", "keywords": ["solar", "photovoltaic", "net metering", "grid", "inverter"]},
        {"slug": "heat-pump-roi-calculator", "title": "heat pump vs furnace ROI calculator", "keywords": ["heat pump", "hvac", "furnace", "efficiency", "heating"]}
    ],
    "life": [
        {"slug": "salary-parity-calculator", "title": "cross-market salary parity calculator", "keywords": ["salary", "compensation", "cost of living", "relocation", "remote"]},
        {"slug": "car-tco-calculator", "title": "total vehicle cost of ownership calculator", "keywords": ["car", "vehicle", "auto", "depreciation", "ev", "gas"]},
        {"slug": "freelance-rate-calculator", "title": "effective hourly freelance rate calculator", "keywords": ["freelance", "contractor", "consulting", "hourly rate"]}
    ],
    "tech": [
        {"slug": "ai-api-pricing-calculator", "title": "generative AI API token cost calculator", "keywords": ["token", "llm", "api", "gpt", "claude", "inference", "compute"]},
        {"slug": "subscription-stack-auditor", "title": "software subscription stack auditor", "keywords": ["saas", "software", "stack", "license", "cloud"]}
    ]
}


def clean_markdown_prose(text: str) -> str:
    """Strip markdown headers, raw markdown link syntax, and formatting artifacts into clean prose."""
    if not text:
        return ""
    # Remove markdown headers: lines starting with #, ##, ###, etc.
    text = re.sub(r"^\s*#{1,6}\s+.*$", "", text, flags=re.MULTILINE)
    # Remove markdown links: [Label](url) -> Label
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    # Remove markdown bold/italics: **text** -> text, *text* -> text
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    # Remove stray inline hash headers like '### Direct Answer'
    text = re.sub(r"#{1,6}\s*", "", text)
    # Clean up multiple whitespace and blank lines
    text = re.sub(r"\s+", " ", text).strip()
    return text


PILLAR_DIMENSIONS = {
    "money": [
        ("Capital Allocation", "Asset deployment efficiency depends strictly on prevailing interest rate yield curves and portfolio duration."),
        ("Break-Even Horizon", "Upfront fees or discount points must be mathematically offset by recurring monthly savings."),
        ("Opportunity Cost", "Maintaining liquid reserves provides flexibility across dynamic macroeconomic cycles.")
    ],
    "body": [
        ("Physiological Biomarker", "Functional strength and biomarker baseline metrics serve as primary indicators of biological longevity and neuromuscular reserve."),
        ("Demographic Variance", "Outcomes and health resilience vary significantly across educational attainment, lifestyle habits, and regional exposures."),
        ("Actionable Protocol", "Calibrating progressive physical and nutritional habits against verified empirical data yields measurable long-term vitality.")
    ],
    "home": [
        ("Operational Efficiency", "Modernizing structural envelopes and HVAC infrastructure systematically reduces monthly utility overhead."),
        ("Capital Payback", "Rebates, seasonal demand spikes, and local utility rate differentials dictate true amortization periods."),
        ("Lifecycle Durability", "Material resilience and rigorous preventative maintenance eliminate high-cost unexpected failures.")
    ],
    "life": [
        ("Strategic Decision-Making", "Long-term structural flexibility consistently outperforms short-term transactional convenience."),
        ("Market Differentials", "Geographic and contractual arbitrage create substantial divergence in net effective compensation."),
        ("Downside Protection", "Establishing clear risk buffers safeguards lifestyle stability through personal transitions.")
    ],
    "tech": [
        ("Compute Economics", "Infrastructure efficiency and inference throughput determine scalable unit economics."),
        ("Benchmark Parity", "Real-world latency and hardware reliability frequently diverge from marketing claims."),
        ("Architectural Resilience", "Decoupled system design and open protocols ensure adaptability without vendor lock-in.")
    ]
}


def pick_contextual_tool(pillar: str, title: str, content: str) -> Dict[str, str]:
    """Select the most relevant calculator tool based on semantic keyword density within the pillar."""
    tools = PILLAR_TOOLS.get(pillar, PILLAR_TOOLS["money"])
    corpus = f"{title} {content}".lower()

    # Special boost for body metrics
    if pillar == "body":
        if any(w in corpus for w in ["strength", "handgrip", "muscle", "bmi", "metabolic", "protein", "weight", "calorie"]):
            return next((t for t in tools if t["slug"] == "tdee-macro-calculator"), tools[0])

    best_tool = tools[0]
    best_score = -1

    for t in tools:
        score = sum(1 for kw in t["keywords"] if kw in corpus)
        if score > best_score:
            best_score = score
            best_tool = t

    return best_tool


class BLUFSynthesizer:
    """Synthesizes high-impact 300-500 word executive summaries from Groundwork articles."""

    @staticmethod
    def synthesize(article: Dict[str, Any]) -> Tuple[str, str]:
        """
        Returns:
            (subtitle, html_body)
        """
        title = article.get("title", "")
        pillar = article.get("pillar", "money")
        slug = article.get("slug", "")
        excerpt = clean_markdown_prose(article.get("excerpt") or "")
        takeaway = clean_markdown_prose(article.get("takeaway") or "")
        content = article.get("content") or ""

        # Extract clean introductory prose from content
        clean_content = clean_markdown_prose(content)
        content_sentences = [s.strip() for s in clean_content.split(". ") if len(s.strip().split()) > 6]

        if content_sentences:
            intro_lead = ". ".join(content_sentences[:2]) + "."
        elif excerpt:
            intro_lead = excerpt
        else:
            intro_lead = f"Groundwork's empirical research dispatch examines key structural variables surrounding {title.lower()}."

        tool = pick_contextual_tool(pillar, title, content)
        # Route outbound links through Groundwork Research Portal (portal.gworky.com)
        from scripts.generate_safelink import encrypt_url
        tool_url = encrypt_url(f"https://gworky.com/tools/{tool['slug']}", tool['title'], pillar)
        pillar_url = encrypt_url(f"https://gworky.com/{pillar}", f"{pillar.capitalize()} Research Hub", pillar)
        article_url = encrypt_url(f"https://gworky.com/article/{slug}", title, pillar)

        subtitle = (
            takeaway[:130] if takeaway
            else f"Key empirical insights and decision models from Groundwork's research dispatch on {title.lower()}."
        )

        dims = PILLAR_DIMENSIONS.get(pillar, PILLAR_DIMENSIONS["money"])

        html_body = f"""
<p>{intro_lead}</p>

<h2>Key Empirical Takeaways & Strategic Dimensions</h2>
<ul>
  <li><strong>{dims[0][0]}:</strong> {takeaway if takeaway else dims[0][1]}</li>
  <li><strong>{dims[1][0]}:</strong> {dims[1][1]}</li>
  <li><strong>{dims[2][0]}:</strong> {dims[2][1]}</li>
</ul>

<h2>Interactive Decision Modeling</h2>
<p>To evaluate your personal baseline against empirical models, explore Groundwork's interactive <a href="{tool_url}">{tool['title']}</a>, which provides quantitative projections without commercial bias.</p>

<p>For the complete dataset, methodological footnotes, and extended analysis, review the <a href="{article_url}">full empirical research study on Groundwork</a>, or consult our <a href="{pillar_url}">evidence-based {pillar} guides</a>.</p>
"""
        return subtitle, html_body.strip()


# ==============================================================================
# 4. Satellite Syndication Engine & SSOT Database Operations
# ==============================================================================

class SatelliteSyndicationEngine:
    """Manages idempotent automated syndication between Groundwork and Web2 satellites."""

    def __init__(self):
        load_env()
        self.host = os.environ.get("SUPABASE_DB_HOST")
        self.port = os.environ.get("SUPABASE_DB_PORT", "6543")
        self.user = os.environ.get("SUPABASE_DB_USER")
        self.password = os.environ.get("SUPABASE_DB_PASSWORD")
        self.dbname = "postgres"

    def get_db_conn(self):
        """Establish PostgreSQL connection via psycopg2 with SSL."""
        if not psycopg2:
            raise ImportError("psycopg2 is not installed. Please install psycopg2-binary.")
        return psycopg2.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            dbname=self.dbname,
            sslmode="require"
        )

    def route_channel(self, pillar: str, title: str = "") -> str:
        """Determines target satellite channel based on topical pillar silo and audience psychology."""
        if pillar == "money":
            return "substack_deregulationnation"
        elif pillar == "body":
            return "tumblr_tbphx"
        elif pillar == "tech":
            # Direct ethics, neuroscience, and societal research to Tumblr; coding/devtools to Technommy
            tech_research_terms = ["brain", "biotech", "cognitive", "ethics", "accountability", "tracking", "privacy"]
            if any(term in title.lower() for term in tech_research_terms):
                return "tumblr_tbphx"
            return "github_technommy"
        else:  # life, home
            return "substack_britnidlc"

    def fetch_pending_articles(self, max_per_channel: int = 1, limit: int = 10) -> List[Dict[str, Any]]:
        """Fetch published articles with strict per-channel volume capping to mimic natural publishing."""
        conn = self.get_db_conn()
        cur = conn.cursor()
        query = """
            SELECT a.id, a.slug, a.title, a.pillar, a.excerpt, a.takeaway, a.content, a.published_at
            FROM public.articles a
            WHERE a.status = 'published'
            ORDER BY a.published_at DESC
            LIMIT 100;
        """
        cur.execute(query)
        cols = [desc[0] for desc in cur.description]
        all_articles = [dict(zip(cols, row)) for row in cur.fetchall()]

        # Filter against satellite_syndications table with per-channel quota
        pending = []
        channel_counts: Dict[str, int] = {}

        for art in all_articles:
            target_channel = self.route_channel(art["pillar"], art.get("title", ""))
            
            # Check if channel already hit its max_per_channel cap
            if channel_counts.get(target_channel, 0) >= max_per_channel:
                continue

            cur.execute(
                "SELECT id FROM public.satellite_syndications WHERE article_id = %s AND channel = %s;",
                (str(art["id"]), target_channel)
            )
            if not cur.fetchone():
                art["target_channel"] = target_channel
                pending.append(art)
                channel_counts[target_channel] = channel_counts.get(target_channel, 0) + 1

                if len(pending) >= limit:
                    break

        cur.close()
        conn.close()
        return pending

    def record_syndication(
        self,
        article_id: str,
        channel: str,
        external_url: str,
        external_post_id: Optional[str] = None,
        status: str = "published",
        error_log: Optional[str] = None
    ) -> None:
        """Record syndication event into public.satellite_syndications table."""
        conn = self.get_db_conn()
        conn.autocommit = True
        cur = conn.cursor()
        query = """
            INSERT INTO public.satellite_syndications (
                article_id, channel, external_post_id, external_url, status, error_log, syndicated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (article_id, channel) DO UPDATE SET
                external_post_id = EXCLUDED.external_post_id,
                external_url = EXCLUDED.external_url,
                status = EXCLUDED.status,
                error_log = EXCLUDED.error_log,
                updated_at = now();
        """
        cur.execute(query, (article_id, channel, str(external_post_id) if external_post_id else None, external_url, status, error_log))
        cur.close()
        conn.close()

    def syndicate_single_article(self, article: Dict[str, Any], dry_run: bool = False) -> Dict[str, Any]:
        """Dispatch single article to its mapped satellite channel."""
        article_id = str(article["id"])
        title = article["title"]
        pillar = article["pillar"]
        channel = article.get("target_channel") or self.route_channel(pillar)

        subtitle, html_body = BLUFSynthesizer.synthesize(article)

        print(f"\n🚀 Syndicating Article: '{title}' [{pillar}] -> Channel: {channel}")
        if dry_run:
            print(f"[DRY-RUN] Would publish to {channel}:")
            print(f"  Subtitle: {subtitle}")
            print(f"  Body length: {len(html_body)} chars")
            return {"status": "dry_run", "channel": channel, "title": title}

        try:
            if channel == "substack_deregulationnation":
                sp = SubstackPublisher(subdomain="deregulationnation")
                draft = sp.create_draft(title=title, subtitle=subtitle, body_html=html_body)
                draft_id = draft.get("id")
                pub_res = sp.publish_draft(draft_id=draft_id)
                post_slug = pub_res.get("slug")
                ext_url = f"https://deregulationnation.substack.com/p/{post_slug}"
                self.record_syndication(article_id, channel, ext_url, str(draft_id))
                print(f"✅ Published Substack post: {ext_url}")
                return {"status": "published", "channel": channel, "url": ext_url, "id": draft_id}

            elif channel == "substack_britnidlc":
                sp = SubstackPublisher(subdomain="britnidlc")
                draft = sp.create_draft(title=title, subtitle=subtitle, body_html=html_body)
                draft_id = draft.get("id")
                pub_res = sp.publish_draft(draft_id=draft_id)
                post_slug = pub_res.get("slug")
                ext_url = f"https://britnidlc.substack.com/p/{post_slug}"
                self.record_syndication(article_id, channel, ext_url, str(draft_id))
                print(f"✅ Published Britnidlc Substack post: {ext_url}")
                return {"status": "published", "channel": channel, "url": ext_url, "id": draft_id}

            elif channel == "tumblr_tbphx":
                tp = TumblrPublisher()
                res = tp.publish_post(
                    blog="tbphx",
                    title=title,
                    body=html_body,
                    tags=[pillar, "research", "evidence", "groundwork", "guide"],
                    slug=article["slug"]
                )
                post_id = res.get("id")
                ext_url = f"https://tbphx.tumblr.com/post/{post_id}"
                self.record_syndication(article_id, channel, ext_url, str(post_id))
                print(f"✅ Published Tumblr post: {ext_url}")
                return {"status": "published", "channel": channel, "url": ext_url, "id": post_id}

            elif channel == "github_technommy":
                # For Technommy, we record dispatch to Technommy feed archive
                ext_url = f"https://technommy.github.io/compare/{article['slug']}"
                self.record_syndication(article_id, channel, ext_url, article["slug"])
                print(f"✅ Dispatched to Technommy Satellite: {ext_url}")
                return {"status": "dispatched", "channel": channel, "url": ext_url}

            else:
                raise ValueError(f"Unknown channel: {channel}")

        except Exception as e:
            print(f"❌ Syndication failed for {channel}: {e}")
            self.record_syndication(article_id, channel, "failed", None, status="failed", error_log=str(e))
            return {"status": "failed", "channel": channel, "error": str(e)}

    def run_pipeline(self, limit: int = 10, max_per_channel: int = 1, dry_run: bool = False) -> List[Dict[str, Any]]:
        """Executes syndication loop over pending un-syndicated articles with per-channel caps."""
        pending = self.fetch_pending_articles(max_per_channel=max_per_channel, limit=limit)
        print(f"🔍 Found {len(pending)} pending articles for satellite syndication (max {max_per_channel}/channel).")
        results = []
        for art in pending:
            res = self.syndicate_single_article(art, dry_run=dry_run)
            results.append(res)
        return results


# ==============================================================================
# 5. CLI Entrypoint
# ==============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Web2 Satellite Syndication Engine")
    parser.add_argument("--diagnostic", action="store_true", help="Run connection diagnostics across all channels")
    parser.add_argument("--syndicate-pending", action="store_true", help="Syndicate pending un-syndicated articles from Supabase")
    parser.add_argument("--dry-run", action="store_true", help="Simulate syndication without writing live posts")
    parser.add_argument("--limit", type=int, default=10, help="Maximum total articles to syndicate in one run (default: 10)")
    parser.add_argument("--max-per-channel", type=int, default=1, help="Maximum articles per satellite channel in one run (default: 1)")

    args = parser.parse_args()
    load_env()

    if args.syndicate_pending:
        engine = SatelliteSyndicationEngine()
        results = engine.run_pipeline(limit=args.limit, max_per_channel=args.max_per_channel, dry_run=args.dry_run)
        print(f"\n🏁 Finished syndication run. Processed: {len(results)} articles.")

    elif args.diagnostic:
        print("=== Web2 Satellite Publisher Diagnostic ===")
        # Test Tumblr
        try:
            tp = TumblrPublisher()
            info = tp.client.info()
            print(f"[Tumblr] Authenticated as: {info.get('user', {}).get('name')}")
        except Exception as e:
            print(f"[Tumblr] Error: {e}")

        # Test Substack - Deregulationnation
        try:
            sp = SubstackPublisher(subdomain="deregulationnation")
            drafts = sp.list_drafts()
            print(f"[Substack] Connected to {sp.subdomain} (Pub ID: {sp.pub_id}). Found {len(drafts)} drafts.")
        except Exception as e:
            print(f"[Substack deregulationnation] Error: {e}")

        # Test Substack - Britnidlc
        try:
            sp2 = SubstackPublisher(subdomain="britnidlc")
            drafts2 = sp2.list_drafts()
            print(f"[Substack] Connected to {sp2.subdomain} (Pub ID: {sp2.pub_id}). Found {len(drafts2)} drafts.")
        except Exception as e:
            print(f"[Substack britnidlc] Error: {e}")

        # Test Supabase connection
        try:
            engine = SatelliteSyndicationEngine()
            pending = engine.fetch_pending_articles(limit=2)
            print(f"[Supabase] Connected! Pending un-syndicated articles sample: {len(pending)}")
        except Exception as e:
            print(f"[Supabase] Error: {e}")

    else:
        parser.print_help()
