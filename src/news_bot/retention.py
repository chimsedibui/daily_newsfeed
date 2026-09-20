"""Luat giu du lieu: xoa gi, giu gi, sau bao lau.

Vi sao can: moi ngay pipeline ghi ~600 bai, ~40 span, ~40 llm_call va hang tram
dong log. Khong don thi .pgdata phinh khong gioi han tren may dev.

Nguyen tac: `pipeline_run` la goc cua cay trace. Xoa mot run se CASCADE sang
node_span, llm_call, app_log, source_health, article_summary va digest - nen chi
can xoa o day. Bang `article` doc lap (bai co the duoc nhieu run dung chung) nen
xoa rieng, va chi xoa bai khong con ban tin nao tro toi.
"""
from __future__ import annotations

from sqlalchemy import text

from .config import get_settings
from .db.engine import session_scope
from .logging_setup import get_logger

log = get_logger(__name__)

# Xoa run cu -> CASCADE keo theo span, llm_call, app_log, source_health,
# article_summary, digest cua chinh run do.
_DELETE_RUNS = text(
    """
    DELETE FROM pipeline_run
    WHERE started_at < now() - make_interval(days => :days)
    """
)

# Bai cu va khong con ban tin nao tro toi. Bai da tung len ban tin duoc giu lai
# cung ban tin do, vi digest.article_ids tro den chung.
_DELETE_ARTICLES = text(
    """
    DELETE FROM article a
    WHERE COALESCE(a.published_at, a.fetched_at) < now() - make_interval(days => :days)
      AND NOT EXISTS (
          SELECT 1
          FROM digest d
          CROSS JOIN LATERAL jsonb_array_elements_text(d.article_ids) AS x(aid)
          WHERE x.aid::bigint = a.id
      )
    """
)

# Bai mo coi: ban tin tro toi no da bi xoa theo run, nen khong con ai giu nua.
_DELETE_ORPHANS = text(
    """
    DELETE FROM article a
    WHERE COALESCE(a.published_at, a.fetched_at) < now() - make_interval(days => :days)
      AND NOT EXISTS (SELECT 1 FROM article_summary s WHERE s.article_id = a.id)
      AND NOT EXISTS (
          SELECT 1
          FROM digest d
          CROSS JOIN LATERAL jsonb_array_elements_text(d.article_ids) AS x(aid)
          WHERE x.aid::bigint = a.id
      )
    """
)


def db_size() -> dict[str, str]:
    """Kich thuoc tung bang, de thay retention co tac dung that khong."""
    with session_scope() as s:
        rows = s.execute(
            text(
                """
                SELECT relname AS bang,
                       pg_size_pretty(pg_total_relation_size(c.oid)) AS kich_thuoc,
                       pg_total_relation_size(c.oid) AS bytes
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'news' AND c.relkind = 'r'
                ORDER BY pg_total_relation_size(c.oid) DESC
                """
            )
        ).mappings().all()
        total = s.execute(
            text("SELECT pg_size_pretty(sum(pg_total_relation_size(c.oid))) "
                 "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                 "WHERE n.nspname = 'news' AND c.relkind = 'r'")
        ).scalar()
    return {"tables": [dict(r) for r in rows], "total": total}


def purge(days: int | None = None, dry_run: bool = False) -> dict:
    """Xoa du lieu cu hon `days` ngay. Tra ve so dong da xoa tung loai."""
    # `days or default` sai o day: days=0 (xoa tat ca) la gia tri hop le nhung
    # falsy, se bi nuot va am tham quay ve mac dinh 14 ngay.
    days = get_settings().retention_days if days is None else days
    before = db_size()["total"]

    if dry_run:
        with session_scope() as s:
            runs = s.execute(
                text("SELECT count(*) FROM pipeline_run "
                     "WHERE started_at < now() - make_interval(days => :days)"),
                {"days": days},
            ).scalar()
            arts = s.execute(
                text("SELECT count(*) FROM article "
                     "WHERE COALESCE(published_at, fetched_at) < "
                     "now() - make_interval(days => :days)"),
                {"days": days},
            ).scalar()
        log.info("retention.dry_run", days=days, runs=runs, articles=arts)
        return {"dry_run": True, "days": days, "runs": runs,
                "articles_candidate": arts, "size": before}

    with session_scope() as s:
        runs = s.execute(_DELETE_RUNS, {"days": days}).rowcount
        articles = s.execute(_DELETE_ARTICLES, {"days": days}).rowcount
        orphans = s.execute(_DELETE_ORPHANS, {"days": days}).rowcount

    # VACUUM khong chay trong transaction -> AUTOCOMMIT rieng.
    from .db.engine import get_engine

    with get_engine().connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("VACUUM ANALYZE news.article"))
        conn.execute(text("VACUUM ANALYZE news.app_log"))
        conn.execute(text("VACUUM ANALYZE news.node_span"))

    after = db_size()["total"]
    result = {"days": days, "runs_deleted": runs, "articles_deleted": articles,
              "orphans_deleted": orphans, "size_before": before, "size_after": after}
    log.info("retention.done", **result)
    return result
