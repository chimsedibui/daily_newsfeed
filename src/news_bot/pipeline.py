"""Cac buoc cap cao, dung chung cho CLI va Airflow.

Airflow goi tung ham nay lam 1 task; CLI goi tuan tu. Nho vay logic chi ton tai
mot cho, DAG chi con lam nhiem vu dieu phoi.
"""
from __future__ import annotations

import uuid
from datetime import date

from . import groups
from .config import Source, get_settings, load_sources
from .db import init_schema
from .db import repository as repo
from .delivery import build_message, send_message
from .graph import run_digest_graph
from .logging_setup import configure_logging, get_logger
from .sources import collect_source
from .tracing.store import TraceStore
from .weather import build_weather_digest

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
            "digest_sizes": {
                g.key: (getattr(s, f"digest_size_{g.key}", 0) or g.default_size)
                for g in groups.NEWS_GROUPS
            },
            "digest_total_cap": s.digest_total_cap,
            "weather_place": s.weather_place if s.weather_enabled else None,
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
    """Chay LangGraph -> tra ve danh sach digest_id (serious, life)."""
    bootstrap()
    final = run_digest_graph(run_id, logical_date)
    return {
        "digest_ids": final.get("digest_ids", []),
        "metrics": final.get("metrics", {}),
        "skip_reason": final.get("skip_reason"),
    }


def build_weather(run_id: str, logical_date: date) -> dict:
    """Dung ban tin thoi tiet. Doc lap hoan toan voi graph tin tuc.

    Chay rieng de hai ly do: khong co bai tin nao thi van phai co du bao, va
    Open-Meteo hong thi khong duoc keo do ca bo tin tuc.
    """
    bootstrap()
    store = TraceStore(run_id)
    store.attach()

    digest = build_weather_digest(logical_date)
    if digest is None:
        return {"digest_id": None, "skipped": True}

    digest_id = repo.save_digest(
        run_id=run_id,
        group="weather",
        digest_date=logical_date,
        headline=digest.headline,
        overview=digest.overview,
        payload=build_message(digest),
        article_ids=[],
        status="pending",
    )
    log.info("weather.saved", run_id=run_id, digest_id=digest_id)
    return {"digest_id": digest_id, "skipped": False}


def deliver(run_id: str, digest_id: int, logical_date: date) -> dict:
    """Gui mot ban tin da render. Idempotent: da `sent` thi bo qua."""
    bootstrap()
    row = repo.get_digest(digest_id)
    if row is None:
        return {"digest_id": digest_id, "status": "missing"}
    if row["status"] == "sent":
        log.info("deliver.already_sent", digest_id=digest_id)
        return {"digest_id": digest_id, "status": "already_sent"}

    s = get_settings()
    # Cung threadKey cho ca ba ban tin -> chung gom vao mot thread theo ngay.
    thread_key = (
        f"daily-news-{logical_date.isoformat()}" if s.google_chat_thread_per_day else None
    )
    try:
        names = send_message(row["payload"], thread_key=thread_key)
        # O DRY_RUN khong co gi duoc gui di that. Danh dau 'sent' se lam bo loc
        # already_sent_urls() loai vinh vien cac bai nay khoi ban tin ngay mai.
        if s.dry_run:
            repo.mark_digest(digest_id, "skipped")
            return {"digest_id": digest_id, "status": "skipped", "reason": "dry_run"}
        repo.mark_digest(digest_id, "sent", message_name=names[0] if names else None)
        return {"digest_id": digest_id, "group": row["group_key"],
                "status": "sent", "messages": names}
    except Exception as exc:
        repo.mark_digest(digest_id, "failed", error=repr(exc))
        log.error("deliver.failed", digest_id=digest_id, error=repr(exc))
        raise


def deliver_all(run_id: str, logical_date: date) -> dict:
    """Gui moi ban tin `pending` cua run, theo thu tu nhom.

    Doc danh sach tu DB chu khong nhan qua tham so: task nay retry doc lap, va
    khi retry thi ban tin nao da gui roi se khong con o trang thai pending.
    """
    bootstrap()
    store = TraceStore(run_id)
    store.attach()

    rows = repo.pending_digests(run_id)
    if not rows:
        log.info("deliver.nothing_pending", run_id=run_id)
        return {"status": "skipped", "sent": [], "reason": "khong co ban tin pending"}

    sent = []
    for row in rows:
        result = deliver(run_id, row["id"], logical_date)
        sent.append({"group": row["group_key"], **result})

    statuses = {r["status"] for r in sent}
    overall = "sent" if statuses <= {"sent", "already_sent"} else "skipped"
    log.info("deliver.done", run_id=run_id, sent=sent)
    return {"status": overall, "sent": sent}


def finalize(run_id: str, status: str = "success", metrics: dict | None = None,
             error: str | None = None) -> None:
    bootstrap()
    store = TraceStore(run_id)
    store.attach()
    store.finish_run(status=status, metrics=metrics or {}, error=error)
