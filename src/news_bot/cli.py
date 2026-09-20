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
        status = "FAIL " if not res.ok else ("STALE" if res.stale else "OK   ")
        age = f"{res.newest_item_age_h}h" if res.newest_item_age_h is not None else "-"
        typer.echo(f"{status} {src.id:26s} {len(res.articles):3d} bai  {res.latency_ms:5d}ms"
                   f"  moi nhat: {age:>6s}  {res.error or ''}")

    failed = [r["id"] for r in rows if not r["ok"]]
    stale = [r["id"] for r in rows if r["stale"]]
    typer.echo(f"\n{len(rows) - len(failed)}/{len(rows)} nguon OK"
               + (f", {len(stale)} nguon DONG BANG: {stale}" if stale else ""))
    if failed or stale:
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

    ingested = []
    for src in load_sources():
        res = pipeline.ingest_one(run_id, src.id)
        ingested.append(res)
        flag = "FAIL " if not res["ok"] else ("STALE" if res["stale"] else "     ")
        typer.echo(f"  {flag} {src.id:26s} new={res['new']:3d} {res['error'] or ''}")

    result = pipeline.build_digest(run_id, day)
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    if send and result.get("digest_id"):
        typer.echo(json.dumps(pipeline.deliver(run_id, result["digest_id"], day),
                              ensure_ascii=False))

    # Cung tieu chi voi task finalize cua Airflow: nguon hong hoac dong bang
    # thi run la 'partial', khong phai 'success'.
    failed = [r["source_id"] for r in ingested if not r["ok"]]
    stale = [r["source_id"] for r in ingested if r["stale"]]
    pipeline.finalize(
        run_id,
        "partial" if (failed or stale) else "success",
        {**(result.get("metrics") or {}),
         "new_articles": sum(r["new"] for r in ingested),
         "failed_sources": failed,
         "stale_sources": stale},
    )


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
    # Chi giu tien to du de nhan dang (sk-proj, https://chat...), khong lo phan bi mat.
    for key in ("openai_api_key", "google_chat_webhook_url", "postgres_dsn"):
        value = str(data.get(key) or "")
        if value:
            data[key] = f"{value[:8]}...({len(value)} ky tu)"
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    app()
