"""CLI cho dev: chay tung buoc hoac ca pipeline ma khong can Airflow."""
from __future__ import annotations

import json
from datetime import date

import typer

from . import pipeline
from .config import get_settings, load_sources
from .sources import collect_source
from .utils import today_in

app = typer.Typer(add_completion=False, help="Bot ban tin hang ngay -> Google Chat")


def _today() -> date:
    return today_in(get_settings().news_timezone)


@app.command("init-db")
def init_db() -> None:
    """Tao schema Postgres (idempotent)."""
    result = pipeline.preflight()
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command("check-sources")
def check_sources(lookback: int = typer.Option(24, help="So gio nhin lai")) -> None:
    """Thu tung feed, in ra so bai lay duoc. Khong ghi DB - dung de kiem tra cau hinh."""
    pipeline.bootstrap()
    rows = []
    for src in load_sources():
        res = collect_source(src, lookback_hours=lookback)
        rows.append(
            {
                "id": src.id,
                "ok": res.ok,
                "http": res.http_status,
                "items": len(res.articles),
                "ms": res.latency_ms,
                "age_h": res.newest_item_age_h,
                "stale": res.stale,
                "error": res.error,
            }
        )
        status = "OK " if res.ok else "FAIL"
        typer.echo(f"{status} {src.id:28s} {len(res.articles):3d} bai  {res.latency_ms:5d}ms"
                   f"  {res.error or ''}")
    failed = [r for r in rows if not r["ok"]]
    typer.echo(f"\n{len(rows) - len(failed)}/{len(rows)} nguon OK")
    if failed:
        raise typer.Exit(code=1)


@app.command("ingest")
def ingest(run_id: str = typer.Option(None, help="Dung lai run co san")) -> None:
    """Thu thap toan bo nguon vao Postgres."""
    run_id = run_id or pipeline.start_run(_today(), trigger="cli")
    typer.echo(f"run_id = {run_id}")
    for src in load_sources():
        typer.echo(json.dumps(pipeline.ingest_one(run_id, src.id), ensure_ascii=False))


@app.command("run")
def run_all(
    send: bool = typer.Option(False, "--send", help="Gui that len Google Chat"),
    logical_date: str = typer.Option(None, help="YYYY-MM-DD, mac dinh hom nay"),
) -> None:
    """Chay ca pipeline: ingest -> LangGraph -> (tuy chon) gui."""
    day = date.fromisoformat(logical_date) if logical_date else _today()
    pipeline.preflight()
    run_id = pipeline.start_run(day, trigger="cli")
    typer.echo(f"run_id = {run_id}")

    for src in load_sources():
        res = pipeline.ingest_one(run_id, src.id)
        typer.echo(f"  {src.id:28s} new={res['new']:3d} {res['error'] or ''}")

    result = pipeline.build_digest(run_id, day)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    if send and result.get("digest_id"):
        typer.echo(json.dumps(pipeline.deliver(run_id, result["digest_id"], day),
                              ensure_ascii=False))
    pipeline.finalize(run_id, "success", result.get("metrics"))


@app.command("preview")
def preview(digest_id: int) -> None:
    """In payload Google Chat cua mot ban tin da render (khong gui)."""
    from .db import repository as repo

    pipeline.bootstrap()
    row = repo.get_digest(digest_id)
    if row is None:
        raise typer.BadParameter(f"khong tim thay digest {digest_id}")
    typer.echo(json.dumps(row["payload"], ensure_ascii=False, indent=2))


@app.command("send")
def send_digest(digest_id: int, run_id: str = typer.Option(...)) -> None:
    """Gui lai mot ban tin da render (dung khi webhook hong luc chay that)."""
    pipeline.bootstrap()
    typer.echo(json.dumps(pipeline.deliver(run_id, digest_id, _today()),
                          ensure_ascii=False))


@app.command("config")
def show_config() -> None:
    """In cau hinh hien tai (da che secret)."""
    s = get_settings()
    data = s.model_dump()
    for key in ("anthropic_api_key", "google_chat_webhook_url", "postgres_dsn"):
        if data.get(key):
            data[key] = str(data[key])[:18] + "..."
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    app()
