#!/usr/bin/env python3
"""
EdTech News Monitor
Checks TES, Schools Week, and BBC for articles about EdTech companies
offering teaching and learning tools. Sends matches via Postmark email.
"""

import hashlib
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
POSTMARK_API_TOKEN = os.environ.get("POSTMARK_API_TOKEN", "")
EMAIL_TO = os.environ.get("EMAIL_TO", "gregadam@gmail.com")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "noreply@lbq.org")
SEEN_FILE = Path(os.environ.get("SEEN_FILE", Path(__file__).parent / "seen_articles.json"))

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

REQUEST_TIMEOUT = 30  # seconds

# ---------------------------------------------------------------------------
# EdTech keyword matching
# ---------------------------------------------------------------------------
# We look for articles that mention edtech companies AND teaching/learning tools.
# Two-tier check: the article must match at least one term from EACH list.

EDTECH_COMPANY_SIGNALS = [
    r"\bedtech\b",
    r"\bed[\-\s]?tech\b",
    r"\beducation\s*technology\b",
    r"\blearning\s*platform\b",
    r"\blearning\s*management\s*system\b",
    r"\blms\b",
    r"\bvirtual\s*learning\b",
    r"\bonline\s*learning\b",
    r"\bdigital\s*learning\b",
    r"\bdigital\s*classroom\b",
    r"\bremote\s*learning\b",
    r"\badaptive\s*learning\b",
    r"\bblended\s*learning\b",
    r"\be[\-\s]?learning\b",
    r"\bclassroom\s*technology\b",
    r"\bsparx\b",
    r"\bkognity\b",
    r"\bseneca\s*learning\b",
    r"\btassomai\b",
    r"\boak\s*national\b",
    r"\bcentury\s*tech\b",
    r"\bshow\s*my\s*homework\b",
    r"\bsatchel\s*one\b",
    r"\bgo4schools\b",
    r"\bbromcom\b",
    r"\bclass\s*charts\b",
    r"\beducake\b",
    r"\bhegarty\s*maths\b",
    r"\bmathswatch\b",
    r"\bgcsepod\b",
    r"\bkerboodle\b",
    r"\bmytutor\b",
    r"\bato\s*interactive\b",
    r"\bfrog\s*education\b",
    r"\bfirefly\s*learning\b",
    r"\bbright\s*hr\b",
    r"\barbitr\b",
    r"\bclassdojo\b",
    r"\bkahoot\b",
    r"\bnearpod\b",
    r"\bquizlet\b",
    r"\bsmartboards?\b",
    r"\bpromethean\b",
    r"\btechnology\s*(company|companies|firm|start[\-\s]?up|startup|provider|vendor)\b",
    r"\bai\s*tutor\b",
    r"\bai[\-\s]powered\s*(learning|teaching|classroom|education)\b",
]

TEACHING_LEARNING_SIGNALS = [
    r"\bteaching\b",
    r"\blearning\b",
    r"\bteachers?\b",
    r"\bpupils?\b",
    r"\bstudents?\b",
    r"\bclassroom\b",
    r"\bcurriculum\b",
    r"\bassessment\b",
    r"\bhomework\b",
    r"\blesson\b",
    r"\bschool\b",
    r"\binstruction\b",
    r"\bpedagog\b",
    r"\btutoring\b",
    r"\beducat\b",
]

_edtech_patterns = [re.compile(p, re.IGNORECASE) for p in EDTECH_COMPANY_SIGNALS]
_teaching_patterns = [re.compile(p, re.IGNORECASE) for p in TEACHING_LEARNING_SIGNALS]


def is_edtech_article(title: str, summary: str) -> bool:
    """Return True if the text matches EdTech + teaching/learning signals."""
    text = f"{title} {summary}"
    has_edtech = any(p.search(text) for p in _edtech_patterns)
    has_teaching = any(p.search(text) for p in _teaching_patterns)
    return has_edtech and has_teaching


# ---------------------------------------------------------------------------
# Seen-article tracking (avoid duplicate emails)
# ---------------------------------------------------------------------------
def load_seen() -> set:
    if SEEN_FILE.exists():
        try:
            data = json.loads(SEEN_FILE.read_text())
            return set(data)
        except (json.JSONDecodeError, TypeError):
            return set()
    return set()


def save_seen(seen: set) -> None:
    # Keep only the most recent 5000 entries to prevent unbounded growth
    trimmed = list(seen)[-5000:]
    SEEN_FILE.write_text(json.dumps(trimmed, indent=2))


