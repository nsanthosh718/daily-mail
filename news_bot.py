#!/usr/bin/env python3
"""Daily news bot -> Telegram.

Pulls topic-based headlines from RSS feeds (deduped, last-24h) and sends a
tidy message per topic to a Telegram chat. Designed to run once a day from
GitHub Actions, but works anywhere you can set two env vars.
"""

import os
import sys
import time
import html
from datetime import datetime, timezone, timedelta
from urllib.parse import quote_plus

import argparse
import feedparser
import requests
from bs4 import BeautifulSoup
import re

# ---------------------------------------------------------------------------
# CONFIG  — edit the TOPICS dict to your taste.
# ---------------------------------------------------------------------------

def google_news(query: str) -> str:
    """Google News RSS search — aggregates many outlets for a query.
    `when:1d` limits results to the last day. Change hl/gl/ceid for your
    language/region (e.g. en-GB / GB / GB:en)."""
    return (
        f"https://news.google.com/rss/search?q={quote_plus(query)}"
        "&hl=en-US&gl=US&ceid=US:en"
    )

# Each topic maps to a list of RSS feed URLs. Google News search feeds give you
# "most sources" per topic; the direct outlet feeds add reliable anchors.
TOPICS = {
    "💻 Tech": [
        google_news("technology when:1d"),
        "https://techcrunch.com/feed/",
        "https://feeds.arstechnica.com/arstechnica/index",
    ],
    "💰 Finance & Markets": [
        google_news("finance OR markets OR economy when:1d"),
        "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    ],
    "🌍 World": [
        google_news("world news when:1d"),
        "https://feeds.bbci.co.uk/news/world/rss.xml",
    ],
}

ITEMS_PER_TOPIC = 6      # max headlines shown per topic
MAX_AGE_HOURS = 24       # drop anything older than this

# Weighted keywords for scoring interesting/breaking stories. Increase a
# keyword's weight to make it more likely to trigger an alert. The scoring
# considers both title and article body (title matches count double).
WEIGHTED_KEYWORDS = {
    "breaking": 4,
    "exclusive": 3,
    "resign": 3,
    "attack": 3,
    "dies": 3,
    "death": 3,
    "dead": 3,
    "arrest": 3,
    "crash": 3,
    "explosion": 4,
    "urgent": 3,
    "investigation": 2,
    "lawsuit": 2,
    "bankruptcy": 3,
    "collapse": 3,
    "earthquake": 4,
}

# ---------------------------------------------------------------------------

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
API = f"https://api.telegram.org/bot{TOKEN}/sendMessage"


def parse_entry_time(entry):
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None


def fetch_topic(feeds):
    """Return up to ITEMS_PER_TOPIC recent, deduped (time, title, link) tuples."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
    seen, items = set(), []
    for url in feeds:
        try:
            parsed = feedparser.parse(url)
        except Exception as e:  # never let one bad feed kill the run
            print(f"  ! failed to parse {url}: {e}", file=sys.stderr)
            continue
        for entry in parsed.entries:
            title = (entry.get("title") or "").strip()
            link = (entry.get("link") or "").strip()
            if not title or not link:
                continue
            key = title.lower()
            if key in seen:
                continue
            ts = parse_entry_time(entry)
            if ts and ts < cutoff:
                continue
            seen.add(key)
            items.append((ts or datetime.now(timezone.utc), title, link))
    items.sort(key=lambda x: x[0], reverse=True)  # newest first
    return items[:ITEMS_PER_TOPIC]


def format_topic(name, items):
    if not items:
        return None
    lines = [f"<b>{html.escape(name)}</b>"]
    for _, title, link in items:
        # try to fetch the article and produce a short summary; fall back to link
        summary = summarize_url(link)
        if summary:
            lines.append(f"• {html.escape(title)} — {html.escape(summary)}")
        else:
            safe_link = html.escape(link, quote=True)
            lines.append(f'• <a href="{safe_link}">{html.escape(title)}</a>')
    return "\n".join(lines)


def summarize_url(url: str, max_chars: int = 600, max_sentences: int = 3) -> str:
    """Fetch a URL and return a short naive summary (first few sentences).
    This is a lightweight fallback summarizer that extracts visible text
    from the page and returns the first sentences. It intentionally avoids
    external APIs so the script can run in CI or small servers.
    """
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if not resp.ok:
            return ""
        soup = BeautifulSoup(resp.text, "html.parser")
        article = soup.find("article") or soup.find("main")
        if article:
            paras = [p.get_text(separator=" ") for p in article.find_all("p")]
        else:
            paras = [p.get_text(separator=" ") for p in soup.find_all("p")]
        text = " ".join(p.strip() for p in paras if p and len(p.strip()) > 20)
        if not text:
            text = soup.get_text(separator=" ")
        text = " ".join(text.split())
        if not text:
            return ""
        # naive sentence split
        sents = re.split(r'(?<=[\\.!?])\\s+', text)
        summary = " ".join(sents[:max_sentences]).strip()
        if len(summary) > max_chars:
            summary = summary[:max_chars].rsplit(" ", 1)[0] + "…"
        return summary
    except Exception:
        return ""


def fetch_article_text(url: str, max_chars: int = 20000) -> str:
    """Fetch article text suitable for scoring. Returns a plain text string.
    Falls back to full page text if no article/main element found.
    """
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if not resp.ok:
            return ""
        soup = BeautifulSoup(resp.text, "html.parser")
        article = soup.find("article") or soup.find("main")
        if article:
            paras = [p.get_text(separator=" ") for p in article.find_all("p")]
        else:
            paras = [p.get_text(separator=" ") for p in soup.find_all("p")]
        text = " ".join(p.strip() for p in paras if p and len(p.strip()) > 20)
        if not text:
            text = soup.get_text(separator=" ")
        text = " ".join(text.split())
        if len(text) > max_chars:
            text = text[:max_chars]
        return text
    except Exception:
        return ""


def score_text(title: str, body: str) -> float:
    """Compute a simple weighted score from `title` and `body` using
    WEIGHTED_KEYWORDS. Title matches count double to prioritize headline
    signals.
    """
    t = (title or "").lower()
    b = (body or "").lower()
    score = 0.0
    for kw, w in WEIGHTED_KEYWORDS.items():
        occ_title = t.count(kw)
        occ_body = b.count(kw)
        score += w * (2 * occ_title + occ_body)
    return score


def title_score(title: str) -> float:
    return score_text(title, "")


def split_message(text, limit=4000):
    """Telegram caps messages at 4096 chars; split on line boundaries."""
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


def send(text):
    resp = requests.post(
        API,
        json={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    if not resp.ok:
        print(f"Telegram error {resp.status_code}: {resp.text}", file=sys.stderr)
    resp.raise_for_status()


def is_interesting(text: str) -> bool:
    t = (text or "").lower()
    return any(kw in t for kw in INTERESTING_KEYWORDS)


def format_topic_with_summaries(name, entries):
    """Format a topic when callers already have summaries.
    `entries` should be an iterable of (title, link, summary) tuples.
    """
    if not entries:
        return None
    lines = [f"<b>{html.escape(name)}</b>"]
    for title, link, summary in entries:
        if summary:
            lines.append(f"• {html.escape(title)} — {html.escape(summary)}")
        else:
            safe_link = html.escape(link, quote=True)
            lines.append(f'• <a href="{safe_link}">{html.escape(title)}</a>')
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Daily news bot")
    parser.add_argument(
        "--mode",
        choices=("daily", "interesting"),
        default="daily",
        help="daily: send full digest; interesting: send only when interesting items found",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=float(os.environ.get("INTERESTING_THRESHOLD", "3.0")),
        help="score threshold for interesting mode (higher = fewer alerts)",
    )
    args = parser.parse_args()

    if not TOKEN or not CHAT_ID:
        sys.exit("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID env vars.")

    if args.mode == "daily":
        today = datetime.now(timezone.utc).strftime("%A, %d %B %Y")
        send(f"🗞 <b>Daily News</b> — {today}")

        any_sent = False
        for name, feeds in TOPICS.items():
            print(f"Fetching {name} ...")
            items = fetch_topic(feeds)
            msg = format_topic(name, items)
            if msg:
                for chunk in split_message(msg):
                    send(chunk)
                    time.sleep(1)  # gentle on rate limits
                any_sent = True
            else:
                print(f"  (no recent items for {name})")

        if not any_sent:
            send("No fresh stories in the last 24h. 🤷")

    else:  # interesting mode
        # collect interesting items across topics (title, link, summary)
        messages = []
        for name, feeds in TOPICS.items():
            print(f"Checking {name} for interesting items...")
            items = fetch_topic(feeds)
            selected = []
            for _, title, link in items:
                # cheap title-only check first
                tscore = title_score(title)
                if tscore >= args.threshold:
                    summary = summarize_url(link)
                    selected.append((title, link, summary))
                    continue
                # otherwise fetch article text and compute combined score
                body = fetch_article_text(link)
                cscore = score_text(title, body)
                if cscore >= args.threshold:
                    summary = summarize_url(link)
                    selected.append((title, link, summary))
            if selected:
                msg = format_topic_with_summaries(name, selected)
                if msg:
                    messages.append(msg)

        if messages:
            send("⚠️ <b>Interesting news detected — immediate update</b>")
            for msg in messages:
                for chunk in split_message(msg):
                    send(chunk)
                    time.sleep(1)


if __name__ == "__main__":
    main()
