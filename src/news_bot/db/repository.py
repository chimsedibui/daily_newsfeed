"""Truy cap du lieu bang SQL Core - du dung, de doc, khong can ORM mapping."""
from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import text

from ..config import get_settings
from ..models import Article, Summary
from ..utils import to_signed_64, today_in
from .engine import session_scope

# xmax = 0 => hàng vừa được INSERT (không phải UPDATE) -> đếm được bài thực sự mới.
_UPSERT_ARTICLE = text(
    """
    INSERT INTO article (url_canonical, url_original, source_id, publisher, category, title,
                         lead, body, author, lang, published_at, content_hash, simhash,
                         image_url, raw)
    VALUES (:url_canonical, :url_original, :source_id, :publisher, :category, :title,
            :lead, :body, :author, :lang, :published_at, :content_hash, :simhash,
            :image_url, CAST(:raw AS jsonb))
    ON CONFLICT (url_canonical) DO UPDATE
    SET title = EXCLUDED.title,
        lead  = COALESCE(EXCLUDED.lead, article.lead),
        body  = COALESCE(EXCLUDED.body, article.body),
        raw   = article.raw || EXCLUDED.raw
    RETURNING id, (xmax = 0) AS inserted
    """
)


def upsert_articles(articles: Iterable[Article]) -> tuple[int, int]:
    """Ghi bai vao store. Tra ve (tong so ghi, so bai thuc su moi)."""
    total = new = 0
    with session_scope() as s:
        for a in articles:
            row = s.execute(
                _UPSERT_ARTICLE,
                {
                    "url_canonical": a.url_canonical,
                    "url_original": a.url_original,
                    "source_id": a.source_id,
                    "publisher": a.publisher,
                    "category": a.category,
                    "title": a.title,
                    "lead": a.lead,
                    "body": a.body,
                    "author": a.author,
                    "lang": a.lang,
                    "published_at": a.published_at,
                    "content_hash": a.content_hash,
                    "simhash": to_signed_64(a.simhash),
                    "image_url": a.image_url,
                    "raw": json.dumps(a.raw, ensure_ascii=False, default=str),
                },
            ).one()
            a.id = row.id
            total += 1
            new += 1 if row.inserted else 0
    return total, new


_SELECT_WINDOW = text(
    """
    SELECT id, url_canonical, url_original, source_id, publisher, category, title,
           lead, body, author, lang, published_at, content_hash, simhash, image_url, raw
    FROM article
    WHERE COALESCE(published_at, fetched_at) >= :since
    ORDER BY COALESCE(published_at, fetched_at) DESC
    LIMIT :limit
    """
)


def fetch_window(lookback_hours: int, limit: int = 500) -> list[Article]:
    """Bai trong cua so thoi gian - day la input cua LangGraph."""
    since = datetime.now(UTC) - timedelta(hours=lookback_hours)
    with session_scope() as s:
        rows = s.execute(_SELECT_WINDOW, {"since": since, "limit": limit}).mappings().all()
    return [
        Article(
            id=r["id"],
            url_canonical=r["url_canonical"],
            url_original=r["url_original"],
            source_id=r["source_id"],
            publisher=r["publisher"],
            category=r["category"],
            group=(r["raw"] or {}).get("group", "serious"),
            title=r["title"],
            lead=r["lead"],
            body=r["body"],
            author=r["author"],
            lang=r["lang"] or "vi",
            published_at=r["published_at"],
            content_hash=r["content_hash"],
            simhash=r["simhash"] or 0,
            image_url=r["image_url"],
            raw=r["raw"] or {},
        )
        for r in rows
    ]


_SELECT_SENT = text(
    """
    SELECT DISTINCT a.url_canonical
    FROM digest d
    CROSS JOIN LATERAL jsonb_array_elements_text(d.article_ids) AS x(aid)
    JOIN article a ON a.id = x.aid::bigint
    WHERE d.status = 'sent' AND d.digest_date >= :since
    """
)


def already_sent_urls(days: int = 7) -> set[str]:
    """URL da len ban tin gan day -> khong lap lai trong digest hom nay."""
    with session_scope() as s:
        rows = s.execute(_SELECT_SENT, {"since": today_in(get_settings().news_timezone) - timedelta(days=days)})
        return set(rows.scalars().all())


