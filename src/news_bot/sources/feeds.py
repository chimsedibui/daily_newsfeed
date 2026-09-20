"""Parse RSS va news-sitemap thanh RawItem."""
from __future__ import annotations

import re
from datetime import datetime

import feedparser
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

from ..config import Source
from ..models import RawItem

_IMG_IN_DESC = re.compile(r'<img[^>]+src="([^"]+)"', re.I)


def _strip_html(value: str | None) -> str | None:
    if not value:
        return None
    text = BeautifulSoup(value, "lxml").get_text(" ", strip=True)
    return text or None


def _entry_dt(entry) -> datetime | None:
    for key in ("published", "updated", "created"):
        raw = entry.get(key)
        if raw:
            try:
                # tz naive duoc xu ly o collector (ensure_aware) theo mui gio
                # nghiep vu, khong ep UTC o day.
                return dateparser.parse(raw)
            except (ValueError, OverflowError, TypeError):
                continue
    return None


def _entry_image(entry) -> str | None:
    # media:content / media:thumbnail (Tuoi Tre, Thanh Nien) ...
    for key in ("media_content", "media_thumbnail"):
        media = entry.get(key)
        if media and isinstance(media, list) and media[0].get("url"):
            return media[0]["url"]
    # ... hoac <img> nhet trong <description> (VnExpress, Dan Tri)
    match = _IMG_IN_DESC.search(entry.get("summary", "") or "")
    return match.group(1) if match else None


def parse_rss(content: bytes, source: Source) -> list[RawItem]:
    feed = feedparser.parse(content)
    limit = source.max_items or 1000
    items: list[RawItem] = []
    for entry in feed.entries[:limit]:
        link = (entry.get("link") or "").strip()
        title = _strip_html(entry.get("title"))
        if not link or not title:
            continue
        items.append(
            RawItem(
                source_id=source.id,
                publisher=source.publisher,
                category=source.category,
                weight=source.weight,
                title=title,
                url=link,
                lead=_strip_html(entry.get("summary")),
                published_at=_entry_dt(entry),
                image_url=_entry_image(entry),
                raw={"feed_id": entry.get("id"), "strategy": "rss"},
            )
        )
    return items


def parse_sitemap(content: bytes, source: Source) -> list[RawItem]:
    """Google News sitemap: <url><loc> + <news:news><news:title>/<news:publication_date>."""
    soup = BeautifulSoup(content, "xml")
    limit = source.max_items or 1000
    items: list[RawItem] = []
    for url_tag in soup.find_all("url")[:limit]:
        loc = url_tag.find("loc")
        if not loc:
            continue
        news = url_tag.find("news")
        title_tag = url_tag.find("title") if news else None
        date_tag = url_tag.find("publication_date") if news else None
        title = (title_tag.get_text(strip=True) if title_tag else None) or loc.get_text(
            strip=True
        )
        published = None
        if date_tag:
            try:
                published = dateparser.parse(date_tag.get_text(strip=True))
            except (ValueError, OverflowError):
                published = None
        items.append(
            RawItem(
                source_id=source.id,
                publisher=source.publisher,
                category=source.category,
                weight=source.weight,
                title=title,
                url=loc.get_text(strip=True),
                published_at=published,
                raw={"strategy": "sitemap"},
            )
        )
    return items


PARSERS = {"rss": parse_rss, "sitemap": parse_sitemap}
