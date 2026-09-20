"""Parse RSS, news-sitemap va API daily papers cua Hugging Face thanh RawItem."""
from __future__ import annotations

import json
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
                group=source.group,
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
                group=source.group,
                weight=source.weight,
                title=title,
                url=loc.get_text(strip=True),
                published_at=published,
                raw={"strategy": "sitemap"},
            )
        )
    return items


def parse_hf_papers(content: bytes, source: Source) -> list[RawItem]:
    """API daily papers cua Hugging Face.

    Tra ve paper da duoc cong dong upvote, tuc la da loc san - khac han firehose
    arXiv (~500 paper/ngay). Upvote duoc dua vao `weight` de bai nhieu upvote
    len hang cao hon o buoc rank.
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []

    limit = source.max_items or 1000
    items: list[RawItem] = []
    for entry in data[:limit]:
        paper = entry.get("paper") or {}
        arxiv_id = paper.get("id")
        title = (paper.get("title") or entry.get("title") or "").strip()
        if not arxiv_id or not title:
            continue
        upvotes = int(paper.get("upvotes") or 0)
        published = None
        raw_date = entry.get("publishedAt") or paper.get("publishedAt")
        if raw_date:
            try:
                published = dateparser.parse(raw_date)
            except (ValueError, OverflowError):
                published = None
        items.append(
            RawItem(
                source_id=source.id,
                publisher=source.publisher,
                category=source.category,
                group=source.group,
                # 50 upvote tro len duoc coi la dang chu y -> cong toi da 0.5.
                weight=source.weight + min(upvotes, 50) / 100.0,
                title=title,
                url=f"https://huggingface.co/papers/{arxiv_id}",
                lead=(paper.get("summary") or "").strip() or None,
                published_at=published,
                raw={
                    "strategy": "hf_papers",
                    "arxiv_id": arxiv_id,
                    "upvotes": upvotes,
                    "arxiv_url": f"https://arxiv.org/abs/{arxiv_id}",
                },
            )
        )
    return items


def parse_github_repos(content: bytes, source: Source) -> list[RawItem]:
    """GitHub Search API: repo moi tao dang len sao nhanh.

    Day la tin hieu trend that - nguoi ta bam sao vi dung duoc, khong phai vi
    ai do tra tien PR. Khong can token: 60 request/gio cho IP an danh, ta goi
    mot lan moi ngay.
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return []

    limit = source.max_items or 1000
    items: list[RawItem] = []
    for repo in (data.get("items") or [])[:limit]:
        name = repo.get("full_name")
        if not name:
            continue
        stars = int(repo.get("stargazers_count") or 0)
        desc = (repo.get("description") or "").strip()
        lang = repo.get("language") or "khong ro"
        topics = ", ".join((repo.get("topics") or [])[:5])

        published = None
        if repo.get("created_at"):
            try:
                published = dateparser.parse(repo["created_at"])
            except (ValueError, OverflowError):
                published = None

        lead = f"{stars:,} sao · {lang}"
        if topics:
            lead += f" · {topics}"
        if desc:
            lead += "\n" + desc

        items.append(
            RawItem(
                source_id=source.id,
                publisher=source.publisher,
                category=source.category,
                group=source.group,
                # 3.000 sao tro len duoc coi la hien tuong -> cong toi da 0.5.
                weight=source.weight + min(stars, 3000) / 6000.0,
                title=f"{name} - {desc[:90]}" if desc else name,
                url=repo.get("html_url") or f"https://github.com/{name}",
                lead=lead,
                published_at=published,
                raw={"strategy": "github_trending", "stars": stars,
                     "language": lang, "repo": name},
            )
        )
    return items


PARSERS = {
    "rss": parse_rss,
    "sitemap": parse_sitemap,
    "hf_papers": parse_hf_papers,
    "github_trending": parse_github_repos,
}
