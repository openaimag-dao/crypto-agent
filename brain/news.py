"""
МОДУЛЬ 3b. Сборщик крипто-новостей из RSS.

Парсим stdlib-ом (xml.etree) — RSS 2.0 и Atom, без зависимости feedparser.
Отдаём только заголовки + время: классификацию (bullish/bearish) делает LLM
одним батчем в общем анализе, отдельных вызовов на каждую новость нет.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests

from config import HTTP_RETRIES, HTTP_RETRY_DELAY, HTTP_TIMEOUT
from utils.logger import get_logger

log = get_logger("brain.news")

RSS_FEEDS: dict[str, str] = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "CoinTelegraph": "https://cointelegraph.com/rss",
    "TheBlock": "https://www.theblock.co/rss.xml",
}

MAX_ITEMS_PER_FEED = 8
_ATOM_NS = "{http://www.w3.org/2005/Atom}"


@dataclass
class NewsItem:
    source: str
    title: str
    published: str


@dataclass
class NewsSnapshot:
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    items: list[NewsItem] = field(default_factory=list)
    failed_sources: list[str] = field(default_factory=list)

    def to_prompt_lines(self) -> list[str]:
        """Строки для промпта LLM: '- [CoinDesk] Заголовок'."""
        return [f"- [{n.source}] {n.title}" for n in self.items]


def parse_feed_xml(source: str, xml_text: str) -> list[NewsItem]:
    """Парсит RSS 2.0 (<item>) или Atom (<entry>). Выделено для офлайн-тестов."""
    root = ET.fromstring(xml_text)
    items: list[NewsItem] = []

    for node in root.iter("item"):                      # RSS 2.0
        title = (node.findtext("title") or "").strip()
        pub = (node.findtext("pubDate") or "").strip()
        if title:
            items.append(NewsItem(source=source, title=title, published=pub))

    if not items:                                        # Atom fallback
        for node in root.iter(f"{_ATOM_NS}entry"):
            title = (node.findtext(f"{_ATOM_NS}title") or "").strip()
            pub = (node.findtext(f"{_ATOM_NS}updated") or "").strip()
            if title:
                items.append(NewsItem(source=source, title=title, published=pub))

    return items[:MAX_ITEMS_PER_FEED]


def _fetch_feed(source: str, url: str) -> list[NewsItem]:
    last_err = ""
    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            resp = requests.get(url, timeout=HTTP_TIMEOUT,
                                headers={"User-Agent": "Mozilla/5.0 crypto-agent/1.0"})
            resp.raise_for_status()
            return parse_feed_xml(source, resp.text)
        except (requests.RequestException, ET.ParseError) as e:
            last_err = str(e)
            log.warning("[%s] попытка %d/%d: %s", source, attempt, HTTP_RETRIES, e)
            if attempt < HTTP_RETRIES:
                time.sleep(HTTP_RETRY_DELAY)
    log.error("[%s] лента недоступна: %s", source, last_err)
    raise ConnectionError(last_err)


def fetch_news_snapshot() -> NewsSnapshot:
    """Главная функция: обходит все ленты, падение одной не роняет остальные."""
    snap = NewsSnapshot()
    for source, url in RSS_FEEDS.items():
        try:
            snap.items.extend(_fetch_feed(source, url))
        except ConnectionError:
            snap.failed_sources.append(source)
    log.info("Новости собраны: %d шт., отказавшие источники: %s",
             len(snap.items), snap.failed_sources or "нет")
    return snap


if __name__ == "__main__":
    s = fetch_news_snapshot()
    for line in s.to_prompt_lines():
        print(line)