def article_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------
def fetch(url: str) -> requests.Response | None:
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp
    except requests.RequestException as exc:
        print(f"  [WARN] Failed to fetch {url}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Source: RSS feed parser (works for TES, Schools Week, BBC)
# ---------------------------------------------------------------------------
def parse_rss(url: str) -> list[dict]:
    """Parse an RSS/Atom feed and return list of {title, url, summary}."""
    resp = fetch(url)
    if resp is None:
        return []

    articles = []
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as exc:
        print(f"  [WARN] RSS parse error for {url}: {exc}")
        return []

    # Handle RSS 2.0
    ns = {"atom": "http://www.w3.org/2005/Atom", "media": "http://search.yahoo.com/mrss/"}
    items = root.findall(".//item")
    if items:
        for item in items:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = (item.findtext("description") or "").strip()
            # Strip HTML from description
            if desc:
                desc = BeautifulSoup(desc, "html.parser").get_text(separator=" ").strip()
            if title and link:
                articles.append({"title": title, "url": link, "summary": desc})
        return articles

    # Handle Atom
    entries = root.findall("atom:entry", ns) or root.findall("{http://www.w3.org/2005/Atom}entry")
    for entry in entries:
        title = entry.findtext("{http://www.w3.org/2005/Atom}title", "").strip()
        link_el = entry.find("{http://www.w3.org/2005/Atom}link[@rel='alternate']")
        if link_el is None:
            link_el = entry.find("{http://www.w3.org/2005/Atom}link")
        link = link_el.get("href", "").strip() if link_el is not None else ""
        summary_el = entry.find("{http://www.w3.org/2005/Atom}summary")
        if summary_el is None:
            summary_el = entry.find("{http://www.w3.org/2005/Atom}content")
        desc = (summary_el.text or "").strip() if summary_el is not None else ""
        if desc:
            desc = BeautifulSoup(desc, "html.parser").get_text(separator=" ").strip()
        if title and link:
            articles.append({"title": title, "url": link, "summary": desc})

    return articles


# ---------------------------------------------------------------------------
# Source: HTML scraper fallbacks (if RSS fails)
# ---------------------------------------------------------------------------
def scrape_tes_html() -> list[dict]:
    """Scrape TES news page as fallback."""
    resp = fetch("https://www.tes.com/magazine/news")
    if resp is None:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    articles = []
    for a_tag in soup.select("a[href*='/magazine/']"):
        title = a_tag.get_text(strip=True)
        href = a_tag.get("href", "")
        if not title or len(title) < 15:
            continue
        url = urljoin("https://www.tes.com", href)
        articles.append({"title": title, "url": url, "summary": ""})
    return articles


def scrape_schools_week_html() -> list[dict]:
    """Scrape Schools Week homepage as fallback."""
    resp = fetch("https://schoolsweek.co.uk/")
    if resp is None:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    articles = []
    for a_tag in soup.select("h2 a, h3 a, .entry-title a"):
        title = a_tag.get_text(strip=True)
        href = a_tag.get("href", "")
        if not title or len(title) < 15:
            continue
        articles.append({"title": title, "url": href, "summary": ""})
    return articles


def scrape_bbc_html() -> list[dict]:
    """Scrape BBC News education & technology pages as fallback."""
    articles = []
    for section_url in [
        "https://www.bbc.co.uk/news/education",
        "https://www.bbc.co.uk/news/technology",
    ]:
        resp = fetch(section_url)
        if resp is None:
            continue
        soup = BeautifulSoup(resp.text, "html.parser")
        for a_tag in soup.select("a[href*='/news/']"):
            title = a_tag.get_text(strip=True)
            href = a_tag.get("href", "")
            if not title or len(title) < 20:
                continue
            url = urljoin("https://www.bbc.co.uk", href)
            articles.append({"title": title, "url": url, "summary": ""})
    return articles


# ---------------------------------------------------------------------------
# Fetch all sources
# ---------------------------------------------------------------------------
RSS_SOURCES = [
    ("TES", "https://www.tes.com/magazine/rss"),
    ("Schools Week", "https://schoolsweek.co.uk/feed/"),
    ("BBC Education", "https://feeds.bbci.co.uk/news/education/rss.xml"),
    ("BBC Technology", "https://feeds.bbci.co.uk/news/technology/rss.xml"),
]

HTML_FALLBACKS = {
    "TES": scrape_tes_html,
    "Schools Week": scrape_schools_week_html,
    "BBC Education": scrape_bbc_html,
}


def gather_articles() -> list[dict]:
    """Fetch articles from all sources, de-duplicate, and filter for EdTech."""
    seen = load_seen()
    new_matches = []

    for source_name, rss_url in RSS_SOURCES:
        print(f"Checking {source_name} (RSS)...")
        articles = parse_rss(rss_url)

        # Fall back to HTML scraping if RSS returned nothing
        if not articles and source_name in HTML_FALLBACKS:
            print(f"  RSS empty, falling back to HTML scrape for {source_name}...")
            articles = HTML_FALLBACKS[source_name]()

        print(f"  Found {len(articles)} articles from {source_name}")

        for art in articles:
            aid = article_id(art["url"])
            if aid in seen:
                continue
            if is_edtech_article(art["title"], art["summary"]):
                art["source"] = source_name
                art["_id"] = aid
                new_matches.append(art)
                print(f"  ✓ MATCH: {art['title'][:80]}")

    return new_matches, seen


# ---------------------------------------------------------------------------
# Email via Postmark
# ---------------------------------------------------------------------------
def send_email(articles: list[dict]) -> bool:
    """Send a digest email of matched articles via Postmark."""
    if not POSTMARK_API_TOKEN:
        print("[ERROR] POSTMARK_API_TOKEN not set. Cannot send email.")
        return False

    # Build HTML body
    now = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")

    if articles:
        rows = ""
        for art in articles:
            summary_html = f"<p style='color:#555;margin:4px 0 0 0;'>{art['summary'][:300]}</p>" if art["summary"] else ""
            rows += f"""
        <tr>
          <td style="padding:12px 16px;border-bottom:1px solid #eee;">
            <span style="color:#888;font-size:12px;">{art.get('source', 'Unknown')}</span><br>
            <a href="{art['url']}" style="color:#1a73e8;font-size:15px;font-weight:600;text-decoration:none;">
              {art['title']}
            </a>
            {summary_html}
          </td>
        </tr>"""
        body_content = f"<p style='color:#555;'>Found <strong>{len(articles)}</strong> new article(s) as of {now}.</p><table style='width:100%;border-collapse:collapse;'>{rows}</table>"
        subject = f"EdTech News Alert — {len(articles)} new article(s)"
        text_body = f"EdTech News Alert — {len(articles)} new article(s) as of {now}\n\n"
        for art in articles:
            text_body += f"[{art.get('source', '')}] {art['title']}\n{art['url']}\n"
            if art["summary"]:
                text_body += f"{art['summary'][:200]}\n"
            text_body += "\n"
    else:
        body_content = f"<p style='color:#555;'>No new EdTech articles found as of {now}.</p>"
        subject = "EdTech News Alert — no new articles"
        text_body = f"EdTech News Alert — no new articles as of {now}\n"

    html_body = f"""
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:640px;margin:0 auto;">
      <h2 style="color:#1a1a1a;border-bottom:2px solid #1a73e8;padding-bottom:8px;">
        EdTech News Alert
      </h2>
      {body_content}
      <p style="color:#999;font-size:12px;margin-top:24px;">
        Sources checked: TES, Schools Week, BBC News (Education &amp; Technology)
      </p>
    </div>
    """

    payload = {
        "From": EMAIL_FROM,
        "To": EMAIL_TO,
        "Subject": subject,
        "HtmlBody": html_body,
        "TextBody": text_body,
        "MessageStream": "outbound",
    }

    try:
        resp = requests.post(
            "https://api.postmarkapp.com/email",
            json=payload,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Postmark-Server-Token": POSTMARK_API_TOKEN,
            },
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            print(f"Email sent successfully to {EMAIL_TO}")
            return True
        else:
            print(f"[ERROR] Postmark responded with {resp.status_code}: {resp.text}")
            return False
    except requests.RequestException as exc:
        print(f"[ERROR] Failed to send email: {exc}")
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print(f"=== EdTech News Monitor — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} ===")
    articles, seen = gather_articles()

    if articles:
        print(f"\n{len(articles)} new EdTech article(s) found. Sending email...")
    else:
        print("\nNo new EdTech articles found this run. Sending email anyway...")

    if send_email(articles):
        # Only mark matched articles as seen once they've been successfully emailed
        for art in articles:
            seen.add(art["_id"])
        save_seen(seen)

    print("Done.\n")


if __name__ == "__main__":
    main()