_UPSERT_SUMMARY = text(
    """
    INSERT INTO article_summary (article_id, run_id, model, summary, bullets, topics, importance)
    VALUES (:article_id, :run_id, :model, :summary,
            CAST(:bullets AS jsonb), CAST(:topics AS jsonb), :importance)
    ON CONFLICT (article_id, run_id) DO UPDATE
    SET summary = EXCLUDED.summary, bullets = EXCLUDED.bullets,
        topics = EXCLUDED.topics, importance = EXCLUDED.importance
    """
)


def save_summaries(run_id: str, summaries: Iterable[Summary]) -> None:
    with session_scope() as s:
        for x in summaries:
            if x.article_id is None:
                continue
            s.execute(
                _UPSERT_SUMMARY,
                {
                    "article_id": x.article_id,
                    "run_id": run_id,
                    "model": x.model,
                    "summary": x.summary,
                    "bullets": json.dumps(x.bullets, ensure_ascii=False),
                    "topics": json.dumps(x.topics, ensure_ascii=False),
                    "importance": x.importance,
                },
            )


_INSERT_DIGEST = text(
    """
    INSERT INTO digest (run_id, group_key, digest_date, headline, overview, payload,
                        article_ids, status)
    VALUES (:run_id, :group_key, :digest_date, :headline, :overview,
            CAST(:payload AS jsonb), CAST(:article_ids AS jsonb), :status)
    RETURNING id
    """
)


def save_digest(
    run_id: str,
    group: str,
    digest_date: date,
    headline: str,
    overview: str,
    payload: dict,
    article_ids: list[int],
    status: str = "pending",
) -> int:
    with session_scope() as s:
        return s.execute(
            _INSERT_DIGEST,
            {
                "run_id": run_id,
                "group_key": group,
                "digest_date": digest_date,
                "headline": headline,
                "overview": overview,
                "payload": json.dumps(payload, ensure_ascii=False),
                "article_ids": json.dumps(article_ids),
                "status": status,
            },
        ).scalar_one()


def get_digest(digest_id: int) -> dict | None:
    with session_scope() as s:
        row = s.execute(
            text("SELECT id, run_id, group_key, digest_date, headline, payload, status "
                 "FROM digest WHERE id = :id"),
            {"id": digest_id},
        ).mappings().first()
    return dict(row) if row else None


def pending_digests(run_id: str) -> list[dict]:
    """Cac ban tin cua run chua gui, theo dung thu tu nhom.

    Thu tu gui do DB quyet dinh chu khong do thu tu goi ham: task `deliver` cua
    Airflow co the retry doc lap, luc do state trong bo nho da mat.
    """
    with session_scope() as s:
        rows = s.execute(
            text(
                """
                SELECT id, run_id, group_key, digest_date, headline, payload, status
                FROM digest
                WHERE run_id = :run_id AND status = 'pending'
                ORDER BY CASE group_key
                             WHEN 'serious' THEN 1
                             WHEN 'life'    THEN 2
                             WHEN 'weather' THEN 3
                             ELSE 9
                         END, id
                """
            ),
            {"run_id": run_id},
        ).mappings().all()
    return [dict(r) for r in rows]


_MARK_DIGEST = text(
    """
    UPDATE digest
    SET status = :status,
        sent_at = CASE WHEN :status = 'sent' THEN now() ELSE sent_at END,
        message_name = COALESCE(:message_name, message_name),
        error = :error
    WHERE id = :id
    """
)


def mark_digest(
    digest_id: int,
    status: str,
    message_name: str | None = None,
    error: str | None = None,
) -> None:
    with session_scope() as s:
        s.execute(
            _MARK_DIGEST,
            {"id": digest_id, "status": status, "message_name": message_name, "error": error},
        )


_INSERT_HEALTH = text(
    """
    INSERT INTO source_health (run_id, source_id, ok, http_status, items_found,
                               items_new, latency_ms, newest_item_age_h, error)
    VALUES (:run_id, :source_id, :ok, :http_status, :items_found,
            :items_new, :latency_ms, :newest_item_age_h, :error)
    """
)


def record_source_health(
    run_id: str,
    source_id: str,
    ok: bool,
    http_status: int | None,
    items_found: int,
    items_new: int,
    latency_ms: int,
    newest_item_age_h: int | None = None,
    error: str | None = None,
) -> None:
    with session_scope() as s:
        s.execute(
            _INSERT_HEALTH,
            {
                "run_id": run_id,
                "source_id": source_id,
                "ok": ok,
                "http_status": http_status,
                "items_found": items_found,
                "items_new": items_new,
                "latency_ms": latency_ms,
                "newest_item_age_h": newest_item_age_h,
                "error": error,
            },
        )
