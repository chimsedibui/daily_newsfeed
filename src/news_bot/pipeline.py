"""Cac buoc cap cao, dung chung cho CLI va Airflow.

Airflow goi tung ham nay lam 1 task; CLI goi tuan tu. Nho vay logic chi ton tai
mot cho, DAG chi con lam nhiem vu dieu phoi.
"""
from __future__ import annotations

import uuid
from datetime import date

from .config import Source, get_settings, load_sources
from .db import init_schema
from .db import repository as repo
from .delivery import send_message
from .graph import run_digest_graph
from .logging_setup import configure_logging, get_logger
from .sources import collect_source
from .tracing.store import TraceStore

log = get_logger(__name__)


def bootstrap() -> None:
    s = get_settings()
    configure_logging(s.log_level, s.log_json)


def preflight() -> dict:
    """Tao schema + kiem tra cau hinh. An toan khi chay lai."""
    bootstrap()
    init_schema()
    sources = load_sources()
    s = get_settings()
    problems = []
    if not s.dry_run and not s.google_chat_webhook_url:
        problems.append("thieu GOOGLE_CHAT_WEBHOOK_URL")
    if not s.dry_run and not s.openai_api_key:
        problems.append("thieu OPENAI_API_KEY")
    log.info("preflight.done", sources=len(sources), problems=problems)
    return {"sources": [x.id for x in sources], "problems": problems}


def start_run(logical_date: date, trigger: str = "manual",
              dag_run_id: str | None = None) -> str:
    bootstrap()
    s = get_settings()
    store = TraceStore(str(uuid.uuid4()))
    store.start_run(
        logical_date=logical_date,
        trigger=trigger,
        dag_run_id=dag_run_id,
        config_snapshot={
            "lookback_hours": s.lookback_hours,
            "digest_size": s.digest_size,
            "summarizer_model": s.summarizer_model,
            "editor_model": s.editor_model,
            "dry_run": s.dry_run,
        },
    )
    return store.run_id


def ingest_one(run_id: str, source_id: str) -> dict:
    """Thu thap 1 nguon. Khong raise khi nguon loi - ghi source_health roi tra ve."""
    bootstrap()
    store = TraceStore(run_id)
    store.attach()
    source: Source = next(x for x in load_sources() if x.id == source_id)

    result = collect_source(source)
    total = new = 0
    if result.articles:
        total, new = repo.upsert_articles(result.articles)

    repo.record_source_health(
        run_id=run_id,
        source_id=source_id,
        ok=result.ok,
        http_status=result.http_status,
        items_found=len(result.articles),
        items_new=new,
        latency_ms=result.latency_ms,
        newest_item_age_h=result.newest_item_age_h,
        error=result.error,
    )
    if result.stale:
        log.warning("source.stale", source_id=source_id,
                    newest_item_age_h=result.newest_item_age_h)
    return {"source_id": source_id, "ok": result.ok, "stale": result.stale,
            "found": total, "new": new,
            "newest_item_age_h": result.newest_item_age_h, "error": result.error}


def build_digest(run_id: str, logical_date: date) -> dict:
    """Chay LangGraph -> tra ve digest_id (hoac None neu khong co gi de gui)."""
    bootstrap()
    final = run_digest_graph(run_id, logical_date)
    return {
        "digest_id": final.get("digest_id"),
        "metrics": final.get("metrics", {}),
        "skip_reason": final.get("skip_reason"),
    }


def deliver(run_id: str, digest_id: int | None, logical_date: date) -> dict:
    """Gui ban tin da render. Idempotent: da `sent` thi bo qua."""
    bootstrap()
    store = TraceStore(run_id)
    store.attach()
    if digest_id is None:
        log.info("deliver.skipped", run_id=run_id, reason="khong co digest")
        return {"status": "skipped"}

    row = repo.get_digest(digest_id)
    if row is None:
        return {"status": "missing"}
    if row["status"] == "sent":
        log.info("deliver.already_sent", digest_id=digest_id)
        return {"status": "already_sent"}

    s = get_settings()
    thread_key = f"daily-news-{logical_date.isoformat()}" if s.google_chat_thread_per_day else None
    try:
        names = send_message(row["payload"], thread_key=thread_key)
        # O DRY_RUN khong co gi duoc gui di that. Danh dau 'sent' se lam bo loc
        # already_sent_urls() loai vinh vien cac bai nay khoi ban tin ngay mai.
        if s.dry_run:
            repo.mark_digest(digest_id, "skipped")
            return {"status": "skipped", "reason": "dry_run"}
        repo.mark_digest(digest_id, "sent", message_name=names[0] if names else None)
        return {"status": "sent", "messages": names}
    except Exception as exc:
        repo.mark_digest(digest_id, "failed", error=repr(exc))
        log.error("deliver.failed", digest_id=digest_id, error=repr(exc))
        raise


def finalize(run_id: str, status: str = "success", metrics: dict | None = None,
             error: str | None = None) -> None:
    bootstrap()
    store = TraceStore(run_id)
    store.attach()
    store.finish_run(status=status, metrics=metrics or {}, error=error)
