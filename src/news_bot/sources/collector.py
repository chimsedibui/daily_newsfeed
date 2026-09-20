"""Thu thap 1 nguon -> Article da chuan hoa; va lam giau fulltext theo lo."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta

from ..config import Source, get_settings
from ..logging_setup import get_logger
from ..models import Article, RawItem
from ..utils import canonical_url, content_hash, ensure_aware, simhash
from .extract import extract_article
from .feeds import PARSERS
from .fetcher import fetch

log = get_logger(__name__)


class CollectResult:
    def __init__(self) -> None:
        self.articles: list[Article] = []
        self.http_status: int | None = None
        self.latency_ms: int = 0
        self.error: str | None = None
        self.newest_item_age_h: int | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def stale(self) -> bool:
        """Feed tra 200 nhung bai moi nhat da qua 7 ngay -> gan nhu chac chan da bi bo."""
        return self.newest_item_age_h is not None and self.newest_item_age_h > 168


def _to_article(item: RawItem) -> Article:
    canon = canonical_url(item.url)
    tz = get_settings().news_timezone
    return Article(
        source_id=item.source_id,
        publisher=item.publisher,
        category=item.category,
        weight=item.weight,
        title=item.title,
        url_canonical=canon,
        url_original=item.url,
        lead=item.lead,
        published_at=ensure_aware(item.published_at, tz),
        image_url=item.image_url,
        content_hash=content_hash(item.title, item.lead),
        simhash=simhash(f"{item.title} {item.lead or ''}"),
        raw=item.raw,
    )


def collect_source(source: Source, lookback_hours: int | None = None) -> CollectResult:
    """Fetch + parse 1 feed. Khong nem exception ra ngoai: mot nguon chet
    khong duoc lam hong ca run - loi duoc ghi vao source_health."""
    s = get_settings()
    lookback_hours = lookback_hours or s.lookback_hours
    cutoff = datetime.now(UTC) - timedelta(hours=lookback_hours)
    result = CollectResult()
    started = time.perf_counter()

    try:
        resp = fetch(source.url, timeout=source.timeout_s, headers=source.headers)
        result.http_status = resp.status_code
        if resp.status_code != 200:
            result.error = f"HTTP {resp.status_code}"
            return result

        parser = PARSERS.get(source.strategy)
        if parser is None:
            result.error = f"strategy khong ho tro: {source.strategy}"
            return result

        items = parser(resp.content, source)
        dated = [i.published_at for i in items if i.published_at]
        if dated:
            newest = max(dated).astimezone(UTC)
            result.newest_item_age_h = int(
                (datetime.now(UTC) - newest).total_seconds() // 3600
            )
        fresh = [
            i for i in items
            if i.published_at is None or i.published_at.astimezone(UTC) >= cutoff
        ]
        fresh = fresh[: s.max_articles_per_source]
        result.articles = [_to_article(i) for i in fresh]
        log.info(
            "source.collected",
            source_id=source.id,
            found=len(items),
            fresh=len(result.articles),
            newest_item_age_h=result.newest_item_age_h,
            stale=result.stale,
        )
    except Exception as exc:
        result.error = repr(exc)
        log.warning("source.failed", source_id=source.id, error=repr(exc))
    finally:
        result.latency_ms = int((time.perf_counter() - started) * 1000)
    return result


def _enrich_one(article: Article) -> Article:
    try:
        resp = fetch(article.url_original)
        if resp.status_code != 200:
            return article
        data = extract_article(resp.text, article.url_original)
        article.title = data.get("title") or article.title
        article.lead = data.get("lead") or article.lead
        article.body = data.get("body") or article.body
        article.author = data.get("author") or article.author
        article.published_at = ensure_aware(
            data.get("published_at"), get_settings().news_timezone
        ) or article.published_at
        article.image_url = data.get("image_url") or article.image_url
        article.lang = data.get("lang") or article.lang
        article.raw = {**article.raw, "extract_kind": data.get("source_kind")}
        article.content_hash = content_hash(article.title, article.lead, article.body)
        article.simhash = simhash(f"{article.title} {article.lead or ''} {article.body or ''}")
    except Exception as exc:
        log.warning("fulltext.failed", url=article.url_canonical, error=repr(exc))
    return article


def enrich_fulltext(articles: list[Article], max_workers: int | None = None) -> list[Article]:
    """Fetch song song trang bai viet. Bai nao fetch loi thi giu nguyen lead tu RSS."""
    if not articles:
        return []
    workers = max_workers or get_settings().http_concurrency
    out: list[Article] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_enrich_one, a): a for a in articles}
        for fut in as_completed(futures):
            out.append(fut.result())
    return out
