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

import feedparser
import requests

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
        safe_link = html.escape(link, quote=True)
        lines.append(f'• <a href="{safe_link}">{html.escape(title)}</a>')
    return "\n".join(lines)


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


def main():
    if not TOKEN or not CHAT_ID:
        sys.exit("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID env vars.")

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


if __name__ == "__main__":
    main()
